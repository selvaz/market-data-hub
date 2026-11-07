# -*- coding: utf-8 -*-
"""custom.py — public write API for app-published series (``custom_series``).

The hub's own connectors (yahoo/fred/binance/...) own their tables; this module
is the sanctioned way for *downstream apps* to expand the hub with series the
connectors do not provide — portfolio NAV histories, composite indicators,
series from providers without a connector. Rows land in the dedicated
``custom_series`` table (never in ``macro_series``), so a custom ``series_id``
can never collide with a curated FRED id.

Reading back goes through the same analysis surface as everything else::

    from market_data_hub.custom import store_series
    store_series("lazyfin:nav:pf-1", {"2026-01-02": 101_500.0}, source="lazyfin")

    from market_data_hub.extract import extract_series
    df, meta = extract_series(["lazyfin:nav:pf-1"], domain="custom")
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Iterable, Mapping, Optional, Tuple, Union

import pandas as pd

from market_data_hub.db.connection import get_conn
from market_data_hub.db.upsert import upsert

Observations = Union[
    Mapping[Union[str, date, datetime], float],
    Iterable[Tuple[Union[str, date, datetime], float]],
    "pd.Series",
]


def _to_date(value) -> date:
    """Normalize str/date/datetime/pandas timestamps to a plain ``date``."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        ts = pd.Timestamp(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"unparseable observation date: {value!r}") from exc
    if pd.isna(ts):
        raise ValueError(f"unparseable observation date: {value!r}")
    # pandas' incomplete type stubs make Timestamp.date() resolve to Any
    result: date = ts.date()
    return result


def _to_frame(series_id: str, observations: Observations) -> pd.DataFrame:
    if isinstance(observations, pd.Series):
        items = list(observations.items())
    elif isinstance(observations, Mapping):
        items = list(observations.items())
    else:
        items = list(observations)
    if not items:
        raise ValueError(f"store_series({series_id!r}): no observations given")
    rows = []
    for when, value in items:
        if value is None:
            raise ValueError(
                f"store_series({series_id!r}): observation at {when!r} is None; "
                "drop missing points before publishing"
            )
        rows.append({"date": _to_date(when), "series_id": series_id,
                     "value": float(value)})
    df = pd.DataFrame(rows)
    dup = df["date"].duplicated()
    if dup.any():
        dates = sorted({d.isoformat() for d in df.loc[dup, "date"]})
        raise ValueError(
            f"store_series({series_id!r}): duplicate observation dates {dates}"
        )
    return df


def store_series(series_id: str, observations: Observations, *,
                 series_name: Optional[str] = None, unit: Optional[str] = None,
                 frequency: Optional[str] = None, source: str = "custom",
                 db_path: Optional[str] = None) -> tuple[int, int]:
    """Upsert observations of one app-published series into ``custom_series``.

    Parameters
    ----------
    series_id    : publisher-chosen id (e.g. ``"lazyfin:nav:pf-1"``). Prefix it
                   with your app name to keep namespaces tidy.
    observations : mapping/iterable of ``(date, value)`` or a pandas Series
                   indexed by date. Values must be floats; ``None`` raises.
    series_name, unit, frequency, source : descriptive metadata stamped on
                   every row (``source`` identifies the publishing app).
    db_path      : explicit DuckDB path; ``None`` uses the hub's resolution.

    Returns ``(rows_added, rows_updated)``. Re-publishing the same dates
    replaces the stored values (INSERT OR REPLACE semantics, same as every
    hub connector).
    """
    if not series_id or not str(series_id).strip():
        raise ValueError("series_id must be a non-empty string")
    df = _to_frame(series_id, observations)
    df["series_name"] = series_name
    df["unit"] = unit
    df["frequency"] = frequency
    df["source"] = source
    con = get_conn(db_path)
    try:
        return upsert(con, "custom_series", df)
    finally:
        con.close()


def delete_series(series_id: str, *, db_path: Optional[str] = None) -> int:
    """Delete every observation of ``series_id``. Returns rows removed."""
    con = get_conn(db_path)
    try:
        row = con.execute(
            "SELECT count(*) FROM custom_series WHERE series_id = ?", [series_id]
        ).fetchone()
        assert row is not None   # COUNT(*) always returns exactly one row
        con.execute("DELETE FROM custom_series WHERE series_id = ?", [series_id])
        return int(row[0])
    finally:
        con.close()


def list_series(db_path: Optional[str] = None) -> pd.DataFrame:
    """Catalog of stored custom series: one row per series_id with metadata,
    observation count and date range."""
    con = get_conn(db_path, read_only=True)
    try:
        return con.execute(
            "SELECT series_id, any_value(series_name) AS series_name, "
            "       any_value(unit) AS unit, any_value(frequency) AS frequency, "
            "       any_value(source) AS source, count(*) AS obs_count, "
            "       min(date) AS first_date, max(date) AS last_date, "
            "       max(updated_at) AS updated_at "
            "FROM custom_series GROUP BY series_id ORDER BY series_id"
        ).fetch_df()
    finally:
        con.close()
