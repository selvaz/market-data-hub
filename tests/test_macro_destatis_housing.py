"""Saved Destatis editions, calendar identity and fail-closed national measure."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from urllib.parse import urlencode

import pytest

from market_data_hub.macro_releases import destatis_housing as de, open_reader, run_daily
from market_data_hub.macro_releases.http import FetchError
from market_data_hub.macro_releases.schedule import RUN_INSTANT, PENDING_EDITIONS, release_instant
from test_macro_m9f import document, offline as offline


class Replay:
    def __init__(self, **kwargs):
        self.requests = []

    def get(self, provider, url, params=None):
        assert provider == "destatis"
        if params:
            url += "?" + urlencode(params)
        self.requests.append(url)
        if url == de.CALENDAR:
            return document("destatis-permits-calendar.html")
        if url == de.INDEX:
            return document("destatis-permits-index.html")
        for edition, month in (("2026-09-18", "july"), ("2026-10-16", "august")):
            if url == de.ARCHIVES[edition]:
                return document("destatis-" + month + "2026.html")
        raise AssertionError("Unexpected Destatis request: " + url)


def test_real_calendar_clock_and_filtered_publication_discovery():
    from market_data_hub.macro_releases.read_api import indicators_for_stage
    assert set(de.calendar_editions(document("destatis-permits-calendar.html"))) == {"2026-11-18", "2026-12-18"}
    assert release_instant("2026-10-16", family=de.FAMILY).hour == 6
    assert release_instant("2026-11-18", family=de.FAMILY).hour == 7
    assert de.publication_links(document("destatis-permits-index.html")) == de.ARCHIVES
    assert indicators_for_stage(de.FAMILY, "regular") == {de.FAMILY, de.FAMILY + "_yy"}
    assert indicators_for_stage(de.FAMILY, "advance") == set()


@pytest.mark.parametrize("before,after", [
    ("16. Oktober 2026", "15. Oktober 2026"),
    ("20\u00a0600", "30\u00a0601"),
    ("Wie das Statistische Bundesamt (Destatis) mitteilt, waren das 6,8", "Wie das Statistische Bundesamt (Destatis) mitteilt, waren das 7,8"),
    ("Wohnungen genehmigt", "Gebäude genehmigt"),
    ("mehr als im August", "weniger als im August"),
])
def test_changed_scope_headline_or_date_needs_review(before, after):
    doc = document("destatis-august2026.html")
    assert before.encode() in doc.body
    with pytest.raises(FetchError, match="needs review"):
        de.parse(replace(doc, body=doc.body.replace(before.encode(), after.encode())))


@pytest.mark.parametrize("before,after", [(b"2026.11.18", b"2026.11.19"),
    (b"October 2026", b"November 2026"), (b"at 8 a.m.", b"at 9 a.m.")])
def test_calendar_drift_needs_review(before, after):
    doc = document("destatis-permits-calendar.html")
    assert before in doc.body
    with pytest.raises(FetchError, match="needs review"):
        de.calendar_editions(replace(doc, body=doc.body.replace(before, after)))


def test_monthly_national_dwellings_not_newbuild_subset_or_year_to_date():
    july = de.parse(document("destatis-july2026.html"))
    august = de.parse(document("destatis-august2026.html"))
    assert [(r.value, r.period) for r in july] == [(22500, "2026-07"), (1.9, "2026-07")]
    assert [(r.value, r.period) for r in august] == [(20600, "2026-08"), (6.8, "2026-08")]
    assert august[0].printed == "20 600"
    assert de.parse(august[0].document, since="2026-09") == []
    with pytest.raises(FetchError, match="needs review"):
        de.parse(replace(august[0].document, url="https://fred.stlouisfed.org/series/OTHER"))


def test_release_gate_publication_delay_and_reviewed_horizon():
    class Historical(Replay):
        def get(self, *args, **kwargs):
            doc = super().get(*args, **kwargs)
            if doc.url == de.CALENDAR:
                # Restore the past date, corroborated by the saved release,
                # when replaying before the rolling calendar dropped it.
                body = doc.body.replace(b"</body>", b"<p>Building permits Reporting period : August 2026 Date of issue : 2026.10.16 ICS/iCal</p></body>")
                return replace(doc, body=body)
            return doc
    class Missing(Historical):
        def get(self, provider, url, params=None):
            if url == de.ARCHIVES["2026-10-16"]:
                raise FetchError("destatis: HTTP 404")
            return super().get(provider, url, params)
    token = RUN_INSTANT.set("2026-09-18T05:59:59Z")
    pending = PENDING_EDITIONS.set({})
    try:
        client = Replay()
        assert de.fetch(client) == [] and client.requests == []
        RUN_INSTANT.set("2026-10-16T05:59:59Z")
        assert len(de.fetch(Historical())) == 2
        RUN_INSTANT.set("2026-10-16T06:00:00Z")
        assert len(de.fetch(Historical())) == 4
        assert len(de.fetch(Missing())) == 2
        assert (de.FAMILY, "2026-10-16") in PENDING_EDITIONS.get()
        PENDING_EDITIONS.get().clear()
        RUN_INSTANT.set("2026-10-16T12:00:00Z")
        with pytest.raises(FetchError, match="missing; needs review"):
            de.fetch(Missing())
        RUN_INSTANT.set("2027-01-01T00:00:00Z")
        with pytest.raises(FetchError, match="horizon exhausted; needs review"):
            de.fetch(Replay())
    finally:
        RUN_INSTANT.reset(token)
        PENDING_EDITIONS.reset(pending)


def test_latest_official_card_repeat_and_changed_source_review(tmp_path, monkeypatch):
    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 24, 18, tzinfo=timezone.utc)
    monkeypatch.setattr(run_daily, "datetime", Clock)
    monkeypatch.setattr(run_daily, "HttpClient", Replay)
    path = tmp_path / "permits.sqlite"
    result = run_daily.execute(path, [de.FAMILY], 24, False, raw_dir=tmp_path / "raw")
    assert result["families"][de.FAMILY]["status"] == "ok", result
    with open_reader(path) as store:
        cards = store.release_cards("2026-10-16", "2026-10-16", indicators=[de.FAMILY, de.FAMILY + "_yy"])["cards"]
        assert {c["indicator_id"]: c["resolution"]["value"] for c in cards} == {de.FAMILY: 20600, de.FAMILY + "_yy": 6.8}
        assert all(c["source_class"] == "official" and c["reference_period"] == "2026-08" and c["released_at"] == "2026-10-16T06:00:00.000000+00:00" for c in cards)
    assert run_daily.execute(path, [de.FAMILY], 24, False, raw_dir=tmp_path / "raw")["families"][de.FAMILY]["status"] == "no_change"
    class Changed(Replay):
        def get(self, *args, **kwargs):
            doc = super().get(*args, **kwargs)
            body = doc.body.replace(b"Wohnungen genehmigt", b"Gebaeude genehmigt")
            return replace(doc, body=body, document_hash=hashlib.sha256(body).hexdigest())
    monkeypatch.setattr(run_daily, "HttpClient", Changed)
    fresh = tmp_path / "review.sqlite"
    result = run_daily.execute(fresh, [de.FAMILY], 24, False, raw_dir=tmp_path / "raw")
    assert result["families"][de.FAMILY]["status"] == "needs_review", result
    with open_reader(fresh) as store:
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 0
