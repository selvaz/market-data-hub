"""Offline worklist decisions: no guessed periods or promoted search values."""
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from market_data_hub.macro_releases import Store, open_reader, open_writer
from market_data_hub.macro_releases.__main__ import main
from market_data_hub.macro_releases.catalogue import INDICATORS
from market_data_hub.macro_releases.legacy import import_calendar
from market_data_hub.macro_releases.models import ReleaseEvent, SourceSeries, ValueVintage
from market_data_hub.macro_releases.resolve import Snapshot
from market_data_hub.macro_releases.worklist import execute, explicit_period, resolve_worklist

NOW = "2026-10-19T12:00:00Z"


def insert(conn, table, **values):
    conn.execute(f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
                 tuple(values.values()))


@pytest.fixture
def legacy(tmp_path):
    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as conn:
        conn.executescript((Path(__file__).parent / "fixtures/legacy_calendar_v4.sql").read_text())
        insert(conn, "runs", run_id="published", as_of="2026-10-18", knowledge_cutoff=NOW,
               started_at="2026-10-18T01:00:00Z", status="published", config_fingerprint="fixture")
        insert(conn, "indicators", code="us_cpi_yy", jurisdiction="USA", name="CPI y/y", agency="BLS",
               unit_code="percent", transform="pct_change", basis="yoy", frequency="M", scale=1,
               created_at=NOW, created_by_run="seed")
        insert(conn, "indicator_aliases", alias_norm="headline cpi yy", jurisdiction="USA",
               code="us_cpi_yy", created_by_run="seed")
    return path


def sighting(legacy, id=1, name="CPI y/y", period="September 2026", geo="USA", value="3.0%",
             date="2026-10-02", code=None, occurrence=None):
    with sqlite3.connect(legacy) as conn:
        insert(conn, "sightings", id=id, run_id="published", searcher="fixture", region="americas",
               jurisdiction=geo, raw_name=name, raw_period=period, raw_actual=value,
               raw_previous="2.9%", release_at_utc=date, release_date=date, unit_hint="percent",
               sources_json='["https://example.test/evidence"]', indicator_code=code, occurrence_id=occurrence)


def occurrence(legacy, id=1, period="2026-09", date="2026-10-02", value=None, observed="2026-10-18T01:00:00Z"):
    with sqlite3.connect(legacy) as conn:
        insert(conn, "occurrences", id=id, indicator_code="us_cpi_yy", period_norm=period,
               release_date=date, created_by_run="published")
        insert(conn, "observations", id=id, occurrence_id=id, run_id="published", observed_at=observed,
               release_date=date, actual_value=value, actual_raw=str(value) if value is not None else None,
               unit_code="percent", scale=1, status="released" if value is not None else "scheduled",
               n_searchers=1, n_domains=1, official_source=0, sources_json="[]")


def issuer(store, *, period="2026-09", stage="regular", value=3.0, verified=True, seen="2026-10-18T01:00:00Z"):
    i = INDICATORS["us_cpi_headline_yy"]
    store.put(i)
    store.put(SourceSeries(id="bls", indicator_id=i.id, source_id="BLS", issuer_id="BLS",
                           distributor_id="BLS", source_key="fixture", evidence_role="issuer"))
    event = f"bls:{period}:{stage}"
    store.put(ReleaseEvent(id=event, issuer_id="BLS", family="us_cpi", estimate_stage=stage, edition=event))
    return store.put(ValueVintage(indicator_id=i.id, reference_period=period, release_event_id=event,
        source_series_id="bls", value=value, published_as=str(value), unit="%", scale=1,
        estimate_stage=stage, revision_kind="initial", issuer_published_at="2026-10-02T12:30:00Z",
        publication_precision="exact", first_seen_at=seen, retrieved_at=seen, document_hash=event,
        parser_version="fixture", extraction_method="api", verification_status="verified" if verified else "unverified"))


