# -*- coding: utf-8 -*-
"""
yahoo.py — Yahoo Finance source (daily OHLCV) + live price injection.

Ported from quant_timeseries_suite/checks1_improved.py:
  - batch download grouped by start date (efficiency)
  - incremental effective_start logic with tail refresh
and from zero_noise_pipeline/data_downolad_live.py:
  - get_last_price_live() with 3 fallback sources
  - delta mapping in adjusted space

Canonical output for prices_daily:
  [date, symbol, open, high, low, close, adj_close, volume, source]
"""
from __future__ import annotations

import time
from datetime import timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

try:
    import yfinance as yf
except ImportError:  # pragma: no cover
    yf = None

_FIELDS = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
_OUT_COLS = ["date", "symbol", "open", "high", "low", "close", "adj_close", "volume"]


# ---------------------------------------------------------------- shared session
# yfinance 0.2.x handles curl_cffi INTERNALLY (with impersonation and crumb
# cache) and respects CURL_CA_BUNDLE (set by _ssl_bootstrap) for the MITM/proxy
# network. Passing it an external curl_cffi session now BREAKS
# ("'str' object has no attribute 'name'"), so we do NOT pass it: get_session
# returns None on 0.2.x. The shared session was only a workaround for the (bogus)
# yfinance 1.2.x; kept for any environments running that version.
_SESSION = None


def _yf_major_ge_1() -> bool:
    """True only for the (anomalous) yfinance 1.x; False for the official 0.2.x."""
    try:
        return int((yf.__version__ or "0").split(".")[0]) >= 1
    except Exception:
        return False


def get_session():
    """Shared curl_cffi session ONLY for yfinance 1.x; None for 0.2.x."""
    global _SESSION
    if not _yf_major_ge_1():
        return None  # 0.2.x: let yfinance handle curl_cffi
    if _SESSION is not None:
        return _SESSION
    try:
        from curl_cffi import requests as _creq
        _SESSION = _creq.Session(impersonate="chrome")
    except Exception:
        _SESSION = None
    return _SESSION


# ---------------------------------------------------------------- incremental
def effective_start(last_date: Optional[pd.Timestamp], global_start: str,
                    tail_refresh_days: int) -> str:
    """Next start date: last_date - tail (revisions) or global_start."""
    if last_date is None or pd.isna(last_date):
        return global_start
    base = pd.Timestamp(last_date) - timedelta(days=int(tail_refresh_days))
    nxt = base.date().isoformat()
    return max(global_start, nxt)


