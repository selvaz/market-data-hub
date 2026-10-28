"""Offline real-publication and official read-card checks for M9i."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import pytest
import requests
from market_data_hub.macro_releases import open_writer, run_daily
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.catalogue import LATAM_FAMILIES
from market_data_hub.macro_releases.http import Document, FetchError
from market_data_hub.macro_releases.latam import LatamAdapter
from market_data_hub.macro_releases.latam_editions import EDITIONS, SCHEDULES
from market_data_hub.macro_releases.schedule import RUN_INSTANT

ROOT = Path(__file__).parent / "fixtures/macro_releases/m9i"
GOLDEN = json.loads((ROOT / "golden-set.json").read_text())["cases"]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network forbidden in M9i tests")
    monkeypatch.setattr(requests.Session, "request", forbidden)
    token = RUN_INSTANT.set("2026-10-27T18:00:00Z")
    yield
    RUN_INSTANT.reset(token)


def document(name):
    body = (ROOT / name).read_bytes()
    meta = json.loads((ROOT / (name + ".meta.json")).read_text())
    assert hashlib.sha256(body).hexdigest() == meta["document_hash"]
    return Document(body, meta["url"], meta["retrieved_at"], meta["document_hash"])


def parse(case, doc=None):
    from importlib import import_module
    module = import_module("market_data_hub.macro_releases." + dict(
        bcb_policy="bcb_policy", banxico_policy="banxico_policy", mx_cpi="inegi_cpi")[case["family"]])
    args = [doc or document(case["fixture"])]
    if case.get("previous"):
        args.append(document(case["previous"]))
    return module.parse(*args, family=case["family"])


@pytest.mark.parametrize("case", GOLDEN, ids=lambda c: c["indicator"] + ":" + c["edition"])
def test_golden_official_read_card(case, tmp_path):
    row = next(r for r in parse(case) if r.indicator_id == case["indicator"])
    assert (row.value, row.period, row.edition, row.stage) == (
        case["value"], case["period"], case["edition"], case["stage"])
    assert row.document.url == case["source_url"]
    assert row.series_key == row.indicator_id + ":published"
    with open_writer(tmp_path / "golden.sqlite") as store:
        persist(store, [row])
        cards = store.release_cards(row.published_at[:10], row.published_at[:10], indicators=[row.indicator_id])["cards"]
        card = next(c for c in cards if c.get("resolution"))
        assert card["resolution"]["status"] == card["source_class"] == "official"
        assert card["resolution"]["value"] == case["value"]
        assert card["resolution"]["edition"] == case["edition"]


def test_calendars_and_two_editions_per_family():
    for family, measures in LATAM_FAMILIES.items():
        cases = [c for c in GOLDEN if c["family"] == family]
        assert len({c["edition"] for c in cases}) >= 2
        assert SCHEDULES[family].startswith("https://")
        assert max(EDITIONS[family])[:7] == "2026-12"
        assert {c["indicator"] for c in cases} == {m.id for m in measures}


class Replay:
    def __init__(self, **kwargs):
        self.requests = []

    def get(self, provider, url, params=None):
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        self.requests.append(url)
        for path in ROOT.glob("*.meta.json"):
            if json.loads(path.read_text())["url"] == url:
                return document(path.name.removesuffix(".meta.json"))
        raise AssertionError("Unrecorded issuer URL: " + url)


@pytest.mark.parametrize("family", LATAM_FAMILIES)
def test_daily_and_idempotence(family, tmp_path, monkeypatch):
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


@pytest.mark.parametrize("case", GOLDEN)
def test_unreviewed_origin_and_format_fail_closed(case):
    doc = document(case["fixture"])
    for changed in (replace(doc, url="https://example.invalid/unreviewed"), replace(doc, body=b"unknown format")):
        with pytest.raises(FetchError, match="needs review"):
            parse(case, changed)


def test_horizon_fail_closed():
    token = RUN_INSTANT.set("2027-01-01T00:00:00Z")
    try:
        with pytest.raises(FetchError, match="needs review"):
            LatamAdapter(Replay()).fetch(list(LATAM_FAMILIES), "2026-01")
    finally:
        RUN_INSTANT.reset(token)
