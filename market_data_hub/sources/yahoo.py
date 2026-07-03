# -*- coding: utf-8 -*-
"""
yahoo.py — Yahoo Finance source (daily OHLCV) + live price injection.

The actual HTTP work lives in :mod:`market_data_hub.sources.yahoo_direct`
(direct chart-v8 API via curl_cffi — no yfinance, no crumb/cookie/cache).
This module keeps the source-level logic on top of it:

  - incremental effective_start logic with tail refresh
  - live→adjusted price mapping (multiplicative delta)

Canonical output for prices_daily:
  [date, symbol, open, high, low, close, adj_close, volume, source]
"""
from __future__ import annotations

from datetime import timedelta
from typing import Dict, List, Optional

import pandas as pd


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
