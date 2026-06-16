# -*- coding: utf-8 -*-
"""
yahoo_direct.py — download OHLCV da Yahoo SENZA yfinance.

Chiama direttamente l'endpoint chart v8 di Yahoo via curl_cffi (impersona
Chrome, rispetta CURL_CA_BUNDLE per la rete MITM). L'endpoint chart NON
richiede crumb/cookie/cache, quindi e' immune ai problemi di yfinance
(versione 1.2.1 fasulla, cache SQLite bloccata, 'str' object has no attribute
'name', 429 sul crumb).

Output canonico identico a sources/yahoo.py:
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

# Sessione curl_cffi per-thread (le Session non sono thread-safe: una per thread)
_local = threading.local()


def _session():
    s = getattr(_local, "s", None)
    if s is None:
        s = _creq.Session(impersonate="chrome") if _creq is not None else None
        _local.s = s
    return s


def _epoch(d: str, end: bool = False) -> int:
    ts = pd.Timestamp(d)
    if end:
        ts = ts + pd.Timedelta(days=1)  # period2 esclusivo -> +1 giorno
    return int(ts.replace(tzinfo=None).timestamp())


def _parse(symbol: str, j: dict) -> pd.DataFrame:
    """Estrae il frame OHLCV dal JSON chart di Yahoo."""
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
    """Scarica un singolo simbolo con retry. Ritorna frame (vuoto se ko)."""
    if _creq is None:
        raise RuntimeError("curl_cffi non installato. pip install curl_cffi")
    last_exc = None
    for attempt in range(1, retries + 1):
        for base in (_BASE, _BASE2):  # fallback host
            try:
                r = _session().get(base + symbol, params=params, timeout=30)
                if r.status_code == 200:
                    return _parse(symbol, r.json())
                if r.status_code in (404, 400):
                    return pd.DataFrame(columns=_OUT_COLS)  # delisted/non valido
            except Exception as e:
                last_exc = e
        if attempt < retries:
            time.sleep(base_sleep * attempt)
    return pd.DataFrame(columns=_OUT_COLS)


def yahoo_batch(tickers: List[str], start: str, end: str, *,
                workers: int = 8, retries: int = 3) -> Dict[str, pd.DataFrame]:
    """Scarica OHLCV daily per piu' ticker; ritorna {symbol: frame OHLCV}.

    Drop-in di sources.yahoo.yahoo_batch ma via API chart diretta (no yfinance).
    Parallelo (curl_cffi e' veloce); ogni thread ha la sua Session.
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
    """Ultimo prezzo intraday per molti ticker via chart API 1m (no yfinance)."""
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
                # prima prova meta.regularMarketPrice, poi ultimo close 1m
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
