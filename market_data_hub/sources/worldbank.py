# -*- coding: utf-8 -*-
"""
worldbank.py — World Bank (WDI / WGI / IDS) source for the cross-country panel.

Ports fetch_worldbank() from macro_dashboard_v2_bundle. One REST call per
(indicator, country). api_source_id selects the WB database: 2=WDI, 3=WGI.

Canonical output for macro_panel:
  [date, country_iso3, indicator_id, value, indicator_name, pillar, orientation,
   source, provider_dataset, provider_code, unit, frequency, status]
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd
import requests

_BASE = "https://api.worldbank.org/v2"

_COLS = ["date", "country_iso3", "indicator_id", "value", "indicator_name",
         "pillar", "orientation", "source", "provider_dataset",
         "provider_code", "unit", "frequency", "status"]


def _get_json(url: str, params: dict, timeout: int, retries: int,
              base_sleep: float):
    headers = {"User-Agent": "market-data-hub/0.1", "Connection": "close"}
    last = None
    for attempt in range(retries):
        try:
            with requests.Session() as s:
                r = s.get(url, params=params, headers=headers, timeout=timeout)
                r.raise_for_status()
                return r.json()
        except Exception as e:
            last = e
            if attempt < retries - 1:
                time.sleep(base_sleep * (4 ** attempt))
    raise last


def _annual_date(period: str):
    """WDI annual: '2023' -> 2023-12-31."""
    try:
        return pd.Timestamp(int(str(period)[:4]), 12, 31).date()
    except Exception:
        return None


def fetch_worldbank(spec: Dict, countries: List[Dict], *,
                    start_year: int = 1990, end_year: Optional[int] = None,
                    timeout: int = 30, retries: int = 3, base_sleep: float = 1.0,
                    batch_size: int = 70) -> pd.DataFrame:
    """
    Download a WB indicator for ALL requested countries.

    The World Bank API accepts multiple countries in a single call
    (country/USA;ITA;BRA/...), so we make 1-2 calls per indicator instead of one
    per country: a huge latency reduction behind slow proxies.
    """
    end_year = end_year or datetime.now().year
    code = spec["code"]
    # map wb_code -> iso3 to reassign countries in the response
    wb_to_iso3 = {(c.get("wb") or c["iso3"]): c["iso3"] for c in countries}
    wb_codes = list(wb_to_iso3.keys())
    rows = []

    # batch of countries (the URL has a length limit; 50 is safe)
    for i in range(0, len(wb_codes), batch_size):
        chunk = wb_codes[i:i + batch_size]
        country_path = ";".join(chunk)
        page = 1
        while True:
            params = {"format": "json", "per_page": 20000, "page": page,
                      "date": f"{start_year}:{end_year}"}
            if spec.get("api_source_id"):
                params["source"] = spec["api_source_id"]
            url = f"{_BASE}/country/{country_path}/indicator/{code}"
            # A page failure (after retries) must propagate: swallowing it here
            # would return a silently-truncated frame that the runner logs as
            # "ok" and that blocks the fallback source from ever being tried.
            data = _get_json(url, params, timeout, retries, base_sleep)
            if not (isinstance(data, list) and len(data) >= 2 and data[1]):
                break
            header = data[0] if isinstance(data[0], dict) else {}
            for obs in data[1]:
                val = obs.get("value")
                if val is None:
                    continue
                d = _annual_date(obs.get("date"))
                if d is None:
                    continue
                iso = obs.get("countryiso3code") or ""
                iso3 = wb_to_iso3.get(iso, iso)
                rows.append({
                    "date": d, "country_iso3": iso3,
                    "indicator_id": spec["id"], "value": float(val),
                    "indicator_name": spec["name"], "pillar": spec["pillar"],
                    "orientation": spec.get("orientation", 0),
                    "source": "worldbank", "provider_dataset": spec["dataset"],
                    "provider_code": code, "unit": spec.get("unit"),
                    "frequency": spec.get("freq", "A"), "status": "ok",
                })
            # pagination
            pages = int(header.get("pages", 1) or 1)
            if page >= pages:
                break
            page += 1

    if not rows:
        return pd.DataFrame(columns=_COLS)
    # some countries may be missing from the batch: keep only the requested ones
    valid = {c["iso3"] for c in countries}
    df = pd.DataFrame(rows)
    df = df[df["country_iso3"].isin(valid)]
    return df[_COLS] if not df.empty else pd.DataFrame(columns=_COLS)
