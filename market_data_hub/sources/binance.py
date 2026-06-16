# -*- coding: utf-8 -*-
"""
binance.py — Binance source (intraday/daily OHLCV klines) via public API.

Ports from crypto_ml_features/binance_downloader_improved.py the klines
pagination logic and the extended fields (quote volume, n_trades, taker buy).
Refreshing recent incomplete candles is handled by the runner, which restarts
from (last_ts - lookback).

Canonical output for crypto_ohlcv:
  [ts, symbol, timeframe, open, high, low, close, volume, volume_quote,
   n_trades, taker_buy_base, is_closed]
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import requests

_BASE = "https://api.binance.com/api/v3/klines"
_MAX = 1000

_TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000,
          "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}

_OUT_COLS = ["ts", "symbol", "timeframe", "open", "high", "low", "close",
             "volume", "volume_quote", "n_trades", "taker_buy_base", "is_closed"]


def _to_ms(dt) -> int:
    ts = pd.Timestamp(dt)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return int(ts.timestamp() * 1000)


def fetch_klines(symbol: str, timeframe: str, start, end, *,
                 timeout: int = 30, retries: int = 3, base_sleep: float = 1.0,
                 request_delay: float = 0.25) -> pd.DataFrame:
    """Download paginated klines for symbol/timeframe between start and end (UTC)."""
    if timeframe not in _TF_MS:
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    symbol = symbol.upper().strip()
    start_ts, end_ts = _to_ms(start), _to_ms(end)
    rows = []
    session = requests.Session()

    while start_ts < end_ts:
        params = {"symbol": symbol, "interval": timeframe,
                  "startTime": start_ts, "endTime": end_ts, "limit": _MAX}
        data = None
        for attempt in range(retries):
            try:
                r = session.get(_BASE, params=params, timeout=timeout)
                r.raise_for_status()
                data = r.json()
                break
            except Exception:
                if attempt < retries - 1:
                    time.sleep(base_sleep * (4 ** attempt))
                else:
                    raise
        if not data:
            break

        rows.extend(data)
        last_close = data[-1][6]
        nxt = last_close + 1
        if nxt <= start_ts:
            break
        start_ts = nxt
        time.sleep(request_delay)

    if not rows:
        return pd.DataFrame(columns=_OUT_COLS)

    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_asset_volume", "number_of_trades", "taker_buy_base",
        "taker_buy_quote", "ignore"])

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    out = pd.DataFrame({
        "ts": pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.tz_localize(None),
        "symbol": symbol,
        "timeframe": timeframe,
        "open": pd.to_numeric(df["open"], errors="coerce"),
        "high": pd.to_numeric(df["high"], errors="coerce"),
        "low": pd.to_numeric(df["low"], errors="coerce"),
        "close": pd.to_numeric(df["close"], errors="coerce"),
        "volume": pd.to_numeric(df["volume"], errors="coerce"),
        "volume_quote": pd.to_numeric(df["quote_asset_volume"], errors="coerce"),
        "n_trades": pd.to_numeric(df["number_of_trades"], errors="coerce").astype("Int64"),
        "taker_buy_base": pd.to_numeric(df["taker_buy_base"], errors="coerce"),
        # candle is closed if its close_time has already passed
        "is_closed": df["close_time"] < now_ms,
    })
    out = (out.dropna(subset=["ts"])
              .drop_duplicates(subset=["ts"], keep="last")
              .sort_values("ts")
              .reset_index(drop=True))
    return out