@pytest.mark.parametrize("raw,freq,expected", [
    ("September 2026", "monthly", "2026-09"), ("Sep. 2026", "M", "2026-09"),
    ("Q3 2026", "Q", "2026-Q3"), ("2026-Q3", "quarterly", "2026-Q3"),
    ("2026-10-02", "W", "2026-10-02"), ("2026", "annual", "2026"),
    ("d:2026-10-02", "M", None), ("September", "M", None), ("Sep 26", "M", None),
    ("2026-02-30", "D", None), ("2026-13", "M", None), ("September 2026", "Q", None),
    (None, "M", None), ("2026-09", "unknown", "2026-09"), ("0000-09", "M", None),
])
def test_only_explicit_valid_frequency_compatible_periods(raw, freq, expected):
    assert explicit_period(raw, freq) == expected


def test_rules_raw_immutability_idempotency_and_reimport(tmp_path, legacy):
    sighting(legacy, 1)  # explicit text month, exact catalogue name
    sighting(legacy, 2, name="Headline CPI YY", period="2026-09")  # existing alias
    sighting(legacy, 3, name="CPI y/y", period=None)  # not inferred from October release
    sighting(legacy, 4, name="Imaginary CPI", period="September 2026")
    sighting(legacy, 5, geo="CAN")  # same name in a different country
    occurrence(legacy, 10, date="2026-10-01")  # past 14-day window
    occurrence(legacy, 11, period="2026-08", date="2026-10-05")  # exactly 14 days: pending
    occurrence(legacy, 12, period="2026-10", date="2026-10-20")  # incomplete, mapped schedule
    occurrence(legacy, 13, period="2026-07", value=3.0)  # late actual still unverified
    occurrence(legacy, 14, period="d:2026-10-02")
    occurrence(legacy, 15, period="d:2026-10-01")
    sighting(legacy, 15, period="September 2026", code="us_cpi_yy", occurrence=15)
    db = tmp_path / "hub.sqlite"
    with open_writer(db) as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        before = [tuple(r) for r in store.conn.execute("SELECT * FROM readings ORDER BY id")]
        result = resolve_worklist(store, as_of=NOW)
        assert result["rules"] == {"catalogue_period_mapped": 4, "late_value_mapped_unverified": 1,
            "lateness_window_expired": 1, "name_not_in_catalogue": 2, "period_not_derivable": 2}
        assert result["pending"] == 1
        assert [tuple(r) for r in store.conn.execute("SELECT * FROM readings ORDER BY id")] == before
        assert store.conn.execute("SELECT count(*) FROM value_vintages").fetchone()[0] == 0
        decisions = [tuple(r) for r in store.conn.execute("SELECT * FROM worklist_resolutions")]
        assert resolve_worklist(store, as_of=NOW)["rules"] == {}
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        assert resolve_worklist(store, as_of=NOW)["rules"] == {}
        assert [tuple(r) for r in store.conn.execute("SELECT * FROM worklist_resolutions")] == decisions
        assert store.conn.execute("PRAGMA foreign_key_check").fetchall() == []
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store.conn.execute("UPDATE worklist_resolutions SET reason='invented'")


def test_official_overdue_links_and_cutoff(tmp_path, legacy):
    occurrence(legacy)
    db = tmp_path / "hub.sqlite"
    with open_writer(db) as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        selected = issuer(store)
        result = resolve_worklist(store, as_of=NOW)
        d = result["decisions"][0]
        assert (d["reason"], d["indicator_id"], d["reference_period"], d["selected_vintage_id"]) == (
            "official_vintage_linked", "us_cpi_headline_yy", "2026-09", selected)
        assert store.conn.execute("SELECT count(*) FROM value_vintages").fetchone()[0] == 1


@pytest.mark.parametrize("verified,seen", [(False, "2026-10-18T01:00:00Z"), (True, "2026-10-20T01:00:00Z")])
def test_unverified_or_future_vintage_cannot_link(tmp_path, legacy, verified, seen):
    occurrence(legacy)
    with open_writer(tmp_path / "hub.sqlite") as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        issuer(store, verified=verified, seen=seen)
        assert resolve_worklist(store, as_of=NOW)["decisions"][0]["reason"] == "lateness_window_expired"


