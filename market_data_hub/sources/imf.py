# -*- coding: utf-8 -*-
"""
imf.py — sorgente IMF DataMapper (WEO) per il panel cross-country.

Porta fetch_imf_datamapper() da macro_dashboard_v2_bundle. Una sola chiamata
REST restituisce TUTTI i paesi per un indicatore, quindi e' molto efficiente:
filtriamo poi sulla nostra lista. Tutti gli indicatori WEO sono annuali.

Output canonico per macro_panel (stesse colonne di worldbank.py).
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

# IMPORTANTE sul WAF Akamai dell'IMF (verificato empiricamente):
#   - UA custom tipo "market-data-hub/0.1"  -> 403
#   - UA browser falsificato (Mozilla/Chrome) -> 403 (UA "browser" + fingerprint
#     TLS di python = firma da bot, Akamai lo blocca)
#   - NESSUN header custom (UA default di python-requests) -> 200  <-- usare questo
# Stesso identico approccio del progetto FRONTIER/new_approach (gira senza
# problemi). requests rispetta REQUESTS_CA_BUNDLE per la rete MITM.


def _get_json(url: str, timeout: int, retries: int, base_sleep: float):
    # 403 = WAF/rate-limit Akamai. Con lo UA default di requests non e' lo UA il
    # problema; un 403 residuo indica rate-limit temporaneo -> attesa piu' lunga.
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=timeout)  # nessun header: UA default
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
    raise last


def fetch_imf(spec: Dict, countries: List[Dict], *,
              start_year: int = 1990, end_year: Optional[int] = None,
              timeout: int = 30, retries: int = 3, base_sleep: float = 1.0
              ) -> pd.DataFrame:
    """Scarica un indicatore WEO per i paesi richiesti. Ritorna frame macro_panel."""
    # Il WEO include proiezioni ~5-6 anni oltre l'anno corrente: NON troncare
    # all'anno corrente (perderemmo i forecast, che servono al forward-looking
    # di Dalio). Default: anno corrente + 6 (il WEO si auto-limita al suo orizzonte).
    end_year = end_year or (datetime.now().year + 6)
    code = spec["code"]
    # Una sola chiamata {base}/{code} ritorna TUTTI i paesi (226): filtriamo i
    # nostri lato client. NON mettere la lista paesi nel path: e' inaffidabile
    # (il WAF Akamai blocca, e con molti paesi l'URL va in 404). Stesso pattern
    # del progetto FRONTIER ("country filtering in URL is unreliable").
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
