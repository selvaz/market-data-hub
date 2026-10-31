"""Optional read-only observations for catalog_docs; never imported by --check."""
from __future__ import annotations

from calendar import monthrange
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import duckdb


def period_end(value, frequency):
    """Normalize period-start labels before calculating observed calendar lag."""
    text = str(value)
    if "Q" in text[:7]:
        year = int(text[:4])
        month = int(text.replace("-", "")[5]) * 3
        return datetime(year, month, monthrange(year, month)[1], tzinfo=timezone.utc)
    if len(text) == 4:
        text += "-12-31"
    elif len(text) == 7:
        text += "-01"
    result = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    if frequency in ("M", "Q", "A"):
        month = result.month if frequency == "M" else ((result.month - 1) // 3 + 1) * 3 if frequency == "Q" else 12
        return result.replace(month=month, day=monthrange(result.year, month)[1])
    if frequency in ("1h", "4h", "1d"):
        return result + timedelta(hours={"1h": 1, "4h": 4, "1d": 24}[frequency])
    return result


def _lag(reference, available, frequency):
    if reference is None or available is None:
        return None
    try:
        stamp = datetime.fromisoformat(str(available).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return round((stamp - period_end(reference, frequency)).total_seconds() / 86400, 4)
    except (ValueError, TypeError, IndexError):
        return None


def _columns(conn, table):
    if not isinstance(conn, sqlite3.Connection) and not conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name=?", [table]
    ).fetchone():
        return set()
    return {r[1] for r in conn.execute(f"PRAGMA table_info('{table}')").fetchall()}


def _observe_hub(conn, catalog):
    # Only generated, schema-validated identifiers enter these SQL templates.
    specs = {
        "prices_daily": ("symbol", "date"),
        "macro_series": ("series_id", "date"),
        "macro_panel": ("indicator_id", "date"),
        "alfred_vintage_observations": ("series_id", "date"),
        "calendar_indicators": ("indicator_key", None),
        "crypto_ohlcv": ("symbol || '/' || timeframe", "ts"),
        "factor_returns": ("factor_set || '/' || factor", "date"),
        "cftc_tff_positioning": ("'tff'", "report_date"),
        "cftc_legacy_positioning": ("'legacy'", "report_date"),
        "treasury_cash_balance": ("'cash'", "record_date"),
        "treasury_debt_outstanding": ("'debt'", "record_date"),
        "treasury_auctions": ("'auctions'", "record_date"),
    }
    for table, (identity, date_column) in specs.items():
        cols = _columns(conn, table)
        selected = [r for r in catalog["series"] if r["store"] == "hub" and r["table"] == table]
        if not cols or date_column is None or date_column not in cols:
            continue
        # Current-state tables cannot prove first availability. Deliberately do
        # not treat updated_at (a mutable refresh timestamp) as an observed lag.
        values = conn.execute(
            f"SELECT {identity}, MIN({date_column}), MAX({date_column}) FROM {table} GROUP BY 1"
        ).fetchall()
        by_id = {r[0]: r[1:] for r in values}
        for row in selected:
            bounds = by_id.get(row["id"])
            row["observed"] = dict(first_date=str(bounds[0]) if bounds else None,
                                   last_date=str(bounds[1]) if bounds else None,
                                   lag_days=None, lag_basis="No first-availability timestamp in current-state table")
    for table, parent, keys in (("macro_series_vintage", "macro_series", ["series_id"]),
                                ("macro_panel_vintage", "macro_panel", ["indicator_id", "country_iso3"])):
        if not _columns(conn, table):
            continue
        identity = keys[0]
        values = conn.execute(
            f"SELECT {identity}, date, MIN(vintage_date) FROM {table} "
            f"WHERE value IS NOT NULL GROUP BY {identity}, date "
            f"QUALIFY ROW_NUMBER() OVER (PARTITION BY {identity} ORDER BY date DESC)=1"
        ).fetchall()
        by_id = {r[0]: r[1:] for r in values}
        for row in catalog["series"]:
            if row["store"] == "hub" and row["table"] == parent and row["id"] in by_id and row.get("observed"):
                reference, available = by_id[row["id"]]
                if str(reference) != row["observed"]["last_date"]:
                    continue
                row["observed"].update(lag_days=_lag(reference, available, row["frequency"]),
                                       lag_basis="Earliest stored hub vintage_date for latest period; date precision, may reflect backfill; panel pools countries")
    # ALFRED preserves vendor availability. Measure the earliest vendor vintage
    # of the latest period, never the last revision date or latest updated_at.
    if _columns(conn, "alfred_vintage_observations"):
        values = conn.execute(
            "SELECT series_id, date, MIN(as_of) FROM alfred_vintage_observations "
            "WHERE value IS NOT NULL GROUP BY series_id, date "
            "QUALIFY ROW_NUMBER() OVER (PARTITION BY series_id ORDER BY date DESC)=1"
        ).fetchall()
        by_id = {r[0]: r[1:] for r in values}
        for row in catalog["series"]:
            if row["table"] == "alfred_vintage_observations" and row["id"] in by_id:
                reference, available = by_id[row["id"]]
                row["observed"].update(lag_days=_lag(reference, available, row["frequency"]),
                                       lag_basis="Earliest stored vendor as_of for latest period; not hub first-seen")


def _observe_macro(conn, catalog):
    cols = _columns(conn, "value_vintages")
    if not {"indicator_id", "reference_period", "first_seen_at"} <= cols:
        raise ValueError("SQLite input is missing macro release vintage columns")
    values = conn.execute(
        "SELECT indicator_id, MIN(reference_period), MAX(reference_period) "
        "FROM value_vintages GROUP BY indicator_id"
    ).fetchall()
    bounds = {r[0]: r[1:] for r in values}
    # First stored numeric availability for the newest reference period. This
    # includes all evidence classes, so it makes no official-certification claim.
    availability = dict(conn.execute(
        "SELECT v.indicator_id, MIN(v.first_seen_at) FROM value_vintages v "
        "JOIN (SELECT indicator_id, MAX(reference_period) p FROM value_vintages GROUP BY indicator_id) n "
        "ON n.indicator_id=v.indicator_id AND n.p=v.reference_period GROUP BY v.indicator_id"
    ).fetchall())
    for row in catalog["series"]:
        if row["store"] != "macro_releases.sqlite":
            continue
        first, last = bounds.get(row["id"], (None, None))
        row["observed"] = dict(first_date=first, last_date=last,
                               lag_days=_lag(last, availability.get(row["id"]), row["frequency"]),
                               lag_basis="First hub numeric first_seen_at for latest reference period; all evidence classes, may reflect backfill")


def observe(catalog, paths):
    """Enrich a generated copy; explicit existing files only, no create/migrate."""
    seen = set()
    catalog["live_sources"] = []
    for path in paths:
        path = Path(path).resolve(strict=True)
        with path.open("rb") as handle:
            sqlite = handle.read(16) == b"SQLite format 3\x00"
        store = "macro_releases.sqlite" if sqlite else "hub"
        if store in seen:
            raise ValueError(f"Supply only one database per store ({store})")
        seen.add(store)
        if sqlite:
            conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
            with closing(conn):
                conn.execute("PRAGMA query_only=ON")
                _observe_macro(conn, catalog)
        else:
            with closing(duckdb.connect(str(path), read_only=True)) as conn:
                _observe_hub(conn, catalog)
        catalog["live_sources"].append(dict(store=store, path=str(path)))
