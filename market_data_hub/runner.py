# -*- coding: utf-8 -*-
"""
runner.py — orchestrator of the incremental daily download.

Modes:
  full      : Yahoo + FRED + Binance (default EOD)
  live-only : intraday live price injection only (liquid assets)
  sources   : subset of {yahoo, fred, binance}

Flow for each source:
  1. read last_date from the DB for each symbol
  2. download only the missing data (with tail refresh for revisions)
  3. atomic upsert + row in download_log
  4. at the end of the run, rebuild coverage_report
"""
from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

import pandas as pd

from market_data_hub.config_loader import (
    get_settings, get_yahoo_tickers, get_fred_series,
    get_countries, get_macro_panel_specs)
from market_data_hub.db.connection import get_conn
from market_data_hub.db.upsert import upsert, log_run, record_vintage
from market_data_hub.coverage.report import rebuild_coverage
from market_data_hub.sources import yahoo as yh
from market_data_hub.sources import fred as fr
from market_data_hub.sources import binance as bn
from market_data_hub.sources import macro_panel as mp


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------- last dates
def _last_prices(con) -> Dict[str, pd.Timestamp]:
    df = con.execute(
        "SELECT symbol, max(date) AS d FROM prices_daily "
        "WHERE is_live = FALSE GROUP BY symbol").fetch_df()
    return {r.symbol: pd.Timestamp(r.d) for r in df.itertuples() if pd.notna(r.d)}


def _last_macro(con) -> Dict[str, pd.Timestamp]:
    df = con.execute(
        "SELECT series_id, max(date) AS d FROM macro_series GROUP BY series_id"
    ).fetch_df()
    return {r.series_id: pd.Timestamp(r.d) for r in df.itertuples() if pd.notna(r.d)}


def _last_crypto(con) -> Dict[tuple, pd.Timestamp]:
    df = con.execute(
        "SELECT symbol, timeframe, max(ts) AS d FROM crypto_ohlcv "
        "WHERE is_closed = TRUE GROUP BY symbol, timeframe").fetch_df()
    return {(r.symbol, r.timeframe): pd.Timestamp(r.d)
            for r in df.itertuples() if pd.notna(r.d)}


# --------------------------------------------------------------- YAHOO
def run_yahoo(con, cfg: dict, run_id: str, *, start_override: Optional[str] = None,
              end: Optional[str] = None) -> None:
    end = end or _today()
    tickers = get_yahoo_tickers()
    tail = cfg["incremental"]["tail_refresh_days"]
    gstart = cfg["backfill_start"]["yahoo"]
    last = _last_prices(con)

    # group by effective_start (efficient batching)
    groups: Dict[str, List[str]] = {}
    for e in tickers:
        sym = e["symbol"]
        s = start_override or yh.effective_start(last.get(sym), gstart, tail)
        groups.setdefault(s, []).append(sym)

    _log(f"YAHOO: {len(tickers)} symbols in {len(groups)} groups (end={end})")
    sleep = cfg["parallelism"]["yahoo_batch_sleep"]

    for gstart_k, syms in sorted(groups.items()):
        t0 = time.time()
        try:
            batch = yh.yahoo_batch(syms, gstart_k, end)
        except Exception as ex:
            _log(f"  ! batch start={gstart_k} failed: {ex}")
            for s in syms:
                log_run(con, run_id=run_id, started_at=datetime.now(timezone.utc),
                        source="yahoo", symbol=s, rows_added=0, rows_updated=0,
                        status="error", error_msg=str(ex), duration_sec=0)
            continue

        for sym, df in batch.items():
            st = datetime.now(timezone.utc)
            if df is None or df.empty:
                log_run(con, run_id=run_id, started_at=st, source="yahoo",
                        symbol=sym, rows_added=0, rows_updated=0,
                        status="empty", error_msg=None, duration_sec=0)
                continue
            df = df.copy()
            df["source"] = "yahoo"
            df["is_live"] = False
            added, updated = upsert(con, "prices_daily", df)
            log_run(con, run_id=run_id, started_at=st, source="yahoo",
                    symbol=sym, rows_added=added, rows_updated=updated,
                    status="ok", error_msg=None, duration_sec=0)
        _log(f"  group start={gstart_k} n={len(syms)} ok ({time.time()-t0:.1f}s)")
        time.sleep(sleep)


