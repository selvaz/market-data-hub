# -*- coding: utf-8 -*-
"""
connection.py — centralized access to the DuckDB database.

The DB path is configurable via settings.yaml or the MARKET_DATA_DB environment
variable. The schema is applied (idempotently) on first open.

In-process sharing: DuckDB refuses to open one file twice in the same process
with different configurations (for example ``read_only=True`` while a
read-write connection is alive). :func:`get_conn` therefore keeps one shared
database instance per resolved path and hands every caller its own cursor
wrapped in a :class:`HubConnection`. See ``_SharedDatabase`` for the rules.
"""

from __future__ import annotations

import math
import os
import re
import json
import threading
import time
import weakref
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

import duckdb

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"
_REPO_ROOT = Path(__file__).resolve().parents[2]

# Current schema version. Bump this whenever schema.sql changes shape and add a
# matching `if current < N:` branch in migrate() below.
SCHEMA_VERSION = 34


class DatabaseNotConfigured(RuntimeError):
    """No database path was given: there is deliberately no implicit default.

    A silent repo-local (or, for an installed wheel, environment-local) default
    let a process that missed its configuration write into a second, parallel
    database instead of the real one.
    """


def _repo_local(path: str) -> str:
    p = Path(path)
    if p.is_absolute():
        return str(p)
    return str(_REPO_ROOT / p)


