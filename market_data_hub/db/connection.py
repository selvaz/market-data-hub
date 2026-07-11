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
_REPO_ROOT = Path(__file__).resolve().parents[2]

# Current schema version. Bump this whenever schema.sql changes shape and add a
# matching `if current < N:` branch in migrate() below.
SCHEMA_VERSION = 6


def _default_db() -> str:
    """Last-resort DB path when neither db_path, MARKET_DATA_DB nor settings.yaml
    provide one.

    Keep the default repo-local so a clone is self-contained and does not depend
    on machine-specific drive letters.
    """
    return str(_REPO_ROOT / "market_data.duckdb")


_DEFAULT_DB = _default_db()


def _repo_local(path: str) -> str:
    p = Path(path)
    if p.is_absolute():
        return str(p)
    return str(_REPO_ROOT / p)


def _resolve_db_path(db_path: Optional[str] = None) -> str:
    if db_path:
        return _repo_local(db_path)
    env = os.environ.get("MARKET_DATA_DB")
    if env:
        return _repo_local(env)
    # settings.yaml takes precedence over the hard-coded default
    try:
        from market_data_hub.config_loader import get_settings
        s = get_settings()
        if s.get("db_path"):
            return _repo_local(s["db_path"])
    except Exception:
        pass
    return _DEFAULT_DB


def _table_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    """True if a base table ``name`` already exists in the database."""
    try:
        row = con.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?",
            [name],
        ).fetchone()
    except duckdb.Error:
        return False
    return row is not None


def apply_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Apply the SQL schema (idempotent) and record schema metadata.

    The ``schema_version`` is stamped here only for a *genuinely new* database
    (no core tables yet and no recorded version). An existing DB that predates
    ``schema_meta`` — unversioned but already populated — is deliberately left
    unstamped: stamping it as the current version would make it look up-to-date
    to migrate() and skip real `if current < N:` steps while `CREATE TABLE IF
    NOT EXISTS` does not add missing columns. Version advancement for such DBs
    is migrate()'s job. The applied-at timestamp is always refreshed.
    """
    # Detect freshness BEFORE creating tables, using a representative core table
    # as sentinel: if it is absent, this is a new database we may stamp.
    fresh = not _table_exists(con, "prices_daily")
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    con.execute(sql)
    now = datetime.now(timezone.utc).isoformat()
    con.execute(
        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES "
        "('schema_applied_at', ?)",
        [now],
    )
    if fresh and get_schema_version(con) is None:
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

    # v2 -> v3 columns (run_id, change_type, prior_value on the vintage tables)
    # must be ADDed *before* apply_schema() below re-runs schema.sql --
    # CREATE TABLE IF NOT EXISTS alone never adds columns to a table that
    # already exists in the old shape. Guarded on table existence: a genuinely
    # fresh DB has no table yet at this point and gets the column baked into
    # CREATE TABLE by apply_schema() instead. ALTER ... ADD COLUMN IF NOT
    # EXISTS is idempotent, safe to run every time.
    for table in ("macro_series_vintage", "macro_panel_vintage"):
        if _table_exists(con, table):
            con.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS run_id VARCHAR")
            con.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS change_type VARCHAR")
            con.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS prior_value DOUBLE")

    # v3 -> v4: drop the run_id indexes that v3 briefly introduced. On duckdb
    # 1.4.x (the last line supporting Python 3.9) a secondary index on a
    # column makes INSERT OR REPLACE silently keep the OLD value of that
    # column on the conflict path, so idx_msv_run / idx_mpv_run broke the
    # same-day vintage replacement run_id. Idempotent, safe to run every time.
    con.execute("DROP INDEX IF EXISTS idx_msv_run")
    con.execute("DROP INDEX IF EXISTS idx_mpv_run")

    apply_schema(con)  # ensures every table exists; stamps baseline only if fresh

    if recorded is None:
        stamped = get_schema_version(con)
        if stamped is not None:
            # Fresh DB: apply_schema() stamped it at the current baseline shape.
            return stamped
        # Unversioned but pre-existing DB (tables present, no schema_version row):
        # treat it as the v1 baseline and walk the ladder forward from there.
        recorded = 1

    current = recorded
    # Ordered ladder of forward migrations. Each future step runs its DDL/DML on
    # the *old* shape, then advances `current`.
    if current < 2:
        # v1 -> v2: custom_series (app-published series). Purely additive ---
        # apply_schema() above already created it via CREATE TABLE IF NOT
        # EXISTS; this step exists so the recorded version tracks the shape.
        current = 2
    if current < 3:
        # v2 -> v3: macro_series_vintage / macro_panel_vintage gain run_id,
        # change_type ('new' | 'revised') and prior_value, so a report can ask
        # "what did *this run* actually add or revise" instead of everything
        # dated today (vintage_date has day granularity, which conflates
        # multiple same-day runs). Columns already ensured above; this step
        # just advances the recorded version to match.
        current = 3
    if current < 4:
        # v3 -> v4: run_id indexes dropped (duckdb 1.4.x INSERT OR REPLACE
        # bug -- see the pre-apply_schema step above, which already ran the
        # idempotent DROPs). This step advances the recorded version.
        current = 4
    if current < 5:
        # v4 -> v5: identity + ingestion ledger (plan v3.1): issuers,
        # instruments, listings, identifier_aliases, ingestion_runs,
        # ingestion_jobs. Purely additive — apply_schema() above already
        # created them via CREATE TABLE IF NOT EXISTS; prices_daily is
        # deliberately untouched (listings.symbol joins prices_daily.symbol).
        current = 5
    if current < 6:
        # v5 -> v6: SEC/EDGAR tables (plan v3.1 Fase 3): sec_filings,
        # sec_company_facts (append-only), sec_coverage. Purely additive —
        # created by apply_schema() above.
        current = 6
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
        # migrate() also calls apply_schema() internally, then walks any
        # pending `if current < N:` ladder steps (e.g. ALTER TABLE ADD COLUMN)
        # that CREATE TABLE IF NOT EXISTS alone can't apply to a table that
        # already exists. Plain apply_schema() here would silently leave an
        # existing DB on an old column shape forever.
        migrate(con)
    return con


