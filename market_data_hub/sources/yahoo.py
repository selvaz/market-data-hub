# -*- coding: utf-8 -*-
"""
yahoo.py — sorgente Yahoo Finance (OHLCV daily) + live price injection.

Porta da quant_timeseries_suite/checks1_improved.py:
  - download batch raggruppato per start date (efficienza)
  - logica incrementale effective_start con tail refresh
e da zero_noise_pipeline/data_downolad_live.py:
  - get_last_price_live() con 3 sorgenti fallback
  - delta mapping nello spazio adjusted

Output canonico per prices_daily:
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
# yfinance 0.2.x gestisce curl_cffi INTERNAMENTE (con impersonation e crumb
# cache) e rispetta CURL_CA_BUNDLE (impostato da _ssl_bootstrap) per la rete
# MITM/proxy. Passargli una sessione curl_cffi esterna ora ROMPE
# ("'str' object has no attribute 'name'"), quindi NON la passiamo: get_session
# ritorna None su 0.2.x. La sessione condivisa serviva solo come workaround per
# la (fasulla) yfinance 1.2.x; conservata per eventuali ambienti con quella.
_SESSION = None


def _yf_major_ge_1() -> bool:
    """True solo per le (anomale) yfinance 1.x; False per le ufficiali 0.2.x."""
    try:
        return int((yf.__version__ or "0").split(".")[0]) >= 1
    except Exception:
        return False


def get_session():
    """Sessione curl_cffi condivisa SOLO per yfinance 1.x; None per 0.2.x."""
    global _SESSION
    if not _yf_major_ge_1():
        return None  # 0.2.x: lascia gestire curl_cffi a yfinance
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
    """Prossima data di partenza: last_date - tail (revisioni) oppure global_start."""
    if last_date is None or pd.isna(last_date):
        return global_start
    base = pd.Timestamp(last_date) - timedelta(days=int(tail_refresh_days))
    nxt = base.date().isoformat()
    return max(global_start, nxt)


# ---------------------------------------------------------------- batch fetch
def _extract_symbol(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Estrae un frame OHLCV per un ticker dal risultato yf.download.

    yfinance 1.x restituisce sempre MultiIndex (Price, Ticker) con
    multi_level_index=True, sia per singolo che per multipli ticker.
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
    """Una chiamata yf.download con retry 429-aware. Ritorna raw o None."""
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
            # cooldown 429 di Yahoo: 20s, 40s, 60s... (la pagina 429 e' HTML ->
            # "unexpected character"); il backoff lungo lo lascia scadere.
            time.sleep(backoff * attempt)
    return raw


def yahoo_batch(tickers: List[str], start: str, end: str,
                **kwargs) -> Dict[str, pd.DataFrame]:
    """Scarica piu' ticker; ritorna {symbol: frame OHLCV}.

    Delega all'API chart diretta (sources.yahoo_direct), che NON usa yfinance
    ne' crumb/cookie/cache: immune ai problemi di yfinance (1.2.1 fasulla, cache
    bloccata, 'str' object, 429 sul crumb). ~1-2s per 100+ ticker in parallelo.
    """
    from market_data_hub.sources import yahoo_direct as _yd
    return _yd.yahoo_batch(tickers, start, end)


# ---------------------------------------------------------------- live prices
def get_last_price_live(ticker: str) -> Optional[float]:
    """
    Ultimo prezzo "live" (spesso ritardato) con fallback multipli.
    Portato da zero_noise_pipeline/data_downolad_live.py.

    NB: NON usa t.info (endpoint quoteSummary), che su molti ticker scatena
    il 429 di Yahoo. Usa fast_info (leggero) e in fallback history 1m, sempre
    sulla sessione condivisa.
    """
    if yf is None:
        return None
    session = get_session()
    t = yf.Ticker(ticker, session=session) if session is not None else yf.Ticker(ticker)
    p = None

    # 1) fast_info (endpoint leggero, niente quoteSummary)
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
    """Ultimo prezzo intraday per molti ticker (via API chart diretta, no yfinance)."""
    from market_data_hub.sources import yahoo_direct as _yd
    return _yd.get_live_prices_batch(tickers)


def adjusted_live_price(live: float, adj_close_eod: float,
                        close_eod: float) -> Optional[float]:
    """
    Mappa il prezzo live nello spazio adjusted via delta additivo:
        adj_live = live + (adj_close_eod - close_eod)
    """
    if any(v is None or pd.isna(v) for v in (live, adj_close_eod, close_eod)):
        return None
    return float(live) + (float(adj_close_eod) - float(close_eod))
