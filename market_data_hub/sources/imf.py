# -*- coding: utf-8 -*-
"""
imf.py — IMF DataMapper (WEO) source for the cross-country panel.

Ports fetch_imf_datamapper() from macro_dashboard_v2_bundle. A single REST call
returns ALL countries for an indicator, so it is very efficient: we then filter
on our list. All WEO indicators are annual.

Canonical output for macro_panel (same columns as worldbank.py).
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd
import requests

_BASE = "https://www.imf.org/external/datamapper/api/v1"

_COLS = ["date", "country_iso3", "indicator_id", "value", "indicator_name",
         "pillar", "orientation", "source", "provider_dataset",
         "provider_code", "unit", "frequency", "status"]

# IMPORTANT about the IMF's Akamai WAF (verified empirically):
#   - custom UA like "market-data-hub/0.1"  -> 403
#   - spoofed browser UA (Mozilla/Chrome) -> 403 ("browser" UA + python's TLS
#     fingerprint = bot signature, Akamai blocks it)
#   - NO custom header (python-requests default UA) -> 200  <-- use this
# Exactly the same approach as the FRONTIER/new_approach project (runs without
# issues). requests respects REQUESTS_CA_BUNDLE for the MITM network.


def _get_json(url: str, timeout: int, retries: int, base_sleep: float):
    # 403 = Akamai WAF/rate-limit. With requests' default UA the UA is not the
    # problem; a residual 403 indicates a temporary rate-limit -> longer wait.
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=timeout)  # no headers: default UA
            if r.status_code == 403:
                raise requests.HTTPError("403 WAF/rate-limit", response=r)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            if attempt < retries - 1:
                is_403 = isinstance(e, requests.HTTPError) and \
                    getattr(e, "response", None) is not None and \
                    e.response.status_code == 403
                wait = (10.0 + 5 * attempt) if is_403 else base_sleep * (2 ** attempt)
                time.sleep(wait)
    # retries<=0 means the loop above never ran, leaving `last` unset --
    # raising it directly would then raise None instead of a real error.
    raise last if last else RuntimeError(f"imf: no attempts made (retries={retries})")


def fetch_imf(spec: Dict, countries: List[Dict], *,
              start_year: int = 1990, end_year: Optional[int] = None,
              timeout: int = 30, retries: int = 3, base_sleep: float = 1.0
              ) -> pd.DataFrame:
    """Download a WEO indicator for the requested countries. Returns a macro_panel frame."""
    # The WEO includes projections ~5-6 years beyond the current year: do NOT
    # truncate at the current year (we would lose the forecasts, needed for
    # Dalio's forward-looking view). Default: current year + 6 (the WEO
    # self-limits to its own horizon).
    end_year = end_year or (datetime.now().year + 6)
    code = spec["code"]
    # A single {base}/{code} call returns ALL countries (226): we filter ours
    # client-side. Do NOT put the country list in the path: it is unreliable
    # (the Akamai WAF blocks it, and with many countries the URL 404s). Same
    # pattern as the FRONTIER project ("country filtering in URL is unreliable").
    url = f"{_BASE}/{code}"
    try:
        data = _get_json(url, timeout, retries, base_sleep)
    except Exception:
        return pd.DataFrame(columns=_COLS)

    series = (data or {}).get("values", {}).get(code, {})
    if not isinstance(series, dict):
        return pd.DataFrame(columns=_COLS)

    rows = []
    for ci in countries:
        imf_code = ci.get("imf") or ci["iso3"]
        cdata = series.get(imf_code, {})
        if not isinstance(cdata, dict):
            continue
        for yr, val in cdata.items():
            if val is None:
                continue
            try:
                y = int(yr)
            except Exception:
                continue
            if y < start_year or y > end_year:
                continue
            rows.append({
                "date": pd.Timestamp(y, 12, 31).date(),
                "country_iso3": ci["iso3"], "indicator_id": spec["id"],
                "value": float(val), "indicator_name": spec["name"],
                "pillar": spec["pillar"], "orientation": spec.get("orientation", 0),
                "source": "imf", "provider_dataset": spec["dataset"],
                "provider_code": code, "unit": spec.get("unit"),
                "frequency": "A", "status": "ok",
            })

    if not rows:
        return pd.DataFrame(columns=_COLS)
    return pd.DataFrame(rows)[_COLS]
