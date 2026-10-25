"""Offline BoE house-purchase approvals and exact monthly release gating."""
from dataclasses import replace
from datetime import datetime, timezone
from io import BytesIO
import hashlib
import re

import pytest

from market_data_hub.macro_releases import boe_housing as boe, open_reader, run_daily
from market_data_hub.macro_releases.http import FetchError
from market_data_hub.macro_releases.schedule import PENDING_EDITIONS, RUN_INSTANT, release_instant
from test_macro_m9f import document, offline as offline


class Replay:
    def __init__(self, **kwargs):
        self.requests = []

    def get(self, provider, url, params=None):
        assert provider == "boe" and not params
        self.requests.append(url)
        if url == boe.CALENDAR:
            return document("boe-tables-calendar.html")
        for edition, month in (("2026-09-01", "july"), ("2026-09-29", "august")):
            if url == boe.publication_url(edition):
                return document("boe-money-credit-" + month + "2026.html")
        raise AssertionError("Unexpected BoE request: " + url)


def test_issuer_clock_calendar_and_only_real_regular_editions():
    from pypdf import PdfReader
    from market_data_hub.macro_releases.read_api import indicators_for_stage
    text = re.sub(r"\s+", " ", " ".join(p.extract_text() for p in PdfReader(BytesIO(document("boe-statistical-code.pdf").body)).pages))
    assert "Statistics are released at 9.30 am" in text
    assert set(boe.calendar_editions(document("boe-tables-calendar.html"))) == {"2026-10-29", "2026-11-30"}
    assert release_instant("2026-09-29", family=boe.FAMILY).hour == 8
    assert release_instant("2026-10-29", family=boe.FAMILY).hour == 9
    assert indicators_for_stage(boe.FAMILY, "regular") == {"uk_mortgage_approvals"}
    assert indicators_for_stage(boe.FAMILY, "advance") == set()
    assert b"5 January 2027" in document("boe-tables-calendar.html").body


@pytest.mark.parametrize("before,after", [
    (b"29 September 2026", b"28 September 2026"),
    (b"Money and Credit - August 2026", b"Money and Credit - July 2026"),
    (b"Net mortgage approvals for house purchases decreased to 54,900", b"Net mortgage approvals for remortgaging decreased to 54,900"),
    (b"decreased to 54,900 in August, below", b"decreased to 64,900 in August, below"),
    (b"(that is, approvals net of cancellations)", b"(including cancellations)"),
    (b"Chart 2: Mortgage approvals", b"Chart 2: All mortgage lending"),
    (b"Seasonally adjusted", b"Not adjusted"),
])
def test_changed_purpose_value_period_or_date_needs_review(before, after):
    doc = document("boe-money-credit-august2026.html")
    assert before in doc.body
    with pytest.raises(FetchError, match="needs review"):
        boe.parse(replace(doc, body=doc.body.replace(before, after)))


@pytest.mark.parametrize("before,after", [(b"29 October 2026", b"30 October 2026"),
    (b"30 November 2026", b"30 December 2026"), (b"September 2026 data", b"December 2026 data")])
def test_calendar_drift_needs_review(before, after):
    doc = document("boe-tables-calendar.html")
    assert before in doc.body
    with pytest.raises(FetchError, match="needs review"):
        boe.calendar_editions(replace(doc, body=doc.body.replace(before, after)))


def test_original_release_not_revised_history_or_remortgaging():
    july, = boe.parse(document("boe-money-credit-july2026.html"))
    august, = boe.parse(document("boe-money-credit-august2026.html"))
    assert (july.value, july.period) == (56100, "2026-07")
    assert (august.value, august.period) == (54900, "2026-08")
    assert b"55,900 in July" in august.document.body
    assert july.series_key == "LPMVTVX:release_headline_rounded_100"
    assert boe.parse(july.document, since="2026-08") == []
    with pytest.raises(FetchError, match="needs review"):
        boe.parse(replace(august.document, url=july.document.url))


