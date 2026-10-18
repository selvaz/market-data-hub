"""Regression coverage for production calendar timestamps and damaged evidence."""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from market_data_hub.macro_releases import ReadAPI, open_reader, open_writer
from market_data_hub.macro_releases.catalogue import INDICATORS
from market_data_hub.macro_releases.models import Reading, utc
from market_data_hub.macro_releases import run_log
from market_data_hub.macro_releases.read_times import timestamp
from market_data_hub.macro_releases.resolve import Snapshot, publication_order
from test_macro_read_api import consensus, evidence, schedule

NOW = "2026-10-18T23:00:00Z"
WINDOW = ("2026-10-10", "2026-10-17")
PRODUCTION_COPY = Path(__file__).resolve().parents[1] / ".tmp/prod-copy.sqlite"


@pytest.fixture
def store(tmp_path):
    with open_writer(tmp_path / "macro.sqlite") as store:
        store.put(INDICATORS["us_cpi_headline_yy"])
        yield store


def legacy(store, *, table="sightings", release="2026-10-14T12:30", observed=None,
           run_started="2026-10-14T13:00:00Z", payload=None):
    """One imported row, with the same unvalidated envelope as the importer."""
    key = "release_at_utc" if table == "sightings" else "scheduled_at_utc"
    raw = {key: release, "release_date": "2026-10-14"}
    if observed is not None:
        raw["observed_at"] = observed
    payload = {"row": raw, "table": table, "legacy_first_seen_at": run_started} if payload is None else payload
    reading = store.put(Reading(
        indicator_id="us_cpi_headline_yy", source_id="lazycommittee_calendar",
        series_key="test-legacy", period_key="2026-09", reference_period="2026-09",
        edition="legacy-run:1", estimate_stage="unknown", value=3.7, unit="%", scale=1,
        issuer_id="unknown", distributor_id="LazyCommittee", evidence_origin_id="legacy:test",
        first_seen_at="2026-10-18T01:00:00Z", retrieved_at="2026-10-18T01:00:00Z",
        content_hash=hashlib.sha256(json.dumps(payload).encode()).hexdigest(),
        parser_version="legacy-calendar-m4-v2", extraction_method="legacy_merge",
        verification_status="unverified", raw_payload_json=json.dumps(payload),
    ))
    store.conn.execute(
        "INSERT INTO legacy_reading_links VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (reading, "lazycommittee_calendar", table, reading, "actual", 0, "cpi",
         "us_cpi_headline_yy", "regular", str(reading), None, None, "published",
         utc("2026-10-18T01:00:00Z")),
    )
    return reading


@pytest.mark.parametrize("table", ["sightings", "observations"])
@pytest.mark.parametrize("release,precision,hour", [
    ("2026-10-14", "date_only", "00:00:00"),
    ("2026-10-14T12:30", "assumed_tz", "12:30:00"),
    ("2026-10-14T12:30:15.123456", "assumed_tz", "12:30:15.123456"),
    ("2026-10-14T08:30:00-04:00", "exact", "12:30:00"),
    (None, "date_only", "00:00:00"),
])
def test_calendar_release_timestamp_formats(store, table, release, precision, hour):
    legacy(store, table=table, release=release)
    result = store.release_cards(*WINDOW, as_known_at=NOW)
    card, = result["cards"]
    assert card["released_at"] == utc("2026-10-14T" + hour + "Z")
    assert card["publication_precision"] == precision
    assert card["provenance"][0]["precision"] == precision
    assert card["provenance"][0]["legacy"]["row"].get(
        "release_at_utc" if table == "sightings" else "scheduled_at_utc") == release
    assert len(result["limitations"]) == 1
    assert store.explain_empty("us_cpi_headline_yy", WINDOW, as_known_at=NOW)["reason"] == "published_not_resolved"
    # The import time, not the original calendar run time, gates knowledge.
    assert not store.release_cards(*WINDOW, as_known_at="2026-10-17T23:59:00Z")["cards"]


