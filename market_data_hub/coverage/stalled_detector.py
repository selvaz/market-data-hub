# -*- coding: utf-8 -*-
"""
stalled_detector.py — rilevamento serie ferme (stalled) e verifica last-date.

Una serie e' "stalled" se il numero di giorni dall'ultima osservazione supera
la soglia ammessa per la sua frequenza. Le soglie sono freq-aware: una serie
annuale non viene marcata ferma per un normale ritardo di ~12 mesi.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd

# Soglie di tolleranza prima di marcare "stalled" (giorni), per frequenza.
# Daily 3gg copre weekend lungo + festivita'. Allineate a checks1_improved.py.
STALLED_THRESHOLD_DAYS = {
    "D": 3,
    "W": 10,
    "M": 45,
    "Q": 120,
    "A": 550,      # WDI/WGI pubblicano con ~18 mesi di lag (World Bank)
    "UNKNOWN": 30,
}


def _threshold_for(freq: Optional[str]) -> int:
    if not freq:
        return STALLED_THRESHOLD_DAYS["UNKNOWN"]
    if freq.startswith("irregular"):
        return STALLED_THRESHOLD_DAYS["UNKNOWN"]
    return STALLED_THRESHOLD_DAYS.get(freq, STALLED_THRESHOLD_DAYS["UNKNOWN"])


def lag_days(last_date, as_of: Optional[date] = None) -> Optional[int]:
    """Giorni tra l'ultima osservazione e oggi (UTC). None se last_date assente."""
    if last_date is None or pd.isna(last_date):
        return None
    ld = pd.Timestamp(last_date).date()
    ref = as_of or pd.Timestamp.now(tz="UTC").date()
    return (ref - ld).days


def is_stalled(last_date, freq: Optional[str],
               as_of: Optional[date] = None) -> bool:
    """True se la serie e' ferma oltre la soglia per la sua frequenza."""
    lag = lag_days(last_date, as_of)
    if lag is None:
        return True  # nessun dato = di fatto fermo
    return lag > _threshold_for(freq)
