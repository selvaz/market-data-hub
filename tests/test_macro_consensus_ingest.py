"""Offline archive-to-card contracts, conservative skips and daily isolation."""

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest
import requests

from market_data_hub.macro_releases import open_reader, open_writer
from market_data_hub.macro_releases import consensus, migrations, run_daily
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.catalogue import INDICATORS
from market_data_hub.macro_releases.models import ConsensusReading, ScheduleVersion
from test_macro_read_api import observation


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("consensus ingestion must never use the network")

    monkeypatch.setattr(requests.Session, "request", forbidden)


@pytest.fixture
def store(tmp_path):
    with open_writer(tmp_path / "macro.sqlite") as store:
        for indicator in INDICATORS.values():
            store.put(indicator)
        yield store


def event(**changes):
    return dict(
        title="CPI y/y (2026-09)",
        country="USD",
        date="2026-10-14T08:30:00-04:00",
        forecast="3.6%",
        previous="3.4%",
        impact="High",
        **changes,
    )


def archive(
    directory,
    events,
    *,
    captured="2026-10-13T21:30:05.123456Z",
    filename="2026-10-13.json",
    manifest=True,
    digest=None,
):
    directory.mkdir(exist_ok=True)
    path = directory / filename
    body = json.dumps(events).encode()
    path.write_bytes(body)
    if manifest:
        with (directory / "manifest.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    dict(
                        outcome="written",
                        file=str(path),
                        captured_at_utc=captured,
                        sha256=digest or hashlib.sha256(body).hexdigest(),
                    )
                )
                + "\n"
            )
    return path


def rows(store):
    return [
        dict(r)
        for r in store.conn.execute("SELECT * FROM consensus_readings ORDER BY id")
    ]


def test_manifest_capture_pre_post_release_separate_providers_and_cards(
    store, tmp_path
):
    persist(store, [observation()])
    raw = tmp_path / "raw"
    archive(raw, [event()])
    first = consensus.ingest(store, raw)
    assert first["readings_added"] == first["events_mapped"] == 1
    pre = rows(store)[0]
    assert pre["provider"] == "ff_forecast" and pre["survey_identity"] is None
    assert pre["captured_at"] == "2026-10-13T21:30:05.123456+00:00"
    assert pre["release_cutoff_at"] == "2026-10-14T12:30:00.000000+00:00"
    assert pre["cutoff_basis"] == "scheduled"
    assert (pre["pre_release_eligible"], pre["post_release"]) == (1, 0)
    archive(raw, [event()], filename="2026-10-14.json", captured="2026-10-14T12:30:00Z")
    assert consensus.ingest(store, raw)["readings_added"] == 1
    post = rows(store)[1]
    assert (post["pre_release_eligible"], post["post_release"]) == (0, 1)
    other = {k: pre[k] for k in ConsensusReading.model_fields}
    store.put(ConsensusReading(**dict(other, provider="survey_fixture", value=3.5)))
    card = store.release_cards("2026-10-12", "2026-10-17")["cards"][0]
    assert card["resolution"]["value"] == 3.7
    values = {r["provider"]: r for r in card["consensus"]}
    assert set(values) == {"ff_forecast", "survey_fixture"}
    assert values["ff_forecast"]["id"] == pre["id"]
    assert values["ff_forecast"]["kind"] == "ff_forecast"
    assert values["ff_forecast"]["surprise"] == 0.1
    assert len(rows(store)) == 3
    # UNIQUE keys still deduplicate when the diagnostic ledger is bypassed.
    assert consensus.ingest(store, raw, reprocess=True)["readings_added"] == 0


def test_post_release_only_has_no_consensus_or_surprise(store, tmp_path):
    persist(store, [observation()])
    archive(tmp_path / "raw", [event()], captured="2026-10-14T12:31:00Z")
    consensus.ingest(store, tmp_path / "raw")
    card = store.release_cards("2026-10-12", "2026-10-17")["cards"][0]
    assert rows(store)[0]["post_release"] == 1
    assert card["consensus"] == []
    assert card["surprise"] is None


