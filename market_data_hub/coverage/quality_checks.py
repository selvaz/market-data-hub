# -*- coding: utf-8 -*-
"""
quality_checks.py — quality checks on a batch of data before the upsert.

Ports the defensive patterns present in all the projects:
  - drop NaN dates, dedup on the temporal key (checks1_improved.merge_dedup)
  - smart merge keeping the last value
  - flag zero/negative prices
  - check for an anomalous adj_close/close ratio (split not applied)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class QualityFlags:
    has_zero_price: bool = False
    has_negative: bool = False
    adj_ratio_anomaly: bool = False


def clean_price_frame(df: pd.DataFrame, date_col: str = "date") -> pd.DataFrame:
    """
    Standard cleaning of an OHLCV/series frame:
      - parse dates, drop NaN on date_col
      - sort and deduplicate on the date keeping the last row
    """
    if df is None or df.empty:
        return df
    out = df.copy()
    out[date_col] = pd.to_datetime(out[date_col], errors="coerce")
    out = (out.dropna(subset=[date_col])
              .sort_values(date_col)
              .drop_duplicates(subset=[date_col], keep="last")
              .reset_index(drop=True))
    return out


def check_prices(df: pd.DataFrame) -> QualityFlags:
    """Quality flags on a frame with close/adj_close columns (if present)."""
    flags = QualityFlags()
    if df is None or df.empty:
        return flags

    price_cols = [c for c in ("open", "high", "low", "close", "adj_close", "value")
                  if c in df.columns]
    if not price_cols:
        return flags

    vals = df[price_cols].apply(pd.to_numeric, errors="coerce")
    flags.has_zero_price = bool((vals == 0).any().any())
    flags.has_negative = bool((vals < 0).any().any())

    # adj/close ratio: should stay near 1 in the short term; huge jumps = suspect
    if "adj_close" in df.columns and "close" in df.columns:
        c = pd.to_numeric(df["close"], errors="coerce")
        a = pd.to_numeric(df["adj_close"], errors="coerce")
        ratio = (a / c).replace([np.inf, -np.inf], np.nan).dropna()
        if len(ratio) > 5:
            # day-over-day change in the ratio above 50% = anomaly
            chg = ratio.pct_change().abs()
            flags.adj_ratio_anomaly = bool((chg > 0.5).any())

    return flags