def resolve_db_path(db_path: Optional[str] = None) -> str:
    """The canonical DB path for this repository — the only such chain.

    Public because it is the contract other entry points resolve against:
    ``lock.py``, ``regime/estimate.py`` and the DataSpace adapter all call
    it rather than re-deriving the order below. A second, independent chain
    is exactly the bug that once made a freshly-generated regime plot
    resolve as "not found" elsewhere in this codebase.

    Resolution order: explicit ``db_path``, then ``MARKET_DATA_DB``, then a
    ``db_path`` set in ``settings.yaml``. There is no implicit default: with none
    of them, :class:`DatabaseNotConfigured` is raised, so a misconfigured process
    fails instead of writing into a parallel database. Relative paths are resolved
    against the repo root.
    """
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
    raise DatabaseNotConfigured(
        "no market-data database configured: set MARKET_DATA_DB (or pass db_path, "
        "or set db_path in settings.yaml)"
    )


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
            con.execute(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS change_type VARCHAR"
            )
            con.execute(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS prior_value DOUBLE"
            )

    # v31 -> v32: estimate_currency must exist before apply_schema() re-creates
    # v_earnings_surprise, which selects it. Old rows stay NULL (unknown).
    for table in ("earnings_events", "earnings_observations"):
        if _table_exists(con, table):
            con.execute(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS estimate_currency VARCHAR"
            )

    # v3 -> v4: drop the run_id indexes that v3 briefly introduced. On duckdb
    # 1.4.x (the last line supporting Python 3.9) a secondary index on a
    # column makes INSERT OR REPLACE silently keep the OLD value of that
    # column on the conflict path, so idx_msv_run / idx_mpv_run broke the
    # same-day vintage replacement run_id. Idempotent, safe to run every time.
    con.execute("DROP INDEX IF EXISTS idx_msv_run")
    con.execute("DROP INDEX IF EXISTS idx_mpv_run")

    # v6 -> v7 rebuild MUST run before apply_schema(): schema.sql's v_returns
    # view now references prices_daily.listing_id and the view binder would
    # fail against the old table shape.
    if _table_exists(con, "prices_daily"):
        _migrate_prices_to_listing_key(con)

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
    if current < 7:
        # v6 -> v7 (audit CA-01): prices_daily rebuilt keyed by
        # (date, listing_id) — executed in the pre-apply_schema block above
        # (the new v_returns view would not bind against the old shape).
        # This step advances the recorded version.
        current = 7
    if current < 8:
        # v7 -> v8 (audit CA-08): sec_filings run provenance survives
        # re-ingestion — first_seen_run_id (immutable) + last_seen_run_id
        # replace the single run_id that INSERT OR REPLACE overwrote.
        if _table_exists(con, "sec_filings"):
            cols = {
                r[1] for r in con.execute("PRAGMA table_info('sec_filings')").fetchall()
            }
            con.execute(
                "ALTER TABLE sec_filings "
                "ADD COLUMN IF NOT EXISTS first_seen_run_id VARCHAR"
            )
            con.execute(
                "ALTER TABLE sec_filings "
                "ADD COLUMN IF NOT EXISTS last_seen_run_id VARCHAR"
            )
            if "run_id" in cols:
                con.execute(
                    "UPDATE sec_filings SET "
                    "first_seen_run_id = coalesce(first_seen_run_id, run_id), "
                    "last_seen_run_id = coalesce(last_seen_run_id, run_id)"
                )
                con.execute("ALTER TABLE sec_filings DROP COLUMN run_id")
        current = 8
    if current < 9:
        # v8 -> v9: etf_classification (curated-universe anagrafica). Purely
        # additive — apply_schema() above already created it via CREATE TABLE
        # IF NOT EXISTS. This step just advances the recorded version so a
        # write-mode open on an existing v8 DB is guaranteed to have run
        # apply_schema() (via migrate(), above) before anything queries the
        # new table — a read-only open on a pre-existing DB never calls
        # apply_schema() at all (see get_conn()), so without this version
        # bump an existing DB could sit indefinitely reporting "current"
        # while the table is still absent.
        current = 9
    if current < 10:
        # v9 -> v10: the economic release calendar (calendar_indicators,
        # calendar_events, calendar_observations, calendar_event_notes).
        # Purely additive: apply_schema() has already created them with
        # CREATE TABLE IF NOT EXISTS. As with the v8 -> v9 step, this exists
        # to advance the recorded version, so a pre-existing DB does not sit
        # declaring itself "current" while the new tables are still absent.
        current = 10
    if current < 11:
        # v10 -> v11: calendar_indicator_aliases, which records what each
        # source means by a name instead of recomputing it from a regex.
        # Additive in the same way. The table starts empty; seeding it from
        # the bindings already in calendar_observations was a deliberate act
        # (the retired econ_calendar.aliases.seed_from_observations), not a migration:
        # importing those bindings is a claim that they are right, and that
        # claim belongs to whoever runs it.
        current = 11
    if current < 12:
        # v11 -> v12: calendar_events.reference_date_origin, which separates a
        # period a provider published from one derived from the release date.
        # ALTER rather than additive-by-CREATE: the table already exists on any
        # DB at v10 or later, so CREATE TABLE IF NOT EXISTS does not add it.
        try:
            con.execute(
                "ALTER TABLE calendar_events ADD COLUMN reference_date_origin VARCHAR"
            )
        except Exception:
            pass  # already present: apply_schema() created the table fresh
        # Everything recorded before this step came from a source, because
        # nothing else could write it yet.
        con.execute(
            "UPDATE calendar_events SET reference_date_origin = 'source' "
            "WHERE reference_date IS NOT NULL AND reference_date_origin IS NULL"
        )
        current = 12
    if current < 13:
        # v12 -> v13: calendar_observations.release_precision, so consolidation
        # can prefer a minute-precision timestamp over a day-only placeholder
        # instead of taking the earliest and recording midnight.
        try:
            con.execute(
                "ALTER TABLE calendar_observations ADD COLUMN release_precision VARCHAR"
            )
        except Exception:
            pass  # already present on a freshly created table
        # Existing rows: midnight is what a day-only collector writes, so it is
        # the only defensible reading of what was already stored.
        con.execute(
            "UPDATE calendar_observations SET release_precision = "
            "CASE WHEN release_utc IS NULL THEN NULL "
            "     WHEN strftime(release_utc, '%H:%M:%S') = '00:00:00' "
            "     THEN 'day' ELSE 'minute' END "
            "WHERE release_precision IS NULL"
        )
        current = 13
    if current < 14:
        # v13 -> v14: drop the index v11 put on calendar_indicator_aliases
        # (status). Same defect as idx_msv_run above: with it in place, an
        # INSERT OR REPLACE that moves an alias from 'confirmed' to 'rejected'
        # keeps the old status, and the decision never lands.
        con.execute("DROP INDEX IF EXISTS idx_cal_alias_status")
        current = 14
    if current < 15:
        # v14 -> v15: and the other one. The index on (indicator_key) has the
        # same effect on the same duckdb line: rejecting an alias left its old
        # indicator_key in place, so the row read 'rejected' while still
        # pointing at the binding it was rejecting.
        con.execute("DROP INDEX IF EXISTS idx_cal_alias_indicator")
        current = 15
    if current < 16:
        # v15 -> v16: the same trap in calendar_events, which is rebuilt with
        # INSERT OR REPLACE on every consolidation. The index on
        # (country_iso3, reference_date) froze a corrected reference period,
        # and the one on release_utc froze a corrected release instant --
        # defeating the precision rule that keeps the point-in-time bridge
        # honest. Found by a probe test, not by reasoning.
        con.execute("DROP INDEX IF EXISTS idx_cal_events_release")
        con.execute("DROP INDEX IF EXISTS idx_cal_events_indicator")
        con.execute("DROP INDEX IF EXISTS idx_cal_events_refdate")
        current = 16
    if current < 17:
        # v16 -> v17: calendar_indicators gains data_type, side and tags, so a
        # caller can ask what exists along the axes people actually think in.
        # ALTER, not CREATE: the table exists on any DB from v10 on.
        for colonna in ("data_type", "side", "tags"):
            try:
                con.execute(
                    f"ALTER TABLE calendar_indicators ADD COLUMN {colonna} VARCHAR"
                )
            except Exception:
                pass  # already there on a freshly created table
        current = 17
    if current < 18:
        # v17 -> v18: the corporate earnings calendar (earnings_events,
        # earnings_observations, v_earnings_surprise). Additive: apply_schema()
        # created them above. The step advances the recorded version so an
        # existing DB does not report itself current while the tables are absent.
        current = 18
    if current < 19:
        # v18 -> v19: U.S. Treasury Fiscal Data (treasury_cash_balance,
        # treasury_debt_outstanding, treasury_auctions). Additive: apply_schema()
        # created them above. The step advances the recorded version so an
        # existing DB does not report itself current while the tables are absent.
        current = 19
    if current < 20:
        # v19 -> v20: CFTC Commitments of Traders positioning
        # (cftc_tff_positioning, cftc_legacy_positioning). Additive:
        # apply_schema() created them above. The step advances the recorded
        # version so an existing DB does not report itself current while
        # the tables are absent.
        current = 20
    if current < 21:
        # v20 -> v21: ALFRED vintage observations (alfred_vintage_observations).
        # Additive: apply_schema() created it above. The step advances the
        # recorded version so an existing DB does not report itself current
        # while the table is absent.
        current = 21
    if current < 22:
        # v21 -> v22: re-key the two CFTC tables on
        # (report_date, cftc_contract_market_code) instead of
        # (report_date, contract_market_name).
        #
        # The name is not unique on a report date. Found by running the
        # ingestion for the first time: on 2026-08-25 the vendor returned two
        # SOFR-3M rows (codes 134741 and 134FM1, 13.0M and 167k open interest)
        # and two XRP rows (176740 and 176LM2), in BOTH reports. Under the old
        # key the upsert kept whichever arrived last and dropped the other
        # without a word -- so the tables would have started life already
        # missing rows, and the loss would have looked like the vendor simply
        # not listing that contract.
        #
        # DuckDB cannot alter a primary key in place, so the tables are
        # rebuilt. Safe to do unconditionally here: they were introduced in
        # v19/v20 and are fed only by a runner added in this same change, so
        # nothing has written to them yet -- but the rows are carried over
        # anyway rather than assuming that, and the DISTINCT ON keeps the
        # highest open interest if a legacy duplicate somehow exists.
        for tabella, indice in (
            ("cftc_tff_positioning", "idx_cftc_tff_contract"),
            ("cftc_legacy_positioning", "idx_cftc_legacy_contract"),
        ):
            if not _table_exists(con, tabella):
                continue  # fresh DB: apply_schema() already built it right
            # The index depends on the table and blocks the rename;
            # apply_schema() recreates it below.
            con.execute(f"DROP INDEX IF EXISTS {indice}")
            con.execute(f"ALTER TABLE {tabella} RENAME TO {tabella}_v21")
            # v24's nullable report discriminator is the last physical column
            # in both fresh and migrated tables. Add it to this pre-v22 copy
            # too so INSERT ... SELECT * keeps matching column order.
            con.execute(
                f"ALTER TABLE {tabella}_v21 ADD COLUMN IF NOT EXISTS "
                "report_variant VARCHAR DEFAULT 'futures_only'"
            )
            apply_schema(con)  # recreates it with the corrected key
            con.execute(
                f"INSERT INTO {tabella} SELECT * FROM {tabella}_v21 "
                f"WHERE cftc_contract_market_code IS NOT NULL "
                f"QUALIFY ROW_NUMBER() OVER ("
                f"  PARTITION BY report_date, cftc_contract_market_code "
                f"  ORDER BY open_interest_all DESC NULLS LAST) = 1"
            )
            con.execute(f"DROP TABLE {tabella}_v21")
        current = 22
    if current < 23:
        # v22 -> v23: calendar_indicator_aliases records whether load_seed()
        # itself wrote a decision. Reconciliation may delete only those rows:
        # decided_by says who made the call and is deliberately caller-supplied,
        # so it cannot safely stand in for ownership.
        if _table_exists(con, "calendar_indicator_aliases"):
            con.execute(
                "ALTER TABLE calendar_indicator_aliases "
                "ADD COLUMN IF NOT EXISTS seeded_from_file BOOLEAN DEFAULT FALSE"
            )
            # A v22 database did not record ownership, so custom decided_by
            # values cannot distinguish a direct/observation upsert from a
            # file decision after the fact. decided_by='seed' is the one
            # concrete, checkable observation-seed case; exclude it. Every
            # other legacy confirmed/rejected row is treated as file-owned so
            # the first v23 load can reconcile decisions already removed from
            # the file. Proposals were never file-owned.
            con.execute(
                "UPDATE calendar_indicator_aliases SET seeded_from_file = TRUE "
                "WHERE status IN ('confirmed', 'rejected') "
                "AND decided_by IS DISTINCT FROM 'seed'"
            )
            con.execute(
                "ALTER TABLE calendar_indicator_aliases "
                "ALTER COLUMN seeded_from_file SET NOT NULL"
            )
        current = 23
    if current < 24:
        # v23 -> v24: CFTC report rows carry a nullable Futures Only versus
        # Futures-and-Options Combined discriminator. Current ingestion has
        # always stored Futures Only, so preserve that meaning on old rows and
        # use it as the insert default for new rows in both tables.
        for table in ("cftc_tff_positioning", "cftc_legacy_positioning"):
            if not _table_exists(con, table):
                continue  # fresh DB already has the column from schema.sql
            con.execute(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "
                "report_variant VARCHAR DEFAULT 'futures_only'"
            )
            con.execute(
                f"UPDATE {table} SET report_variant = 'futures_only' "
                "WHERE report_variant IS NULL"
            )
        current = 24
    if current < 25:
        # v24 -> v25: additive sovereign_bond_metadata table is created by
        # apply_schema above; macro history and its vintage rows are unchanged.
        current = 25
    if current < 26:
        # v26: bounded ALFRED validity intervals and resumable backfill ledger.
        con.execute(
            "ALTER TABLE alfred_vintage_observations "
            "ADD COLUMN IF NOT EXISTS realtime_end DATE"
        )
        current = 26
    if current < 27:
        # v27 adds sovereign catalogue, snapshot budget and comparisons.
        # All three are new tables, created idempotently by apply_schema().
        current = 27
    if current < 28:
        # v28 drops the two read views over the retired EconCalendar tables:
        # nothing has written calendar_events since 25/09/2026, so they served
        # month-old "upcoming" releases. The tables stay as history.
        con.execute("DROP VIEW IF EXISTS v_calendar_surprise")
        con.execute("DROP VIEW IF EXISTS v_calendar_upcoming")
        current = 28
    if current < 29:
        # The two snapshot tables are created by apply_schema().
        current = 29
    if current < 30:
        for column, kind in (
            ("source_as_of", "DATE"),
            ("proxy_symbol", "VARCHAR"),
            ("index_currency", "VARCHAR"),
            ("hedge_currency", "VARCHAR"),
        ):
            con.execute(
                f"ALTER TABLE etf_carry_snapshots ADD COLUMN IF NOT EXISTS {column} {kind}"
            )
        current = 30
    if current < 31:
        # The new table is created by apply_schema above.
        current = 31
    if current < 32:
        # earnings estimate_currency columns are added before apply_schema()
        # above; old rows stay NULL (unknown), never backfilled.
        current = 32
    if current < 33:
        # Caller-supplied listing metadata is retained as non-authoritative
        # provenance in the table created by apply_schema().
        current = 33
    if current < 34:
        # apply_schema() adds the listing registration hint lookup index.
        current = 34
    if current < SCHEMA_VERSION:
        current = SCHEMA_VERSION

    con.execute(
        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', ?)",
        [str(current)],
    )
    return current