# ---------------------------------------------------------------- batch fetch
def _extract_symbol(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Extract an OHLCV frame for a ticker from the yf.download result.

    yfinance 1.x always returns a MultiIndex (Price, Ticker) with
    multi_level_index=True, for both single and multiple tickers.
    """
    cols = {}
    for f in _FIELDS:
        key = (f, ticker)
        if key in raw.columns:
            cols[f] = raw[key]
    if not cols:
        return pd.DataFrame(columns=_OUT_COLS)

    df = pd.DataFrame(cols)
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df = df.reset_index()
    df.columns = [str(c[0]) if isinstance(c, tuple) else str(c)
                  for c in df.columns]
    date_col = df.columns[0]

    out = pd.DataFrame({
        "date": pd.to_datetime(df[date_col], errors="coerce"),
        "symbol": ticker,
        "open":      pd.to_numeric(df.get("Open"),      errors="coerce"),
        "high":      pd.to_numeric(df.get("High"),      errors="coerce"),
        "low":       pd.to_numeric(df.get("Low"),       errors="coerce"),
        "close":     pd.to_numeric(df.get("Close"),     errors="coerce"),
        "adj_close": pd.to_numeric(df.get("Adj Close", df.get("Close")),
                                   errors="coerce"),
        "volume":    pd.to_numeric(df.get("Volume"),    errors="coerce"),
    })
    out = out.dropna(subset=["date"])
    out = out[out[["open", "high", "low", "close", "adj_close"]].notna().any(axis=1)]
    return out.reset_index(drop=True)


def _download_chunk(tickers: List[str], start: str, end_query: str, session,
                    retries: int, backoff: float):
    """A single yf.download call with 429-aware retry. Returns raw or None."""
    raw = None
    for attempt in range(1, retries + 1):
        try:
            kwargs = dict(
                tickers=tickers, start=start, end=end_query,
                auto_adjust=False, multi_level_index=True,
                ignore_tz=True, progress=False, threads=False,
            )
            if session is not None:
                kwargs["session"] = session
            raw = yf.download(**kwargs)
        except Exception:
            raw = None
        if raw is not None and not raw.empty:
            return raw
        if attempt < retries:
            # Yahoo 429 cooldown: 20s, 40s, 60s... (the 429 page is HTML ->
            # "unexpected character"); the long backoff lets it expire.
            time.sleep(backoff * attempt)
    return raw


def yahoo_batch(tickers: List[str], start: str, end: str,
                **kwargs) -> Dict[str, pd.DataFrame]:
    """Download multiple tickers; returns {symbol: OHLCV frame}.

    Delegates to the direct chart API (sources.yahoo_direct), which does NOT use
    yfinance nor crumb/cookie/cache: immune to yfinance issues (bogus 1.2.1,
    blocked cache, 'str' object, 429 on crumb). ~1-2s for 100+ tickers in parallel.
    """
    from market_data_hub.sources import yahoo_direct as _yd
    return _yd.yahoo_batch(tickers, start, end, **kwargs)


# ---------------------------------------------------------------- live prices
def get_last_price_live(ticker: str) -> Optional[float]:
    """
    Last "live" price (often delayed) with multiple fallbacks.
    Ported from zero_noise_pipeline/data_downolad_live.py.

    Note: does NOT use t.info (quoteSummary endpoint), which on many tickers
    triggers Yahoo's 429. Uses fast_info (lightweight) and, as a fallback, 1m
    history, always on the shared session.
    """
    if yf is None:
        return None
    session = get_session()
    t = yf.Ticker(ticker, session=session) if session is not None else yf.Ticker(ticker)
    p = None

    # 1) fast_info (lightweight endpoint, no quoteSummary)
    try:
        fi = getattr(t, "fast_info", None)
        if fi is not None:
            p = (fi.get("last_price") if hasattr(fi, "get") else None) \
                or (fi.get("lastPrice") if hasattr(fi, "get") else None)
    except Exception:
        p = None

    # 2) intraday 1m (fallback)
    if p is None:
        try:
            intr = t.history(period="1d", interval="1m", prepost=True)
            if intr is not None and len(intr) > 0:
                s = intr["Close"].dropna()
                if len(s) > 0:
                    p = float(s.iloc[-1])
        except Exception:
            p = None

    if p is None or (isinstance(p, float) and np.isnan(p)):
        return None
    return float(p)


def get_live_prices_batch(tickers: List[str]) -> Dict[str, float]:
    """Last intraday price for many tickers (via direct chart API, no yfinance)."""
    from market_data_hub.sources import yahoo_direct as _yd
    return _yd.get_live_prices_batch(tickers)


def adjusted_live_price(live: float, adj_close_eod: float,
                        close_eod: float) -> Optional[float]:
    """
    Map the live (raw) price into adjusted space via the EOD adjustment ratio:
        adj_live = live * (adj_close_eod / close_eod)

    The adjustment factor (adj_close / close) is multiplicative — that is how
    split/dividend adjustment works — so the live price must be scaled by the
    same ratio. An additive delta distorts assets with large corporate actions
    (e.g. close=100, adj_close=50, live=102 -> ratio gives 51, not 52).
    """
    if any(v is None or pd.isna(v) for v in (live, adj_close_eod, close_eod)):
        return None
    if float(close_eod) == 0:
        return None
    return float(live) * (float(adj_close_eod) / float(close_eod))
