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
from market_data_hub.macro_releases.models import (
    ReleaseEvent,
    SourceSeries,
    ValueVintage,
)
from market_data_hub.macro_releases.resolve import Snapshot
from market_data_hub.macro_releases.worklist import (
    execute,
    explicit_period,
    resolve_worklist,
)
from market_data_hub.macro_releases.worklist import (
    EvidenceIndex,
    ensure_audit,
    revert_worklist,
)
from market_data_hub.macro_releases import worklist
from market_data_hub.macro_releases.lease import RunLease
from market_data_hub.macro_releases.models import utc
from filelock import FileLock

NOW = "2026-10-19T12:00:00Z"


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(worklist, "now_utc", lambda: utc("2026-10-26T12:00:00Z"))


def insert(conn, table, **values):
    conn.execute(
        f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
        tuple(values.values()),
    )


@pytest.fixture
def legacy(tmp_path):
    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            (Path(__file__).parent / "fixtures/legacy_calendar_v4.sql").read_text()
        )
        insert(
            conn,
            "runs",
            run_id="published",
            as_of="2026-10-18",
            knowledge_cutoff=NOW,
            started_at="2026-10-18T01:00:00Z",
            status="published",
            config_fingerprint="fixture",
        )
        insert(
            conn,
            "indicators",
            code="us_cpi_yy",
            jurisdiction="USA",
            name="CPI y/y",
            agency="BLS",
            unit_code="percent",
            transform="pct_change",
            basis="yoy",
            frequency="M",
            scale=1,
            created_at=NOW,
            created_by_run="seed",
        )
        insert(
            conn,
            "indicator_aliases",
            alias_norm="headline cpi yy",
            jurisdiction="USA",
            code="us_cpi_yy",
            created_by_run="seed",
        )
    return path


def sighting(
    legacy,
    id=1,
    name="CPI y/y",
    period="September 2026",
    geo="USA",
    value="3.0%",
    date="2026-10-02",
    code=None,
    occurrence=None,
):
    with sqlite3.connect(legacy) as conn:
        insert(
            conn,
            "sightings",
            id=id,
            run_id="published",
            searcher="fixture",
            region="americas",
            jurisdiction=geo,
            raw_name=name,
            raw_period=period,
            raw_actual=value,
            raw_previous="2.9%",
            release_at_utc=date,
            release_date=date,
            unit_hint="percent",
            sources_json='["https://example.test/evidence"]',
            indicator_code=code,
            occurrence_id=occurrence,
        )


def occurrence(
    legacy,
    id=1,
    period="2026-09",
    date="2026-10-02",
    value=None,
    observed="2026-10-18T01:00:00Z",
    code="us_cpi_yy",
    status=None,
    raw_actual=None,
):
    with sqlite3.connect(legacy) as conn:
        insert(
            conn,
            "occurrences",
            id=id,
            indicator_code=code,
            period_norm=period,
            release_date=date,
            created_by_run="published",
        )
        insert(
            conn,
            "observations",
            id=id,
            occurrence_id=id,
            run_id="published",
            observed_at=observed,
            release_date=date,
            actual_value=value,
            actual_raw=str(value) if value is not None else raw_actual,
            unit_code="percent",
            scale=1,
            status=status or ("released" if value is not None else "scheduled"),
            n_searchers=1,
            n_domains=1,
            official_source=0,
            sources_json="[]",
        )


