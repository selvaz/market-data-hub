"""C3/C4/C5 regressions from the shapes diagnosed on the production copy."""
from dataclasses import replace
import hashlib
import json

import pytest
import requests

from market_data_hub.macro_releases import open_writer
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.discovery import fetch_daily
from market_data_hub.macro_releases.http import FetchError
from market_data_hub.macro_releases.ons import ONSAdapter
from market_data_hub.macro_releases.run_daily import persist_counts
from test_macro_official_adapters import document
from test_macro_daily_review import Replay
from test_macro_read_api import observation


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network forbidden in card-gap fixtures")
    monkeypatch.setattr(requests.Session, "request", forbidden)


def reread(obs, suffix="transport envelope changed"):
    body = obs.document.body + suffix.encode()
    return replace(obs, document=replace(obs.document, body=body,
                   document_hash=hashlib.sha256(body).hexdigest(), retrieved_at="2026-10-20T14:00:00Z"))


@pytest.mark.parametrize("indicator,family,source,period,stage", [
    ("us_gdp_qq_saar", "us_gdp", "BEA", "2026-Q2", "third"),
    ("uk_gdp_mm", "uk_gdp", "ONS", "2026-08", "regular"),
])
def test_unchanged_daily_reading_skipped_across_document_hashes(tmp_path, indicator, family, source, period, stage):
    obs = observation(indicator, family, period, .1, source=source, stage=stage)
    with open_writer(tmp_path / "macro.sqlite") as store:
        assert persist_counts(store, [obs]) == {"vintages_added": 1, "readings_added": 1, "readings_skipped": 0}
        first = dict(store.conn.execute("SELECT * FROM readings").fetchone())
        for suffix in ("request-id-2", "request-id-3"):
            assert persist_counts(store, [reread(obs, suffix)]) == {
                "vintages_added": 0, "readings_added": 0, "readings_skipped": 1}
        assert dict(store.conn.execute("SELECT * FROM readings").fetchone()) == first
        assert store.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 3
        # A real correction and a new edition with unchanged value both survive.
        changed = replace(reread(obs, "correction"), value=.2, printed="0.2")
        assert persist_counts(store, [changed])["readings_added"] == 1
        new_edition = replace(changed, edition="2026-10-21", published_at="2026-10-21T12:30:00Z")
        assert persist_counts(store, [new_edition])["readings_added"] == 1
        assert persist_counts(store, [reread(new_edition)])["readings_skipped"] == 1


def test_reading_dedup_keeps_stage_and_verification_provenance(tmp_path):
    obs = observation("us_gdp_qq_saar", "us_gdp", "2026-Q2", 2.2, source="BEA", stage="third")
    with open_writer(tmp_path / "macro.sqlite") as store:
        persist(store, [obs])
        alternate = replace(reread(obs), stage="regular")
        assert persist_counts(store, [alternate])["readings_added"] == 1
        review = replace(reread(obs, "review"), needs_review=True)
        assert persist_counts(store, [review])["readings_added"] == 1
        assert persist_counts(store, [reread(review, "same review")])["readings_skipped"] == 1


def test_ons_index_uses_dataset_release_date_not_bulletin_or_last_updated(tmp_path):
    meta = document("ons/gdp69.json").json()
    rows = ONSAdapter.parse_levels(document("ons/gdp69-csv.csv"), meta, "2026-06",
                                  publication=document("ons/june-content.response.json"))
    assert {o.edition for o in rows} == {"2026-08-24"}
    assert {o.provider_vintage for o in rows} == {"69"}
    assert all(o.published_at.startswith("2026-08-24T00:00:00") and o.precision == "date_only" for o in rows)
    with open_writer(tmp_path / "macro.sqlite") as store:
        persist(store, rows)
        cards = store.release_cards("2026-08-24", "2026-08-24", indicators="uk_gdp_index",
                                    as_known_at="2026-10-20T23:59:59Z")["cards"]
        assert len(cards) == 1
        assert cards[0]["release_event_id"] == "ONS:uk_gdp:regular:2026-08-24"
        assert cards[0]["reference_period"] == "2026-06"


@pytest.mark.parametrize("release_date", [None, "not-a-date", "2026-08-24"])
def test_ons_missing_dataset_release_date_fails_explicitly(release_date):
    meta = dict(document("ons/gdp69.json").json(), release_date=release_date)
    with pytest.raises(FetchError, match="release_date.*not tier-1-resolvable"):
        ONSAdapter.parse_levels(document("ons/gdp69-csv.csv"), meta, "2026-06",
                               publication=document("ons/june-content.response.json"))


def test_ons_daily_discovery_records_missing_update_date_reason():
    client = Replay("uk_gdp")
    meta = client.docs["index"].json()
    meta["months"][0].pop("updateDate")
    body = json.dumps(meta).encode()
    client.docs["index"] = replace(client.docs["index"], body=body, document_hash=hashlib.sha256(body).hexdigest())
    batch = fetch_daily(client, "uk_gdp", "2026-06")
    assert "index_series_or_json_shape_changed" in batch.issues
    assert not any(o.indicator_id == "uk_gdp_index" for o in batch.observations)
    assert any(o.indicator_id == "uk_gdp_mm" for o in batch.observations)


