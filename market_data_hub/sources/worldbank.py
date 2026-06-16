# -*- coding: utf-8 -*-
"""
worldbank.py — sorgente World Bank (WDI / WGI / IDS) per il panel cross-country.

Porta fetch_worldbank() da macro_dashboard_v2_bundle. Una chiamata REST per
(indicatore, paese). api_source_id seleziona il database WB: 2=WDI, 3=WGI.

Output canonico per macro_panel:
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
    Scarica un indicatore WB per TUTTI i paesi richiesti.

    L'API World Bank accetta piu' paesi in una sola chiamata
    (country/USA;ITA;BRA/...), quindi facciamo 1-2 chiamate per indicatore
    invece di una per paese: enorme riduzione di latenza dietro proxy lenti.
    """
    end_year = end_year or datetime.now().year
    code = spec["code"]
    # mappa wb_code -> iso3 per riassegnare i paesi nella risposta
    wb_to_iso3 = {(c.get("wb") or c["iso3"]): c["iso3"] for c in countries}
    wb_codes = list(wb_to_iso3.keys())
    rows = []

    # batch di paesi (l'URL ha un limite di lunghezza; 50 e' sicuro)
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
            try:
                data = _get_json(url, params, timeout, retries, base_sleep)
            except Exception:
                break
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
            # paginazione
            pages = int(header.get("pages", 1) or 1)
            if page >= pages:
                break
            page += 1

    if not rows:
        return pd.DataFrame(columns=_COLS)
    # alcuni paesi possono mancare nel batch: teniamo solo quelli richiesti
    valid = {c["iso3"] for c in countries}
    df = pd.DataFrame(rows)
    df = df[df["country_iso3"].isin(valid)]
    return df[_COLS] if not df.empty else pd.DataFrame(columns=_COLS)
