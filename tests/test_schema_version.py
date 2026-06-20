# -*- coding: utf-8 -*-
"""Schema versioning: apply_schema records the version, migrate is idempotent."""
from __future__ import annotations

from market_data_hub.db import connection as C


def test_apply_schema_records_version(tmp_db):
    con = C.get_conn()  # get_conn() applies the schema on open
    assert C.get_schema_version(con) == C.SCHEMA_VERSION
    # schema_applied_at is also recorded
    row = con.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_applied_at'"
    ).fetchone()
    assert row is not None and row[0]
    con.close()


def test_get_schema_version_none_when_absent(tmp_db):
    con = C.get_conn()
    con.execute("DELETE FROM schema_meta WHERE key = 'schema_version'")
    assert C.get_schema_version(con) is None
    con.close()


def test_apply_schema_does_not_overwrite_existing_version(tmp_db):
    # An existing DB recorded at an older version must NOT be re-stamped to the
    # current SCHEMA_VERSION by apply_schema(): doing so would let an open via
    # get_conn() mask a pending migration. migrate() is what walks it forward.
    con = C.get_conn()
    con.execute(
        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', '0')"
    )
    C.apply_schema(con)  # re-applying schema keeps the recorded (older) version
    assert C.get_schema_version(con) == 0
    # migrate() brings the recorded version up to the code's SCHEMA_VERSION.
    assert C.migrate(con) == C.SCHEMA_VERSION
    assert C.get_schema_version(con) == C.SCHEMA_VERSION
    con.close()


def test_migrate_is_idempotent(tmp_db):
    con = C.get_conn()
    v1 = C.migrate(con)
    v2 = C.migrate(con)  # running twice keeps the version stable
    assert v1 == v2 == C.SCHEMA_VERSION
    assert C.get_schema_version(con) == C.SCHEMA_VERSION
    con.close()