def issuer(
    store,
    *,
    period="2026-09",
    stage="regular",
    value=3.0,
    verified=True,
    seen="2026-10-18T01:00:00Z",
    indicator_id="us_cpi_headline_yy",
):
    i = INDICATORS[indicator_id]
    store.put(i)
    store.put(
        SourceSeries(
            id="bls",
            indicator_id=i.id,
            source_id="BLS",
            issuer_id="BLS",
            distributor_id="BLS",
            source_key="fixture",
            evidence_role="issuer",
        )
    )
    event = f"bls:{period}:{stage}"
    store.put(
        ReleaseEvent(
            id=event,
            issuer_id="BLS",
            family="us_cpi",
            estimate_stage=stage,
            edition=event,
        )
    )
    return store.put(
        ValueVintage(
            indicator_id=i.id,
            reference_period=period,
            release_event_id=event,
            source_series_id="bls",
            value=value,
            published_as=str(value),
            unit="%",
            scale=1,
            estimate_stage=stage,
            revision_kind="initial",
            issuer_published_at="2026-10-02T12:30:00Z",
            publication_precision="exact",
            first_seen_at=seen,
            retrieved_at=seen,
            document_hash=event,
            parser_version="fixture",
            extraction_method="api",
            verification_status="verified" if verified else "unverified",
        )
    )


@pytest.mark.parametrize(
    "raw,freq,expected",
    [
        ("September 2026", "monthly", "2026-09"),
        ("Sep. 2026", "M", "2026-09"),
        ("Q3 2026", "Q", "2026-Q3"),
        ("2026-Q3", "quarterly", "2026-Q3"),
        ("2026-10-02", "W", "2026-10-02"),
        ("2026", "annual", "2026"),
        ("d:2026-10-02", "M", None),
        ("September", "M", None),
        ("Sep 26", "M", None),
        ("2026-02-30", "D", None),
        ("2026-13", "M", None),
        ("September 2026", "Q", None),
        (None, "M", None),
        ("2026-09", "unknown", "2026-09"),
        ("0000-09", "M", None),
    ],
)
def test_only_explicit_valid_frequency_compatible_periods(raw, freq, expected):
    assert explicit_period(raw, freq) == expected


def test_rules_raw_immutability_idempotency_and_reimport(tmp_path, legacy):
    sighting(legacy, 1)  # explicit text month, exact catalogue name
    sighting(legacy, 2, name="Headline CPI YY", period="2026-09")  # existing alias
    sighting(
        legacy, 3, name="CPI y/y", period=None
    )  # not inferred from October release
    sighting(legacy, 4, name="Imaginary CPI", period="September 2026")
    sighting(legacy, 5, geo="CAN")  # same name in a different country
    occurrence(legacy, 10, date="2026-10-01")  # past 14-day window
    occurrence(
        legacy, 11, period="2026-08", date="2026-10-05"
    )  # exactly 14 days: pending
    occurrence(
        legacy, 12, period="2026-10", date="2026-10-20"
    )  # incomplete, mapped schedule
    occurrence(legacy, 13, period="2026-07", value=3.0)  # late actual still unverified
    occurrence(legacy, 14, period="d:2026-10-02")
    occurrence(legacy, 15, period="d:2026-10-01")
    sighting(legacy, 15, period="September 2026", code="us_cpi_yy", occurrence=15)
    db = tmp_path / "hub.sqlite"
    with open_writer(db) as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        before = [
            tuple(r) for r in store.conn.execute("SELECT * FROM readings ORDER BY id")
        ]
        result = resolve_worklist(store, as_of=NOW)
        assert result["rules"] == {
            "catalogue_period_mapped": 3,
            "late_value_mapped_unverified": 1,
            "lateness_window_expired": 1,
            "outside_catalogue_scope": 1,
            "catalogue_jurisdiction_mismatch": 1,
            "period_not_derivable": 2,
            "release_in_future": 1,
        }
        assert result["pending"] == 2
        assert [
            tuple(r) for r in store.conn.execute("SELECT * FROM readings ORDER BY id")
        ] == before
        assert (
            store.conn.execute("SELECT count(*) FROM value_vintages").fetchone()[0] == 0
        )
        decisions = [
            tuple(r) for r in store.conn.execute("SELECT * FROM worklist_resolutions")
        ]
        assert resolve_worklist(store, as_of=NOW)["rules"] == {}
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        assert resolve_worklist(store, as_of=NOW)["rules"] == {
            "release_in_future": 1,
            "lateness_window_expired": 1,
        }
        assert [
            tuple(r)
            for r in store.conn.execute(
                "SELECT * FROM worklist_resolutions ORDER BY audit_id"
            )
        ][: len(decisions)] == decisions
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
        assert (
            d["reason"],
            d["indicator_id"],
            d["reference_period"],
            d["selected_vintage_id"],
        ) == ("official_vintage_linked", "us_cpi_headline_yy", "2026-09", selected)
        assert (
            store.conn.execute("SELECT count(*) FROM value_vintages").fetchone()[0] == 1
        )


