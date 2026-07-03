# -*- coding: utf-8 -*-
"""
report.py — builds/updates the coverage_report table by reading the DB.

For each (symbol, source) it computes: first/last date, obs, detected frequency,
lag_days, stalled flag, gap_count, missing_pct, coverage_score and quality flags.
Called at the end of each daily run.
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
    """Map symbol/series_id -> {asset_class, priority}."""
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
    """Recompute coverage_report for all series in the DB. Returns the row count."""
    meta = _meta_lookup()
    rows = []

    # --- prices_daily (yahoo) ---
    # Exclude intraday live rows (is_live = TRUE): a stale EOD series with a
    # fresh same-day live row must still register as stalled. Coverage measures
    # the settled EOD history only.
    pdf = con.execute(
        "SELECT date, symbol, source, open, high, low, close, adj_close "
        "FROM prices_daily WHERE is_live = FALSE ORDER BY symbol, date"
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

    # --- crypto_ohlcv (binance) — key symbol:timeframe ---
    cdf = con.execute(
        "SELECT ts AS date, symbol, timeframe, open, high, low, close "
        "FROM crypto_ohlcv ORDER BY symbol, timeframe, ts"
    ).fetch_df()
    if not cdf.empty:
        for (symbol, tf), g in cdf.groupby(["symbol", "timeframe"]):
            key = f"{symbol}:{tf}"
            rows.append(_row_for(key, "binance", g, "date",
                                 {"asset_class": "CRYPTO", "priority": 1}, run_id))

    # macro_panel is scored separately by rebuild_macro_panel_coverage() — it is a
    # (date, country, indicator) panel, not a per-symbol series, so it does not
    # belong in coverage_report.

    if not rows:
        return 0

    cov = pd.DataFrame(rows)
    # Full rebuild: without the DELETE, a symbol removed from the universe (or
    # whose rows were pruned) keeps its last coverage row forever, permanently
    # inflating the stalled-series alert. Same policy as macro_panel_coverage.
    con.execute("DELETE FROM coverage_report")
    upsert(con, "coverage_report", cov)
    return len(cov)


def rebuild_macro_panel_coverage(con: duckdb.DuckDBPyConnection, run_id: str,
                                 n_countries_total: int) -> int:
    """Score cross-country availability of the macro_panel, one row per indicator.

    Reuses the same coverage engine (frequency detection, lag/stalled, date span)
    but on the panel's natural grain: how many of the expected countries carry the
    indicator, the freshest date, and a freq-aware stalled flag.
    """
    df = con.execute(
        "SELECT date, country_iso3, indicator_id, pillar, source, frequency "
        "FROM macro_panel WHERE value IS NOT NULL"
    ).fetch_df()
    if df.empty:
        return 0

    now = datetime.now(timezone.utc)
    rows = []
    for iid, g in df.groupby("indicator_id"):
        first, last, obs = date_span(g["date"])
        # declared frequency (config) and detected frequency on the densest country
        declared = g["frequency"].mode().iloc[0] if g["frequency"].notna().any() else None
        densest = g.groupby("country_iso3").size().idxmax()
        freq_detected = detect_frequency(g.loc[g["country_iso3"] == densest, "date"])
        n_countries = int(g["country_iso3"].nunique())
        stalled = is_stalled(last, declared or freq_detected)
        status = "stalled" if stalled else "ok"
        rows.append({
            "indicator_id": iid,
            "pillar": g["pillar"].iloc[0],
            "source": ",".join(sorted(s for s in g["source"].dropna().unique())),
            "n_sources": int(g["source"].nunique()),
            "frequency": declared,
            "freq_detected": freq_detected,
            "n_countries": n_countries,
            "n_countries_total": int(n_countries_total),
            "coverage_pct": round(100.0 * n_countries / n_countries_total, 1)
            if n_countries_total else None,
            "first_date": first,
            "last_date": last,
            "lag_days": lag_days(last),
            "stalled": stalled,
            "obs_count": int(obs),
            "status": status,
            "last_run_id": run_id,
            "updated_at": now,
        })

    con.execute("DELETE FROM macro_panel_coverage")   # full rebuild each run
    upsert(con, "macro_panel_coverage", pd.DataFrame(rows))
    return len(rows)