# --------------------------------------------------------------- FRED
def run_fred(con, cfg: dict, run_id: str, *, start_override: Optional[str] = None,
             end: Optional[str] = None) -> None:
    end = end or _today()
    series = get_fred_series()
    gstart = cfg["backfill_start"]["fred"]
    api_key = cfg.get("fred_api_key") or None
    http = cfg["http"]
    sleep = cfg["parallelism"]["fred_sleep"]
    last = _last_macro(con)

    _log(f"FRED: {len(series)} series (api_key={'yes' if api_key else 'no/CSV'})")

    for e in series:
        sid = e["symbol"]
        if start_override:
            s = start_override
        elif sid in last:
            # tail refresh ~95 days to cover macro revisions
            s = max(gstart, (last[sid] - timedelta(days=95)).date().isoformat())
        else:
            s = gstart

        st = datetime.now(timezone.utc)
        try:
            df = fr.fetch_fred(sid, s, end, api_key=api_key,
                               timeout=http["timeout"], retries=http["max_retries"],
                               base_sleep=http["retry_base_sleep"], meta=e)
            if df.empty:
                log_run(con, run_id=run_id, started_at=st, source="fred",
                        symbol=sid, rows_added=0, rows_updated=0,
                        status="empty", error_msg=None, duration_sec=0)
            else:
                added, updated = upsert(con, "macro_series", df)
                record_vintage(con, "macro_series", df, _today())
                log_run(con, run_id=run_id, started_at=st, source="fred",
                        symbol=sid, rows_added=added, rows_updated=updated,
                        status="ok", error_msg=None, duration_sec=0)
        except Exception as ex:
            log_run(con, run_id=run_id, started_at=st, source="fred",
                    symbol=sid, rows_added=0, rows_updated=0,
                    status="error", error_msg=str(ex), duration_sec=0)
        time.sleep(sleep)
    _log("FRED: completed")


# --------------------------------------------------------------- BINANCE
def run_binance(con, cfg: dict, run_id: str, *, start_override: Optional[str] = None,
                end: Optional[str] = None) -> None:
    end = end or datetime.now(timezone.utc).isoformat()
    syms = cfg["crypto"]["symbols"]
    tfs = cfg["crypto"]["timeframes"]
    gstart = cfg["backfill_start"]["binance"]
    http = cfg["http"]
    workers = cfg["parallelism"]["binance_workers"]
    last = _last_crypto(con)

    # lookback to refresh recent candles: 3 steps of the timeframe
    lookback = {"1h": timedelta(hours=10), "4h": timedelta(hours=40),
                "1d": timedelta(days=3), "5m": timedelta(minutes=50),
                "15m": timedelta(minutes=150), "1m": timedelta(minutes=10)}

    jobs = []
    for sym in syms:
        for tf in tfs:
            if start_override:
                s = start_override
            elif (sym, tf) in last:
                s = (last[(sym, tf)] - lookback.get(tf, timedelta(days=1))).isoformat()
            else:
                s = gstart
            jobs.append((sym, tf, s))

    _log(f"BINANCE: {len(jobs)} jobs ({len(syms)} symbols x {len(tfs)} tf)")

    def _do(job):
        sym, tf, s = job
        df = bn.fetch_klines(sym, tf, s, end, timeout=http["timeout"],
                             retries=http["max_retries"],
                             base_sleep=http["retry_base_sleep"])
        return sym, tf, df

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_do, j): j for j in jobs}
        for fut in as_completed(futs):
            sym, tf, s = futs[fut]
            st = datetime.now(timezone.utc)
            try:
                _, _, df = fut.result()
                if df is None or df.empty:
                    log_run(con, run_id=run_id, started_at=st, source="binance",
                            symbol=f"{sym}:{tf}", rows_added=0, rows_updated=0,
                            status="empty", error_msg=None, duration_sec=0)
                else:
                    added, updated = upsert(con, "crypto_ohlcv", df)
                    log_run(con, run_id=run_id, started_at=st, source="binance",
                            symbol=f"{sym}:{tf}", rows_added=added,
                            rows_updated=updated, status="ok", error_msg=None,
                            duration_sec=0)
            except Exception as exc:
                log_run(con, run_id=run_id, started_at=st, source="binance",
                        symbol=f"{sym}:{tf}", rows_added=0, rows_updated=0,
                        status="error", error_msg=str(exc), duration_sec=0)
    _log("BINANCE: completed")