def _migrate_prices_to_listing_key(con: duckdb.DuckDBPyConnection) -> None:
    """v6 -> v7: rebuild prices_daily keyed by (date, listing_id).

    No-op when the column is already there (fresh DBs get the new shape from
    schema.sql directly). Runs BEFORE apply_schema(), so the identity tables
    it joins on may not exist yet on very old DBs — created inline (same DDL
    as schema.sql, idempotent).
    """
    cols = {r[1] for r in con.execute("PRAGMA table_info('prices_daily')").fetchall()}
    if "listing_id" in cols:
        return

    from market_data_hub.db.identity import ensure_listing

    # identity tables may predate apply_schema() at this point of migrate()
    con.execute("""
        CREATE TABLE IF NOT EXISTS instruments (
            instrument_id VARCHAR PRIMARY KEY, issuer_id VARCHAR,
            kind VARCHAR NOT NULL, name VARCHAR,
            created_at TIMESTAMP, updated_at TIMESTAMP)""")
    con.execute("""
        CREATE TABLE IF NOT EXISTS listings (
            listing_id VARCHAR PRIMARY KEY, instrument_id VARCHAR NOT NULL,
            symbol VARCHAR NOT NULL, exchange VARCHAR, currency VARCHAR,
            provider VARCHAR, provider_symbol VARCHAR,
            active_from DATE, active_to DATE,
            created_at TIMESTAMP, updated_at TIMESTAMP)""")
    con.execute("""
        CREATE TABLE IF NOT EXISTS identifier_aliases (
            namespace VARCHAR NOT NULL, value VARCHAR NOT NULL,
            target_type VARCHAR NOT NULL, target_id VARCHAR NOT NULL,
            valid_from DATE, valid_to DATE, updated_at TIMESTAMP,
            PRIMARY KEY (namespace, value, target_type, target_id))""")

    # 1. auto-register listings for symbols that have none
    orphans = [
        r[0]
        for r in con.execute("""
        SELECT DISTINCT p.symbol FROM prices_daily p
        LEFT JOIN listings l ON l.symbol = p.symbol AND l.active_to IS NULL
        WHERE l.listing_id IS NULL
    """).fetchall()
    ]
    for sym in orphans:
        ensure_listing(con, sym)

    # 2. collision report: a symbol with >1 active listing cannot have its
    #    historical rows attributed — abort loudly, never guess
    dups = con.execute("""
        SELECT l.symbol, COUNT(*) AS n FROM listings l
        WHERE l.active_to IS NULL
          AND l.symbol IN (SELECT DISTINCT symbol FROM prices_daily)
        GROUP BY l.symbol HAVING COUNT(*) > 1
    """).fetchall()
    if dups:
        raise RuntimeError(
            "prices_daily v7 migration aborted: these symbols map to multiple "
            f"active listings, attribute the history manually first: {dups}"
        )

    # 3. rebuild with the new key
    con.execute("DROP INDEX IF EXISTS idx_prices_symbol")
    con.execute("""
        CREATE TABLE prices_daily_v7 (
            date        DATE        NOT NULL,
            listing_id  VARCHAR     NOT NULL,
            symbol      VARCHAR     NOT NULL,
            open        DOUBLE,
            high        DOUBLE,
            low         DOUBLE,
            close       DOUBLE,
            adj_close   DOUBLE,
            volume      BIGINT,
            source      VARCHAR,
            is_live     BOOLEAN DEFAULT FALSE,
            updated_at  TIMESTAMP,
            PRIMARY KEY (date, listing_id)
        )
    """)
    con.execute("""
        INSERT INTO prices_daily_v7
        SELECT p.date, l.listing_id, p.symbol, p.open, p.high, p.low, p.close,
               p.adj_close, p.volume, p.source, p.is_live, p.updated_at
        FROM prices_daily p
        JOIN listings l ON l.symbol = p.symbol AND l.active_to IS NULL
    """)
    row_before = con.execute("SELECT COUNT(*) FROM prices_daily").fetchone()
    row_after = con.execute("SELECT COUNT(*) FROM prices_daily_v7").fetchone()
    assert row_before is not None and row_after is not None  # COUNT(*) is total
    before, after = row_before[0], row_after[0]
    if before != after:
        raise RuntimeError(
            f"prices_daily v7 migration aborted: row count changed "
            f"({before} -> {after})"
        )
    con.execute("DROP TABLE prices_daily")
    con.execute("ALTER TABLE prices_daily_v7 RENAME TO prices_daily")