def test_processed_snapshot_bypasses_parsing_and_new_capture_ingests(
    store, tmp_path, monkeypatch
):
    raw = tmp_path / "raw"
    archive(raw, [event()])
    consensus.ingest(store, raw)
    original = consensus.prepare
    monkeypatch.setattr(
        consensus, "prepare", lambda *a: pytest.fail("old snapshot parsed again")
    )
    result = consensus.ingest(store, raw)
    assert result["snapshots_processed"] == result["readings_added"] == 0
    assert result["snapshots_unchanged"] == 1
    monkeypatch.setattr(consensus, "prepare", original)
    archive(raw, [event()], filename="2026-10-14.json", captured="2026-10-14T11:30:00Z")
    result = consensus.ingest(store, raw)
    assert (
        result["snapshots_processed"]
        == result["snapshots_unchanged"]
        == result["readings_added"]
        == 1
    )


@pytest.mark.parametrize(
    "changes,reason,mapped",
    [
        ({"title": "CPI-ish y/y (2026-09)"}, "unmapped", 0),
        ({"title": "CPI y/y"}, "period_unknown", 1),
        ({"title": "CPI y/y (Sep)"}, "unmapped", 0),
        ({"country": "GBP"}, "unmapped", 0),
        ({"country": "All"}, "unmapped", 0),
        ({"title": "German CPI y/y (2026-09)", "country": "EUR"}, "unmapped", 0),
        ({"title": "Final CPI y/y (2026-09)"}, "unmapped", 0),
        ({"forecast": ""}, "forecast_missing", 1),
        ({"forecast": "around 3.6%"}, "forecast_format_or_unit_unknown", 1),
        ({"forecast": "3.6"}, "forecast_format_or_unit_unknown", 1),
        ({"date": "2026-10-14T08:30:00"}, "release_schedule_unknown", 1),
        ({"period": "2026-08"}, "period_unknown", 1),
        ({"estimate_stage": "final"}, "stage_unknown_or_conflicting", 1),
    ],
)
def test_no_guesses_and_diagnostic_counts(store, tmp_path, changes, reason, mapped):
    item = dict(event(), **changes)
    archive(tmp_path / "raw", [item])
    result = consensus.ingest(store, tmp_path / "raw")
    assert result["events_mapped"] == mapped
    assert result["events_skipped"] == 1 and result["readings_added"] == 0
    assert result["skip_reasons"] == {reason: 1}
    assert result["skipped_titles"][0]["title"] == item["title"]


def test_mtime_is_not_capture_time_and_later_manifest_evidence_can_retry(
    store, tmp_path
):
    raw = tmp_path / "raw"
    archive(raw, [event()], manifest=False)
    first = consensus.ingest(store, raw)
    assert first["skip_reasons"] == {"capture_time_not_in_manifest": 1}
    assert first["snapshots"][0]["captured_at"] is None and rows(store) == []
    archive(raw, [event()])
    assert consensus.ingest(store, raw)["readings_added"] == 1


@pytest.mark.parametrize(
    "captured,flags",
    [
        ("2026-10-14T03:59:59Z", (1, 0)),
        ("2026-10-14T04:00:00Z", (0, 0)),
        ("2026-10-15T04:00:00Z", (0, 0)),
    ],
)
def test_date_only_issuer_local_lower_bound(store, tmp_path, captured, flags):
    archive(tmp_path / "raw", [dict(event(), date="2026-10-14")], captured=captured)
    consensus.ingest(store, tmp_path / "raw")
    reading = rows(store)[0]
    assert reading["release_cutoff_at"] == "2026-10-14T04:00:00.000000+00:00"
    assert reading["cutoff_precision"] == "date_only"
    assert (reading["pre_release_eligible"], reading["post_release"]) == flags


def test_existing_schedule_known_at_capture_wins_over_ff_time(store, tmp_path):
    persist(store, [observation()])
    release = "BLS:us_cpi:regular:2026-10-14"
    for recorded, expected in [
        ("2026-10-01T00:00:00Z", "2026-10-14T13:00:00Z"),
        ("2026-10-14T00:00:00Z", "2026-10-14T14:00:00Z"),
    ]:
        store.put(
            ScheduleVersion(
                release_event_id=release,
                expected_at=expected,
                issuer_timezone="America/New_York",
                precision="exact",
                source_id="BLS",
                recorded_at=recorded,
            )
        )
    archive(tmp_path / "raw", [event()])
    consensus.ingest(store, tmp_path / "raw")
    assert rows(store)[0]["release_cutoff_at"] == "2026-10-14T13:00:00.000000+00:00"