@pytest.mark.parametrize("field", ["observed", "run_started"])
@pytest.mark.parametrize("value,precision", [
    ("2026-10-14", "date_only"),
    ("2026-10-14T13:00", "assumed_tz"),
    ("2026-10-14T13:00:00.123456", "assumed_tz"),
    ("2026-10-14T15:00:00+02:00", "exact"),
])
def test_legacy_ordering_is_normalized_before_resolution(store, field, value, precision):
    legacy(store, **{field: value})
    snapshot = Snapshot.load(store, NOW)
    row, = snapshot.evidence
    assert publication_order(row) == timestamp(value, assumed_timezone="UTC")[0]
    assert row.timestamp_precisions["legacy_publication_order"] == precision
    assert store.release_cards(*WINDOW, as_known_at=NOW)["cards"]
    assert store.explain_empty("us_cpi_core_yy", WINDOW, as_known_at=NOW)["reason"] == "published_not_resolved"


@pytest.mark.parametrize("field", ["release", "observed", "run_started", "payload"])
@pytest.mark.parametrize("bad", ["not-a-time", "2026-02-30T12:00", 123, ["bad"], {"bad": True}])
def test_bad_legacy_row_is_skipped_and_reported_by_both_apis(store, field, bad):
    damaged = legacy(store, **{field: bad})
    # The healthy issuer row must still resolve, even beside damaged legacy data.
    evidence(store)
    result = store.release_cards(*WINDOW, as_known_at=NOW)
    assert len(result["cards"]) == 1
    assert result["cards"][0]["resolution"]["status"] == "official"
    assert any(f"Skipped reading:{damaged}:" in text for text in result["limitations"])
    empty = store.explain_empty("us_cpi_core_yy", WINDOW, as_known_at=NOW)
    assert any(f"Skipped reading:{damaged}:" in text for text in empty["limitations"])


@pytest.mark.parametrize("value", ["2026-10-14T12:30", "2026-10-14 12:30:15.123456", "2026-10-14"])
def test_unknown_zone_retains_only_the_date(store, value):
    # Bypass the strict writer to model pre-contract stored evidence.
    assert timestamp(value) == (utc("2026-10-14T00:00:00Z"), "date_only")
    evidence(store)
    store.conn.execute("DROP TRIGGER value_vintages_no_update")
    store.conn.execute("UPDATE value_vintages SET issuer_published_at=?", (value,))
    card, = store.release_cards(*WINDOW, as_known_at=NOW)["cards"]
    assert card["released_at"] == utc("2026-10-14T00:00:00Z")
    assert card["publication_precision"] == "date_only"
    assert not store.release_cards("2026-10-14T12:00:00Z", "2026-10-14T13:00:00Z", as_known_at=NOW)["cards"]


def test_stored_schedule_and_knowledge_time_are_normalized(store):
    schedule(store)
    store.conn.execute("DROP TRIGGER schedule_versions_no_update")
    store.conn.execute("UPDATE schedule_versions SET expected_at='2026-10-14T12:30', recorded_at='2026-10-01'")
    snapshot = Snapshot.load(store, NOW)
    row, = snapshot.schedules
    assert row["expected_at"] == utc("2026-10-14T00:00:00Z")
    assert row["precision"] == "date_only"
    assert not Snapshot.load(store, "2026-10-01T12:00:00Z").schedules
    assert store.explain_empty("us_cpi_headline_yy", WINDOW, as_known_at=NOW)["reason"] == "scheduled_not_captured"
    store.conn.execute("UPDATE schedule_versions SET expected_at='broken'")
    result = store.release_cards(*WINDOW, as_known_at=NOW)
    assert any("Skipped schedule_versions:" in text for text in result["limitations"])


@pytest.mark.parametrize("table,field", [
    ("readings", "published_at"), ("readings", "first_seen_at"),
    ("value_vintages", "issuer_published_at"), ("value_vintages", "first_seen_at"),
    ("legacy_reading_links", "updated_at"),
    ("schedule_versions", "expected_at"), ("schedule_versions", "recorded_at"),
    ("consensus_readings", "published_at"), ("consensus_readings", "captured_at"),
    ("consensus_readings", "release_cutoff_at"),
])
def test_other_stored_timestamps_are_isolated_at_snapshot_boundary(store, table, field):
    legacy(store)
    event, _ = evidence(store)
    schedule(store, edition="2026-10-15", expected="2026-10-15T12:30:00Z")
    consensus(store, event, "Reuters", 3.6)
    store.conn.execute(f"DROP TRIGGER IF EXISTS {table}_no_update")
    # The SQL below is fixture damage only; production is opened query_only.
    store.conn.execute("PRAGMA ignore_check_constraints=ON")
    store.conn.execute(f"UPDATE {table} SET {field}='broken'")
    result = store.release_cards(*WINDOW, as_known_at=NOW)
    assert any("Skipped " in text for text in result["limitations"])
    assert any("Skipped " in text for text in store.explain_empty(
        "us_cpi_core_yy", WINDOW, as_known_at=NOW)["limitations"])