def _budget_from_env(name: str, default: str) -> float:
    """A wait budget from the environment, defensively.

    A non-finite or negative value would turn a bound into no bound at all:
    ``time.monotonic() >= float("nan")`` is always false, so a permanently
    held lock would be waited on forever, which is the one outcome a budget
    exists to prevent. Fall back to the default rather than hang.
    """
    try:
        value = float(os.environ.get(name, default))
    except ValueError:
        return float(default)
    if not math.isfinite(value) or value < 0:
        return float(default)
    return value


_READER_LOCK_WAIT_S = _budget_from_env("MARKET_DATA_READER_LOCK_WAIT_S", "300")
_READER_LOCK_POLL_S = 5.0
_WRITER_LOCK_WAIT_S = _budget_from_env("MARKET_DATA_WRITER_LOCK_WAIT_S", "300")
_WRITER_LOCK_POLL_S = 5.0

#: How each platform says "another process holds this file". DuckDB reports
#: the operating system's own wording, and the two share no substring at
#: all -- verified by holding the file from a second process on each:
#:
#:   Windows  Cannot open file "...": The process cannot access the file
#:            because it is being used by another process.
#:   Linux    Could not set lock on file "...": Conflicting lock is held
#:            in /opt/.../python3.11 (PID 2484).
#:
#: Matching one of them is why the waiter did nothing on the other: the
#: guard re-raised immediately and the reader never waited. This is a tuple
#: rather than a looser test because the contract is that only a lock is
#: waited out -- a missing file, a permission error or a corrupt database
#: are IOExceptions too, and waiting five minutes to re-raise them would be
#: worse than failing at once.
_LOCK_HELD_MARKERS = (
    "being used by another process",  # Windows
    "Conflicting lock",  # Linux, macOS
)


def _is_lock_held(exc: BaseException) -> bool:
    """Whether this IO error is another process holding the file.

    DuckDB embeds the database path in its message, so matching the raw text
    would call any IO error a held lock as soon as a directory happened to be
    named like the wording we look for. Strip the quoted path first: what is
    left is the operating system's own sentence about the lock.
    """
    return any(m in re.sub(r'"[^"]*"', '""', str(exc)) for m in _LOCK_HELD_MARKERS)


def _connect_waiting(
    path: str, *, read_only: bool, budget_s: float, poll_s: float
) -> duckdb.DuckDBPyConnection:
    """Open the database, waiting out another process that holds the file.

    DuckDB gives the file to one writer and fails everyone else outright
    rather than queueing them. On a machine with long-lived readers that take
    no advisory lock, contention is the normal condition rather than a
    programming error, and a job dying instantly loses hours of work over a
    lock that clears in seconds. Both directions therefore wait, on separate
    budgets so they can be tuned apart.

    Only a held lock is waited out. A missing file, a permission error or a
    corrupt database are IOExceptions too, and waiting five minutes before
    re-raising them would be worse than failing at once.

    What the budget bounds is the time spent WAITING, and one last attempt is
    made when it expires: a lock that clears during the final sleep yields a
    connection rather than a failure, which is the whole point of having
    waited. The alternative reading -- give up without trying again -- throws
    away the outcome the budget was spent on.

    The two budgets are read from the environment once, at import. That is a
    start-up setting, not a knob: a process that edits ``os.environ`` and then
    opens a connection keeps the value it started with.

    Residual risks, deliberately accepted: two waiting writers serialize, a
    lock outliving the budget still raises, and a forgotten connection is
    indistinguishable from honest contention, so it delays its own alarm by
    the length of the budget.
    """
    deadline = time.monotonic() + budget_s
    while True:
        try:
            return duckdb.connect(path, read_only=read_only)
        except duckdb.IOException as exc:
            if not _is_lock_held(exc):
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            # Never sleep past the deadline. A fixed poll longer than what is
            # left would either hand back a connection after the budget had
            # expired, or raise a whole poll interval late -- in both cases
            # the budget would not be the bound it claims to be.
            time.sleep(min(poll_s, remaining))


def _connect_read_only_waiting(path: str) -> duckdb.DuckDBPyConnection:
    """A reader, waiting out whoever holds the file."""
    return _connect_waiting(
        path, read_only=True, budget_s=_READER_LOCK_WAIT_S, poll_s=_READER_LOCK_POLL_S
    )