@pytest.mark.parametrize("stage,value,other_period,expected", [
    ("unknown", "3.0%", False, "period_not_derivable"),
    ("regular", "3.0%", False, "official_vintage_linked"),
    ("regular", "3.1%", False, "period_not_derivable"),
    ("regular", "3.0%", True, "period_not_derivable"),
])
def test_missing_period_exact_official_match_requires_stage_and_unique_period(
        tmp_path, legacy, stage, value, other_period, expected):
    sighting(legacy, period=None, value=value)
    with open_writer(tmp_path / "hub.sqlite") as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        issuer(store)
        store.conn.execute("UPDATE legacy_candidates SET estimate_stage=?", (stage,))
        store.conn.execute("UPDATE indicator_aliases SET estimate_stage=?", (stage,))
        if other_period:
            issuer(store, period="2026-08")
        result = resolve_worklist(store, as_of=NOW)
        assert result["decisions"][0]["reason"] == expected


def test_mapping_overlay_is_cutoff_aware_and_previous_stays_unknown(tmp_path, legacy):
    sighting(legacy)
    with open_writer(tmp_path / "hub.sqlite") as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        resolve_worklist(store, as_of=NOW)
        before = Snapshot.load(store, "2026-10-19T11:59:59Z")
        assert before.evidence == []
        after = Snapshot.load(store, NOW)
        actual = next(e for e in after.evidence if e.reading_kind == "actual")
        previous = next(e for e in after.evidence if e.reading_kind == "previous")
        assert actual.reference_period == "2026-09" and not actual.verified
        assert previous.reference_period is None


def test_ambiguous_alias_and_conflicting_periods_are_terminal(tmp_path, legacy):
    with sqlite3.connect(legacy) as conn:
        insert(conn, "indicators", code="other", jurisdiction="USA", name="Other CPI", agency="BLS",
               unit_code="percent", transform="pct_change", basis="yoy", frequency="M", scale=1,
               created_at=NOW, created_by_run="seed")
        insert(conn, "indicator_aliases", alias_norm="cpi y y", jurisdiction="USA", code="other", created_by_run="seed")
    sighting(legacy, 1)
    occurrence(legacy, 2, period="2026-09")
    sighting(legacy, 2, period="August 2026", code="us_cpi_yy", occurrence=2)
    with open_writer(tmp_path / "hub.sqlite") as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        result = resolve_worklist(store, as_of=NOW)
        assert result["rules"] == {"conflicting_reference_periods": 1, "indicator_ambiguous": 1}


def test_changed_schedule_extends_window_and_pending_expires_on_rerun(tmp_path, legacy):
    occurrence(legacy, date="2026-10-01")
    sighting(legacy, code="us_cpi_yy", period="2026-09", occurrence=1, date="2026-10-10")
    with open_writer(tmp_path / "hub.sqlite") as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        assert resolve_worklist(store, as_of=NOW)["pending"] == 1
        later = resolve_worklist(store, as_of="2026-10-25T12:00:00Z")
        assert later["rules"] == {"lateness_window_expired": 1}
        assert later["pending"] == 0


def test_completed_occurrence_mapping_also_overlays_linked_sightings(tmp_path, legacy):
    occurrence(legacy, period="d:2026-10-02")
    sighting(legacy, period="September 2026", code="us_cpi_yy", occurrence=1)
    with open_writer(tmp_path / "hub.sqlite") as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        assert resolve_worklist(store, as_of=NOW)["rules"] == {"catalogue_period_mapped": 1}
        before = Snapshot.load(store, "2026-10-19T11:59:59Z")
        assert all(e.reference_period is None for e in before.evidence)
        after = Snapshot.load(store, NOW)
        actuals = [e for e in after.evidence if e.reading_kind in ("actual", "legacy_merged_actual")]
        assert len(actuals) == 2
        assert all(e.reference_period == "2026-09" and not e.verified for e in actuals)


