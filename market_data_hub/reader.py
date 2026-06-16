# -*- coding: utf-8 -*-
"""
reader.py — public read API for the other projects.

Opens the DB in read-only mode (multiple processes can read in parallel).
The returned DataFrames have a structure compatible with the parquet/CSV files
used so far by the projects (date index, columns = symbols) to minimize changes.

Examples:
    from market_data_hub.reader import read_prices, read_macro, read_crypto
    px = read_prices(["SPY", "^VIX"], start="2020-01-01")          # wide adj_close
    vix = read_prices(["^VIX9D","^VIX","^VIX3M"], field="adj_close")
    macro = read_macro(["DGS10", "CPIAUCSL"])
    btc = read_crypto("BTCUSDT", "1h", start="2024-01-01")
"""
from __future__ import annotations

from typing import List, Optional, Union

import pandas as pd

from market_data_hub.db.connection import get_conn


def _con(db_path: Optional[str]):
    return get_conn(db_path, read_only=True)


def _asof_query(table: str, part_keys: List[str], filters: dict, asof: str,
                start: Optional[str], end: Optional[str]):
    """Build the (sql, params) for a point-in-time read of a *_vintage table:
    for each partition (part_keys) keep the row with the greatest
    vintage_date <= asof, then apply the date window. ``filters`` maps a column
    to a list of allowed values."""
    sel = ", ".join(part_keys + ["value"])
    clauses, params = ["vintage_date <= ?"], [asof]
    for col, vals in filters.items():
        clauses.append(f"{col} IN (" + ",".join(["?"] * len(vals)) + ")")
        params += list(vals)
    inner = (f"SELECT {sel}, row_number() OVER (PARTITION BY {', '.join(part_keys)} "
             f"ORDER BY vintage_date DESC) AS rn FROM {table} "
             f"WHERE {' AND '.join(clauses)}")
    outer = ["rn = 1"]
    if start:
        outer.append("date >= ?"); params.append(start)
    if end:
        outer.append("date <= ?"); params.append(end)
    return (f"SELECT {sel} FROM ({inner}) t WHERE {' AND '.join(outer)} "
            f"ORDER BY {', '.join(part_keys)}"), params


def _read_asof(con, table: str, id_col: str, ids: List[str],
               start: Optional[str], end: Optional[str], wide: bool,
               asof: str) -> pd.DataFrame:
    """Point-in-time macro_series read (one id column, pivoted by it when wide)."""
    q, params = _asof_query(table, ["date", id_col], {id_col: ids}, asof, start, end)
    df = con.execute(q, params).fetch_df()
    if not wide:
        return df
    if df.empty:
        return pd.DataFrame()
    out = df.pivot_table(index="date", columns=id_col, values="value", aggfunc="last")
    out.index = pd.to_datetime(out.index)
    return out.sort_index()


def read_prices(symbols: Union[str, List[str]], start: Optional[str] = None,
                end: Optional[str] = None, field: str = "adj_close",
                wide: bool = True, include_live: bool = False,
                db_path: Optional[str] = None) -> pd.DataFrame:
    """
    Daily prices. wide=True -> date index, symbol columns (field `field`).
    wide=False -> long format with all OHLCV columns.
    """
    if isinstance(symbols, str):
        symbols = [symbols]
    con = _con(db_path)
    try:
        clauses = ["symbol IN (" + ",".join(["?"] * len(symbols)) + ")"]
        params: list = list(symbols)
        if not include_live:
            clauses.append("is_live = FALSE")
        if start:
            clauses.append("date >= ?"); params.append(start)
        if end:
            clauses.append("date <= ?"); params.append(end)
        where = " AND ".join(clauses)
        if wide:
            df = con.execute(
                f"SELECT date, symbol, {field} AS v FROM prices_daily "
                f"WHERE {where} ORDER BY date", params).fetch_df()
            if df.empty:
                return pd.DataFrame()
            out = df.pivot_table(index="date", columns="symbol", values="v",
                                 aggfunc="last")
            out.index = pd.to_datetime(out.index)
            return out.sort_index()
        else:
            return con.execute(
                f"SELECT * FROM prices_daily WHERE {where} ORDER BY symbol, date",
                params).fetch_df()
    finally:
        con.close()