def _connect_read_write_waiting(path: str) -> duckdb.DuckDBPyConnection:
    """A writer, waiting out whoever holds the file."""
    return _connect_waiting(
        path, read_only=False, budget_s=_WRITER_LOCK_WAIT_S, poll_s=_WRITER_LOCK_POLL_S
    )


# ---------------------------------------------------------------------------
# In-process sharing of one DuckDB instance per file
# ---------------------------------------------------------------------------

#: Statement types a read-only handle refuses outright. DuckDB enforces this
#: natively on a read-only database; the guard gives the same answer when a
#: read-only request is served from a read-write instance shared with a
#: writer. CREATE (TEMP allowed), COPY (``COPY (query) TO`` allowed) and
#: CALL/PRAGMA (checkpoints refused) are refined in ``_check_read_only``.
_WRITE_STATEMENT_TYPES = frozenset({
    "INSERT", "UPDATE", "DELETE", "ALTER", "DROP", "VACUUM", "COPY_DATABASE",
    "ATTACH", "DETACH", "MERGE_INTO", "CREATE_FUNC", "ANALYZE",
})
_TEMP_CREATE = re.compile(
    r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?TEMP(?:ORARY)?\b", re.IGNORECASE
)
_COPY_QUERY_TO = re.compile(r"^\s*COPY\s*\(", re.IGNORECASE)
_CHECKPOINT = re.compile(r"\b(?:FORCE_?\s*)?CHECKPOINT\b", re.IGNORECASE)

#: How long open_existing() waits for another thread of this process that is
#: opening or closing the same file, or for a queued writer, before giving up.
_OPEN_EXISTING_WAIT_S = 5.0


class ReadOnlyViolation(duckdb.InvalidInputException):
    """A write was attempted through a handle opened with ``read_only=True``."""


class SharedDatabaseBusy(duckdb.IOException):
    """The in-process shared instance could not be obtained in time."""


def _refuse(what: str) -> ReadOnlyViolation:
    return ReadOnlyViolation(
        f"Cannot {what} through a read-only market-data-hub connection")


#: Functions that change database state even inside a SELECT (sequences,
#: checkpoints called as table functions). ``currval`` only reads.
_STATE_CHANGING_FUNCTIONS = frozenset({
    "nextval", "setval", "checkpoint", "force_checkpoint",
})
_STATE_CHANGING_HINT = re.compile(
    r"\b(?:nextval|setval|checkpoint|force_checkpoint)\b", re.IGNORECASE)
_AST_CON: Optional[duckdb.DuckDBPyConnection] = None
_AST_LOCK = threading.Lock()


def _function_names(sql: str) -> Optional[set]:
    """Every function name in a SELECT, from DuckDB's own serialised AST.

    ``None`` when DuckDB cannot serialise it (callers then refuse).
    """
    global _AST_CON
    with _AST_LOCK:
        if _AST_CON is None:
            _AST_CON = duckdb.connect(":memory:")
        try:
            row = _AST_CON.execute("SELECT json_serialize_sql(?)", [sql]).fetchone()
        except duckdb.Error:
            return None
    if row is None:
        return None
    tree = json.loads(row[0])
    if tree.get("error"):
        return None
    names: set = set()
    stack: list = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            name = node.get("function_name")
            if isinstance(name, str):
                names.add(name.lower())
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return names


def _check_statement(kind: str, text: str) -> None:
    """Refuse a statement that could change the database.

    Scope: this is a safety net that keeps read-only callers honest when
    they share a read-write instance, not a security boundary. It parses
    with DuckDB (statement types, and function names from the serialised
    AST of SELECTs that mention a state-changing function); anything it
    cannot classify on that path is refused rather than allowed.
    """
    if kind == "SELECT" and _STATE_CHANGING_HINT.search(text):
        names = _function_names(text)
        if names is None:
            raise _refuse("execute a SELECT whose functions cannot be classified")
        hit = names & _STATE_CHANGING_FUNCTIONS
        if hit:
            raise _refuse(f"call {', '.join(sorted(hit))}()")
    if kind in _WRITE_STATEMENT_TYPES:
        raise _refuse(f'execute a statement of type "{kind}"')
    if kind == "CREATE" and not _TEMP_CREATE.match(text):
        raise _refuse('execute a statement of type "CREATE" (non-TEMP)')
    if kind == "COPY" and not _COPY_QUERY_TO.match(text):
        raise _refuse('execute "COPY" (only "COPY (query) TO" is allowed)')
    if kind in ("CALL", "PRAGMA") and _CHECKPOINT.search(text):
        raise _refuse("checkpoint")


def _check_read_only(query: Any) -> None:
    """Refuse anything that could write, parsed by DuckDB's own parser."""
    if isinstance(query, duckdb.Statement):
        _check_statement(query.type.name, query.query)
        return
    if not isinstance(query, str):
        raise _refuse(f"execute a {type(query).__name__} (only SQL text is checked)")
    try:
        statements = duckdb.extract_statements(query)
    except duckdb.Error:
        return  # executing it fails in the same parser; let DuckDB say why
    for stmt in statements:
        _check_statement(stmt.type.name, stmt.query)


# --- relations ---------------------------------------------------------------

#: Relation methods that write to the database (``create_view`` and
#: ``to_view`` create persistent views: verified on a read-only database).
_RELATION_WRITE_METHODS = frozenset({
    "create", "create_view", "insert", "insert_into", "to_table", "to_view",
    "update",
})
#: Relation methods and attributes that only read. An explicit list rather
#: than a fall-through: a method a future DuckDB adds is unavailable until
#: someone classifies it here.
_RELATION_READ_METHODS = frozenset("""
    aggregate alias any_value apply arg_max arg_min arrow avg bit_and bit_or
    bit_xor bitstring_agg bool_and bool_or columns count cross cume_dist
    dense_rank describe description df distinct dtypes except_ execute explain
    favg fetch_arrow_reader fetch_arrow_table fetch_df_chunk fetch_record_batch
    fetchall fetchdf fetchmany fetchnumpy fetchone filter first first_value
    fsum geomean histogram intersect join lag last last_value lead limit list
    map max mean median min mode n_tile nth_value order percent_rank pl
    product project quantile quantile_cont quantile_disc rank rank_dense
    row_number select select_dtypes select_types set_alias shape show sort
    sql_query std stddev stddev_pop stddev_samp string_agg sum tf
    to_arrow_reader to_arrow_table to_csv to_df to_parquet torch type types
    union unique value_counts var var_pop var_samp variance write_csv
    write_parquet
""".split())


