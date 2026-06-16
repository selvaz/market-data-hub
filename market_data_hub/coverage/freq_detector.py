# -*- coding: utf-8 -*-
"""
freq_detector.py — inferenza della frequenza di una serie temporale.

Porta la logica di:
  - quant_timeseries_suite/checks1_improved.py :: guess_freq()
  - macro_dashboard_v2_bundle/macro_dashboard.py :: detect_frequency()

Restituisce un codice canonico: 'D' | 'W' | 'M' | 'Q' | 'A' | 'irregular_Xd' | 'UNKNOWN'
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def detect_frequency(dates) -> str:
    """Inferisce D/W/M/Q/A dallo spacing mediano delle date (pandas 3.x safe)."""
    ds = (pd.to_datetime(pd.Series(list(dates)), errors="coerce")
          .dropna().sort_values().unique())
    if len(ds) < 2:
        return "UNKNOWN"
    deltas = np.diff(ds).astype("timedelta64[D]").astype(int)
    med = float(np.median(deltas))
    if med <= 0:
        return "UNKNOWN"
    if med <= 3:
        return "D"
    if med <= 10:
        return "W"
    if med <= 45:
        return "M"
    if med <= 135:
        return "Q"
    if med <= 400:
        return "A"
    # spacing molto largo ma non riconducibile: segnala come irregolare
    return f"irregular_{int(med)}d"


# soglie di osservazioni minime attese per frequenza (per coverage_score)
MIN_OBS_BY_FREQ = {"A": 10, "Q": 20, "M": 36, "W": 52, "D": 250, "UNKNOWN": 10}

# tolleranza di freshness (giorni) per frequenza
LAG_TOLERANCE = {"A": 500, "Q": 270, "M": 120, "W": 45, "D": 21, "UNKNOWN": 500}

# giorni attesi tra osservazioni consecutive (per gap detection)
EXPECTED_SPACING_DAYS = {"D": 1, "W": 7, "M": 30, "Q": 91, "A": 365}
