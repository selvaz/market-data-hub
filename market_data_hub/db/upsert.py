# -*- coding: utf-8 -*-
"""
upsert.py — atomic upsert DataFrame -> DuckDB via INSERT OR REPLACE.

All tables have a PRIMARY KEY, so INSERT OR REPLACE replaces conflicting rows
and inserts new ones. Returns (rows_added, rows_total).
"""
from __future__ import annotations

from datetime import datetime, timezone

import duckdb
import pandas as pd

# Expected columns per table (guaranteed order in the INSERT)
_COLUMNS = {
    "prices_daily": [
        "date", "listing_id", "symbol", "open", "high", "low", "close",
        "adj_close", "volume", "source", "is_live", "updated_at",
    ],
    "crypto_ohlcv": [
        "ts", "symbol", "timeframe", "open", "high", "low", "close",
        "volume", "volume_quote", "n_trades", "taker_buy_base",
        "is_closed", "updated_at",
    ],
    "macro_series": [
        "date", "series_id", "value", "series_name", "unit",
        "frequency", "source", "country", "updated_at",
    ],
    "custom_series": [
        "date", "series_id", "value", "series_name", "unit",
        "frequency", "source", "updated_at",
    ],
    "macro_panel": [
        "date", "country_iso3", "indicator_id", "value", "indicator_name",
        "pillar", "orientation", "source", "provider_dataset",
        "provider_code", "unit", "frequency", "updated_at",
    ],
    "coverage_report": [
        "symbol", "source", "asset_class", "first_date", "last_date",
        "obs_count", "freq_detected", "lag_days", "stalled", "gap_count",
        "missing_pct", "coverage_score", "has_zero_price", "has_negative",
        "status", "error_msg", "last_run_id", "updated_at",
    ],
    "factor_returns": [
        "date", "factor_set", "factor", "value", "frequency", "source",
        "updated_at",
    ],
    "macro_panel_coverage": [
        "indicator_id", "pillar", "source", "n_sources", "frequency",
        "freq_detected", "n_countries", "n_countries_total", "coverage_pct",
        "first_date", "last_date", "lag_days", "stalled", "obs_count",
        "status", "last_run_id", "updated_at",
    ],
}

_PK = {
    "prices_daily": ["date", "listing_id"],
    "crypto_ohlcv": ["ts", "symbol", "timeframe"],
    "macro_series": ["date", "series_id"],
    "custom_series": ["date", "series_id"],
    "macro_panel": ["date", "country_iso3", "indicator_id"],
    "coverage_report": ["symbol", "source"],
    "factor_returns": ["date", "factor_set", "factor"],
    "macro_panel_coverage": ["indicator_id"],
}


def _attach_listing_ids(con: duckdb.DuckDBPyConnection,
                        df: pd.DataFrame) -> pd.DataFrame:
    """Map prices_daily rows to their listing_id via the listings table
    (audit CA-01: the price series is keyed by listing, not by symbol).

    Symbols with no listing yet are auto-registered (the batch runner's
    config universe path); a symbol mapping to MORE than one active listing
    raises — dual listings must be written with an explicit listing_id
    (services.prices.ensure_price_history does), never guessed here.
    """
    from market_data_hub.db.identity import AmbiguousSymbolError, ensure_listing

    out = df.copy()
    symbols = sorted({str(s) for s in out["symbol"].dropna().unique()})
    rows = con.execute(
        f"SELECT symbol, listing_id FROM listings WHERE active_to IS NULL "
        f"AND symbol IN ({','.join('?' * len(symbols))})", symbols).fetchall()
    mapping: dict[str, str] = {}
    ambiguous = set()
    for sym, lid in rows:
        if sym in mapping:
            ambiguous.add(sym)
        mapping[sym] = lid
    if ambiguous:
        raise AmbiguousSymbolError(
            f"symbols map to multiple active listings, pass listing_id "
            f"explicitly: {sorted(ambiguous)}")
    for sym in symbols:
        if sym not in mapping:
            mapping[sym] = ensure_listing(con, sym)
    out["listing_id"] = out["symbol"].map(mapping)
    return out


