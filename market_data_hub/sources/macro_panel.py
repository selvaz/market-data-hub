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


def fetch_indicator(spec: Dict, countries: List[Dict], *, start_year: int,
                    http: Dict) -> Tuple[pd.DataFrame, str, str]:
    """Download an indicator with fallback. Returns (df, source_used, status)."""
    df = _fetch_one(spec["source"], spec, countries, start_year=start_year, http=http)
    if not df.empty:
        return df, spec["source"], "ok"

    fb = spec.get("fallback")
    if fb:
        # the fallback spec inherits id/name/pillar/orientation/unit/freq
        fbspec = {**spec, **fb}
        fdf = _fetch_one(fb["source"], fbspec, countries, start_year=start_year, http=http)
        if not fdf.empty:
            return fdf, f"{fb['source']}(fallback)", "fallback"

    return df, spec["source"], "empty"