def read_macro(series_ids: Union[str, List[str]], start: Optional[str] = None,
               end: Optional[str] = None, wide: bool = True,
               asof: Optional[str] = None,
               db_path: Optional[str] = None) -> pd.DataFrame:
    """Macro series. wide=True -> date index, series_id columns.

    asof=<YYYY-MM-DD> -> point-in-time read from macro_series_vintage: the value
    as it was known on that date (greatest vintage_date <= asof), avoiding
    revision look-ahead in backtests. Without asof, the latest values are used.
    """
    if isinstance(series_ids, str):
        series_ids = [series_ids]
    con = _con(db_path)
    try:
        if asof:
            return _read_asof(
                con, "macro_series_vintage", "series_id", series_ids,
                start, end, wide, asof)
        clauses = ["series_id IN (" + ",".join(["?"] * len(series_ids)) + ")"]
        params: list = list(series_ids)
        if start:
            clauses.append("date >= ?"); params.append(start)
        if end:
            clauses.append("date <= ?"); params.append(end)
        where = " AND ".join(clauses)
        if wide:
            df = con.execute(
                f"SELECT date, series_id, value FROM macro_series "
                f"WHERE {where} ORDER BY date", params).fetch_df()
            if df.empty:
                return pd.DataFrame()
            out = df.pivot_table(index="date", columns="series_id",
                                 values="value", aggfunc="last")
            out.index = pd.to_datetime(out.index)
            return out.sort_index()
        return con.execute(
            f"SELECT * FROM macro_series WHERE {where} ORDER BY series_id, date",
            params).fetch_df()
    finally:
        con.close()


def read_crypto(symbols: Union[str, List[str]], timeframe: str = "1h",
                start: Optional[str] = None, end: Optional[str] = None,
                db_path: Optional[str] = None) -> pd.DataFrame:
    """Crypto OHLCV in long format for one or more symbols at a given timeframe."""
    if isinstance(symbols, str):
        symbols = [symbols]
    con = _con(db_path)
    try:
        clauses = ["symbol IN (" + ",".join(["?"] * len(symbols)) + ")",
                   "timeframe = ?"]
        params: list = list(symbols) + [timeframe]
        if start:
            clauses.append("ts >= ?"); params.append(start)
        if end:
            clauses.append("ts <= ?"); params.append(end)
        where = " AND ".join(clauses)
        return con.execute(
            f"SELECT * FROM crypto_ohlcv WHERE {where} ORDER BY symbol, ts",
            params).fetch_df()
    finally:
        con.close()


def read_macro_panel(indicators: Union[str, List[str]],
                     countries: Optional[Union[str, List[str]]] = None,
                     start: Optional[str] = None, end: Optional[str] = None,
                     wide: bool = False, asof: Optional[str] = None,
                     db_path: Optional[str] = None) -> pd.DataFrame:
    """
    Cross-country macro panel (World Bank / IMF / BIS).
    wide=False -> long format (date, country_iso3, indicator_id, value, ...).
    wide=True  -> date×country pivot for a SINGLE indicator.
    asof=<YYYY-MM-DD> -> point-in-time read from macro_panel_vintage (value as
    known on that date), avoiding revision look-ahead in backtests.
    """
    if isinstance(indicators, str):
        indicators = [indicators]
    if isinstance(countries, str):
        countries = [countries]
    con = _con(db_path)
    try:
        if asof:
            filters = {"indicator_id": indicators}
            if countries:
                filters["country_iso3"] = countries
            q, params = _asof_query(
                "macro_panel_vintage", ["date", "country_iso3", "indicator_id"],
                filters, asof, start, end)
            df = con.execute(q, params).fetch_df()
            if wide and not df.empty:
                if len(indicators) != 1:
                    raise ValueError("wide=True requires a single indicator")
                out = df.pivot_table(index="date", columns="country_iso3",
                                     values="value", aggfunc="last")
                out.index = pd.to_datetime(out.index)
                return out.sort_index()
            return df
        clauses = ["indicator_id IN (" + ",".join(["?"] * len(indicators)) + ")"]
        params: list = list(indicators)
        if countries:
            if isinstance(countries, str):
                countries = [countries]
            clauses.append("country_iso3 IN (" + ",".join(["?"] * len(countries)) + ")")
            params += list(countries)
        if start:
            clauses.append("date >= ?"); params.append(start)
        if end:
            clauses.append("date <= ?"); params.append(end)
        where = " AND ".join(clauses)
        df = con.execute(
            f"SELECT * FROM macro_panel WHERE {where} ORDER BY indicator_id, "
            f"country_iso3, date", params).fetch_df()
        if wide and not df.empty:
            if len(indicators) != 1:
                raise ValueError("wide=True requires a single indicator")
            out = df.pivot_table(index="date", columns="country_iso3",
                                 values="value", aggfunc="last")
            out.index = pd.to_datetime(out.index)
            return out.sort_index()
        return df
    finally:
        con.close()


