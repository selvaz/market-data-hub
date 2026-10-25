"""Housing adopts main's stage-aware calendar and schema-v9 snapshot contract."""
import json
import sqlite3

import pytest

from market_data_hub.macro_releases import open_writer, run_daily
from market_data_hub.macro_releases.catalogue import HOUSING_FAMILIES
from market_data_hub.macro_releases.consensus import ingest
from market_data_hub.macro_releases.housing_editions import EDITIONS, SCHEDULES
from market_data_hub.macro_releases.read_api import indicators_for_stage
from market_data_hub.macro_releases.schedule import release_instant
from test_macro_m9f import offline as offline


def test_missing_ff_archive_still_seeds_only_real_housing_memberships(tmp_path):
    with open_writer(tmp_path / "housing.sqlite") as store:
        with pytest.raises(FileNotFoundError):
            ingest(store, tmp_path / "missing-ff-archive")
        for family, measures in HOUSING_FAMILIES.items():
            rows = store.conn.execute(
                "SELECT e.edition,e.estimate_stage,r.indicator_id,r.reference_period,"
                "r.value,r.published_at,r.url,r.raw_payload_json,s.expected_at "
                "FROM release_events e JOIN readings r ON r.release_event_id=e.id "
                "JOIN schedule_versions s ON s.release_event_id=e.id WHERE e.family=?", (family,)).fetchall()
            expected = {(edition, stage, measure.id, period)
                        for edition, (period, stage, _) in EDITIONS[family].items() for measure in measures}
            assert {(r[0], r[1], r[2], r[3]) for r in rows} == expected
            assert len(rows) == len(expected)
            for row in rows:
                assert row[4] is row[5] is None  # schedules cannot certify actuals
                assert row[6] == SCHEDULES[family]
                assert json.loads(row[7])["calendar_url"] == SCHEDULES[family]
                assert row[8] == release_instant(row[0], family=family).isoformat(timespec="microseconds")
            for stage in {"regular", "flash", "final", "advance"}:
                expected_ids = ({m.id for m in measures}
                                if stage in {meta[1] for meta in EDITIONS[family].values()} else set())
                assert indicators_for_stage(family, stage) == expected_ids
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 0


def test_current_snapshot_schema_is_checked_before_private_migration(tmp_path):
    old = tmp_path / "old.sqlite"
    with sqlite3.connect(str(old)) as conn:
        conn.execute("PRAGMA user_version=8")
    before = old.read_bytes()
    with pytest.raises(RuntimeError, match="required schema"):
        with run_daily.preview_store(old, expected_schema=9):
            pytest.fail("An old backup must not be silently migrated into current-source evidence")
    assert old.read_bytes() == before
    current = tmp_path / "current.sqlite"
    with open_writer(current):
        pass
    before = current.read_bytes()
    with run_daily.preview_store(current, expected_schema=9) as copy:
        assert copy.conn.execute("PRAGMA user_version").fetchone()[0] == 9
    assert current.read_bytes() == before