@pytest.mark.parametrize(
    "verified,seen", [(False, "2026-10-18T01:00:00Z"), (True, "2026-10-20T01:00:00Z")]
)
def test_unverified_or_future_vintage_cannot_link(tmp_path, legacy, verified, seen):
    occurrence(legacy)
    with open_writer(tmp_path / "hub.sqlite") as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        issuer(store, verified=verified, seen=seen)
        assert (
            resolve_worklist(store, as_of=NOW)["decisions"][0]["reason"]
            == "lateness_window_expired"
        )


@pytest.mark.parametrize(
    "stage,value,other_period,expected",
    [
        ("unknown", "3.0%", False, "period_not_derivable"),
        ("regular", "3.0%", False, "official_vintage_linked"),
        ("regular", "3.1%", False, "period_not_derivable"),
        ("regular", "3.0%", True, "period_not_derivable"),
    ],
)
def test_missing_period_exact_official_match_requires_stage_and_unique_period(
    tmp_path, legacy, stage, value, other_period, expected
):
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
        insert(
            conn,
            "indicators",
            code="other",
            jurisdiction="USA",
            name="Other CPI",
            agency="BLS",
            unit_code="percent",
            transform="pct_change",
            basis="yoy",
            frequency="M",
            scale=1,
            created_at=NOW,
            created_by_run="seed",
        )
        insert(
            conn,
            "indicator_aliases",
            alias_norm="cpi y y",
            jurisdiction="USA",
            code="other",
            created_by_run="seed",
        )
    sighting(legacy, 1)
    occurrence(legacy, 2, period="2026-09")
    sighting(legacy, 2, period="August 2026", code="us_cpi_yy", occurrence=2)
    with open_writer(tmp_path / "hub.sqlite") as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        result = resolve_worklist(store, as_of=NOW)
        assert result["rules"] == {
            "conflicting_reference_periods": 1,
            "indicator_ambiguous": 1,
        }


def test_changed_schedule_extends_window_and_pending_expires_on_rerun(tmp_path, legacy):
    occurrence(legacy, date="2026-10-01")
    sighting(
        legacy,
        code="us_cpi_yy",
        period="2026-09",
        occurrence=1,
        date="2026-10-10",
        value=None,
    )
    with open_writer(tmp_path / "hub.sqlite") as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        assert resolve_worklist(store, as_of=NOW)["pending"] == 1
        later = resolve_worklist(store, as_of="2026-10-25T12:00:00Z")
        assert later["rules"] == {"evidence_horizon_before_deadline": 1}
        assert later["pending"] == 1


def test_completed_occurrence_mapping_also_overlays_linked_sightings(tmp_path, legacy):
    occurrence(legacy, period="d:2026-10-02")
    sighting(legacy, period="September 2026", code="us_cpi_yy", occurrence=1)
    with open_writer(tmp_path / "hub.sqlite") as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        assert resolve_worklist(store, as_of=NOW)["rules"] == {
            "catalogue_period_mapped": 1
        }
        before = Snapshot.load(store, "2026-10-19T11:59:59Z")
        assert all(e.reference_period is None for e in before.evidence)
        after = Snapshot.load(store, NOW)
        actuals = [
            e
            for e in after.evidence
            if e.reading_kind in ("actual", "legacy_merged_actual")
        ]
        assert len(actuals) == 2
        assert all(e.reference_period == "2026-09" and not e.verified for e in actuals)


