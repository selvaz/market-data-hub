# -*- coding: utf-8 -*-
"""
imf_sdmx.py — IMF SDMX 3.0 data API (api.imf.org) for the cross-country panel.

Different from imf.py (the WEO DataMapper): this speaks the full IMF SDMX 3.0
service, which carries the MFS (monetary & financial statistics), IIP/BOP, IRFCL
and IIPCC dataflows — the euro area aside (that reports to the ECB, see ecb.py).

    https://api.imf.org/external/sdmx/3.0/data/dataflow/IMF.STA/{FLOW}/+/{KEY}

Two facts drive the design:
  - COUNTRY = ISO3 (matches our panel; reuse ci['imf'] == iso3), no transcoding.
  - the service does NOT honour dimension wildcards, so the key must be fully
    specified and we loop one call per country (like FRED).

The spec's `code` is the FULL SDMX key with an `{iso3}` placeholder, e.g.
`{iso3}.MFS166_RT_PT_A_PT.M` (MFS_IR); the dot-order is that dataflow's DSD
dimension order. Confirmed working:
  - MFS_IR  policy rate  {iso3}.MFS166_RT_PT_A_PT.M

Canonical output for macro_panel (same columns as worldbank.py/imf.py/bis.py).
"""
from __future__ import annotations

import csv
import io
import logging
import time
from typing import Dict, List, Optional

import pandas as pd
import requests

log = logging.getLogger(__name__)

_BASE = "https://api.imf.org/external/sdmx/3.0/data/dataflow/IMF.STA"

_COLS = ["date", "country_iso3", "indicator_id", "value", "indicator_name",
         "pillar", "orientation", "source", "provider_dataset",
         "provider_code", "unit", "frequency", "status"]


def _period_end(period: str) -> Optional[pd.Timestamp]:
    """IMF SDMX periods: '2025-M06' (month), '2025-Q2' (quarter), '2024' (year)."""
    try:
        if "-M" in period:
            y, m = period.split("-M")
            return pd.Period(f"{y}-{int(m):02d}", freq="M").end_time.normalize()
        if "-Q" in period:
            y, q = period.split("-Q")
            return pd.Period(f"{y}Q{q}", freq="Q").end_time.normalize()
        if len(period) == 4 and period.isdigit():
            return pd.Timestamp(int(period), 12, 31)
        return pd.Timestamp(period).normalize()
    except Exception:
        return None


def _get_csv(url: str, timeout: int, retries: int, base_sleep: float) -> Optional[str]:
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=timeout,
                             headers={"Accept": "application/vnd.sdmx.data+csv"})
            if r.status_code in (404, 204):    # no data for this key: not an error
                return None
            r.raise_for_status()
            return r.text
        except Exception as e:
            last = e
            if attempt < retries - 1:
                time.sleep(base_sleep * (2 ** attempt))
    if last:
        raise last
    return None


def fetch_imf_sdmx(spec: Dict, countries: List[Dict], *,
                   start_year: int = 1990, timeout: int = 60,
                   retries: int = 3, base_sleep: float = 0.3) -> pd.DataFrame:
    """Download an IMF SDMX 3.0 indicator, one call per country (no wildcards).

    The spec must contain:
      dataset : dataflow id (e.g. 'MFS_IR', 'IIP', 'MFS_CBS')
      code    : full SDMX key with an {iso3} placeholder
                (e.g. '{iso3}.MFS166_RT_PT_A_PT.M')
    """
    dataset = spec.get("dataset")
    key_tpl = spec.get("code", "")
    if not dataset or "{iso3}" not in key_tpl:
        return pd.DataFrame(columns=_COLS)
    freq = spec.get("freq", "M")

    rows = []
    for c in countries:
        iso3 = c.get("imf") or c["iso3"]
        key = key_tpl.replace("{iso3}", iso3)
        url = f"{_BASE}/{dataset}/+/{key}?startPeriod={start_year}"
        try:
            text = _get_csv(url, timeout, retries, base_sleep)
        except Exception as e:
            # exhausted retries: without this line a total fetch failure is
            # indistinguishable from "no data for this key" (404/204 -> None)
            log.warning("imf_sdmx: %s (%s) fetch failed for %s after retries: %s",
                        spec.get("id"), spec.get("name"), iso3, e)
            continue
        if not text:
            continue
        for rec in csv.DictReader(io.StringIO(text)):
            # the service prepends one metadata row with blank dimension fields
            if rec.get("COUNTRY") != iso3:
                continue
            per = rec.get("TIME_PERIOD")
            val = pd.to_numeric(rec.get("OBS_VALUE"), errors="coerce")
            if not per or pd.isna(val):
                continue
            # SDMX scale metadata: OBS_VALUE is expressed in 10**UNIT_MULT
            # units (e.g. 6 = millions). Absent column / non-numeric cell ->
            # value stored as-is. NOTE: rows fetched before this fix may need
            # a re-fetch if the feed carries a non-zero multiplier.
            mult = pd.to_numeric(rec.get("UNIT_MULT", rec.get("UNIT_MULTIPLIER")),
                                 errors="coerce")
            if not pd.isna(mult):
                val = val * 10.0 ** float(mult)
            dt = _period_end(per)
            if dt is None or dt.year < start_year:
                continue
            rows.append({
                "date": dt.date(), "country_iso3": iso3,
                "indicator_id": spec["id"], "value": float(val),
                "indicator_name": spec["name"], "pillar": spec["pillar"],
                "orientation": spec.get("orientation", 0), "source": "imf_sdmx",
                "provider_dataset": dataset, "provider_code": key_tpl,
                "unit": spec.get("unit"), "frequency": freq, "status": "ok",
            })
        time.sleep(base_sleep)   # courtesy pause between countries

    if not rows:
        return pd.DataFrame(columns=_COLS)
    return pd.DataFrame(rows)[_COLS]