def read_factors(factors: Optional[Union[str, List[str]]] = None,
                 factor_set: Optional[str] = None, start: Optional[str] = None,
                 end: Optional[str] = None, wide: bool = True,
                 db_path: Optional[str] = None) -> pd.DataFrame:
    """Fama-French / momentum factor returns (decimal).

    wide=True -> date index, factor columns (filter to one factor_set to avoid
    collapsing same-named factors across datasets). wide=False -> long format.
    """
    con = _con(db_path)
    try:
        clauses: list = []
        params: list = []
        if factor_set:
            clauses.append("factor_set = ?"); params.append(factor_set)
        if factors:
            if isinstance(factors, str):
                factors = [factors]
            clauses.append("factor IN (" + ",".join(["?"] * len(factors)) + ")")
            params += list(factors)
        if start:
            clauses.append("date >= ?"); params.append(start)
        if end:
            clauses.append("date <= ?"); params.append(end)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        if wide:
            df = con.execute(
                f"SELECT date, factor, value FROM factor_returns{where} "
                f"ORDER BY date", params).fetch_df()
            if df.empty:
                return pd.DataFrame()
            out = df.pivot_table(index="date", columns="factor", values="value",
                                 aggfunc="last")
            out.index = pd.to_datetime(out.index)
            return out.sort_index()
        return con.execute(
            f"SELECT * FROM factor_returns{where} "
            f"ORDER BY factor_set, factor, date", params).fetch_df()
    finally:
        con.close()


def get_coverage(symbols: Optional[List[str]] = None,
                 db_path: Optional[str] = None) -> pd.DataFrame:
    """coverage_report table (optionally filtered on a list of symbols)."""
    con = _con(db_path)
    try:
        if symbols:
            ph = ",".join(["?"] * len(symbols))
            return con.execute(
                f"SELECT * FROM coverage_report WHERE symbol IN ({ph}) "
                f"ORDER BY coverage_score", list(symbols)).fetch_df()
        return con.execute(
            "SELECT * FROM coverage_report ORDER BY coverage_score").fetch_df()
    finally:
        con.close()


def get_macro_panel_coverage(db_path: Optional[str] = None) -> pd.DataFrame:
    """Cross-country availability per macro_panel indicator (ordered worst first)."""
    con = _con(db_path)
    try:
        return con.execute(
            "SELECT * FROM macro_panel_coverage ORDER BY coverage_pct, last_date"
        ).fetch_df()
    finally:
        con.close()


def get_stalled(db_path: Optional[str] = None) -> pd.DataFrame:
    """Only the stalled symbols."""
    con = _con(db_path)
    try:
        return con.execute("SELECT * FROM v_stalled").fetch_df()
    finally:
        con.close()


def get_latest(symbol: str, db_path: Optional[str] = None) -> dict:
    """Latest data point + coverage metrics for a symbol."""
    con = _con(db_path)
    try:
        px = con.execute(
            "SELECT date, close, adj_close, volume FROM prices_daily "
            "WHERE symbol = ? ORDER BY date DESC LIMIT 1", [symbol]).fetch_df()
        cov = con.execute(
            "SELECT lag_days, coverage_score, stalled, freq_detected, status "
            "FROM coverage_report WHERE symbol = ? LIMIT 1", [symbol]).fetch_df()
        out: dict = {"symbol": symbol}
        if not px.empty:
            out.update(px.iloc[0].to_dict())
        if not cov.empty:
            out.update(cov.iloc[0].to_dict())
        return out
    finally:
        con.close()