def test_official_bridge_does_not_repair_contradictory_semantics(tmp_path, legacy):
    occurrence(legacy)
    with open_writer(tmp_path / "hub.sqlite") as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        issuer(store)
        store.conn.execute(
            "UPDATE indicators SET transform='mm' WHERE id='lazycommittee_calendar:us_cpi_yy'"
        )
        result = resolve_worklist(store, as_of=NOW)
        assert result["rules"] == {"lateness_window_expired": 1}


def test_failed_and_unknown_reasons_are_not_reclassified(tmp_path, legacy):
    sighting(legacy)
    with open_writer(tmp_path / "hub.sqlite") as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        store.conn.execute(
            "UPDATE import_worklist SET status='terminal',reason='failed_run'"
        )
        assert resolve_worklist(store, as_of=NOW)["decisions"] == []
        store.conn.execute(
            "UPDATE import_worklist SET status='pending',reason='running_run_deferred'"
        )
        assert resolve_worklist(store, as_of=NOW)["decisions"] == []


def test_atomic_rollback_on_persistence_failure(tmp_path, legacy):
    sighting(legacy, 1)
    sighting(legacy, 2)
    with open_writer(tmp_path / "hub.sqlite") as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        with store.batch():
            ensure_audit(store)
        store.conn.execute(
            "CREATE TRIGGER fail_second BEFORE INSERT ON worklist_resolutions "
            "WHEN NEW.occurrence_key='sighting:2' BEGIN SELECT RAISE(ABORT,'fixture failure'); END"
        )
        with pytest.raises(sqlite3.IntegrityError, match="fixture failure"):
            resolve_worklist(store, as_of=NOW)
        assert (
            store.conn.execute("SELECT count(*) FROM worklist_resolutions").fetchone()[
                0
            ]
            == 0
        )
        assert (
            store.conn.execute(
                "SELECT count(*) FROM import_worklist WHERE status='pending'"
            ).fetchone()[0]
            == 2
        )


def file_hashes(path):
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in path.parent.iterdir()
        if p.is_file()
    }


def test_cli_preview_matches_apply_without_any_target_writes(tmp_path, legacy, capsys):
    sighting(legacy)
    db = tmp_path / "hub.sqlite"
    with open_writer(db) as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        # A real WAL stays open while preview snapshots it.
        before = file_hashes(tmp_path)
        assert (
            main(["resolve-worklist", "--db", str(db), "--dry-run", "--as-of", NOW])
            == 0
        )
        preview = json.loads(capsys.readouterr().out)
        assert file_hashes(tmp_path) == before
    applied = execute(db, as_of=NOW)
    assert {k: v for k, v in preview.items() if k not in {"dry_run", "run_id"}} == {
        k: v for k, v in applied.items() if k not in {"dry_run", "run_id"}
    }
    with open_reader(db) as store:
        from market_data_hub.macro_releases.migrations import SCHEMA_VERSION

        assert store.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_v5_preview_and_apply_preserve_all_existing_evidence(tmp_path, legacy):
    from market_data_hub.macro_releases import migrations

    sighting(legacy)
    db = tmp_path / "v5.sqlite"
    conn = sqlite3.connect(db, isolation_level=None)
    conn.row_factory = sqlite3.Row
    for script in (
        "schema.sql",
        "migration_003.sql",
        "migration_004.sql",
        "migration_005.sql",
    ):
        conn.executescript(Path(migrations.__file__).with_name(script).read_text())
    conn.execute("PRAGMA user_version=5")
    with Store(conn, writable=True) as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        evidence = [tuple(r) for r in store.conn.execute("SELECT * FROM readings")]
        compatibility = [
            tuple(r) for r in store.conn.execute("SELECT * FROM schema_compatibility")
        ]
    before = file_hashes(tmp_path)
    preview = execute(db, dry_run=True, as_of=NOW)
    assert file_hashes(tmp_path) == before
    with open_reader(db) as store:
        assert store.conn.execute("PRAGMA user_version").fetchone()[0] == 5
        assert not store.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='worklist_resolutions'"
        ).fetchone()
    applied = execute(db, as_of=NOW)
    assert applied["decisions"] == preview["decisions"]
    with open_reader(db) as store:
        assert (
            store.conn.execute("PRAGMA user_version").fetchone()[0]
            == migrations.SCHEMA_VERSION
        )
        assert [
            tuple(r) for r in store.conn.execute("SELECT * FROM readings")
        ] == evidence
        assert [
            tuple(r)
            for r in store.conn.execute(
                "SELECT * FROM schema_compatibility WHERE schema_version<=5"
            )
        ] == compatibility
        assert migrations.SCHEMA_VERSION == migrations.READER_CEILING == 8
    with open_writer(db) as store:
        migrations.migrate(store.conn)
        assert (
            store.conn.execute("PRAGMA user_version").fetchone()[0]
            == migrations.SCHEMA_VERSION
        )
        assert resolve_worklist(store, as_of=NOW)["rules"] == {}


