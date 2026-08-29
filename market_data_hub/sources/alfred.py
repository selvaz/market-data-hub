# -*- coding: utf-8 -*-
"""
alfred.py — ALFRED point-in-time access through the FRED JSON API.

Canonical output for an ALFRED vintage lookup:
  [date, series_id, value, as_of, source]
"""
from __future__ import annotations

import os
import time
import warnings

import pandas as pd
import requests

_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
_VINTAGE_DATES_URL = "https://api.stlouisfed.org/fred/series/vintagedates"
_COLUMNS = ["date", "series_id", "value", "as_of", "source"]


# Kept local rather than importing fred._http_get: that helper is private to
# its sibling module, and this duplicate preserves each source's HTTP seam.
def _http_get(url: str, params: dict, timeout: int, retries: int,
              base_sleep: float) -> requests.Response:
    headers = {"User-Agent": "market-data-hub/0.1", "Connection": "close"}
    last = None
    for attempt in range(retries):
        try:
            with requests.Session() as session:
                response = session.get(
                    url, params=params, headers=headers, timeout=timeout)
                response.raise_for_status()
                return response
        except Exception as exc:
            last = exc
            if attempt < retries - 1:
                time.sleep(base_sleep * (4 ** attempt))
    raise last if last else RuntimeError(
        f"alfred: no attempts made (retries={retries})")


def _require_api_key(api_key: str | None) -> str:
    key = api_key or os.environ.get("FRED_API_KEY")
    if not key:
        raise ValueError(
            "ALFRED requires a FRED API key; pass api_key or set FRED_API_KEY")
    return key


def fetch_vintage_dates(series_id: str, *, api_key: str | None = None,
                        limit: int = 2000, timeout=30, retries=3,
                        base_sleep=1.0) -> list[str]:
    """Return up to ``limit`` vendor vintage dates, newest first.

    The 2,000-date default deliberately avoids pagination while comfortably
    covering realistic FRED series histories.
    """
    params = {
        "series_id": series_id,
        "api_key": _require_api_key(api_key),
        "file_type": "json",
        "realtime_start": "1776-07-04",
        "realtime_end": "9999-12-31",
        "sort_order": "desc",
        "limit": limit,
        "offset": 0,
    }
    response = _http_get(
        _VINTAGE_DATES_URL, params, timeout, retries, base_sleep)
    payload = response.json()
    dates = payload.get("vintage_dates", [])
    total = payload.get("count")
    if isinstance(total, int) and total > len(dates):
        # No pagination here on purpose (see docstring) -- but silently
        # returning a partial list with no signal is how a caller ends up
        # trusting an incomplete vintage history. Newest-first + desc sort
        # means the OLDEST vintages are the ones dropped, not the newest.
        warnings.warn(
            f"alfred.fetch_vintage_dates({series_id!r}): the vendor reports "
            f"{total} vintage dates but only the newest {len(dates)} "
            f"(limit={limit}) were returned; the oldest vintages are missing. "
            f"Raise limit if you need the full history.",
            stacklevel=2,
        )
    return dates


def fetch_as_of(series_id: str, start: str, end: str, as_of: str, *,
                api_key: str | None = None, timeout=30, retries=3,
                base_sleep=1.0) -> pd.DataFrame:
    """Return observations exactly as FRED reported them on ``as_of``."""
    params = {
        "series_id": series_id,
        "api_key": _require_api_key(api_key),
        "file_type": "json",
        "observation_start": start,
        "observation_end": end,
        "realtime_start": as_of,
        "realtime_end": as_of,
    }
    response = _http_get(
        _OBSERVATIONS_URL, params, timeout, retries, base_sleep)
    observations = response.json().get("observations", [])
    df = pd.DataFrame(observations)
    if df.empty or "date" not in df.columns or "value" not in df.columns:
        return pd.DataFrame(columns=_COLUMNS)

    df = df[["date", "value"]]
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["date", "value"])
    df = df[(df["date"] >= pd.to_datetime(start))
            & (df["date"] <= pd.to_datetime(end))]
    df = df.sort_values("date").reset_index(drop=True)
    df["series_id"] = series_id
    df["as_of"] = pd.to_datetime(as_of)
    df["source"] = "alfred"
    return df[_COLUMNS]