def test_existing_malformed_ons_index_is_explicit_and_never_a_card(tmp_path):
    obs = observation("uk_gdp_index", "uk_gdp", "2026-06", 101.2, source="ONS", edition="time-series:69",
                      published="2026-08-13T00:00:00Z", seen="2026-10-18T18:50:00Z")
    with open_writer(tmp_path / "macro.sqlite") as store:
        persist(store, [obs])
        for since in ("2026-08-01", "2026-10-13"):
            result = store.release_cards(since, "2026-10-20", indicators="uk_gdp_index",
                                         as_known_at="2026-10-20T23:59:59Z")
            assert not result["cards"]
            assert result["empty"][0]["reason"] == "invalid_publication_edition"
        corrected = replace(reread(obs), edition="2026-08-24", published_at="2026-08-24T00:00:00Z")
        persist(store, [corrected])
        result = store.release_cards("2026-08-24", "2026-08-24", indicators="uk_gdp_index",
                                     as_known_at="2026-10-20T23:59:59Z")
        assert len(result["cards"]) == 1
        weekly = store.release_cards("2026-10-13", "2026-10-20", indicators="uk_gdp_index",
                                     as_known_at="2026-10-20T23:59:59Z")
        assert weekly["empty"][0]["reason"] == "no_release_in_window"


def hicp(indicator, period, edition="2026-10-16", stage="final", value=2.2, **kwargs):
    return observation(indicator, "ea_hicp", period, value, source="Eurostat", edition=edition,
                       stage=stage, seen="2026-10-18T18:00:00Z", **kwargs)


@pytest.fixture
def ea20_ea21():
    # Current minr edition re-observes all of EA20 Sep-Dec 2025, while EA21
    # publishes Sep 2026. Both transforms share the same final event/date.
    return [hicp(f"ea_hicp_{basis}", f"2025-{month:02}")
            for basis in ("mm", "yy") for month in range(9, 13)] + [
        hicp(f"ea21_hicp_{basis}", period) for basis in ("mm", "yy") for period in ("2026-08", "2026-09")]


@pytest.mark.parametrize("indicators,geography", [(None, None), (["ea_hicp_mm", "ea_hicp_yy"], None), (None, "EA20")])
def test_ea20_history_not_a_current_card_even_when_filtered(tmp_path, ea20_ea21, indicators, geography):
    with open_writer(tmp_path / "macro.sqlite") as store:
        persist(store, ea20_ea21)
        result = store.release_cards("2026-10-13", "2026-10-20", indicators=indicators,
                                     geography=geography, as_known_at="2026-10-20T23:59:59Z")
        assert not any(c["indicator_id"].startswith("ea_hicp_") for c in result["cards"])
        if indicators is None and geography is None:
            assert {(c["indicator_id"], c["reference_period"]) for c in result["cards"]} == {
                ("ea21_hicp_mm", "2026-09"), ("ea21_hicp_yy", "2026-09")}
        if geography is None:
            assert all(e["reason"] == "discontinued_series" for e in result["empty"]
                       if e["indicator_id"] in ("ea_hicp_mm", "ea_hicp_yy"))
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages WHERE indicator_id='ea_hicp_mm'").fetchone()[0] == 4


def test_discontinued_guard_works_across_events_and_no_future_successor_leak(tmp_path):
    old = hicp("ea_hicp_yy", "2025-12")
    successor = hicp("ea21_hicp_yy", "2026-08", edition="2026-09-17")
    with open_writer(tmp_path / "macro.sqlite") as store:
        persist(store, [old, successor])
        result = store.release_cards("2026-10-13", "2026-10-20", indicators="ea_hicp_yy",
                                     as_known_at="2026-10-20T23:59:59Z")
        assert not result["cards"]
        assert result["empty"][0]["reason"] == "discontinued_series"
        # The legitimate January publication must survive a wide historical query.
        historical = hicp("ea_hicp_yy", "2025-12", edition="2026-01-19")
        persist(store, [historical])
        result = store.release_cards("2026-01-01", "2026-10-20", indicators="ea_hicp_yy",
                                     as_known_at="2026-10-20T23:59:59Z")
        assert [c["released_at"][:10] for c in result["cards"]] == ["2026-01-19"]


def test_discontinued_guard_requires_three_months_and_known_successor(tmp_path):
    with open_writer(tmp_path / "macro.sqlite") as store:
        persist(store, [hicp("ea_hicp_yy", "2025-12", edition="2026-02-19"),
                        hicp("ea21_hicp_yy", "2026-01", edition="2026-02-18")])
        result = store.release_cards("2026-02-01", "2026-02-28", indicators="ea_hicp_yy",
                                     as_known_at="2026-10-20T23:59:59Z")
        assert len(result["cards"]) == 1


def test_successor_first_seen_after_cutoff_cannot_suppress_card(tmp_path):
    successor = replace(hicp("ea21_hicp_yy", "2026-09"),
                        document=replace(hicp("ea21_hicp_yy", "2026-09").document,
                                         retrieved_at="2026-10-19T18:00:00Z"))
    with open_writer(tmp_path / "macro.sqlite") as store:
        persist(store, [hicp("ea_hicp_yy", "2025-12"), successor])
        result = store.release_cards("2026-10-13", "2026-10-20", indicators="ea_hicp_yy",
                                     as_known_at="2026-10-18T23:59:59Z")
        assert len(result["cards"]) == 1
        later = store.release_cards("2026-10-13", "2026-10-20", indicators="ea_hicp_yy",
                                    as_known_at="2026-10-20T23:59:59Z")
        assert not later["cards"]


def test_edition_head_only_retains_prior_period_revision_context(tmp_path):
    with open_writer(tmp_path / "macro.sqlite") as store:
        old = hicp("ea21_hicp_yy", "2026-08", edition="2026-09-17", value=2.1)
        persist(store, [old, hicp("ea21_hicp_yy", "2026-08", value=2.2), hicp("ea21_hicp_yy", "2026-09")])
        result = store.release_cards("2026-10-13", "2026-10-20", indicators="ea21_hicp_yy",
                                     as_known_at="2026-10-20T23:59:59Z")
        assert len(result["cards"]) == 1
        assert result["cards"][0]["reference_period"] == "2026-09"
        assert result["cards"][0]["revisions"][0]["reference_period"] == "2026-08"