def _count_existing(con: duckdb.DuckDBPyConnection, table: str,
                    df: pd.DataFrame) -> int:
    """How many rows of the df already exist (to estimate added vs updated)."""
    pk = _PK[table]
    con.register("_inc", df[pk])
    q = (f"SELECT COUNT(*) FROM {table} t "
         f"SEMI JOIN _inc i ON " + " AND ".join(f"t.{c} = i.{c}" for c in pk))
    row = con.execute(q).fetchone()
    assert row is not None   # COUNT(*) always returns exactly one row
    con.unregister("_inc")
    return int(row[0])


def upsert(con: duckdb.DuckDBPyConnection, table: str,
           df: pd.DataFrame, *, outer_txn: bool = False) -> tuple[int, int]:
    """
    Atomic upsert. Returns (rows_added, rows_updated).
    Columns missing in the df are filled with NULL; updated_at is set.

    The count + INSERT OR REPLACE run inside an explicit transaction so a
    failure mid-write rolls back cleanly and never leaves a partial batch.
    With ``outer_txn=True`` the CALLER owns the transaction (BEGIN/COMMIT/
    ROLLBACK) and this function only executes the statements — DuckDB does
    not nest transactions (audit CA-06: the ensure_* services wrap payload +
    ledger in one atomic commit).
    """
    if df is None or df.empty:
        return 0, 0
    if table not in _COLUMNS:
        raise ValueError(f"Table not handled by upsert(): {table}")

    cols = _COLUMNS[table]
    out = df.copy()

    if table == "prices_daily" and ("listing_id" not in out.columns
                                    or out["listing_id"].isna().any()):
        out = _attach_listing_ids(con, out)

    # Collapse intra-batch primary-key duplicates before counting: INSERT OR
    # REPLACE keeps the last conflicting row anyway, but len(out) would count
    # every duplicate as an extra "added" row, inflating the added/updated
    # estimate that download_log reports (e.g. a BIS batch with repeated
    # (date, country) keys showed a constant phantom rows_added every run).
    out = out.drop_duplicates(subset=_PK[table], keep="last")

    if "updated_at" in cols and "updated_at" not in out.columns:
        out["updated_at"] = datetime.now(timezone.utc)

    for c in cols:
        if c not in out.columns:
            out[c] = None
    out = out[cols]

    col_list = ", ".join(cols)
    con.register("_upsert_src", out)
    if not outer_txn:
        con.execute("BEGIN TRANSACTION")
    try:
        updated = _count_existing(con, table, out)
        added = len(out) - updated
        con.execute(
            f"INSERT OR REPLACE INTO {table} ({col_list}) "
            f"SELECT {col_list} FROM _upsert_src"
        )
        if not outer_txn:
            con.execute("COMMIT")
    except Exception:
        if not outer_txn:
            con.execute("ROLLBACK")
        raise
    finally:
        con.unregister("_upsert_src")
    return added, updated


# Revisable tables that carry a point-in-time vintage history:
#   table -> (key columns, vintage table)
_VINTAGE = {
    "macro_series": (["date", "series_id"], "macro_series_vintage"),
    "macro_panel": (["date", "country_iso3", "indicator_id"], "macro_panel_vintage"),
}


