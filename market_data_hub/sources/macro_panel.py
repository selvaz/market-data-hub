# -*- coding: utf-8 -*-
"""
macro_panel.py — fetch a cross-country indicator with primary→fallback logic.

Abstracts over worldbank.py and imf.py:
  - tries the spec's primary source
  - if empty and a fallback exists, tries the fallback
Returns (canonical macro_panel DataFrame, source_used, status).
"""
from __future__ import annotations

from typing import Dict, List, Tuple

import pandas as pd

from market_data_hub.sources import worldbank as wb
from market_data_hub.sources import imf as im
from market_data_hub.sources import bis as bs


def _fetch_one(source: str, spec: Dict, countries: List[Dict], *,
               start_year: int, http: Dict) -> pd.DataFrame:
    if source == "IMF":
        return im.fetch_imf(spec, countries, start_year=start_year,
                            timeout=http["timeout"], retries=http["max_retries"],
                            base_sleep=http["retry_base_sleep"])
    if source == "BIS":
        return bs.fetch_bis(spec, countries, start_year=start_year,
                            timeout=http["timeout"], retries=http["max_retries"],
                            base_sleep=http["retry_base_sleep"])
    return wb.fetch_worldbank(spec, countries, start_year=start_year,
                             timeout=http["timeout"], retries=http["max_retries"],
                             base_sleep=http["retry_base_sleep"])


def _country_coverage(df: pd.DataFrame) -> int:
    """Distinct countries with a non-null value — proxy for cross-country coverage."""
    if df is None or df.empty:
        return 0
    d = df[df["value"].notna()] if "value" in df.columns else df
    return int(d["country_iso3"].nunique())


def fetch_indicator(spec: Dict, countries: List[Dict], *, start_year: int,
                    http: Dict, select_best: bool = False
                    ) -> Tuple[pd.DataFrame, str, str]:
    """Download an indicator with fallback. Returns (df, source_used, status).

    Default: try primary; if empty and a fallback exists, use the fallback.
    select_best=True: when a fallback exists, fetch BOTH and keep whichever covers
    more countries (ports the "select best source" idea; costs one extra call per
    indicator that has a fallback).
    """
    df = _fetch_one(spec["source"], spec, countries, start_year=start_year, http=http)
    fb = spec.get("fallback")

    if not select_best:
        if not df.empty:
            return df, spec["source"], "ok"
        if fb:
            # the fallback spec inherits id/name/pillar/orientation/unit/freq
            fbspec = {**spec, **fb}
            fdf = _fetch_one(fb["source"], fbspec, countries, start_year=start_year, http=http)
            if not fdf.empty:
                return fdf, f"{fb['source']}(fallback)", "fallback"
        return df, spec["source"], "empty"

    # select_best: compare cross-country coverage of primary vs fallback
    if not fb:
        return (df, spec["source"], "ok") if not df.empty else (df, spec["source"], "empty")
    fdf = _fetch_one(fb["source"], {**spec, **fb}, countries, start_year=start_year, http=http)
    if df.empty and fdf.empty:
        return df, spec["source"], "empty"
    if _country_coverage(fdf) > _country_coverage(df):
        return fdf, f"{fb['source']}(fallback)", "fallback"
    return df, spec["source"], "ok"
