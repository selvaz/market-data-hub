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
    "prices_daily": ["date", "symbol"],
    "crypto_ohlcv": ["ts", "symbol", "timeframe"],
    "macro_series": ["date", "series_id"],
    "custom_series": ["date", "series_id"],
    "macro_panel": ["date", "country_iso3", "indicator_id"],
    "coverage_report": ["symbol", "source"],
    "factor_returns": ["date", "factor_set", "factor"],
    "macro_panel_coverage": ["indicator_id"],
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

    The count + INSERT OR REPLACE run inside an explicit transaction so a
    failure mid-write rolls back cleanly and never leaves a partial batch.
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

    col_list = ", ".join(cols)
    con.register("_upsert_src", out)
    con.execute("BEGIN TRANSACTION")
    try:
        updated = _count_existing(con, table, out)
        added = len(out) - updated
        con.execute(
            f"INSERT OR REPLACE INTO {table} ({col_list}) "
            f"SELECT {col_list} FROM _upsert_src"
        )
        con.execute("COMMIT")
    except Exception:
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
                   df: pd.DataFrame, vintage_date) -> int:
    """Append point-in-time rows to ``{table}_vintage`` for any key whose value
    is new or differs from the latest stored vintage (append-on-change).

    ``vintage_date`` is the date our ingest observed these values. Backtests can
    then query the value as-known on a past date (greatest vintage_date <= as-of),
    avoiding revision look-ahead. Returns the number of vintage rows written.
    Tables without a vintage history are silently ignored.
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
    n0 = con.execute(f"SELECT count(*) FROM {vt}").fetchone()[0]
    con.execute(
        f"INSERT OR REPLACE INTO {vt} ({key_list}, value, vintage_date, source) "
        f"WITH latest AS ("
        f"  SELECT v.* FROM {vt} v JOIN ("
        f"    SELECT {key_list}, max(vintage_date) AS md FROM {vt} GROUP BY {key_list}"
        f"  ) m ON {join_vm} AND v.vintage_date = m.md"
        f") "
        f"SELECT {sel_keys}, s.value, ?::DATE, s.source "
        f"FROM _vtsrc s LEFT JOIN latest l ON {join_sl} "
        f"WHERE l.{keys[0]} IS NULL OR l.value IS DISTINCT FROM s.value",
        [str(vintage_date)],
    )
    con.unregister("_vtsrc")
    n1 = con.execute(f"SELECT count(*) FROM {vt}").fetchone()[0]
    return n1 - n0


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
