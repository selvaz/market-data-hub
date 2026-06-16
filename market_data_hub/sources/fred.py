# -*- coding: utf-8 -*-
"""
fred.py — FRED (St. Louis Fed) source for single-value macro series.

Uses the official JSON API if an API key is configured, otherwise the public
CSV endpoint (fredgraph.csv) which requires no key. All of the project's macro
series (US rates, CPI, GDP, credit spreads, and the euro-area series replicated
from FRED) go through here.

Canonical output for macro_series:
  [date, series_id, value, series_name, unit, frequency, source, country]
"""
from __future__ import annotations

import time
from io import StringIO
from typing import Optional

import pandas as pd
import requests

_API_URL = "https://api.stlouisfed.org/fred/series/observations"
_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


def _http_get(url: str, params: dict, timeout: int, retries: int,
              base_sleep: float) -> requests.Response:
    # Connection: close avoids reusing "dead" keep-alive connections
    # through corporate proxies (a frequent cause of RemoteDisconnected).
    headers = {"User-Agent": "market-data-hub/0.1", "Connection": "close"}
    last = None
    for attempt in range(retries):
        try:
            with requests.Session() as s:
                r = s.get(url, params=params, headers=headers, timeout=timeout)
                r.raise_for_status()
                return r
        except Exception as e:
            last = e
            if attempt < retries - 1:
                time.sleep(base_sleep * (4 ** attempt))  # 1, 4, 16s
    raise last


def fetch_fred(series_id: str, start: str, end: str, *,
               api_key: Optional[str] = None, timeout: int = 30,
               retries: int = 3, base_sleep: float = 1.0,
               meta: Optional[dict] = None) -> pd.DataFrame:
    """Download a FRED series between start and end (inclusive)."""
    meta = meta or {}

    if api_key:
        params = {
            "series_id": series_id, "api_key": api_key, "file_type": "json",
            "observation_start": start, "observation_end": end,
        }
        r = _http_get(_API_URL, params, timeout, retries, base_sleep)
        obs = r.json().get("observations", [])
        df = pd.DataFrame(obs)
        if df.empty or "date" not in df.columns:
            return _empty()
        df = df[["date", "value"]]
    else:
        # public CSV endpoint (no key)
        params = {"id": series_id, "cosd": start, "coed": end}
        r = _http_get(_CSV_URL, params, timeout, retries, base_sleep)
        df = pd.read_csv(StringIO(r.text))
        if df.shape[1] < 2:
            return _empty()
        df.columns = ["date", "value"]

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")  # "." -> NaN
    df = df.dropna(subset=["date"]).sort_values("date")
    df = df[(df["date"] >= pd.to_datetime(start)) & (df["date"] <= pd.to_datetime(end))]
    df = df.dropna(subset=["value"]).reset_index(drop=True)

    df["series_id"] = series_id
    df["series_name"] = meta.get("name", series_id)
    df["unit"] = meta.get("unit")
    df["frequency"] = None  # inferred by the coverage engine
    df["source"] = "fred"
    df["country"] = meta.get("country", "US")
    return df[["date", "series_id", "value", "series_name", "unit",
               "frequency", "source", "country"]]


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=["date", "series_id", "value", "series_name",
                                 "unit", "frequency", "source", "country"])
