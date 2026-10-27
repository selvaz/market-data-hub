"""Offline official Japanese/Chinese publication and edition regression tests."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest
import requests

from market_data_hub.macro_releases import open_writer, run_daily
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.asia import AsiaAdapter
from market_data_hub.macro_releases.asia_editions import EDITIONS, SCHEDULES
from market_data_hub.macro_releases.catalogue import ASIA_FAMILIES
from market_data_hub.macro_releases.http import Document, FetchError
from market_data_hub.macro_releases.schedule import RUN_INSTANT, due

ROOT = Path(__file__).parent / "fixtures/macro_releases/m9d1"
GOLDEN = json.loads((ROOT / "golden-set.json").read_text())["cases"]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network forbidden in M9d1 tests")
    monkeypatch.setattr(requests.Session, "request", forbidden)
    token = RUN_INSTANT.set("2026-10-27T18:00:00Z")
    yield
    RUN_INSTANT.reset(token)


def document(name):
    body = (ROOT / name).read_bytes()
    meta = json.loads((ROOT / (name + ".meta.json")).read_text())
    assert hashlib.sha256(body).hexdigest() == meta["document_hash"]
    return Document(body, meta["url"], meta["retrieved_at"], meta["document_hash"])


def parser(family):
    from market_data_hub.macro_releases import nbs_macro
    return nbs_macro.parse


@pytest.mark.parametrize("case", GOLDEN, ids=lambda c: c["family"] + ":" + c["edition"] + ":" + c["indicator"])
def test_issuer_golden_and_read_card(case, tmp_path):
    rows = parser(case["family"])(document(case["fixture"]), case["family"])
    row = next(r for r in rows if r.indicator_id == case["indicator"])
    assert (row.value, row.period, row.edition, row.stage) == (
        case["value"], case["period"], case["edition"], case["stage"])
    assert row.document.url == case["source_url"]
    assert row.series_key.startswith(row.indicator_id + ":")
    with open_writer(tmp_path / "golden.sqlite") as store:
        persist(store, [row])
        cards = store.release_cards(row.edition, row.edition, indicators=[row.indicator_id])["cards"]
        actual = next(c for c in cards if c.get("resolution"))
        assert actual["source_class"] == actual["resolution"]["status"] == "official"
        assert actual["resolution"]["value"] == case["value"]
        assert actual["resolution"]["edition"] == case["edition"]


def test_family_calendars_and_golden_completeness():
    for family in ASIA_FAMILIES:
        cases = [c for c in GOLDEN if c["family"] == family]
        assert len({c["edition"] for c in cases}) >= 2
        assert SCHEDULES[family].startswith("https://")
        assert max(EDITIONS[family]) <= "2026-12-31"
        assert {c["indicator"] for c in cases} == {i.id for i in ASIA_FAMILIES[family]}


class Replay:
    def __init__(self, **kwargs):
        self.requests = []

    def get(self, provider, url, params=None):
        self.requests.append(url)
        for path in ROOT.glob("*.meta.json"):
            meta = json.loads(path.read_text())
            if meta["url"] == url:
                return document(path.name.removesuffix(".meta.json"))
        raise AssertionError("Unrecorded issuer URL: " + url)


@pytest.mark.parametrize("family", ASIA_FAMILIES)
def test_daily_schedules_and_idempotence(family, tmp_path, monkeypatch):
    monkeypatch.setattr(run_daily, "HttpClient", Replay)
    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 27, 18, tzinfo=timezone.utc)
    monkeypatch.setattr(run_daily, "datetime", Clock)
    target = tmp_path / "daily.sqlite"
    first = run_daily.execute(target, [family], 24, False, raw_dir=tmp_path / "none")
    assert first["families"][family]["status"] == "ok", first
    assert first["families"][family]["vintages_added"] >= 2
    second = run_daily.execute(target, [family], 24, False, raw_dir=tmp_path / "none")
    assert second["families"][family]["status"] == "no_change", second


def test_cpi_unknown_date_clock_measure_and_disagreement_fail_closed():
    doc = document("cn-cpi-sep.html")
    for before, after in ((b"2026/10/15 09:30", b"2026/10/16 09:30"),
                          (b"2026/10/15 09:30", b"2026/10/15 10:00"),
                          (b"Consumer Price Index in September 2026", b"Consumer Price Index in August 2026"),
                          (b"increased by 0.9% year on year", b"increased by 9.9% year on year")):
        assert before in doc.body
        with pytest.raises(FetchError, match="needs review"):
            parser("cn_cpi")(replace(doc, body=doc.body.replace(before, after)), "cn_cpi")


def test_release_instant_and_horizon_fail_closed():
    client = Replay()
    token = RUN_INSTANT.set("2026-09-10T01:29:59Z")
    try:
        assert AsiaAdapter(client).fetch(["cn_cpi"], "2026-01") == []
        assert client.requests == []
    finally:
        RUN_INSTANT.reset(token)
    token = RUN_INSTANT.set("2027-01-01T00:00:00Z")
    try:
        with pytest.raises(FetchError, match="needs review"):
            AsiaAdapter(client).fetch(["cn_cpi"], "2026-01")
    finally:
        RUN_INSTANT.reset(token)
