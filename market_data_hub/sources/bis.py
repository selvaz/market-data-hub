# -*- coding: utf-8 -*-
"""
bis.py — sorgente BIS (API nativa stats.bis.org v2) per il panel cross-country.

Si usa l'API NATIVA BIS (non il mirror DBnomics, che e' ~1 anno indietro):
  https://stats.bis.org/api/v2/data/dataflow/BIS/{dataset}/1.0/{key}?format=csv
La chiave SDMX con la posizione-paese vuota = wildcard "tutti i paesi" in una
sola chiamata (es. 'Q..P' = tutti i paesi, settore privato). Si mappa poi
l'ISO2 BIS sul nostro ISO3 e si filtrano i nostri paesi.

Serie chiave per il ciclo del debito (metodo Dalio):
  - WS_DSR        : debt service ratio privato        key Q.{iso2}.P
  - WS_CREDIT_GAP : credit-to-GDP gap privato (HP)     key Q.{iso2}.P.A.C
  - WS_CBPOL      : tasso di policy banca centrale      key M.{iso2}

Output canonico per macro_panel (stesse colonne di worldbank.py/imf.py).
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
    """'2025-Q4' (trimestre), '2026-05' (mese), '2025' (anno) -> fine periodo."""
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
    """Scarica un indicatore BIS (API nativa) per i paesi richiesti.

    Lo spec deve contenere:
      dataset         : 'WS_DSR' | 'WS_CREDIT_GAP' | 'WS_CBPOL'
      code            : chiave SDMX con {iso2} (es. 'Q.{iso2}.P'); {iso2} viene
                        svuotato per fetchare tutti i paesi in una chiamata
      bis_country_dim : colonna CSV con il codice paese
                        (BORROWERS_CTY per DSR/gap, REF_AREA per policy)
    """
    dataset = spec.get("dataset")
    key_tpl = spec.get("code", "")
    if not dataset or "{iso2}" not in key_tpl:
        return pd.DataFrame(columns=_COLS)

    cty_dim = spec.get("bis_country_dim", "BORROWERS_CTY")
    freq = spec.get("freq", "Q")

    # ISO2 BIS -> nostro ISO3
    iso2_to_iso3 = {c["iso2"]: c["iso3"] for c in countries if c.get("iso2")}
    wanted = set(iso2_to_iso3)

    # Broadcast area euro: i membri euro non hanno un policy rate individuale
    # (vale quello ECB). Se spec['euro_aggregate'] e' valorizzato (es. 'XM'),
    # le osservazioni di quel codice BIS vengono replicate su tutti i nostri
    # paesi con euro=True. Zero nuove fonti: e' lo stesso WS_CBPOL.
    euro_agg = spec.get("euro_aggregate")
    euro_members = [c["iso3"] for c in countries if c.get("euro")]
    agg_obs = []  # (date, value) dell'aggregato

    # chiave wildcard: posizione-paese vuota = tutti i paesi
    wild_key = key_tpl.replace("{iso2}", "")
    url = f"{_BASE}/{dataset}/1.0/{wild_key}?format=csv"
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
            agg_obs.append((dt, float(val)))   # cattura aggregato euro
        if iso2 not in wanted:
            continue
        rows.append(_mkrow(dt, iso2_to_iso3[iso2], val))

    # replica l'aggregato euro su ogni membro euro privo di serie propria
    if euro_agg and agg_obs and euro_members:
        for iso3 in euro_members:
            for dt, val in agg_obs:
                rows.append(_mkrow(dt, iso3, val))

    if not rows:
        return pd.DataFrame(columns=_COLS)
    return pd.DataFrame(rows)[_COLS]
