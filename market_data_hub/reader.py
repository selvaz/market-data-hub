# -*- coding: utf-8 -*-
"""
reader.py — API pubblica di lettura per gli altri progetti.

Apre il DB in sola lettura (piu' processi possono leggere in parallelo).
I DataFrame restituiti hanno una struttura compatibile con i parquet/CSV usati
finora dai progetti (indice data, colonne = simboli) per minimizzare le modifiche.

Esempi:
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


def read_prices(symbols: Union[str, List[str]], start: Optional[str] = None,
                end: Optional[str] = None, field: str = "adj_close",
                wide: bool = True, include_live: bool = False,
                db_path: Optional[str] = None) -> pd.DataFrame:
    """
    Prezzi giornalieri. wide=True -> indice date, colonne simboli (campo `field`).
    wide=False -> formato lungo con tutte le colonne OHLCV.
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
               db_path: Optional[str] = None) -> pd.DataFrame:
    """Serie macro. wide=True -> indice date, colonne series_id."""
    if isinstance(series_ids, str):
        series_ids = [series_ids]
    con = _con(db_path)
    try:
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
    """OHLCV crypto in formato lungo per uno o piu' simboli a un dato timeframe."""
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
                     wide: bool = False, db_path: Optional[str] = None) -> pd.DataFrame:
    """
    Panel macro cross-country (World Bank / IMF).
    wide=False -> formato lungo (date, country_iso3, indicator_id, value, ...).
    wide=True  -> pivot date×country per UN solo indicatore.
    """
    if isinstance(indicators, str):
        indicators = [indicators]
    con = _con(db_path)
    try:
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
                raise ValueError("wide=True richiede un solo indicatore")
            out = df.pivot_table(index="date", columns="country_iso3",
                                 values="value", aggfunc="last")
            out.index = pd.to_datetime(out.index)
            return out.sort_index()
        return df
    finally:
        con.close()


def get_coverage(symbols: Optional[List[str]] = None,
                 db_path: Optional[str] = None) -> pd.DataFrame:
    """Tabella coverage_report (opzionalmente filtrata su una lista di simboli)."""
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


def get_stalled(db_path: Optional[str] = None) -> pd.DataFrame:
    """Solo i simboli fermi."""
    con = _con(db_path)
    try:
        return con.execute("SELECT * FROM v_stalled").fetch_df()
    finally:
        con.close()


def get_latest(symbol: str, db_path: Optional[str] = None) -> dict:
    """Ultimo dato + metriche coverage per un simbolo."""
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