def test_offset_knowledge_is_filtered_after_utc_conversion(store):
    evidence(store)
    store.conn.execute("DROP TRIGGER value_vintages_no_update")
    store.conn.execute("UPDATE value_vintages SET first_seen_at='2026-10-14T08:31:00-04:00'")
    assert not store.release_cards(*WINDOW, as_known_at="2026-10-14T12:30:30Z")["cards"]
    assert store.release_cards(*WINDOW, as_known_at="2026-10-14T12:31:00Z")["cards"]


def test_naive_legacy_failure_clock_reports_its_precision(store):
    legacy(store, run_started="2026-10-14T13:00")
    store.conn.execute("UPDATE legacy_reading_links SET legacy_run_status='failed'")
    result = store.explain_empty("us_cpi_headline_yy", WINDOW, as_known_at=NOW)
    assert result["reason"] == "source_failed"
    assert result["time"] == utc("2026-10-14T13:00:00Z")
    assert result["time_precision"] == "assumed_tz"


@pytest.mark.parametrize("table,field", [
    ("collect_run_families", "finished_at"), ("collect_runs", "started_at"),
    ("collect_run_families", "fetch_since"), ("collect_run_families", "fetch_until"),
])
def test_invalid_collection_clock_or_bounds_do_not_break_reads(store, monkeypatch, table, field):
    monkeypatch.setattr(run_log, "now", lambda: utc("2026-10-18T12:00:00Z"))
    run_log.start(store, "test", ["us_cpi"], {})
    run_log.family_result(store, "test", "us_cpi", {
        "status": "failed", "vintages_added": 0, "readings_added": 0, "readings_skipped": 0,
        "fetch_since": "2026-09", "fetch_until": "2026-10-17",
    })
    store.conn.execute(f"DROP TRIGGER {table}_no_update")
    store.conn.execute(f"UPDATE {table} SET {field}='broken'")
    for response in (store.release_cards(*WINDOW, as_known_at=NOW),
                     store.explain_empty("us_cpi_headline_yy", WINDOW, as_known_at=NOW)):
        assert any("Skipped collect_run_families:test:" in text for text in response["limitations"])


@pytest.mark.skipif(not PRODUCTION_COPY.is_file(), reason="needs local read-only .tmp/prod-copy.sqlite")
def test_production_copy_last_eight_weeks():
    """Local integration test; CI deliberately has no production database."""
    cutoff = datetime.now(timezone.utc).isoformat()
    until = date(2026, 10, 18)  # import day of this production snapshot
    since = (until - timedelta(weeks=8) + timedelta(days=1)).isoformat()
    digest_before = hashlib.sha256(PRODUCTION_COPY.read_bytes()).digest()
    with open_reader(PRODUCTION_COPY) as store:
        api = ReadAPI(store)
        result = api.release_cards(since, until.isoformat(), as_known_at=cutoff)
        assert {"us_cpi_headline_yy", "us_cpi_core_yy", "us_cpi_headline_mm", "us_cpi_core_mm"} <= {
            card["indicator_id"] for card in result["cards"]
            if card["family"] == "us_cpi" and card["released_at"].startswith("2026-10-14")
            and card["resolution"]["status"] == "official"
        }
        # release_cards already explains every missing stored/catalogue indicator.
        # Also exercise the public method across the same whole-store snapshot,
        # covering every official family, a legacy card and an absent indicator.
        indicators = {"us_cpi_core_yy", "us_payrolls_change", "us_gdp_qq_saar",
                      "ea_hicp_yy", "uk_gdp_mm", result["empty"][0]["indicator_id"]}
        indicators.add(next(c["indicator_id"] for c in result["cards"] if c["time_basis"] == "legacy_calendar"))
        for indicator in sorted(indicators):
            assert api.explain_empty(indicator, (since, until.isoformat()), as_known_at=cutoff)["reason"]
        assert store.conn.total_changes == 0
    assert hashlib.sha256(PRODUCTION_COPY.read_bytes()).digest() == digest_before