# --------------------------------------------------------------- MACRO PANEL
def run_macro_panel(con, cfg: dict, run_id: str, *,
                    start_year: Optional[int] = None) -> None:
    """Download the cross-country panel (World Bank + IMF) with fallback.

    IMF calls are spaced out (the IMF WAF blocks bursts). The primary->fallback
    logic guarantees data even when IMF is temporarily blocked (World Bank
    fallback).
    """
    specs = get_macro_panel_specs()
    countries = get_countries()
    http = cfg["http"]
    sy = start_year or int(cfg["backfill_start"]["fred"][:4])
    imf_sleep = cfg.get("parallelism", {}).get("imf_sleep", 0.5)
    wb_workers = cfg.get("parallelism", {}).get("wb_workers", 5)

    # World Bank indicators are downloaded in parallel (concurrent fetches);
    # all the other sources (IMF, BIS) are sequential and spaced out.
    # The upsert into DuckDB is ALWAYS serialized in the main thread.
    wb_specs = [s for s in specs if s["source"] == "WB"]
    seq_specs = [s for s in specs if s["source"] != "WB"]   # IMF + BIS + others
    _log(f"MACRO PANEL: {len(specs)} indicators x {len(countries)} countries "
         f"(WB parallel x{wb_workers}, {len(seq_specs)} sequential)")
    n_ok = n_fb = n_empty = 0

    def _upsert_result(spec, df, status, st):
        nonlocal n_ok, n_fb, n_empty
        if df is None or df.empty:
            n_empty += 1
            log_run(con, run_id=run_id, started_at=st, source="macro_panel",
                    symbol=spec["id"], rows_added=0, rows_updated=0,
                    status="empty", error_msg="no data (primary+fallback)",
                    duration_sec=0)
            return
        added, updated = upsert(con, "macro_panel", df)
        record_vintage(con, "macro_panel", df, _today())
        if status == "fallback":
            n_fb += 1
        else:
            n_ok += 1
        log_run(con, run_id=run_id, started_at=st, source="macro_panel",
                symbol=spec["id"], rows_added=added, rows_updated=updated,
                status=status, error_msg=None, duration_sec=0)

    # --- WB in parallel: concurrent fetch, serialized upsert ---
    def _fetch_wb(spec):
        st = datetime.now(timezone.utc)
        df, _src, status = mp.fetch_indicator(spec, countries, start_year=sy, http=http)
        return spec, df, status, st

    with ThreadPoolExecutor(max_workers=wb_workers) as ex:
        futs = {ex.submit(_fetch_wb, s): s for s in wb_specs}
        done = 0
        for fut in as_completed(futs):
            spec = futs[fut]
            try:
                sp, df, status, st = fut.result()
                _upsert_result(sp, df, status, st)
            except Exception as exc:
                n_empty += 1
                log_run(con, run_id=run_id, started_at=datetime.now(timezone.utc),
                        source="macro_panel", symbol=spec["id"], rows_added=0,
                        rows_updated=0, status="error", error_msg=str(exc),
                        duration_sec=0)
            done += 1
            if done % 5 == 0:
                _log(f"  WB {done}/{len(wb_specs)} completed")

    # --- Sequential sources (IMF, BIS, ...): exactly the same path, with a
    # small courtesy pause between calls. ---
    for spec in seq_specs:
        st = datetime.now(timezone.utc)
        time.sleep(imf_sleep)
        try:
            df, _src, status = mp.fetch_indicator(spec, countries, start_year=sy, http=http)
            _upsert_result(spec, df, status, st)
        except Exception as ex:
            n_empty += 1
            log_run(con, run_id=run_id, started_at=st, source="macro_panel",
                    symbol=spec["id"], rows_added=0, rows_updated=0,
                    status="error", error_msg=str(ex), duration_sec=0)

    _log(f"MACRO PANEL: ok={n_ok} fallback={n_fb} empty={n_empty}")