def test_linked_explicit_period_can_retain_unknown_cutoff(store, tmp_path):
    persist(store, [observation()])
    archive(tmp_path / "raw", [dict(event(), date=None)])
    assert consensus.ingest(store, tmp_path / "raw")["readings_added"] == 1
    reading = rows(store)[0]
    assert reading["release_event_id"] == "BLS:us_cpi:regular:2026-10-14"
    assert reading["release_cutoff_at"] is reading["cutoff_precision"] is None
    assert reading["cutoff_basis"] == "unknown"
    assert reading["pre_release_eligible"] == reading["post_release"] == 0


def test_offset_proves_schedule_even_without_known_issuer_zone():
    edition, cutoff, precision = consensus.event_clock(
        {"date": "2026-10-14T23:30:00-04:00"}, {"issuer_id": "other_catalogue_issuer"}
    )
    assert edition is None
    assert (cutoff, precision) == ("2026-10-15T03:30:00.000000+00:00", "exact")


def test_bad_snapshot_hash_retries_and_other_snapshots_continue(store, tmp_path):
    raw = tmp_path / "raw"
    archive(raw, [event()], digest="incorrect")
    archive(raw, [event()], filename="2026-10-14.json", captured="2026-10-14T11:00:00Z")
    result = consensus.ingest(store, raw)
    assert result["status"] == "failed" and result["readings_added"] == 1
    assert result["failures"] == [
        {"snapshot_file": "2026-10-13.json", "error_type": "ValueError"}
    ]
    assert (
        store.conn.execute("SELECT COUNT(*) FROM consensus_snapshots").fetchone()[0]
        == 1
    )