def test_refuse_missing_or_programdata_paths_before_open(tmp_path):
    with pytest.raises(ValueError, match="existing"):
        execute(tmp_path / "missing.sqlite")
    with pytest.raises(ValueError, match="ProgramData"):
        execute(tmp_path / "ProgramData" / "original.sqlite", dry_run=True)


@pytest.mark.parametrize(
    "raw,freq,geo,expected",
    [
        ("9/2026", "monthly", "USA", "2026-09"),
        ("Q2/2026", "quarterly", "USA", "2026-Q2"),
        ("2nd Quarter 2026", "Q", "USA", "2026-Q2"),
        ("II Quarter 2026", "quarterly", "USA", "2026-Q2"),
        ("Third Quarter of 2026", "Q", "USA", "2026-Q3"),
        ("2026 Q3", "Q", "USA", "2026-Q3"),
        ("Quarter 4/2026", "Q", "USA", "2026-Q4"),
        ("Segundo trimestre de 2026", "Q", "BRA", "2026-Q2"),
        ("Septiembre de 2026", "M", "MEX", "2026-09"),
        ("Settembre 2026", "M", "ITA", "2026-09"),
        ("août 2026", "M", "FRA", "2026-08"),
        ("set 2026", "M", "BRA", "2026-09"),
        ("Sept. 2026", "monthly", "USA", "2026-09"),
        ("2026.09", "M", "USA", "2026-09"),
        ("115年8月", "M", "TWN", "2026-08"),
        ("11509", "monthly", "TWN", "2026-09"),
        ("ROC 115/09", "monthly", "TWN", "2026-09"),
        ("民國115年9月", "M", None, "2026-09"),
        ("11509", "M", "USA", None),
        ("115年9月", "M", None, None),
        ("week ending September 19, 2026", "weekly", "USA", "2026-09-19"),
        ("week ending 2026-10-03", "fortnightly", "USA", "2026-10-03"),
        ("week ending October 3, 2026", "biweekly", "USA", "2026-10-03"),
        ("week ending October 3", "W", "USA", None),
        ("2026-09-24", "E", "CHE", "2026-09-24"),
        ("Sep 2026", "aperiodic", "USA", "2026-09"),
        ("Aug-2026", "monthly", "USA", "2026-08"),
        ("13/2026", "M", "USA", None),
    ],
)
def test_review_period_formats_and_frequency_aliases(raw, freq, geo, expected):
    assert explicit_period(raw, freq, geo) == expected


@pytest.mark.parametrize(
    "reason", ["incomplete", "late_value_needs_review", "overdue", "period_unknown"]
)
def test_future_release_never_completes_even_with_actual_or_official(
    tmp_path, legacy, reason
):
    occurrence(legacy, date="2026-10-20", value=3.0)
    db = tmp_path / "hub.sqlite"
    with open_writer(db) as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        insert(
            store.conn,
            "import_worklist",
            source_id="lazycommittee_calendar",
            occurrence_key="1",
            indicator_id="lazycommittee_calendar:us_cpi_yy",
            reason=reason,
            status="pending",
            first_seen_at=utc("2026-10-18T12:00:00Z"),
            last_checked_at=utc("2026-10-18T12:00:00Z"),
        )
        store.conn.execute("UPDATE import_worklist SET reason=?", (reason,))
        issuer(store)
        d = resolve_worklist(store, as_of=NOW)["decisions"][0]
        assert (d["status"], d["reason"], d["selected_vintage_id"]) == (
            "pending",
            "release_in_future",
            None,
        )


