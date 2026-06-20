# -*- coding: utf-8 -*-
"""
retention.py — data retention / pruning for the DuckDB store.

The store grows fastest in three places: the per-run ``download_log`` audit
trail, intraday ``crypto_ohlcv`` candles, and the point-in-time vintage history
tables. ``prune()`` trims each target independently; every target is opt-in
(None = skip) and explicit arguments are authoritative over settings.yaml.

All deletes run inside a single transaction so a failure rolls back cleanly and
never leaves a half-pruned DB. ``dry_run=True`` runs the equivalent COUNT
queries and deletes nothing, returning what *would* be removed.
"""
from __future__ import annotations

from typing import Optional

import duckdb

# Vintage tables and the logical-key columns that identify one revisable series.
# We keep the most recent N distinct vintage_date rows per key.
_VINTAGE_KEYS = {
    "macro_series_vintage": ["date", "series_id"],
    "macro_panel_vintage": ["date", "country_iso3", "indicator_id"],
}


def _vintage_excess_filter(table: str, keys: list[str], keep: int) -> str:
    """Build the WHERE clause selecting vintage rows BEYOND the newest `keep`
    per logical key (ranked by vintage_date descending)."""
    key_list = ", ".join(keys)
    return (
        f"rowid IN (SELECT rowid FROM ("
        f"  SELECT rowid, row_number() OVER ("
        f"    PARTITION BY {key_list} ORDER BY vintage_date DESC"
        f"  ) AS rn FROM {table}"
        f") WHERE rn > {keep})"
    )


def prune(
    con: duckdb.DuckDBPyConnection,
    *,
    download_log_days: Optional[int] = 90,
    crypto_days: Optional[int] = None,
    vintage_keep_per_key: Optional[int] = None,
    dry_run: bool = False,
    db_path: Optional[str] = None,  # accepted for symmetry/callers; unused here
) -> dict:
    """Prune retained data and return ``{target: rows_deleted_or_would_delete}``.

    Parameters (each ``None`` skips that target):
      download_log_days    — delete download_log rows older than N days (started_at).
      crypto_days          — delete crypto_ohlcv rows older than N days (ts).
      vintage_keep_per_key — keep only the newest N vintage_date rows per logical
                             key in macro_series_vintage and macro_panel_vintage.
      dry_run              — COUNT only, delete nothing.
    """
    counts: dict[str, int] = {}

    def _count(table: str, where: str) -> int:
        return int(
            con.execute(f"SELECT count(*) FROM {table} WHERE {where}").fetchone()[0]
        )

    # Build (target, table, where) work items.
    items: list[tuple[str, str, str]] = []
    if download_log_days is not None:
        items.append((
            "download_log",
            "download_log",
            f"started_at < now() - INTERVAL '{int(download_log_days)} days'",
        ))
    if crypto_days is not None:
        items.append((
            "crypto_ohlcv",
            "crypto_ohlcv",
            f"ts < now() - INTERVAL '{int(crypto_days)} days'",
        ))
    if vintage_keep_per_key is not None:
        keep = int(vintage_keep_per_key)
        for table, keys in _VINTAGE_KEYS.items():
            items.append((table, table, _vintage_excess_filter(table, keys, keep)))

    if dry_run:
        for target, table, where in items:
            counts[target] = _count(table, where)
        return counts

    con.execute("BEGIN TRANSACTION")
    try:
        for target, table, where in items:
            n = _count(table, where)
            con.execute(f"DELETE FROM {table} WHERE {where}")
            counts[target] = n
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return counts
