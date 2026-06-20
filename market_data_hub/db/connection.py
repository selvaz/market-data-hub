# -*- coding: utf-8 -*-
"""
connection.py — centralized access to the DuckDB database.

The DB path is configurable via settings.yaml or the MARKET_DATA_DB environment
variable. The schema is applied (idempotently) on first open.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import duckdb

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# Current schema version. Bump this whenever schema.sql changes shape and add a
# matching `if current < N:` branch in migrate() below.
SCHEMA_VERSION = 1


def _default_db() -> str:
    """Last-resort DB path when neither db_path, MARKET_DATA_DB nor settings.yaml
    provide one. Windows keeps the historical D:\\market_data location; other
    platforms fall back to a portable path under the user's home."""
    if os.name == "nt":
        return r"D:\market_data\market_data.duckdb"
    return str(Path.home() / ".market_data" / "market_data.duckdb")


_DEFAULT_DB = _default_db()


def _resolve_db_path(db_path: Optional[str] = None) -> str:
    if db_path:
        return db_path
    env = os.environ.get("MARKET_DATA_DB")
    if env:
        return env
    # settings.yaml takes precedence over the hard-coded default
    try:
        from market_data_hub.config_loader import get_settings
        s = get_settings()
        if s.get("db_path"):
            return s["db_path"]
    except Exception:
        pass
    return _DEFAULT_DB


def apply_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Apply the SQL schema (idempotent) and record schema metadata.

    The ``schema_version`` is stamped only when it is *absent* (a fresh
    database). Bumping the recorded version is migrate()'s job: opening an
    existing, older DB under newer code — directly or via get_conn() — must not
    pre-stamp it as current, or any `if current < N:` migration would be skipped
    while the DB is still at the old shape. The applied-at timestamp is always
    refreshed.
    """
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    con.execute(sql)
    now = datetime.now(timezone.utc).isoformat()
    con.execute(
        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES "
        "('schema_applied_at', ?)",
        [now],
    )
    if get_schema_version(con) is None:
        con.execute(
            "INSERT OR REPLACE INTO schema_meta (key, value) VALUES "
            "('schema_version', ?)",
            [str(SCHEMA_VERSION)],
        )


def get_schema_version(con: duckdb.DuckDBPyConnection) -> Optional[int]:
    """Return the schema_version recorded in schema_meta, or None if absent
    (table missing, or no row yet)."""
    try:
        row = con.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
    except duckdb.Error:
        return None
    if row is None or row[0] is None:
        return None
    return int(row[0])


def migrate(con: duckdb.DuckDBPyConnection) -> int:
    """Idempotent forward-migration entry point. Returns the resulting version.

    The recorded version is read *before* the schema is applied, so an existing
    DB walks the ordered ladder of `if current < N:` branches from its real
    version rather than being pre-stamped as current (which would mask pending
    migrations). A fresh DB is created at the baseline shape by apply_schema()
    and needs no ladder steps. Running migrate() again on an already-current DB
    is a no-op. To add a migration: raise SCHEMA_VERSION, append a new
    `if current < N:` block here, and update schema.sql so a fresh DB lands at
    the same shape.
    """
    recorded = get_schema_version(con)  # read BEFORE apply_schema stamps a baseline
    apply_schema(con)  # ensures every table exists; stamps baseline only if absent

    if recorded is None:
        # Fresh DB: apply_schema() just stamped it at the current baseline shape.
        return get_schema_version(con) or SCHEMA_VERSION

    current = recorded
    # Ordered ladder of forward migrations. Each future step runs its DDL/DML on
    # the *old* shape, then advances `current`, e.g.:
    #   if current < 2:
    #       con.execute(...)  # migrate v1 -> v2
    #       current = 2
    # (No active steps yet — v1 is the baseline.)
    if current < SCHEMA_VERSION:
        current = SCHEMA_VERSION

    con.execute(
        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES "
        "('schema_version', ?)",
        [str(current)],
    )
    return current


def get_conn(db_path: Optional[str] = None, *, read_only: bool = False
             ) -> duckdb.DuckDBPyConnection:
    """
    Open (creating if absent) the DuckDB database and ensure the schema.

    read_only=True for readers (reader.py, diagnose.py) so multiple processes
    can read in parallel without locking.
    """
    path = _resolve_db_path(db_path)
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    if read_only and not os.path.exists(path):
        # a reader on a nonexistent DB: create it once in write mode
        tmp = duckdb.connect(path)
        apply_schema(tmp)
        tmp.close()

    con = duckdb.connect(path, read_only=read_only)
    if not read_only:
        apply_schema(con)
    return con