def test_release_instant_and_missing_publication_grace():
    class Historical(Replay):
        def get(self, *args, **kwargs):
            doc = super().get(*args, **kwargs)
            if doc.url == boe.CALENDAR:
                # The captured rolling calendar has dropped September dates.
                # Reinsert the two dates corroborated by the saved publications
                # when replaying a September run; values stay in real fixtures.
                body = doc.body.replace(b"29 October 2026", b"1 September 2026 (July 2026 data) "
                    b"29 September 2026 (August 2026 data) 29 October 2026", 1)
                return replace(doc, body=body)
            return doc
    class Missing(Historical):
        def get(self, provider, url, params=None):
            if url == boe.publication_url("2026-09-29"):
                raise FetchError("boe: HTTP 404")
            return super().get(provider, url, params)
    token = RUN_INSTANT.set("2026-09-01T08:29:59Z")
    pending = PENDING_EDITIONS.set({})
    try:
        replay = Historical()
        assert boe.fetch(replay) == [] and replay.requests == []
        RUN_INSTANT.set("2026-09-29T08:29:59Z")
        assert len(boe.fetch(Historical())) == 1
        RUN_INSTANT.set("2026-09-29T08:30:00Z")
        assert len(boe.fetch(Historical())) == 2
        assert len(boe.fetch(Missing())) == 1
        assert (boe.FAMILY, "2026-09-29") in PENDING_EDITIONS.get()
        PENDING_EDITIONS.get().clear()
        RUN_INSTANT.set("2026-09-29T14:30:00Z")
        with pytest.raises(FetchError, match="missing; needs review"):
            boe.fetch(Missing())
        assert boe.fetch(Replay(), since="2026-09") == []
        RUN_INSTANT.set("2027-01-01T00:00:00Z")
        with pytest.raises(FetchError, match="horizon exhausted; needs review"):
            boe.fetch(Replay())
    finally:
        RUN_INSTANT.reset(token)
        PENDING_EDITIONS.reset(pending)


def test_daily_latest_official_card_and_idempotence(tmp_path, monkeypatch):
    monkeypatch.setattr(run_daily, "HttpClient", Replay)
    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 24, 18, tzinfo=timezone.utc)
    monkeypatch.setattr(run_daily, "datetime", Clock)
    path = tmp_path / "boe.sqlite"
    first = run_daily.execute(path, [boe.FAMILY], 24, False, raw_dir=tmp_path / "raw")
    assert (first["families"][boe.FAMILY]["status"], first["families"][boe.FAMILY]["vintages_added"]) == ("ok", 2), first
    with open_reader(path) as store:
        card, = store.release_cards("2026-09-29", "2026-09-29", indicators=["uk_mortgage_approvals"])["cards"]
        assert (card["reference_period"], card["resolution"]["value"], card["source_class"], card["released_at"]) == (
            "2026-08", 54900, "official", "2026-09-29T08:30:00.000000+00:00")
        assert {r["reference_period"]: r["value"] for r in store.latest_official("uk_mortgage_approvals")} == {"2026-07": 56100, "2026-08": 54900}
    assert run_daily.execute(path, [boe.FAMILY], 24, False, raw_dir=tmp_path / "raw")["families"][boe.FAMILY]["status"] == "no_change"


def test_changed_page_is_retained_for_review_without_partial_values(tmp_path, monkeypatch):
    class Changed(Replay):
        def get(self, *args, **kwargs):
            doc = super().get(*args, **kwargs)
            body = doc.body.replace(b"approvals net of cancellations", b"approvals including cancellations")
            return replace(doc, body=body, document_hash=hashlib.sha256(body).hexdigest())
    monkeypatch.setattr(run_daily, "HttpClient", Changed)
    path = tmp_path / "review.sqlite"
    result = run_daily.execute(path, [boe.FAMILY], 24, False, raw_dir=tmp_path / "raw")
    assert result["families"][boe.FAMILY]["status"] == "needs_review", result
    with open_reader(path) as store:
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 0
        assert store.conn.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0] > 0