@pytest.mark.parametrize(
    "status,raw,value",
    [
        ("released", None, None),
        ("scheduled", "unparsed currency", None),
        ("scheduled", None, 3.0),
    ],
)
def test_expired_overdue_with_released_evidence_is_mapped_not_expired(
    tmp_path, legacy, status, raw, value
):
    occurrence(legacy, status=status, raw_actual=raw, value=value)
    with open_writer(tmp_path / "hub.sqlite") as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        d = resolve_worklist(store, as_of=NOW)["decisions"][0]
        assert (d["status"], d["reason"]) == ("complete", "catalogue_period_mapped")


@pytest.mark.parametrize(
    "raw_date,value,period,expected",
    [
        ("2026-10-02", 3.0, "2026-09", True),
        ("2026-10-03", 3.0, "2026-09", False),
        ("2026-10-02", 3.1, "2026-09", False),
        ("2026-10-02", 3.0, "2026-08", False),
    ],
)
def test_explicit_period_official_link_requires_date_value_and_period(
    tmp_path, legacy, raw_date, value, period, expected
):
    occurrence(legacy, date=raw_date, value=value, period=period)
    with open_writer(tmp_path / "hub.sqlite") as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        selected = issuer(store)
        d = resolve_worklist(store, as_of=NOW)["decisions"][0]
        assert (d["selected_vintage_id"] == selected) is expected


def test_same_date_period_value_from_different_indicator_cannot_link(tmp_path, legacy):
    occurrence(legacy, value=3.0)
    with open_writer(tmp_path / "hub.sqlite") as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        issuer(store, indicator_id="us_cpi_core_yy")
        d = resolve_worklist(store, as_of=NOW)["decisions"][0]
        assert d["selected_vintage_id"] is None
        assert d["indicator_id"] == "lazycommittee_calendar:us_cpi_yy"


def test_overlay_refuses_row_period_that_conflicts_with_audit(tmp_path, legacy):
    sighting(legacy, period="2026-09")
    with open_writer(tmp_path / "hub.sqlite") as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        resolve_worklist(store, as_of=NOW)
        index = EvidenceIndex(store)
        item = dict(store.conn.execute("SELECT * FROM import_worklist").fetchone())
        # A syntactically valid but inconsistent audit from an older rule must
        # never stamp its August period over the immutable September reading.
        with store.batch():
            values = dict(
                store.conn.execute("SELECT * FROM worklist_resolutions").fetchone()
            )
            values.pop("audit_id")
            values.update(run_id="older-bad-rule", reference_period="2026-08")
            insert(store.conn, "worklist_resolutions", **values)
        snapshot = Snapshot.load(store, NOW)
        assert snapshot.limitations and not any(
            e.reference_period == "2026-08" for e in snapshot.evidence
        )
        assert index.rows[item["source_id"], item["occurrence_key"]]


def test_future_and_stale_cutoffs_are_rejected_without_writes(tmp_path, legacy):
    sighting(legacy)
    db = tmp_path / "hub.sqlite"
    with open_writer(db) as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
    before = file_hashes(tmp_path)
    with pytest.raises(ValueError, match="later than"):
        execute(db, as_of="2031-01-01T00:00:00Z")
    assert file_hashes(tmp_path) == before
    # import_worklist keeps only its latest state: a cutoff before the last import/update would judge
    # items by reasons that did not exist yet, so it is refused rather than warned about.
    for dry_run in (True, False):
        with pytest.raises(ValueError, match="precedes the worklist state"):
            execute(db, dry_run=dry_run, as_of="2026-10-17T00:00:00Z")
    assert file_hashes(tmp_path) == before


