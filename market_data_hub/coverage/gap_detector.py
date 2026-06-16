# -*- coding: utf-8 -*-
"""
gap_detector.py — rilevamento buchi e percentuale di osservazioni mancanti.

Concetto portato da:
  - crypto_ml_features :: identify_missing_periods()
  - macro_dashboard    :: _missing_pct_inside_span()

Per le serie daily il calcolo e' trading-day-aware: i weekend non contano come
gap (si usano i business day come baseline atteso).
"""
from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd

from market_data_hub.coverage.freq_detector import EXPECTED_SPACING_DAYS


def missing_pct(dates: pd.Series, freq: str) -> float:
    """
    Percentuale di osservazioni mancanti tra prima e ultima data, dato il
    passo atteso per la frequenza. 0.0 = nessun buco, 1.0 = tutto mancante.
    """
    d = pd.to_datetime(pd.Series(dates), errors="coerce").dropna().sort_values()
    d = pd.DatetimeIndex(d.unique())
    if len(d) < 2:
        return 0.0

    if freq == "D":
        # baseline: business days nell'intervallo
        expected = len(pd.bdate_range(d[0], d[-1]))
    else:
        step = EXPECTED_SPACING_DAYS.get(freq, 1)
        span_days = (d[-1] - d[0]).days
        expected = max(1, int(round(span_days / step)) + 1)

    actual = len(d)
    if expected <= 0:
        return 0.0
    return float(max(0.0, 1.0 - actual / expected))


def gap_count(dates: pd.Series, freq: str) -> int:
    """
    Numero di buchi (sequenze consecutive mancanti) nella serie.
    Per daily un gap e' un intervallo > 4 giorni di calendario tra osservazioni
    consecutive (tollera weekend lungo + festivita'); per le altre frequenze
    un delta > 2x il passo atteso.
    """
    d = pd.to_datetime(pd.Series(dates), errors="coerce").dropna().sort_values()
    d = pd.DatetimeIndex(d.unique())
    if len(d) < 2:
        return 0
    deltas = (d[1:] - d[:-1]).days
    if freq == "D":
        threshold = 4
    else:
        threshold = 2 * EXPECTED_SPACING_DAYS.get(freq, 1)
    return int(np.sum(np.asarray(deltas) > threshold))


def date_span(dates: pd.Series) -> Tuple[object, object, int]:
    """Ritorna (first_date, last_date, obs_count) gestendo serie vuote."""
    d = pd.to_datetime(pd.Series(dates), errors="coerce").dropna().sort_values()
    if d.empty:
        return None, None, 0
    return d.iloc[0].date(), d.iloc[-1].date(), int(len(d.unique()))
