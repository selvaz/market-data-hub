# -*- coding: utf-8 -*-
"""
bis.py — BIS source (native stats.bis.org v2 API) for the cross-country panel.

We use the NATIVE BIS API (not the DBnomics mirror, which is ~1 year behind):
  https://stats.bis.org/api/v2/data/dataflow/BIS/{dataset}/1.0/{key}?format=csv
The SDMX key with an empty country position = wildcard "all countries" in a
single call (e.g. 'Q..P' = all countries, private sector). We then map the BIS
ISO2 to our ISO3 and filter our countries.

Key series for the debt cycle (Dalio method):
  - WS_DSR        : private debt service ratio          key Q.{iso2}.P
  - WS_CREDIT_GAP : private credit-to-GDP gap (HP)       key Q.{iso2}.P.A.C
  - WS_CBPOL      : central bank policy rate             key M.{iso2}

Canonical output for macro_panel (same columns as worldbank.py/imf.py).
"""
from __future__ import annotations

import csv
import io
import time
from typing import Dict, List, Optional

import pandas as pd
import requests

_BASE = "https://stats.bis.org/api/v2/data/dataflow/BIS"

_COLS = ["date", "country_iso3", "indicator_id", "value", "indicator_name",
         "pillar", "orientation", "source", "provider_dataset",
         "provider_code", "unit", "frequency", "status"]


def _period_end(period: str) -> Optional[pd.Timestamp]:
    """'2025-Q4' (quarter), '2026-05' (month), '2025' (year) -> end of period."""
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
            r = requests.get(url, timeout=timeout)
            r.raise_for_status()
            return r.text
        except Exception as e:
            last = e
            if attempt < retries - 1:
                time.sleep(base_sleep * (2 ** attempt))
    if last:
        raise last
    return None


def fetch_bis(spec: Dict, countries: List[Dict], *,
              start_year: int = 1990, timeout: int = 60,
              retries: int = 3, base_sleep: float = 1.0) -> pd.DataFrame:
    """Download a BIS indicator (native API) for the requested countries.

    The spec must contain:
      dataset         : 'WS_DSR' | 'WS_CREDIT_GAP' | 'WS_CBPOL'
      code            : SDMX key with {iso2} (e.g. 'Q.{iso2}.P'); {iso2} is
                        emptied to fetch all countries in one call
      bis_country_dim : CSV column with the country code
                        (BORROWERS_CTY for DSR/gap, REF_AREA for policy)
    """
    dataset = spec.get("dataset")
    key_tpl = spec.get("code", "")
    if not dataset or "{iso2}" not in key_tpl:
        return pd.DataFrame(columns=_COLS)

    cty_dim = spec.get("bis_country_dim", "BORROWERS_CTY")
    freq = spec.get("freq", "Q")

    # BIS ISO2 -> our ISO3
    iso2_to_iso3 = {c["iso2"]: c["iso3"] for c in countries if c.get("iso2")}
    wanted = set(iso2_to_iso3)

    # Euro-area broadcast: euro members do not have an individual policy rate
    # (the ECB's applies). If spec['euro_aggregate'] is set (e.g. 'XM'), the
    # observations of that BIS code are replicated across all our countries with
    # euro=True. Zero new sources: it is the same WS_CBPOL.
    euro_agg = spec.get("euro_aggregate")
    euro_members = [c["iso3"] for c in countries if c.get("euro")]
    agg_obs = []  # (date, value) of the aggregate

    # wildcard key: empty country position = all countries
    wild_key = key_tpl.replace("{iso2}", "")
    # startPeriod bounds the response server-side. Without it a voluminous
    # monthly dataflow (e.g. WS_EER, monthly since 1994 x 64 economies) is
    # truncated by the API to an alphabetical slice of countries; passing the
    # same start_year we already filter on client-side returns every country.
    url = f"{_BASE}/{dataset}/1.0/{wild_key}?startPeriod={start_year}&format=csv"
    try:
        text = _get_csv(url, timeout, retries, base_sleep)
    except Exception:
        return pd.DataFrame(columns=_COLS)
    if not text:
        return pd.DataFrame(columns=_COLS)

    def _mkrow(dt, iso3, val):
        return {
            "date": dt.date(), "country_iso3": iso3,
            "indicator_id": spec["id"], "value": float(val),
            "indicator_name": spec["name"], "pillar": spec["pillar"],
            "orientation": spec.get("orientation", 0), "source": "bis",
            "provider_dataset": dataset, "provider_code": key_tpl,
            "unit": spec.get("unit"), "frequency": freq, "status": "ok",
        }

    rows = []
    for rec in csv.DictReader(io.StringIO(text)):
        iso2 = rec.get(cty_dim)
        per = rec.get("TIME_PERIOD")
        val = pd.to_numeric(rec.get("OBS_VALUE"), errors="coerce")
        if not per or pd.isna(val):
            continue
        dt = _period_end(per)
        if dt is None or dt.year < start_year:
            continue
        if euro_agg and iso2 == euro_agg:
            agg_obs.append((dt, float(val)))   # capture euro aggregate
        if iso2 not in wanted:
            continue
        rows.append(_mkrow(dt, iso2_to_iso3[iso2], val))

    # Replicate the euro aggregate onto each euro member, but only for
    # (date, member) pairs with no own observation: some members carry their
    # own national series at BIS for pre-adoption years (HRV until 2022, GRC
    # in 2000), and an unconditional broadcast would emit a duplicate primary
    # key for those dates -- INSERT OR REPLACE then keeps whichever lands
    # last (the aggregate), silently replacing genuine national history with
    # the ECB rate.
    if euro_agg and agg_obs and euro_members:
        own = {(row["date"], row["country_iso3"]) for row in rows}
        for iso3 in euro_members:
            for dt, val in agg_obs:
                if (dt.date(), iso3) not in own:
                    rows.append(_mkrow(dt, iso3, val))

    if not rows:
        return pd.DataFrame(columns=_COLS)
    return pd.DataFrame(rows)[_COLS]