def test_official_bridge_does_not_repair_contradictory_semantics(tmp_path, legacy):
    occurrence(legacy)
    with open_writer(tmp_path / "hub.sqlite") as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        issuer(store)
        store.conn.execute("UPDATE indicators SET transform='mm' WHERE id='lazycommittee_calendar:us_cpi_yy'")
        result = resolve_worklist(store, as_of=NOW)
        assert result["rules"] == {"lateness_window_expired": 1}


def test_failed_and_unknown_reasons_are_not_reclassified(tmp_path, legacy):
    sighting(legacy)
    with open_writer(tmp_path / "hub.sqlite") as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        store.conn.execute("UPDATE import_worklist SET status='terminal',reason='failed_run'")
        assert resolve_worklist(store, as_of=NOW)["decisions"] == []
        store.conn.execute("UPDATE import_worklist SET status='pending',reason='running_run_deferred'")
        assert resolve_worklist(store, as_of=NOW)["decisions"] == []


def test_atomic_rollback_on_persistence_failure(tmp_path, legacy):
    sighting(legacy, 1)
    sighting(legacy, 2)
    with open_writer(tmp_path / "hub.sqlite") as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        store.conn.execute("CREATE TRIGGER fail_second BEFORE INSERT ON worklist_resolutions "
                           "WHEN NEW.occurrence_key='sighting:2' BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
        with pytest.raises(sqlite3.IntegrityError, match="fixture failure"):
            resolve_worklist(store, as_of=NOW)
        assert store.conn.execute("SELECT count(*) FROM worklist_resolutions").fetchone()[0] == 0
        assert store.conn.execute("SELECT count(*) FROM import_worklist WHERE status='pending'").fetchone()[0] == 2


def file_hashes(path):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in path.parent.iterdir() if p.is_file()}


def test_cli_preview_matches_apply_without_any_target_writes(tmp_path, legacy, capsys):
    sighting(legacy)
    db = tmp_path / "hub.sqlite"
    with open_writer(db) as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        # A real WAL stays open while preview snapshots it.
        before = file_hashes(tmp_path)
        assert main(["resolve-worklist", "--db", str(db), "--dry-run", "--as-of", NOW]) == 0
        preview = json.loads(capsys.readouterr().out)
        assert file_hashes(tmp_path) == before
    applied = execute(db, as_of=NOW)
    assert {k: v for k, v in preview.items() if k != "dry_run"} == {
        k: v for k, v in applied.items() if k != "dry_run"}
    with open_reader(db) as store:
        assert store.conn.execute("PRAGMA user_version").fetchone()[0] == 6


def test_v5_preview_and_apply_preserve_all_existing_evidence(tmp_path, legacy):
    from market_data_hub.macro_releases import migrations

    sighting(legacy)
    db = tmp_path / "v5.sqlite"
    conn = sqlite3.connect(db, isolation_level=None)
    conn.row_factory = sqlite3.Row
    for script in ("schema.sql", "migration_003.sql", "migration_004.sql", "migration_005.sql"):
        conn.executescript(Path(migrations.__file__).with_name(script).read_text())
    conn.execute("PRAGMA user_version=5")
    with Store(conn, writable=True) as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        evidence = [tuple(r) for r in store.conn.execute("SELECT * FROM readings")]
    before = file_hashes(tmp_path)
    preview = execute(db, dry_run=True, as_of=NOW)
    assert file_hashes(tmp_path) == before
    with open_reader(db) as store:
        assert store.conn.execute("PRAGMA user_version").fetchone()[0] == 5
        assert not store.conn.execute("SELECT 1 FROM sqlite_master WHERE name='worklist_resolutions'").fetchone()
    applied = execute(db, as_of=NOW)
    assert applied["decisions"] == preview["decisions"]
    with open_reader(db) as store:
        assert store.conn.execute("PRAGMA user_version").fetchone()[0] == 6
        assert [tuple(r) for r in store.conn.execute("SELECT * FROM readings")] == evidence


def test_refuse_missing_or_programdata_paths_before_open(tmp_path):
    with pytest.raises(ValueError, match="existing"):
        execute(tmp_path / "missing.sqlite")
    with pytest.raises(ValueError, match="ProgramData"):
        execute(tmp_path / "ProgramData" / "original.sqlite", dry_run=True)
