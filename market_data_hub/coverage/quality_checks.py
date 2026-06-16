# -*- coding: utf-8 -*-
"""
quality_checks.py — controlli di qualita' su un batch di dati prima dell'upsert.

Porta i pattern difensivi presenti in tutti i progetti:
  - drop date NaN, dedup su chiave temporale (checks1_improved.merge_dedup)
  - smart merge tenendo l'ultimo valore
  - flag prezzi zero/negativi
  - check ratio adj_close/close anomalo (split non applicato)
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
    Pulizia standard di un frame OHLCV/serie:
      - parse date, drop NaN su date_col
      - ordina e deduplica sulla data tenendo l'ultima riga
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
    """Flag di qualita' su un frame con colonne close/adj_close (se presenti)."""
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

    # ratio adj/close: deve restare vicino a 1 nel breve; salti enormi = sospetto
    if "adj_close" in df.columns and "close" in df.columns:
        c = pd.to_numeric(df["close"], errors="coerce")
        a = pd.to_numeric(df["adj_close"], errors="coerce")
        ratio = (a / c).replace([np.inf, -np.inf], np.nan).dropna()
        if len(ratio) > 5:
            # variazione giorno-su-giorno del ratio oltre 50% = anomalia
            chg = ratio.pct_change().abs()
            flags.adj_ratio_anomaly = bool((chg > 0.5).any())

    return flags