def test_unrecognised_nonempty_period_stays_pending_and_reruns_stably(tmp_path, legacy):
    sighting(legacy, period="week ending October 3")
    with open_writer(tmp_path / "hub.sqlite") as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        assert resolve_worklist(store, as_of=NOW)["rules"] == {"period_year_missing": 1}
        assert resolve_worklist(store, as_of=NOW)["rules"] == {}


def test_ids_46_47_same_quarter_maps_from_iso_and_ordinal_text(tmp_path, legacy):
    with sqlite3.connect(legacy) as conn:
        insert(
            conn,
            "indicators",
            code="us_gdp",
            jurisdiction="USA",
            name="GDP q/q annualizzato",
            agency="BEA",
            unit_code="percent",
            transform="pct_change",
            basis="qoq",
            frequency="quarterly",
            scale=1,
            created_at=NOW,
            created_by_run="seed",
        )
    occurrence(
        legacy, 46, code="us_gdp", period="2026-Q2", date="2026-09-30", value=2.2
    )
    occurrence(legacy, 47, code="us_gdp", period="d:2026-09-30", date="2026-09-30")
    sighting(
        legacy,
        47,
        code="us_gdp",
        name="GDP q/q annualizzato",
        period="2nd Quarter 2026",
        date="2026-09-30",
        occurrence=47,
        value=None,
    )
    with open_writer(tmp_path / "hub.sqlite") as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        result = resolve_worklist(store, as_of=NOW)
        assert {d["reference_period"] for d in result["decisions"]} == {"2026-Q2"}
        assert all(d["status"] == "complete" for d in result["decisions"])


def test_audit_preserves_every_prior_field_and_revert_is_atomic_idempotent(
    tmp_path, legacy
):
    sighting(legacy)
    db = tmp_path / "hub.sqlite"
    with open_writer(db) as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        original = [
            dict(r) for r in store.conn.execute("SELECT * FROM import_worklist")
        ]
        result = resolve_worklist(store, as_of=NOW)
        audit = dict(
            store.conn.execute("SELECT * FROM worklist_resolutions").fetchone()
        )
        assert (audit["prior_status"], audit["prior_reason"]) == (
            "pending",
            "period_unknown",
        )
        assert json.loads(audit["prior_state_json"]) == original[0]
    preview = execute(db, dry_run=True, revert=result["run_id"])
    assert preview["reverted"] == 1
    applied = execute(db, revert=result["run_id"])
    assert applied["after"] == result["before"]
    with open_reader(db) as store:
        assert [
            dict(r) for r in store.conn.execute("SELECT * FROM import_worklist")
        ] == original
        assert Snapshot.load(store).evidence == []
        assert Snapshot.load(
            store, NOW
        ).evidence  # historical mapping before revert remains available
    assert execute(db, revert=result["run_id"])["reverted"] == 0
    assert execute(db, as_of=NOW)["rules"] == {"catalogue_period_mapped": 1}


def test_expiration_can_link_later_vintage_and_revert_requires_newest_run_first(
    tmp_path, legacy
):
    occurrence(legacy)
    with open_writer(tmp_path / "hub.sqlite") as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        first = resolve_worklist(store, as_of=NOW)
        assert first["rules"] == {"lateness_window_expired": 1}
        issuer(store, seen="2026-10-20T01:00:00Z")
        second = resolve_worklist(store, as_of="2026-10-20T12:00:00Z")
        assert second["rules"] == {"official_vintage_linked": 1}
        with pytest.raises(ValueError, match="newer run"):
            revert_worklist(store, first["run_id"])
        assert revert_worklist(store, second["run_id"])["after"] == first["after"]
        assert revert_worklist(store, first["run_id"])["after"] == first["before"]


def test_revert_refuses_concurrent_item_changes_without_partial_restore(
    tmp_path, legacy
):
    sighting(legacy, 1)
    sighting(legacy, 2)
    with open_writer(tmp_path / "hub.sqlite") as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        result = resolve_worklist(store, as_of=NOW)
        store.conn.execute(
            "UPDATE import_worklist SET last_checked_at='concurrent change' WHERE occurrence_key='sighting:1'"
        )
        before = [tuple(r) for r in store.conn.execute("SELECT * FROM import_worklist")]
        with pytest.raises(ValueError, match="changed"):
            revert_worklist(store, result["run_id"])
        assert [
            tuple(r) for r in store.conn.execute("SELECT * FROM import_worklist")
        ] == before


