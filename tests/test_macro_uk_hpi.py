"""Saved UK HPI issuer headlines, first-publication clocks and fail-closed gates."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib

import pytest

from market_data_hub.macro_releases import open_reader, run_daily, uk_hpi as hpi
from market_data_hub.macro_releases.http import FetchError
from market_data_hub.macro_releases.schedule import PENDING_EDITIONS, RUN_INSTANT
from test_macro_m9f import document, offline as offline


class Replay:
    def __init__(self, **kwargs):
        self.requests = []

    def get(self, provider, url, params=None):
        assert provider == "ons" and not params
        self.requests.append(url)
        if url == hpi.CALENDAR:
            return document("uk-hpi-calendar.html")
        for edition, name in (("2026-09-16", "july"), ("2026-10-21", "august")):
            if url == hpi.publication_url(edition):
                return document("uk-hpi-" + name + "2026.html")
        raise AssertionError("Unexpected UK HPI request: " + url)


@pytest.mark.parametrize("before,after", [
    (b"annual price change for a property in the UK was", b"annual price change for a property in England was"),
    (b"was <em>0.9%", b"was <em>9.9%"),
    (b"August 2026", b"July 2026"),
    (b"non-seasonally adjusted basis", b"seasonally adjusted basis"),
    (b'2026-10-21T09:30:08+01:00', b'2026-10-22T09:30:08+01:00'),
    (b'2026-10-21T09:30:08+01:00', b'2026-10-21T08:30:08+01:00'),
    (b'2026-10-21T09:30:08+01:00', b'2026-10-21T09:30:08'),
])
def test_changed_identity_period_value_or_clock_needs_review(before, after):
    doc = document("uk-hpi-august2026.html")
    assert before in doc.body
    with pytest.raises(FetchError, match="needs review"):
        hpi.parse(replace(doc, body=doc.body.replace(before, after)))


def test_first_publication_not_updated_time_or_revised_history():
    july, = hpi.parse(document("uk-hpi-july2026.html"))
    august, = hpi.parse(document("uk-hpi-august2026.html"))
    assert (july.value, july.edition, july.published_at) == (1.4, "2026-09-16", "2026-09-16T08:30:03+00:00")
    assert (august.value, august.period, august.published_at) == (0.9, "2026-08", "2026-10-21T08:30:08+00:00")
    assert july.issuer_timezone == "Europe/London"
    # The October report revises July to 1.2; it cannot overwrite July's 1.4.
    assert b"1.2%" in august.document.body
    for url in (august.document.url.replace("www.gov.uk", "example.com"), july.document.url):
        with pytest.raises(FetchError, match="needs review"):
            hpi.parse(replace(august.document, url=url))


@pytest.mark.parametrize("before,after", [(b"18 November 2026", b"19 November 2026"),
    (b"16 December 2026", b"16 December 2027"), (b"(9:30am)", b"(8:30am)")])
def test_changed_future_calendar_needs_review(before, after):
    doc = document("uk-hpi-calendar.html")
    assert before in doc.body
    with pytest.raises(FetchError, match="needs review"):
        hpi.calendar_editions(replace(doc, body=doc.body.replace(before, after)))


def test_calendar_real_stages_and_dst():
    from market_data_hub.macro_releases.read_api import indicators_for_stage
    from market_data_hub.macro_releases.schedule import release_instant
    assert set(hpi.calendar_editions(document("uk-hpi-calendar.html"))) == {"2026-11-18", "2026-12-16"}
    assert indicators_for_stage(hpi.FAMILY, "regular") == {"uk_hpi_yy"}
    assert indicators_for_stage(hpi.FAMILY, "flash") == set()
    assert release_instant("2026-10-21", family=hpi.FAMILY).hour == 8
    assert release_instant("2026-11-18", family=hpi.FAMILY).hour == 9


def test_release_instant_and_observed_seconds_both_gate_actuals():
    pending = PENDING_EDITIONS.set({})
    token = RUN_INSTANT.set("2026-09-16T08:29:59Z")
    try:
        replay = Replay()
        assert hpi.fetch(replay) == [] and replay.requests == []
        RUN_INSTANT.set("2026-10-21T08:30:00Z")
        assert [o.period for o in hpi.fetch(Replay())] == ["2026-07"]
        assert (hpi.FAMILY, "2026-10-21") in PENDING_EDITIONS.get()
        PENDING_EDITIONS.get().clear()
        RUN_INSTANT.set("2026-10-21T08:30:08Z")
        assert [o.period for o in hpi.fetch(Replay())] == ["2026-07", "2026-08"]
    finally:
        RUN_INSTANT.reset(token)
        PENDING_EDITIONS.reset(pending)


def test_new_404_grace_then_review_since_and_horizon():
    class Missing(Replay):
        def get(self, provider, url, params=None):
            if url == hpi.publication_url("2026-10-21"):
                raise FetchError("ons: HTTP 404")
            return super().get(provider, url, params)
    token = RUN_INSTANT.set("2026-10-21T08:30:00Z")
    pending = PENDING_EDITIONS.set({})
    try:
        assert len(hpi.fetch(Missing())) == 1
        assert (hpi.FAMILY, "2026-10-21") in PENDING_EDITIONS.get()
        RUN_INSTANT.set("2026-10-21T14:30:00Z")
        PENDING_EDITIONS.get().clear()
        with pytest.raises(FetchError, match="missing; needs review"):
            hpi.fetch(Missing())
        assert len(hpi.fetch(Replay(), since="2026-08")) == 1
        assert hpi.fetch(Replay(), since="2026-09") == []
        RUN_INSTANT.set("2027-01-01T00:00:00Z")
        with pytest.raises(FetchError, match="horizon exhausted; needs review"):
            hpi.fetch(Replay())
    finally:
        RUN_INSTANT.reset(token)
        PENDING_EDITIONS.reset(pending)


def test_daily_latest_cards_original_history_and_idempotence(tmp_path, monkeypatch):
    monkeypatch.setattr(run_daily, "HttpClient", Replay)
    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 24, 18, tzinfo=timezone.utc)
    monkeypatch.setattr(run_daily, "datetime", Clock)
    path = tmp_path / "hpi.sqlite"
    first = run_daily.execute(path, [hpi.FAMILY], 24, False, raw_dir=tmp_path / "raw")
    assert (first["families"][hpi.FAMILY]["status"], first["families"][hpi.FAMILY]["vintages_added"]) == ("ok", 2), first
    with open_reader(path) as store:
        card, = store.release_cards("2026-10-21", "2026-10-21", indicators=["uk_hpi_yy"])["cards"]
        assert (card["reference_period"], card["resolution"]["value"], card["released_at"], card["source_class"]) == (
            "2026-08", 0.9, "2026-10-21T08:30:08.000000+00:00", "official")
        assert {r["reference_period"]: r["value"] for r in store.latest_official("uk_hpi_yy")} == {"2026-07": 1.4, "2026-08": 0.9}
    assert run_daily.execute(path, [hpi.FAMILY], 24, False, raw_dir=tmp_path / "raw")["families"][hpi.FAMILY]["status"] == "no_change"


def test_changed_page_retained_without_partial_certification(tmp_path, monkeypatch):
    class Changed(Replay):
        def get(self, *args, **kwargs):
            doc = super().get(*args, **kwargs)
            body = doc.body.replace(b"annual price change for a property in the UK was", b"annual price change for a property in England was")
            return replace(doc, body=body, document_hash=hashlib.sha256(body).hexdigest())
    monkeypatch.setattr(run_daily, "HttpClient", Changed)
    path = tmp_path / "review.sqlite"
    result = run_daily.execute(path, [hpi.FAMILY], 24, False, raw_dir=tmp_path / "raw")
    assert result["families"][hpi.FAMILY]["status"] == "needs_review", result
    with open_reader(path) as store:
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 0
        assert store.conn.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0] > 0
