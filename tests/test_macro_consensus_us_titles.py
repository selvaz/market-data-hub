"""Reviewed FF aliases for US activity, retail, CPI y/y and AHE titles.

The fixture uses the exact FF weekly-feed event shape (title, country, date,
impact, forecast, previous). No network and no production database.
"""
import json
from pathlib import Path

import pytest
import requests
from test_macro_consensus_ingest import archive, rows

from market_data_hub.macro_releases import consensus, open_writer
from market_data_hub.macro_releases.catalogue import INDICATORS

FIXTURE = Path(__file__).parent / "fixtures/macro_releases/ff/us-titles-2026-10.json"
EVENTS = json.loads(FIXTURE.read_text(encoding="utf-8"))

# FF title -> (indicator, reviewed stage, issuer event id, headline period)
EXPECTED = {
    "CPI y/y": ("us_cpi_headline_yy", "regular", "BLS:us_cpi:regular:2026-10-14", "2026-09"),
    "Core CPI y/y": ("us_cpi_core_yy", "regular", "BLS:us_cpi:regular:2026-10-14", "2026-09"),
    "Retail Sales m/m": ("us_retail_sales_mm", "advance",
                         "Census:us_retail_sales:advance:2026-10-15", "2026-09"),
    "Core Retail Sales m/m": ("us_retail_sales_ex_auto_mm", "advance",
                              "Census:us_retail_sales:advance:2026-10-15", "2026-09"),
    "Industrial Production m/m": ("us_industrial_production_mm", "regular",
                                  "FederalReserve:us_industrial_production:regular:2026-10-16",
                                  "2026-09"),
    "Capacity Utilization Rate": ("us_capacity_utilization", "regular",
                                  "FederalReserve:us_industrial_production:regular:2026-10-16",
                                  "2026-09"),
    "Average Hourly Earnings m/m": ("us_avg_hourly_earnings_mm", "regular",
                                    "BLS:us_ahe:regular:2026-11-06", "2026-10"),
}
NEAR_MISSES = ("Industrial Production y/y", "Average Hourly Earnings y/y", "Core Retail Sales y/y")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("consensus linking must not use the network")
    monkeypatch.setattr(requests.Session, "request", forbidden)


@pytest.fixture
def store(tmp_path):
    with open_writer(tmp_path / "macro.sqlite") as store:
        for indicator in INDICATORS.values():
            store.put(indicator)
        yield store


def test_fixture_covers_every_title_and_near_miss():
    assert {e["title"] for e in EVENTS} == set(EXPECTED) | set(NEAR_MISSES)
    assert all(e["country"] == "USD" and e["forecast"] for e in EVENTS)


@pytest.mark.parametrize("title", sorted(EXPECTED))
def test_reviewed_alias_matches_exact_indicator_and_stage(store, title):
    indicator, stage, _, _ = EXPECTED[title]
    item = next(e for e in EVENTS if e["title"] == title)
    mapping, reason = consensus.Catalogue(store).match(item, consensus.mapping_title(item))
    assert reason is None
    assert (mapping["indicator_id"], mapping["stage"], mapping["currency"]) == (indicator, stage, "USD")
    assert mapping["geography"] == INDICATORS[indicator].geography == "USA"


def test_core_retail_sales_is_ex_motor_vehicles_and_ahe_is_monthly():
    assert "excluding motor vehicle" in INDICATORS["us_retail_sales_ex_auto_mm"].definition
    ahe = INDICATORS["us_avg_hourly_earnings_mm"]
    assert ahe.transform == "mm" and "mm percent change" in ahe.definition


@pytest.mark.parametrize("title", NEAR_MISSES)
def test_near_miss_titles_do_not_map(store, title):
    item = next(e for e in EVENTS if e["title"] == title)
    mapping, reason = consensus.Catalogue(store).match(item, title)
    assert mapping is None and reason == "unmapped"


def ingest_fixture(store, raw):
    archive(raw, EVENTS, filename="2026-10-13.json", captured="2026-10-13T21:30:00Z")
    return consensus.ingest(store, raw)


def assert_linked(store):
    linked = {r["indicator_id"]: r for r in rows(store)}
    assert set(linked) == {v[0] for v in EXPECTED.values()}
    for title, (indicator, _, event_id, period) in EXPECTED.items():
        item = next(e for e in EVENTS if e["title"] == title)
        reading = linked[indicator]
        assert reading["release_event_id"] == event_id
        assert reading["reference_period"] == period
        assert reading["value"] == float(item["forecast"].rstrip("%"))
        assert reading["pre_release_eligible"] == 1


def test_snapshot_links_every_title_and_skips_near_misses(store, tmp_path):
    result = ingest_fixture(store, tmp_path / "raw")
    assert result["status"] == "ok" and result["readings_added"] == len(EXPECTED), result
    assert_linked(store)
    skipped = {(s["title"], s["reason"]) for s in result["skipped_titles"]}
    assert skipped == {(t, "unmapped") for t in NEAR_MISSES}


def test_alias_file_change_relinks_an_already_processed_snapshot(store, tmp_path, monkeypatch):
    # A snapshot first processed without these reviewed aliases is checkpointed
    # as unmapped. The alias-file hash is part of the checkpoint version, so the
    # next ordinary ingest re-evaluates it and links the past capture.
    reduced = json.loads(consensus.ALIAS_FILE.read_text(encoding="utf-8"))
    reduced["aliases"] = [a for a in reduced["aliases"]
                          if not (a["currency"] == "USD" and a["title"] in EXPECTED)]
    stripped = tmp_path / "ff_aliases_v1.json"
    stripped.write_text(json.dumps(reduced), encoding="utf-8")
    real = consensus.ALIAS_FILE
    monkeypatch.setattr(consensus, "ALIAS_FILE", stripped)
    raw = tmp_path / "raw"
    first = ingest_fixture(store, raw)
    assert first["readings_added"] == 0
    assert {s["title"] for s in first["skipped_titles"] if s["reason"] == "unmapped"} >= set(EXPECTED)
    assert consensus.ingest(store, raw)["snapshots_unchanged"] == 1

    monkeypatch.setattr(consensus, "ALIAS_FILE", real)
    second = consensus.ingest(store, raw)
    assert second["snapshots_processed"] == 1 and second["snapshots_unchanged"] == 0
    assert second["readings_added"] == len(EXPECTED), second
    assert_linked(store)
    assert consensus.ingest(store, raw)["snapshots_unchanged"] == 1


def test_cli_reprocess_relinks_without_duplicates(tmp_path):
    db, raw = tmp_path / "copy.sqlite", tmp_path / "raw"
    with open_writer(db) as store:
        ingest_fixture(store, raw)
        before = len(rows(store))
    assert consensus.main(["--db", str(db), "--raw-dir", str(raw), "--reprocess"]) == 0
    with open_writer(db) as store:
        assert len(rows(store)) == before == len(EXPECTED)
