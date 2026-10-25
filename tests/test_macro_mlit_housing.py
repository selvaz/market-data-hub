"""Offline MLIT residential-starts headlines and the issuer's Reiwa calendar."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib

import pytest

from market_data_hub.macro_releases import mlit_housing as jp, open_reader, run_daily
from market_data_hub.macro_releases.http import FetchError
from market_data_hub.macro_releases.schedule import RUN_INSTANT, PENDING_EDITIONS, release_instant
from test_macro_m9f import document, offline as offline


class Replay:
    def __init__(self, **kwargs):
        self.requests = []

    def get(self, provider, url, params=None):
        assert provider == "mlit" and not params
        self.requests.append(url)
        if url == jp.CALENDAR:
            return document("mlit-calendar-current.html")
        if url == jp.index_url("2026-09-30"):
            return document("mlit-september-index.html")
        for name in ("mlit-july2026.html", "mlit-august2026.html"):
            doc = document(name)
            if url == doc.url:
                return doc
        raise AssertionError("Unexpected MLIT request: " + url)


def test_calendar_clock_real_regular_stages_and_index_discovery(monkeypatch):
    from market_data_hub.macro_releases.read_api import indicators_for_stage
    assert set(jp.calendar_editions(document("mlit-calendar-current.html"))) == {"2026-08-31", "2026-09-30", "2026-10-30", "2026-11-30", "2026-12-25"}
    assert release_instant("2026-09-30", family=jp.FAMILY).isoformat() == "2026-09-30T05:00:00+00:00"
    assert release_instant("2026-12-25", family=jp.FAMILY).hour == 5
    assert jp.publication_link(document("mlit-september-index.html"), "2026-09-30") == jp.ARCHIVES["2026-09-30"]
    monkeypatch.setattr(jp, "ARCHIVES", {"2026-08-31": jp.ARCHIVES["2026-08-31"]})
    assert len(jp.fetch(Replay())) == 2
    assert indicators_for_stage(jp.FAMILY, "regular") == {"jp_housing_starts_yy"}
    assert indicators_for_stage(jp.FAMILY, "advance") == set()


@pytest.mark.parametrize("before,after", [("令和8年9月30日", "令和8年9月29日"),
    ("令和８年８月分", "令和８年７月分"), ("全体で前年同月比", "持家だけで前年同月比"),
    ("◇住宅着工統計", "◇受注動態統計"), ("6.1％の増加", "未公表の増加")])
def test_changed_date_scope_reference_or_value_needs_review(before, after):
    doc = document("mlit-august2026.html")
    assert before.encode() in doc.body
    with pytest.raises(FetchError, match="needs review"):
        jp.parse(replace(doc, body=doc.body.replace(before.encode(), after.encode())))


@pytest.mark.parametrize("before,after", [("12月25日14時00分", "12月26日14時00分"),
    ("10月30日14時00分", "10月30日15時00分"), ("令和8年10月分", "令和8年11月分")])
def test_calendar_drift_needs_review(before, after):
    doc = document("mlit-calendar-current.html")
    assert before.encode() in doc.body
    with pytest.raises(FetchError, match="needs review"):
        jp.calendar_editions(replace(doc, body=doc.body.replace(before.encode(), after.encode())))


def test_national_annual_headline_not_saar_monthly_or_nonresidential():
    july, = jp.parse(document("mlit-july2026.html"))
    august, = jp.parse(document("mlit-august2026.html"))
    assert (july.value, july.period) == (8.2, "2026-07")
    assert (august.value, august.period) == (6.1, "2026-08")
    assert "前月比2.7％の減少".encode() in august.document.body
    decrease = replace(august.document, body=august.document.body.replace("6.1％の増加".encode(), "6.1％の減少".encode()))
    assert jp.parse(decrease)[0].value == -6.1
    assert jp.parse(august.document, since="2026-09") == []
    with pytest.raises(FetchError, match="needs review"):
        jp.parse(replace(august.document, url=july.document.url))


def test_release_instant_publication_delay_and_horizon():
    class Missing(Replay):
        def get(self, provider, url, params=None):
            if url == jp.ARCHIVES["2026-09-30"]:
                raise FetchError("mlit: HTTP 404")
            return super().get(provider, url, params)
    token = RUN_INSTANT.set("2026-08-31T04:59:59Z")
    pending = PENDING_EDITIONS.set({})
    try:
        client = Replay()
        assert jp.fetch(client) == [] and client.requests == []
        RUN_INSTANT.set("2026-09-30T04:59:59Z")
        assert len(jp.fetch(Replay())) == 1
        RUN_INSTANT.set("2026-09-30T05:00:00Z")
        assert len(jp.fetch(Replay())) == 2
        assert len(jp.fetch(Missing())) == 1
        assert (jp.FAMILY, "2026-09-30") in PENDING_EDITIONS.get()
        PENDING_EDITIONS.get().clear()
        RUN_INSTANT.set("2026-09-30T11:00:00Z")
        with pytest.raises(FetchError, match="missing; needs review"):
            jp.fetch(Missing())
        RUN_INSTANT.set("2027-01-01T00:00:00Z")
        with pytest.raises(FetchError, match="horizon exhausted; needs review"):
            jp.fetch(Replay())
    finally:
        RUN_INSTANT.reset(token)
        PENDING_EDITIONS.reset(pending)


def test_latest_official_card_idempotence_and_changed_source_review(tmp_path, monkeypatch):
    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 24, 18, tzinfo=timezone.utc)
    monkeypatch.setattr(run_daily, "datetime", Clock)
    monkeypatch.setattr(run_daily, "HttpClient", Replay)
    path = tmp_path / "mlit.sqlite"
    result = run_daily.execute(path, [jp.FAMILY], 24, False, raw_dir=tmp_path / "raw")
    assert result["families"][jp.FAMILY]["status"] == "ok", result
    with open_reader(path) as store:
        card, = store.release_cards("2026-09-30", "2026-09-30", indicators=["jp_housing_starts_yy"])["cards"]
        assert (card["reference_period"], card["resolution"]["value"], card["source_class"], card["released_at"]) == (
            "2026-08", 6.1, "official", "2026-09-30T05:00:00.000000+00:00")
    assert run_daily.execute(path, [jp.FAMILY], 24, False, raw_dir=tmp_path / "raw")["families"][jp.FAMILY]["status"] == "no_change"
    class Changed(Replay):
        def get(self, *args, **kwargs):
            doc = super().get(*args, **kwargs)
            body = doc.body.replace("全体で前年同月比".encode(), "持家だけで前年同月比".encode())
            return replace(doc, body=body, document_hash=hashlib.sha256(body).hexdigest())
    monkeypatch.setattr(run_daily, "HttpClient", Changed)
    fresh = tmp_path / "review.sqlite"
    assert run_daily.execute(fresh, [jp.FAMILY], 24, False, raw_dir=tmp_path / "raw")["families"][jp.FAMILY]["status"] == "needs_review"
    with open_reader(fresh) as store:
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 0
