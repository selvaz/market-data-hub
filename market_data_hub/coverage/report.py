# -*- coding: utf-8 -*-
"""
report.py — costruisce/aggiorna la tabella coverage_report leggendo il DB.

Per ogni (symbol, source) calcola: first/last date, obs, frequenza rilevata,
lag_days, flag stalled, gap_count, missing_pct, coverage_score e flag di qualita'.
Chiamato a fine di ogni run giornaliero.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict

import duckdb
import pandas as pd

from market_data_hub.config_loader import get_yahoo_tickers, get_fred_series
from market_data_hub.coverage.freq_detector import detect_frequency
from market_data_hub.coverage.stalled_detector import lag_days, is_stalled
from market_data_hub.coverage.gap_detector import missing_pct, gap_count, date_span
from market_data_hub.coverage.quality_checks import check_prices
from market_data_hub.coverage.score import coverage_score
from market_data_hub.db.upsert import upsert


def _meta_lookup() -> Dict[str, dict]:
    """Mappa symbol/series_id -> {asset_class, priority}."""
    m: Dict[str, dict] = {}
    for e in get_yahoo_tickers():
        m[e["symbol"]] = {"asset_class": e.get("asset_class", ""),
                          "priority": e.get("priority", 3)}
    for e in get_fred_series():
        m[e["symbol"]] = {"asset_class": e.get("asset_class", "MACRO"),
                          "priority": e.get("priority", 2)}
    return m


def _row_for(symbol: str, source: str, df: pd.DataFrame, date_col: str,
             meta: dict, run_id: str) -> dict:
    first, last, obs = date_span(df[date_col])
    freq = detect_frequency(df[date_col]) if obs >= 3 else "UNKNOWN"
    lag = lag_days(last)
    stalled = is_stalled(last, freq)
    mpct = missing_pct(df[date_col], freq)
    gaps = gap_count(df[date_col], freq)
    flags = check_prices(df)
    score = coverage_score(obs, mpct, lag, meta.get("priority", 3), freq)

    status = "ok"
    if obs == 0:
        status = "empty"
    elif stalled:
        status = "stalled"

    return {
        "symbol": symbol,
        "source": source,
        "asset_class": meta.get("asset_class", ""),
        "first_date": first,
        "last_date": last,
        "obs_count": obs,
        "freq_detected": freq,
        "lag_days": lag,
        "stalled": stalled,
        "gap_count": gaps,
        "missing_pct": round(mpct, 4),
        "coverage_score": score,
        "has_zero_price": flags.has_zero_price,
        "has_negative": flags.has_negative,
        "status": status,
        "error_msg": None,
        "last_run_id": run_id,
        "updated_at": datetime.now(timezone.utc),
    }


def rebuild_coverage(con: duckdb.DuckDBPyConnection, run_id: str) -> int:
    """Ricalcola coverage_report per tutte le serie nel DB. Ritorna n. righe."""
    meta = _meta_lookup()
    rows = []

    # --- prices_daily (yahoo) ---
    pdf = con.execute(
        "SELECT date, symbol, source, open, high, low, close, adj_close "
        "FROM prices_daily ORDER BY symbol, date"
    ).fetch_df()
    if not pdf.empty:
        for symbol, g in pdf.groupby("symbol"):
            src = g["source"].iloc[0] if "source" in g else "yahoo"
            rows.append(_row_for(symbol, src or "yahoo", g, "date",
                                 meta.get(symbol, {}), run_id))

    # --- macro_series (fred) ---
    mdf = con.execute(
        "SELECT date, series_id, value FROM macro_series ORDER BY series_id, date"
    ).fetch_df()
    if not mdf.empty:
        for sid, g in mdf.groupby("series_id"):
            rows.append(_row_for(sid, "fred", g, "date",
                                 meta.get(sid, {"asset_class": "MACRO",
                                                "priority": 2}), run_id))

    # --- crypto_ohlcv (binance) — chiave symbol:timeframe ---
    cdf = con.execute(
        "SELECT ts AS date, symbol, timeframe, open, high, low, close "
        "FROM crypto_ohlcv ORDER BY symbol, timeframe, ts"
    ).fetch_df()
    if not cdf.empty:
        for (symbol, tf), g in cdf.groupby(["symbol", "timeframe"]):
            key = f"{symbol}:{tf}"
            rows.append(_row_for(key, "binance", g, "date",
                                 {"asset_class": "CRYPTO", "priority": 1}, run_id))

    # --- macro_panel — un record per indicatore (date aggregate sui paesi) ---
    mpdf = con.execute(
        "SELECT date, indicator_id, pillar, value FROM macro_panel "
        "ORDER BY indicator_id, date"
    ).fetch_df()
    if not mpdf.empty:
        for iid, g in mpdf.groupby("indicator_id"):
            pillar = g["pillar"].iloc[0] if "pillar" in g else ""
            rows.append(_row_for(iid, "macro_panel", g, "date",
                                 {"asset_class": pillar, "priority": 2}, run_id))

    if not rows:
        return 0

    cov = pd.DataFrame(rows)
    upsert(con, "coverage_report", cov)
    return len(cov)
