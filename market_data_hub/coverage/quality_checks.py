# -*- coding: utf-8 -*-
"""
quality_checks.py — price-quality flags consumed by coverage/report.py
(zero and negative prices; see QualityFlags).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class QualityFlags:
    has_zero_price: bool = False
    has_negative: bool = False


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

    return flags