class _GuardedRelation:
    """A DuckDB relation bound to a :class:`HubConnection`.

    It holds the handle, so garbage collection cannot close the cursor a
    lazily evaluated relation still needs; it exposes only classified
    methods; and on a read-only handle it refuses the writing ones. Methods
    returning relations return guarded relations.
    """

    __slots__ = ("_rel", "_hub")

    def __init__(self, rel: duckdb.DuckDBPyRelation, hub: "HubConnection") -> None:
        self._rel = rel
        self._hub = hub

    def __getattr__(self, name: str) -> Any:
        if name in _RELATION_WRITE_METHODS:
            if self._hub._read_only:
                raise _refuse(f"call relation.{name}()")
        elif name not in _RELATION_READ_METHODS:
            raise AttributeError(
                f"relation.{name} is not exposed by market-data-hub connections "
                "(classify it in _RELATION_READ_METHODS or _RELATION_WRITE_METHODS)")
        attr = getattr(self._rel, name)
        return self._hub._wrapping(attr) if callable(attr) else attr

    def query(self, virtual_table_name: str, sql_query: str) -> Any:
        self._hub._use()
        if self._hub._read_only:
            _check_read_only(sql_query)
        return self._hub._wrap(self._rel.query(virtual_table_name, sql_query))

    def close(self) -> None:
        self._rel.close()

    def __getitem__(self, item: Any) -> Any:
        self._hub._use()
        return self._hub._wrap(self._rel[item])

    def __len__(self) -> int:
        return len(self._rel)

    def __repr__(self) -> str:
        return repr(self._rel)

    def __str__(self) -> str:
        return str(self._rel)


# --- the shared instance -----------------------------------------------------

class _SharedDatabase:
    """One open DuckDB database instance for one file in this process.

    Rules (all state is guarded by ``_REGISTRY_COND``):

    * The instance is opened by the first caller and closed when the last
      handle on it is closed (or garbage collected), so the file lock other
      processes see is held no longer than before this registry existed.
      While it is being opened or closed the entry stays registered with
      ``busy`` set, so nobody else touches the file meanwhile.
    * Only a read-write request (:func:`get_conn` without ``read_only``)
      ever opens a read-write instance, and it runs ``migrate()`` on it
      before any handle exists. A read-only request joins whatever instance
      is open (through a guarded handle if it is read-write), opens a
      read-only instance when no writer is queued, or waits for the queued
      writer's instance. It never opens read-write and never migrates.
    * A read-write request arriving while a read-only instance is open waits,
      on the writer budget, for the read-only handles to drain. New readers
      queue behind a waiting writer unless their thread uses a handle here
      (that would deadlock).
    * A read-write instance stays read-write until its last handle closes,
      even when only readers remain; it keeps the exclusive file lock
      meanwhile.
    * Every wait is bounded and raises :class:`SharedDatabaseBusy`.

    Thread ownership: a handle belongs to the thread that last used it
    (created it, called a method on it, or entered it). Ownership decides
    whether a thread "holds" a read-only instance, for the same-thread
    upgrade check and for ``lock.db_write_lock``'s fail-fast.
    """

    __slots__ = ("key", "read_only", "root", "refs", "busy", "handles")

    def __init__(self, key: str, read_only: bool) -> None:
        self.key = key
        self.read_only = read_only
        self.root: Optional[duckdb.DuckDBPyConnection] = None
        self.refs = 0
        self.busy = True  # being opened
        self.handles: "weakref.WeakSet[HubConnection]" = weakref.WeakSet()

    def used_by(self, thread: int) -> bool:
        return any(h._user == thread and not h._closed for h in list(self.handles))


_REGISTRY: dict[str, _SharedDatabase] = {}
_REGISTRY_COND = threading.Condition(threading.Lock())
#: Entries whose handle was garbage collected, waiting to be released. A
#: finaliser can run on any thread at any allocation -- including a thread
#: that holds the (non-reentrant) registry lock -- so ``__del__`` never
#: blocks on the lock: it queues the release here and drains the queue only
#: if it can take the lock without waiting. Whoever holds the lock drains
#: the queue right after releasing it (``_registry()``).
_PENDING_RELEASES: "deque[_SharedDatabase]" = deque()


@contextmanager
def _registry() -> Iterator[threading.Condition]:
    """Hold the registry lock; drain finalised releases once it is free."""
    cond = _REGISTRY_COND
    try:
        with cond:
            yield cond
    finally:
        _drain_pending_releases()


def _drain_pending_releases() -> None:
    while _PENDING_RELEASES:
        cond = _REGISTRY_COND
        if not cond.acquire(blocking=False):
            return  # the holder drains when it lets go
        closing = []
        try:
            while _PENDING_RELEASES:
                entry = _PENDING_RELEASES.popleft()
                root = _release_locked(entry)
                if root is not None:
                    closing.append((entry, root))
        finally:
            cond.release()
        for entry, root in closing:
            _finish_close(entry, root)
#: Writers queued per key, waiting for a read-only instance to drain.
_WRITERS_WAITING: dict[str, int] = {}