@pytest.mark.parametrize("held", ["lease", "writer"])
def test_apply_and_revert_refuse_daily_lease_or_writer_lock(
    tmp_path, legacy, capsys, held
):
    sighting(legacy)
    db = tmp_path / "hub.sqlite"
    with open_writer(db) as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
    context = RunLease(db) if held == "lease" else FileLock(str(db) + ".writer.lock")
    with context:
        for extra in ([], ["--revert", "run"]):
            with pytest.raises(SystemExit) as error:
                main(["resolve-worklist", "--db", str(db), *extra])
            assert error.value.code != 0
            assert (
                "lease is live" if held == "lease" else "writer lock is held"
            ) in capsys.readouterr().err


def test_missing_release_date_skips_one_item_and_other_items_still_resolve(
    tmp_path, legacy, monkeypatch
):
    sighting(legacy, 1)
    sighting(legacy, 2)
    with open_writer(tmp_path / "hub.sqlite") as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        index = EvidenceIndex(store)
        for row in index.rows["lazycommittee_calendar", "sighting:1"]:
            row["raw"].pop("release_date")
        monkeypatch.setattr(worklist, "EvidenceIndex", lambda store: index)
        result = resolve_worklist(store, as_of=NOW)
        assert result["rules"] == {
            "catalogue_period_mapped": 1,
            "release_date_missing": 1,
        }
        assert result["pending"] == 1


def test_lateness_cli_option_has_floor_and_controls_policy(tmp_path, legacy, capsys):
    occurrence(legacy, date="2026-10-01")
    db = tmp_path / "hub.sqlite"
    with open_writer(db) as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
    with pytest.raises(SystemExit):
        main(["resolve-worklist", "--db", str(db), "--lateness-days", "0"])
    capsys.readouterr()
    main(
        [
            "resolve-worklist",
            "--db",
            str(db),
            "--dry-run",
            "--as-of",
            NOW,
            "--lateness-days",
            "21",
        ]
    )
    result = json.loads(capsys.readouterr().out)
    assert (
        result["pending"] == 1
        and result["decisions"][0]["evidence"]["lateness_days"] == 21
    )


def test_v3_meeting_mapping_survives_read_overlay_and_revert(tmp_path, legacy):
    with sqlite3.connect(legacy) as conn:
        conn.execute("UPDATE indicators SET frequency='irregular'")
    occurrence(legacy, period="d:2026-10-02")
    sighting(
        legacy, 1, period="9 September 2026 meeting", code="us_cpi_yy", occurrence=1
    )
    sighting(
        legacy, 2, period="September 9, 2026 meeting", code="us_cpi_yy", occurrence=1
    )
    with open_writer(tmp_path / "hub.sqlite") as store:
        import_calendar(store, legacy, captured_at="2026-10-18T12:00:00Z")
        raw = [
            tuple(row)
            for row in store.conn.execute("SELECT * FROM readings ORDER BY id")
        ]
        result = resolve_worklist(store, as_of=NOW)
        assert result["rule_version"] == "legacy-worklist-v3"
        assert result["rules"] == {"catalogue_period_mapped": 1}
        after = Snapshot.load(store, NOW)
        actuals = [
            e
            for e in after.evidence
            if e.reading_kind in {"actual", "legacy_merged_actual"}
        ]
        assert len(actuals) == 3
        assert all(
            e.reference_period == "2026-09-09" and not e.verified for e in actuals
        )
        assert not any(
            "conflicting reference period" in message for message in after.limitations
        )
        assert resolve_worklist(store, as_of=NOW)["rules"] == {}
        revert_worklist(store, result["run_id"])
        assert [
            tuple(row)
            for row in store.conn.execute("SELECT * FROM readings ORDER BY id")
        ] == raw