def test_snapshot_rows_and_ledger_roll_back_together(store, tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    archive(raw, [event(), dict(event(), title="CPI m/m (2026-09)", forecast="0.6%")])
    original = store.put

    def fail(record):
        if (
            isinstance(record, ConsensusReading)
            and record.indicator_id == "us_cpi_headline_mm"
        ):
            raise ValueError("fixture failure")
        return original(record)

    monkeypatch.setattr(store, "put", fail)
    assert consensus.ingest(store, raw)["status"] == "failed"
    assert rows(store) == []
    assert (
        store.conn.execute("SELECT COUNT(*) FROM consensus_snapshots").fetchone()[0]
        == 0
    )
    monkeypatch.setattr(store, "put", original)
    assert consensus.ingest(store, raw)["readings_added"] == 2


def test_reviewed_aliases_are_limited_exact_and_justified(store, tmp_path):
    catalogue = consensus.Catalogue(store)
    assert len(catalogue.aliases) == 6
    for alias in catalogue.aliases:
        indicator = INDICATORS[alias["indicator_id"]]
        assert indicator.tier == "T1" and indicator.geography in {"USA", "GBR", "EA21"}
        assert alias["title"] in alias["justification"]
        item = dict(
            event(), title=alias["title"] + " (2026-09)", country=alias["currency"]
        )
        mapped, reason = catalogue.match(item, consensus.mapping_title(item))
        assert reason is None and mapped == alias
    raw = tmp_path / "raw"
    archive(raw, [dict(event(), title="Final CPI y/y (2025-09)", country="EUR")])
    assert consensus.ingest(store, raw)["skip_reasons"] == {
        "geography_period_mismatch": 1
    }


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("broken", [False, True])
def test_runner_after_official_same_lease_independent_status(
    tmp_path, monkeypatch, dry_run, broken
):
    db = tmp_path / "runner.sqlite"
    raw = tmp_path / "raw"
    archive(raw, [event()])

    class Replay:
        def __init__(self, client):
            pass

        def fetch(self, *args):
            return [replace(observation(), revision="initial")]

    monkeypatch.setitem(run_daily.ADAPTERS, "us_cpi", Replay)
    original = run_daily.ingest
    seen = []

    def checked(store, directory, **kwargs):
        assert (
            store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 1
        )
        if not dry_run:
            assert Path(str(db) + ".daily.lease.json").exists()
        seen.append(1)
        if broken:
            raise RuntimeError("fixture failure")
        return original(store, directory, **kwargs)

    monkeypatch.setattr(run_daily, "ingest", checked)
    result = run_daily.execute(db, ["us_cpi"], 24, dry_run, raw_dir=raw)
    assert (
        seen == [1]
        and result["status"] == result["families"]["us_cpi"]["status"] == "ok"
    )
    assert result["exit_code"] == 0
    assert result["consensus"]["status"] == ("failed" if broken else "ok")
    if dry_run:
        assert not db.exists()
    else:
        with open_reader(db) as reader:
            log = reader.conn.execute("SELECT * FROM collect_run_consensus").fetchone()
            assert log["status"] == result["consensus"]["status"]
            assert json.loads(log["result_json"]) == result["consensus"]
            assert (
                reader.conn.execute(
                    "SELECT status FROM collect_runs WHERE finished_at IS NOT NULL"
                ).fetchone()[0]
                == "ok"
            )


def test_v5_migration_is_additive_and_ledgers_append_only(tmp_path):
    db = tmp_path / "v5.sqlite"
    with sqlite3.connect(db) as conn:
        for filename in (
            "schema.sql",
            "migration_003.sql",
            "migration_004.sql",
            "migration_005.sql",
        ):
            conn.executescript(
                Path(migrations.__file__).with_name(filename).read_text()
            )
        conn.execute("PRAGMA user_version=5")
        conn.execute(
            "INSERT INTO collect_runs VALUES ('old','2026-10-01',NULL,'running','[]','{}','v5','host')"
        )
    with open_writer(db) as store:
        assert store.conn.execute("PRAGMA user_version").fetchone()[0] == 6
        assert (
            store.conn.execute("SELECT code_version FROM collect_runs").fetchone()[0]
            == "v5"
        )
        assert store.conn.execute("PRAGMA foreign_key_check").fetchall() == []
        store.conn.execute(
            "INSERT INTO consensus_snapshots VALUES ('file','time','hash','rule','now','{}')"
        )
        store.conn.execute(
            "INSERT INTO collect_run_consensus VALUES ('old','running','no_change','now','{}')"
        )
        for table in ("consensus_snapshots", "collect_run_consensus"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                store.conn.execute(f"DELETE FROM {table}")


def test_recovery_of_consensus_interruption_preserves_completed_official_status(store):
    from market_data_hub.macro_releases import run_log

    parameters = {
        "consensus_raw_dir": "archive",
        "fetch_windows": {
            "us_cpi": {"fetch_since": "2024-10", "fetch_until": "2026-10-20"}
        },
    }
    run_log.start(store, "interrupted-consensus", ["us_cpi"], parameters)
    run_log.family_result(
        store,
        "interrupted-consensus",
        "us_cpi",
        dict(
            parameters["fetch_windows"]["us_cpi"],
            status="ok",
            vintages_added=1,
            readings_added=1,
            readings_skipped=0,
        ),
    )
    run_log.recover(store)
    terminal = store.conn.execute(
        "SELECT status FROM collect_runs WHERE run_id='interrupted-consensus' AND finished_at IS NOT NULL"
    ).fetchone()
    assert terminal[0] == "ok"
    failed = store.conn.execute("SELECT * FROM collect_run_consensus").fetchone()
    assert failed["status"] == "failed"
    assert json.loads(failed["result_json"])["error_type"] == "InterruptedRun"
    run_log.recover(store)  # append-only recovery is idempotent
    assert (
        store.conn.execute("SELECT COUNT(*) FROM collect_run_consensus").fetchone()[0]
        == 1
    )


def test_existing_catalogue_alias_requires_exact_stage_event_and_respects_scale(
    store, tmp_path
):
    persist(
        store,
        [
            observation(
                "us_payrolls_change",
                "us_payrolls",
                value=29,
                edition="2026-10-02",
                published="2026-10-02T12:30:00Z",
            )
        ],
    )
    store.conn.execute(
        "INSERT INTO indicator_aliases VALUES ('test','Non-Farm Employment Change','USA','fixture',"
        "'us_payrolls_change','regular','candidate')"
    )
    item = dict(
        event(),
        title="Non-Farm Employment Change",
        reference_period="2026-09",
        date="2026-10-02T08:30:00-04:00",
        forecast="50K",
    )
    archive(tmp_path / "raw", [item], captured="2026-10-01T21:30:00Z")
    assert consensus.ingest(store, tmp_path / "raw")["readings_added"] == 1
    reading = rows(store)[0]
    assert (reading["value"], reading["scale"], reading["unit"]) == (
        50,
        1000,
        "persons",
    )
    assert reading["release_event_id"] == "BLS:us_payrolls:regular:2026-10-02"