def _registry_key(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def thread_holds_read_only_instance(db_path: Optional[str] = None) -> bool:
    """Whether this thread uses a handle on a read-only shared instance.

    Such a thread can never become a writer on that file until the handle is
    closed: a writer waits for read-only handles to drain. ``db_write_lock``
    uses this to fail fast instead of waiting on the advisory lock, which a
    writer holding it while waiting for that handle would never release (a
    lock-ordering deadlock). "Uses" follows the ownership rule documented on
    ``_SharedDatabase``: the thread that last used the handle.
    """
    key = _registry_key(resolve_db_path(db_path))
    me = threading.get_ident()
    with _registry():
        entry = _REGISTRY.get(key)
        return bool(entry is not None and entry.read_only and entry.used_by(me))


# --- the handle ----------------------------------------------------------------

def _sql_method(name: str) -> Any:
    def method(self: "HubConnection", query: Any, *args: Any, **kwargs: Any) -> Any:
        self._use()
        if self._read_only:
            _check_read_only(query)
        return self._wrap(getattr(self._cursor, name)(query, *args, **kwargs))
    method.__name__ = name
    method.__doc__ = f"``DuckDBPyConnection.{name}``, read-only checked."
    return method


def _plain_method(name: str) -> Any:
    def method(self: "HubConnection", *args: Any, **kwargs: Any) -> Any:
        self._use()
        return self._wrap(getattr(self._cursor, name)(*args, **kwargs))
    method.__name__ = name
    method.__doc__ = f"``DuckDBPyConnection.{name}``."
    return method


def _write_method(name: str) -> Any:
    def method(self: "HubConnection", *args: Any, **kwargs: Any) -> Any:
        self._use()
        if self._read_only:
            raise _refuse(f"call {name}()")
        return self._wrap(getattr(self._cursor, name)(*args, **kwargs))
    method.__name__ = name
    method.__doc__ = f"``DuckDBPyConnection.{name}``; refused when read-only."
    return method


def _result_property(name: str) -> property:
    def getter(self: "HubConnection") -> Any:
        return getattr(self._cursor, name)
    getter.__name__ = name
    return property(getter, doc=f"``DuckDBPyConnection.{name}``.")


#: SQL entry points: the statement is parsed and checked on read-only handles.
_HUB_SQL_METHODS = ("execute", "executemany", "sql", "query", "from_query")
#: Reads of the pending result, relation constructors over files, frames or
#: existing tables/views, and connection-local session state (temporary
#: registrations, Python UDFs, transactions). None of them writes the file.
_HUB_PLAIN_METHODS = (
    "fetchone", "fetchall", "fetchmany", "fetchdf", "fetch_df", "fetchnumpy",
    "fetch_df_chunk", "fetch_arrow_table", "fetch_record_batch", "df", "pl",
    "arrow", "tf", "torch", "to_arrow_table", "to_arrow_reader",
    "table", "view", "values", "table_function", "read_csv", "read_json",
    "read_parquet", "from_df", "from_arrow", "from_parquet", "from_csv_auto",
    "register", "unregister", "create_function", "remove_function",
    "begin", "commit", "rollback", "get_table_names", "extract_statements",
    "interrupt",
)
#: Methods that write without SQL text.
_HUB_WRITE_METHODS = ("append", "checkpoint")
#: Plain attributes of the pending result.
_HUB_PROPERTIES = ("description", "rowcount")


class HubConnection:
    """A per-caller cursor on the shared database instance for one file.

    Exposes an explicit allow-list of ``duckdb.DuckDBPyConnection`` methods
    (see ``_HUB_*`` above); anything else raises ``AttributeError`` rather
    than reaching the raw cursor. SQL is checked on read-only handles with
    DuckDB's parser; calls returning the connection return this handle (so
    ``con.execute(...).fetchone()`` chains stay guarded and keep the handle
    alive); relations come back as guarded relations; ``cursor()`` and
    ``duplicate()`` return further tracked handles. ``close()`` closes only
    this handle; the shared instance closes with its last handle. Like any
    DuckDB connection, one handle must not be used by two threads at once.
    """

    def __init__(self, entry: Optional[_SharedDatabase],
                 cursor: duckdb.DuckDBPyConnection, read_only: bool) -> None:
        self._entry = entry
        self._cursor = cursor
        self._read_only = read_only
        self._user = threading.get_ident()
        self._closed = False

    # -- bookkeeping --------------------------------------------------------
    def _use(self) -> None:
        self._user = threading.get_ident()

    def _wrap(self, value: Any) -> Any:
        if value is self._cursor:
            return self
        if isinstance(value, duckdb.DuckDBPyRelation):
            return _GuardedRelation(value, self)
        if isinstance(value, duckdb.DuckDBPyConnection):
            # Defensive: no exposed method returns another connection, and
            # an untracked one must never escape.
            value.close()
            raise TypeError("an untracked DuckDB connection would escape the registry")
        return value

    def _wrapping(self, fn: Any) -> Any:
        def call(*args: Any, **kwargs: Any) -> Any:
            self._use()
            return self._wrap(fn(*args, **kwargs))
        call.__name__ = getattr(fn, "__name__", "call")
        return call

    # -- public surface -----------------------------------------------------
    @property
    def read_only(self) -> bool:
        return self._read_only

    @property
    def closed(self) -> bool:
        return self._closed

    def cursor(self) -> "HubConnection":
        """Another handle on the same instance, tracked like any other."""
        self._use()
        if self._closed:
            raise duckdb.ConnectionException("Connection already closed!")
        if self._entry is None:  # private in-memory database
            return HubConnection(None, self._cursor.cursor(), self._read_only)
        with _registry():
            return _hand_out(self._entry, self._read_only)

    duplicate = cursor

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(
            f"HubConnection.{name} is not exposed: market-data-hub connections "
            "allow only classified DuckDB methods (see _HUB_* in "
            "market_data_hub/db/connection.py)")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._cursor.close()
        finally:
            _release(self._entry)

    def __enter__(self) -> "HubConnection":
        self._use()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __del__(self) -> None:
        # A forgotten handle must not pin the file for the life of the
        # process: before the registry, garbage collection closed it. This
        # may run on a thread that holds the registry lock, so never wait for
        # it here: close the cursor, queue the release, drain if free.
        try:
            if self._closed:
                return
            self._closed = True
            try:
                self._cursor.close()
            finally:
                if self._entry is not None:
                    _PENDING_RELEASES.append(self._entry)
                    _drain_pending_releases()
        except Exception:
            pass

    def __repr__(self) -> str:
        mode = "read-only" if self._read_only else "read-write"
        state = "closed" if self._closed else "open"
        return f"<HubConnection {mode} {state}>"


for _name in _HUB_SQL_METHODS:
    setattr(HubConnection, _name, _sql_method(_name))
for _name in _HUB_PLAIN_METHODS:
    setattr(HubConnection, _name, _plain_method(_name))
for _name in _HUB_WRITE_METHODS:
    setattr(HubConnection, _name, _write_method(_name))
for _name in _HUB_PROPERTIES:
    setattr(HubConnection, _name, _result_property(_name))
del _name


# --- acquire / release ---------------------------------------------------------

def _release(entry: Optional[_SharedDatabase]) -> None:
    """Release one handle explicitly (never called with the lock held)."""
    if entry is None:
        return
    with _registry():
        root = _release_locked(entry)
    if root is not None:
        _finish_close(entry, root)


def _release_locked(entry: _SharedDatabase) -> Optional[duckdb.DuckDBPyConnection]:
    """Count one handle out (lock held); the root to close if it was the last."""
    entry.refs -= 1
    if entry.refs <= 0 and _REGISTRY.get(entry.key) is entry:
        # Keep the entry registered, marked busy, until the file is really
        # closed: a caller that opened a fresh instance in another mode
        # before root.close() returned would meet the very configuration
        # error this registry exists to prevent.
        entry.busy = True
        root = entry.root
        entry.root = None
        return root
    _REGISTRY_COND.notify_all()
    return None


def _finish_close(entry: _SharedDatabase, root: duckdb.DuckDBPyConnection) -> None:
    """Close the instance and withdraw its entry (lock not held)."""
    try:
        root.close()
    finally:
        with _registry():
            if _REGISTRY.get(entry.key) is entry:
                del _REGISTRY[entry.key]
            _REGISTRY_COND.notify_all()


def _open_root(path: str, *, read_only: bool, create: bool
               ) -> duckdb.DuckDBPyConnection:
    """Open the file for a new shared instance (no registry lock held).

    Read-write (only ever for :func:`get_conn` writers): create a missing
    file, wait out other processes, migrate, checkpoint. Read-only with
    ``create`` (get_conn readers): create a missing file with the schema,
    then wait out other processes. Read-only without ``create``
    (open_existing): the file must exist; one attempt.
    """
    if not read_only:
        assert create, "only get_conn writers open a read-write instance"
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        con = _connect_read_write_waiting(path)
        try:
            # migrate() also calls apply_schema() internally, then walks any
            # pending `if current < N:` ladder steps (e.g. ALTER TABLE ADD
            # COLUMN) that CREATE TABLE IF NOT EXISTS alone can't apply to a
            # table that already exists.
            migrate(con)
            # Flush the schema work before any cursor exists. Otherwise a
            # cursor that later updates rows and then alters a constraint
            # (SET NOT NULL) fails with "Cannot create index with
            # outstanding updates" while the root connection is alive.
            con.execute("CHECKPOINT")
        except BaseException:
            con.close()
            raise
        return con
    if not create:
        if not os.path.exists(path):
            raise FileNotFoundError(f"database file does not exist: {path}")
        return duckdb.connect(path, read_only=True)
    if not os.path.exists(path):
        # A reader on a nonexistent DB: create it once with the schema (the
        # behaviour get_conn(read_only=True) always had). Two processes can
        # see the file missing at the same instant, and the loser meets the
        # winner's lock -- the same contention as anywhere else, so it waits.
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp = _connect_read_write_waiting(path)
        try:
            apply_schema(tmp)
        finally:
            tmp.close()
    return _connect_read_only_waiting(path)


def _acquire(path: str, *, read_only: bool, create: bool, budget_s: float
             ) -> HubConnection:
    key = _registry_key(path)
    me = threading.get_ident()
    deadline = time.monotonic() + budget_s
    waiting = False  # counted in _WRITERS_WAITING
    with _registry() as cond:
        try:
            while True:
                entry = _REGISTRY.get(key)
                if entry is None:
                    if not read_only or not _WRITERS_WAITING.get(key):
                        entry = _SharedDatabase(key, read_only)
                        _REGISTRY[key] = entry
                        break  # we open it, outside the lock
                    # A reader never opens read-write: the queued writer
                    # opens (and migrates) the instance, then we join it.
                    reason = "a queued writer to open the file"
                elif entry.busy:
                    reason = "another thread opening or closing the file"
                else:
                    mine = entry.used_by(me)
                    if entry.read_only and not read_only:
                        if mine:
                            raise SharedDatabaseBusy(
                                "this thread uses a read-only market-data-hub "
                                "connection on the same file; close it before "
                                "opening a read-write one")
                        if not waiting:
                            _WRITERS_WAITING[key] = _WRITERS_WAITING.get(key, 0) + 1
                            waiting = True
                        reason = "read-only connections in this process to close"
                    elif entry.read_only and _WRITERS_WAITING.get(key) and not mine:
                        reason = "a queued writer"  # do not starve it
                    else:
                        return _hand_out(entry, read_only)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SharedDatabaseBusy(
                        f"timed out after {budget_s:g}s waiting for {reason} "
                        f"({os.path.basename(path)})")
                cond.wait(remaining)
        finally:
            if waiting:
                left = _WRITERS_WAITING[key] - 1
                if left:
                    _WRITERS_WAITING[key] = left
                else:
                    del _WRITERS_WAITING[key]
                # Readers may be queued behind this writer; whether it got a
                # handle, timed out or failed, they must re-check now.
                _REGISTRY_COND.notify_all()

    # We own a placeholder in the busy state: open the file without holding
    # the registry lock (opening may wait minutes for another process), then
    # publish it or withdraw the placeholder.
    try:
        root = _open_root(path, read_only=read_only, create=create)
    except BaseException:
        with _registry():
            if _REGISTRY.get(key) is entry:
                del _REGISTRY[key]
            _REGISTRY_COND.notify_all()
        raise
    with _registry():
        entry.root = root
        entry.busy = False
        try:
            return _hand_out(entry, read_only)
        finally:
            if entry.refs == 0:  # cursor() failed: do not leave it pinned
                del _REGISTRY[key]
                entry.root = None
                root.close()
            _REGISTRY_COND.notify_all()


def _hand_out(entry: _SharedDatabase, read_only: bool) -> HubConnection:
    """A new handle on an open entry (registry lock held)."""
    assert entry.root is not None and not entry.busy
    cursor = entry.root.cursor()
    entry.refs += 1
    handle = HubConnection(entry, cursor, read_only)
    entry.handles.add(handle)
    return handle


def get_conn(
    db_path: Optional[str] = None, *, read_only: bool = False
) -> HubConnection:
    """
    Open (creating if absent) the DuckDB database and ensure the schema.

    read_only=True for readers (reader.py, diagnose.py) so multiple processes
    can read in parallel without locking.

    Within one process every call for the same file shares one database
    instance and gets its own cursor, so readers and writers on different
    threads never trip DuckDB's "different configuration" error. Close the
    handle when done; it never closes the instance under another caller.
    """
    path = resolve_db_path(db_path)
    if path == ":memory:" or path.startswith(":memory:"):
        con = duckdb.connect(path)
        migrate(con)
        return HubConnection(None, con, read_only)
    budget = _READER_LOCK_WAIT_S if read_only else _WRITER_LOCK_WAIT_S
    return _acquire(path, read_only=read_only, create=True, budget_s=budget)


def open_existing(path: str) -> HubConnection:
    """Open an existing hub file read-only without creating or migrating it.

    For health checks and tools that must not create a missing database:
    raises ``FileNotFoundError`` when the file is absent and DuckDB's own
    error when another process holds it (one attempt, no waiting). Within
    this process it shares the instance like :func:`get_conn`, so it is safe
    next to concurrent writers; it waits at most ``_OPEN_EXISTING_WAIT_S``
    for another thread opening or closing the file, or for a queued writer,
    then raises :class:`SharedDatabaseBusy`.
    """
    return _acquire(str(path), read_only=True, create=False,
                    budget_s=_OPEN_EXISTING_WAIT_S)
