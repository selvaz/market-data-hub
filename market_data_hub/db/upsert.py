# -*- coding: utf-8 -*-
"""
upsert.py — atomic upsert DataFrame -> DuckDB via INSERT OR REPLACE.

All tables have a PRIMARY KEY, so INSERT OR REPLACE replaces conflicting rows
and inserts new ones. Returns (rows_added, rows_total).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List

import duckdb
import pandas as pd

# Expected columns per table (guaranteed order in the INSERT)
_COLUMNS = {
    "prices_daily": [
        "date", "symbol", "open", "high", "low", "close",
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
}

_PK = {
    "prices_daily": ["date", "symbol"],
    "crypto_ohlcv": ["ts", "symbol", "timeframe"],
    "macro_series": ["date", "series_id"],
    "macro_panel": ["date", "country_iso3", "indicator_id"],
    "coverage_report": ["symbol", "source"],
}


def _count_existing(con: duckdb.DuckDBPyConnection, table: str,
                    df: pd.DataFrame) -> int:
    """How many rows of the df already exist (to estimate added vs updated)."""
    pk = _PK[table]
    con.register("_inc", df[pk])
    q = (f"SELECT COUNT(*) FROM {table} t "
         f"SEMI JOIN _inc i ON " + " AND ".join(f"t.{c} = i.{c}" for c in pk))
    n = con.execute(q).fetchone()[0]
    con.unregister("_inc")
    return int(n)


def upsert(con: duckdb.DuckDBPyConnection, table: str,
           df: pd.DataFrame) -> tuple[int, int]:
    """
    Atomic upsert. Returns (rows_added, rows_updated).
    Columns missing in the df are filled with NULL; updated_at is set.
    """
    if df is None or df.empty:
        return 0, 0
    if table not in _COLUMNS:
        raise ValueError(f"Table not handled by upsert(): {table}")

    cols = _COLUMNS[table]
    out = df.copy()

    if "updated_at" in cols and "updated_at" not in out.columns:
        out["updated_at"] = datetime.now(timezone.utc)

    for c in cols:
        if c not in out.columns:
            out[c] = None
    out = out[cols]

    updated = _count_existing(con, table, out)
    added = len(out) - updated

    con.register("_upsert_src", out)
    col_list = ", ".join(cols)
    con.execute(
        f"INSERT OR REPLACE INTO {table} ({col_list}) "
        f"SELECT {col_list} FROM _upsert_src"
    )
    con.unregister("_upsert_src")
    return added, updated


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