# --------------------------------------------------------------- LIVE
def run_live(con, cfg: dict, run_id: str) -> None:
    """Update the 'today' row with live prices mapped into the adjusted space."""
    if not cfg.get("live", {}).get("enabled", False):
        _log("LIVE: disabled in settings")
        return
    allowed = set(cfg["live"]["asset_classes"])
    tickers = [e for e in get_yahoo_tickers() if e.get("asset_class") in allowed]
    today = pd.Timestamp(_today())

    # latest EOD (adj_close, close) per symbol
    eod = con.execute(
        "SELECT p.symbol, p.adj_close, p.close FROM prices_daily p "
        "JOIN (SELECT symbol, max(date) d FROM prices_daily "
        "      WHERE is_live = FALSE GROUP BY symbol) m "
        "  ON p.symbol = m.symbol AND p.date = m.d "
        "WHERE p.is_live = FALSE").fetch_df()
    eod_map = {r.symbol: (r.adj_close, r.close) for r in eod.itertuples()}

    # live prices in a SINGLE download batch (no per-ticker loop -> no 429)
    syms = [e["symbol"] for e in tickers if e["symbol"] in eod_map]
    live_prices = yh.get_live_prices_batch(syms)

    rows = []
    n_ok = 0
    for sym in syms:
        live = live_prices.get(sym)
        if live is None:
            continue
        adj_eod, close_eod = eod_map[sym]
        adj_live = yh.adjusted_live_price(live, adj_eod, close_eod)
        if adj_live is None:
            continue
        rows.append({"date": today.date(), "symbol": sym, "open": None,
                     "high": None, "low": None, "close": live,
                     "adj_close": adj_live, "volume": None,
                     "source": "yahoo", "is_live": True,
                     "updated_at": datetime.now(timezone.utc)})
        n_ok += 1

    if rows:
        upsert(con, "prices_daily", pd.DataFrame(rows))
    _log(f"LIVE: updated {n_ok}/{len(tickers)} symbols")
    log_run(con, run_id=run_id, started_at=datetime.now(timezone.utc),
            source="live", symbol="*", rows_added=n_ok, rows_updated=0,
            status="ok", error_msg=None, duration_sec=0)


# --------------------------------------------------------------- ENTRY
def run(mode: str = "full", sources: Optional[List[str]] = None,
        start_override: Optional[str] = None, end: Optional[str] = None,
        db_path: Optional[str] = None) -> None:
    cfg = get_settings()
    run_id = uuid.uuid4().hex[:12]
    t0 = time.time()
    con = get_conn(db_path)
    _log(f"=== RUN {run_id} mode={mode} ===")

    try:
        if mode == "live-only":
            run_live(con, cfg, run_id)
        else:
            active = sources or ["yahoo", "fred", "binance", "macro_panel"]
            if "yahoo" in active:
                run_yahoo(con, cfg, run_id, start_override=start_override, end=end)
            if "fred" in active:
                run_fred(con, cfg, run_id, start_override=start_override, end=end)
            if "binance" in active:
                run_binance(con, cfg, run_id, start_override=start_override, end=end)
            if "macro_panel" in active:
                run_macro_panel(con, cfg, run_id)
            if mode == "full" and cfg.get("live", {}).get("enabled"):
                run_live(con, cfg, run_id)

        _log("Rebuilding coverage_report...")
        n = rebuild_coverage(con, run_id)
        _log(f"coverage_report: {n} series")

        # stalled alert
        st = con.execute(
            "SELECT count(*) FROM coverage_report WHERE stalled = TRUE").fetchone()[0]
        if st:
            _log(f"  WARNING: {st} series are stalled (see diagnose.py --stalled)")
    finally:
        con.close()

    # --- Ray Dalio analytical layer (after the panel: cycle phases + regime) ---
    macro_done = mode != "live-only" and "macro_panel" in (
        sources or ["yahoo", "fred", "binance", "macro_panel"])
    if macro_done:
        try:
            from market_data_hub.dalio import run_dalio
            s = run_dalio(db_path)
            _log(f"DALIO: {s['countries']} countries | phases {s['phases']} | regimes {s['regimes']}")
            _log(f"DALIO: WEO forecast horizon = {s['weo_horizon']}")
            if s["forecast_stale"]:
                _log("  WARNING: WEO forecasts NOT up to date (horizon <= current "
                     "year). Check the macro_panel/IMF download.")
        except Exception as ex:
            _log(f"DALIO: error (non-blocking): {ex}")
        try:
            from market_data_hub.classify import classify_countries
            cl = classify_countries(db_path)
            _log(f"CLASSIFY: {cl['countries']} countries | development {cl['development']} "
                 f"| energy {cl['energy']}")
        except Exception as ex:
            _log(f"CLASSIFY: error (non-blocking): {ex}")

    _log(f"=== END {run_id} ({time.time()-t0:.1f}s) ===")
