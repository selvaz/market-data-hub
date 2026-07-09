# -*- coding: utf-8 -*-
"""
ecb.py — ECB Data Portal (SDMX 2.1 REST) source for the cross-country panel.

The euro area reports its bank/monetary statistics to the ECB, not to the IMF
(verified: euro members return empty on IMF MFS). The ECB SDMX service supports
wildcards, so a single call with an empty REF_AREA position returns every
country for a series (like the BIS native API). We map the ECB ISO2 to our ISO3.

    https://data-api.ecb.europa.eu/service/data/{flow}/{key}?format=csvdata

Key series for the euro-area rate gap (Dalio: cost of credit):
  - MIR  Cost of borrowing, corporations       M.{iso2}.B.A2I.AM.R.A.2240.EUR.N
  - MIR  Cost of borrowing, households (house)  M.{iso2}.B.A2C.AM.R.A.2250.EUR.N

Canonical output for macro_panel (same columns as worldbank.py/imf.py/bis.py).
"""
from __future__ import annotations

import csv
import io
import time
from typing import Dict, List, Optional

import pandas as pd
import requests

_BASE = "https://data-api.ecb.europa.eu/service/data"

_COLS = ["date", "country_iso3", "indicator_id", "value", "indicator_name",
         "pillar", "orientation", "source", "provider_dataset",
         "provider_code", "unit", "frequency", "status"]


def _period_end(period: str) -> Optional[pd.Timestamp]:
    """'2026-05' (month), '2026-Q1' (quarter), '2026' (year) -> end of period."""
    try:
        if "Q" in period:
            y, q = period.split("-Q")
            return pd.Period(f"{y}Q{q}", freq="Q").end_time.normalize()
        if "-" in period:
            return pd.Period(period, freq="M").end_time.normalize()
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
                             headers={"Accept": "text/csv"})
            if r.status_code == 404:      # no data for this key -> not an error
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


def fetch_ecb(spec: Dict, countries: List[Dict], *,
              start_year: int = 1990, timeout: int = 60,
              retries: int = 3, base_sleep: float = 1.0) -> pd.DataFrame:
    """Download an ECB indicator (SDMX 2.1) for the requested countries.

    The spec must contain:
      dataset         : dataflow ref (e.g. 'MIR')
      code            : SDMX key with {iso2} (e.g. 'M.{iso2}.B.A2I.AM.R.A.2240.EUR.N');
                        {iso2} is emptied to fetch all countries in one call
      ecb_country_dim : CSV column holding the country code (default 'REF_AREA')
    """
    dataset = spec.get("dataset")
    key_tpl = spec.get("code", "")
    if not dataset or "{iso2}" not in key_tpl:
        return pd.DataFrame(columns=_COLS)

    cty_dim = spec.get("ecb_country_dim", "REF_AREA")
    freq = spec.get("freq", "M")

    iso2_to_iso3 = {c["iso2"]: c["iso3"] for c in countries if c.get("iso2")}
    wanted = set(iso2_to_iso3)

    wild_key = key_tpl.replace("{iso2}", "")
    url = (f"{_BASE}/{dataset}/{wild_key}"
           f"?startPeriod={start_year}-01&format=csvdata")
    try:
        text = _get_csv(url, timeout, retries, base_sleep)
    except Exception:
        return pd.DataFrame(columns=_COLS)
    if not text:
        return pd.DataFrame(columns=_COLS)

    rows = []
    for rec in csv.DictReader(io.StringIO(text)):
        iso2 = rec.get(cty_dim)
        if iso2 not in wanted:
            continue
        per = rec.get("TIME_PERIOD")
        val = pd.to_numeric(rec.get("OBS_VALUE"), errors="coerce")
        if not per or pd.isna(val):
            continue
        dt = _period_end(per)
        if dt is None or dt.year < start_year:
            continue
        rows.append({
            "date": dt.date(), "country_iso3": iso2_to_iso3[iso2],
            "indicator_id": spec["id"], "value": float(val),
            "indicator_name": spec["name"], "pillar": spec["pillar"],
            "orientation": spec.get("orientation", 0), "source": "ecb",
            "provider_dataset": dataset, "provider_code": key_tpl,
            "unit": spec.get("unit"), "frequency": freq, "status": "ok",
        })

    if not rows:
        return pd.DataFrame(columns=_COLS)
    return pd.DataFrame(rows)[_COLS]
