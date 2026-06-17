# -*- coding: utf-8 -*-
"""
yahoo_direct.py — download OHLCV from Yahoo WITHOUT yfinance.

Calls Yahoo's chart v8 endpoint directly via curl_cffi (impersonates Chrome,
respects CURL_CA_BUNDLE for the MITM network). The chart endpoint does NOT
require crumb/cookie/cache, so it is immune to yfinance issues (bogus version
1.2.1, locked SQLite cache, 'str' object has no attribute 'name', 429 on the
crumb).

Canonical output identical to sources/yahoo.py:
  [date, symbol, open, high, low, close, adj_close, volume]
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import pandas as pd

try:
    from curl_cffi import requests as _creq
except ImportError:  # pragma: no cover
    _creq = None

_OUT_COLS = ["date", "symbol", "open", "high", "low", "close", "adj_close", "volume"]
_BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"
_BASE2 = "https://query2.finance.yahoo.com/v8/finance/chart/"

# Per-thread curl_cffi session (Sessions are not thread-safe: one per thread)
_local = threading.local()


def _session():
    s = getattr(_local, "s", None)
    if s is None:
        s = _creq.Session(impersonate="chrome") if _creq is not None else None
        _local.s = s
    return s


def _epoch(d: str, end: bool = False) -> int:
    """Calendar date -> UNIX epoch seconds, interpreted in UTC.

    A naive Timestamp.timestamp() would use the *local* timezone, shifting the
    request window by the machine's UTC offset (wrong day boundaries for a
    financial API). Pin the date to UTC explicitly so the result is portable.
    """
    ts = pd.Timestamp(d)
    if end:
        ts = ts + pd.Timedelta(days=1)  # period2 exclusive -> +1 day
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return int(ts.timestamp())


def _parse(symbol: str, j: dict) -> pd.DataFrame:
    """Extract the OHLCV frame from Yahoo's chart JSON."""
    try:
        res = (j.get("chart", {}).get("result") or [None])[0]
        if not res:
            return pd.DataFrame(columns=_OUT_COLS)
        ts = res.get("timestamp")
        if not ts:
            return pd.DataFrame(columns=_OUT_COLS)
        ind = res.get("indicators", {})
        q = (ind.get("quote") or [{}])[0]
        adj_block = ind.get("adjclose") or [{}]
        adj = adj_block[0].get("adjclose") if adj_block else None

        dates = pd.to_datetime(ts, unit="s", utc=True).tz_convert(None).normalize()
        out = pd.DataFrame({
            "date": dates,
            "symbol": symbol,
            "open":   pd.to_numeric(q.get("open"),   errors="coerce"),
            "high":   pd.to_numeric(q.get("high"),   errors="coerce"),
            "low":    pd.to_numeric(q.get("low"),    errors="coerce"),
            "close":  pd.to_numeric(q.get("close"),  errors="coerce"),
            "adj_close": pd.to_numeric(adj if adj is not None else q.get("close"),
                                       errors="coerce"),
            "volume": pd.to_numeric(q.get("volume"), errors="coerce"),
        })
        out = out.dropna(subset=["date"])
        out = out[out[["open", "high", "low", "close", "adj_close"]]
                  .notna().any(axis=1)]
        return out.reset_index(drop=True)
    except Exception:
        return pd.DataFrame(columns=_OUT_COLS)


def _fetch_one(symbol: str, params: dict, *, retries: int = 3,
               base_sleep: float = 1.5) -> pd.DataFrame:
    """Download a single symbol with retry. Returns frame (empty on failure)."""
    if _creq is None:
        raise RuntimeError("curl_cffi not installed. pip install curl_cffi")
    last_exc = None
    for attempt in range(1, retries + 1):
        for base in (_BASE, _BASE2):  # host fallback
            try:
                r = _session().get(base + symbol, params=params, timeout=30)
                if r.status_code == 200:
                    return _parse(symbol, r.json())
                if r.status_code in (404, 400):
                    return pd.DataFrame(columns=_OUT_COLS)  # delisted/invalid
            except Exception as e:
                last_exc = e
        if attempt < retries:
            time.sleep(base_sleep * attempt)
    return pd.DataFrame(columns=_OUT_COLS)


def yahoo_batch(tickers: List[str], start: str, end: str, *,
                workers: int = 8, retries: int = 3) -> Dict[str, pd.DataFrame]:
    """Download daily OHLCV for multiple tickers; returns {symbol: OHLCV frame}.

    Drop-in for sources.yahoo.yahoo_batch but via the direct chart API (no yfinance).
    Parallel (curl_cffi is fast); each thread has its own Session.
    """
    if not tickers:
        return {}

    params = {
        "period1": _epoch(start),
        "period2": _epoch(end, end=True),
        "interval": "1d",
        "events": "div,splits",
    }
    start_dt = pd.to_datetime(start, errors="coerce")
    end_dt = pd.to_datetime(end, errors="coerce")

    results: Dict[str, pd.DataFrame] = {}

    def _do(sym: str):
        df = _fetch_one(sym, params, retries=retries)
        if not df.empty:
            if not pd.isna(start_dt):
                df = df[df["date"] >= start_dt]
            if not pd.isna(end_dt):
                df = df[df["date"].dt.date <= end_dt.date()]
            df = df.reset_index(drop=True)
        return sym, df

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_do, s): s for s in tickers}
        for fut in as_completed(futs):
            try:
                sym, df = fut.result()
            except Exception:
                sym, df = futs[fut], pd.DataFrame(columns=_OUT_COLS)
            results[sym] = df
    return results


def get_live_prices_batch(tickers: List[str], *, workers: int = 8
                          ) -> Dict[str, float]:
    """Last intraday price for many tickers via the 1m chart API (no yfinance)."""
    if not tickers:
        return {}
    params = {"range": "1d", "interval": "1m", "includePrePost": "true"}

    out: Dict[str, float] = {}

    def _do(sym: str):
        if _creq is None:
            return sym, None
        for base in (_BASE, _BASE2):
            try:
                r = _session().get(base + sym, params=params, timeout=20)
                if r.status_code != 200:
                    continue
                res = (r.json().get("chart", {}).get("result") or [None])[0]
                if not res:
                    continue
                # first try meta.regularMarketPrice, then last 1m close
                meta = res.get("meta", {})
                p = meta.get("regularMarketPrice")
                if p is None:
                    q = (res.get("indicators", {}).get("quote") or [{}])[0]
                    closes = [c for c in (q.get("close") or []) if c is not None]
                    p = closes[-1] if closes else None
                if p is not None:
                    return sym, float(p)
            except Exception:
                continue
        return sym, None

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_do, s): s for s in tickers}
        for fut in as_completed(futs):
            try:
                sym, p = fut.result()
            except Exception:
                sym, p = futs[fut], None
            if p is not None:
                out[sym] = p
    return out