def record_vintage(con: duckdb.DuckDBPyConnection, table: str,
                   df: pd.DataFrame, vintage_date, *, run_id: str | None = None) -> int:
    """Append point-in-time rows to ``{table}_vintage`` for any key whose value
    is new or differs from the latest stored vintage (append-on-change).

    ``vintage_date`` is the date our ingest observed these values. Backtests can
    then query the value as-known on a past date (greatest vintage_date <= as-of),
    avoiding revision look-ahead. Returns the number of vintage rows written
    (including same-day replacements). Tables without a vintage history are
    silently ignored.

    Each written row also records ``run_id`` (which run observed it),
    ``change_type`` ('new' when the (date, key) combination had no prior
    vintage row at all, 'revised' when it did but with a different value) and
    ``prior_value`` (the value it replaced, for 'revised' rows).

    **Day-granularity semantics.** ``vintage_date`` is a DATE and part of the
    primary key, so one calendar day holds at most ONE vintage row per key:
    the day is the vintage unit, and a same-day re-observation with a
    different value REPLACES that day's row (INSERT OR REPLACE) rather than
    appending. The replacement *merges* rather than overwrites: it inherits
    the same-day predecessor's ``change_type`` and ``prior_value`` so the
    surviving row always describes the day as a whole relative to the
    previous day's knowledge -- "this date is new today, final value X" or
    "today this date went from <yesterday's value> to X" -- never the
    intermediate intraday step. What is intentionally NOT preserved:
    intermediate same-day values (below the day granularity by design;
    ``run_id`` reflects the last run that touched the row), and a day whose
    value ends back where it started leaves a row with value == prior_value.
    As-of reads are unaffected: greatest vintage_date <= asof always sees the
    end-of-day value.
    """
    if df is None or df.empty or table not in _VINTAGE:
        return 0
    keys, vt = _VINTAGE[table]

    src = df.copy()
    if "source" not in src.columns:
        src["source"] = None
    src = src[keys + ["value", "source"]].drop_duplicates(subset=keys, keep="last")

    con.register("_vtsrc", src)
    key_list = ", ".join(keys)
    join_sl = " AND ".join(f"s.{k} = l.{k}" for k in keys)
    join_vm = " AND ".join(f"v.{k} = m.{k}" for k in keys)
    sel_keys = ", ".join(f"s.{k}" for k in keys)
    inserted = con.execute(
        f"INSERT OR REPLACE INTO {vt} "
        f"({key_list}, value, vintage_date, source, run_id, change_type, prior_value) "
        f"WITH latest AS ("
        f"  SELECT v.* FROM {vt} v JOIN ("
        f"    SELECT {key_list}, max(vintage_date) AS md FROM {vt} GROUP BY {key_list}"
        f"  ) m ON {join_vm} AND v.vintage_date = m.md"
        f") "
        f"SELECT {sel_keys}, s.value, ?::DATE, s.source, ?, "
        f"       CASE WHEN l.{keys[0]} IS NULL THEN 'new' "
        f"            WHEN l.vintage_date = ?::DATE THEN l.change_type "
        f"            ELSE 'revised' END, "
        f"       CASE WHEN l.{keys[0]} IS NULL THEN NULL "
        f"            WHEN l.vintage_date = ?::DATE THEN l.prior_value "
        f"            ELSE l.value END "
        f"FROM _vtsrc s LEFT JOIN latest l ON {join_sl} "
        f"WHERE l.{keys[0]} IS NULL OR l.value IS DISTINCT FROM s.value",
        [str(vintage_date), run_id, str(vintage_date), str(vintage_date)],
    ).fetchone()
    con.unregister("_vtsrc")
    assert inserted is not None   # INSERT always reports a row count
    return int(inserted[0])


def log_run(con: duckdb.DuckDBPyConnection, *, run_id: str, started_at: datetime,
            source: str, symbol: str, rows_added: int, rows_updated: int,
            status: str, error_msg: str | None, duration_sec: float) -> None:
    """Insert a row into download_log."""
    con.execute(
        "INSERT INTO download_log "
        "(run_id, started_at, ended_at, source, symbol, rows_added, "
        " rows_updated, status, error_msg, duration_sec) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [run_id, started_at, datetime.now(timezone.utc), source, symbol,
         int(rows_added), int(rows_updated), status, error_msg,
         float(duration_sec)],
    )
