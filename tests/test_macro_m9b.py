"""Offline golden certification from unmodified, hashed issuer publications."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest
import requests

from market_data_hub.macro_releases import open_reader, open_writer, run_daily
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.eurostat_activity import parse as parse_eurostat
from market_data_hub.macro_releases.http import Document, FetchError

ROOT = Path(__file__).parent / "fixtures/macro_releases/m9b"
GOLDEN = json.loads((ROOT / "golden-set.json").read_text())["cases"]


def parse(doc, family, since="1900-01"):
    if family == "uk_total_pay":
        from market_data_hub.macro_releases.ons_pay import parse as parse_pay
        return parse_pay(doc, family, since)
    if family == "us_new_home_sales":
        from market_data_hub.macro_releases.census_sales import parse as parse_census
        return parse_census(doc, family, since)
    if family == "uk_retail_sales":
        from market_data_hub.macro_releases.ons_retail import parse as parse_ons
        return parse_ons(doc, family, since)
    return parse_eurostat(doc, family, since)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network forbidden in M9b tests")
    monkeypatch.setattr(requests.Session, "request", forbidden)


def document(name):
    body = (ROOT / name).read_bytes()
    meta = json.loads((ROOT / (name + ".meta.json")).read_text())
    assert hashlib.sha256(body).hexdigest() == meta["sha256"]
    return Document(body, meta["url"], meta["retrieved_at"], meta["sha256"])


@pytest.mark.parametrize("case", GOLDEN, ids=lambda c: c["family"] + ":" + c["edition"])
def test_golden(case, tmp_path):
    rows = parse(document(case["fixture"]), case["family"], "2026-01")
    row = next(o for o in rows if o.indicator_id == case["indicator"] and o.period == case["period"])
    assert (row.value, row.edition, row.stage) == (case["value"], case["edition"], case["stage"])
    assert row.document.url == case["source_url"]
    with open_writer(tmp_path / "golden.sqlite") as store:
        persist(store, [row])
        assert store.latest_official(row.indicator_id)[0]["value"] == case["value"]


def test_unknown_eurostat_edition_and_membership_fail_closed():
    doc = document("ea-unemployment.html")
    for before, after in ((b"2026-10-01", b"2026-10-02"), (b"Euro area (EA21)", b"Euro area (EA22)"),
                          (b"seasonally adjusted unemployment rate", b"unadjusted unemployment rate")):
        with pytest.raises(FetchError, match="needs review"):
            parse(replace(doc, body=doc.body.replace(before, after)), "ea_unemployment")
