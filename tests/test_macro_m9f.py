"""Offline issuer publications, edition gates, review retention and read cards."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest
import requests

from market_data_hub.macro_releases import open_reader, open_writer, run_daily
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.housing import PARSERS
from market_data_hub.macro_releases.http import Document, FetchError
from market_data_hub.macro_releases.schedule import RUN_INSTANT

ROOT = Path(__file__).parent / "fixtures/macro_releases/m9f"
GOLDEN = json.loads((ROOT / "golden-set.json").read_text())["cases"]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network forbidden in M9f tests")
    monkeypatch.setattr(requests.Session, "request", forbidden)
    token = RUN_INSTANT.set(datetime(2026, 10, 24, 18, tzinfo=timezone.utc))
    yield
    RUN_INSTANT.reset(token)


def document(name):
    body = (ROOT / name).read_bytes()
    meta = json.loads((ROOT / (name + ".meta.json")).read_text())
    assert hashlib.sha256(body).hexdigest() == meta["sha256"]
    return Document(body, meta["url"], meta["retrieved_at"], meta["sha256"])


@pytest.mark.parametrize("case", GOLDEN, ids=lambda c: c["family"] + ":" + c["edition"] + ":" + c["indicator"])
def test_golden(case, tmp_path):
    rows = PARSERS[case["family"]](document(case["fixture"]), case["family"])
    row = next(o for o in rows if o.indicator_id == case["indicator"] and o.period == case["period"])
    assert (row.value, row.edition, row.stage) == (case["value"], case["edition"], "regular")
    assert row.document.url == case["source_url"]
    with open_writer(tmp_path / "golden.sqlite") as store:
        persist(store, [row])
        assert store.latest_official(row.indicator_id)[0]["value"] == row.value
        cards = store.release_cards(row.edition, row.edition, indicators=[row.indicator_id])["cards"]
        assert cards[0]["resolution"]["status"] == "official"


def test_unknown_fhfa_date_fails_closed():
    doc = document("fhfa-sept.pdf")
    with pytest.raises(FetchError, match="needs review"):
        from market_data_hub.macro_releases.housing_editions import reviewed
        reviewed("us_fhfa_hpi", "2027-01-01")


def test_every_housing_family_has_two_distinct_golden_editions_and_calendar():
    from market_data_hub.macro_releases.catalogue import HOUSING_FAMILIES
    from market_data_hub.macro_releases.housing_editions import EDITIONS, SCHEDULES
    for family in HOUSING_FAMILIES:
        assert len({c["edition"] for c in GOLDEN if c["family"] == family}) >= 2
        assert SCHEDULES[family].startswith("https://")
        assert max(EDITIONS[family]) <= "2026-12-31"
