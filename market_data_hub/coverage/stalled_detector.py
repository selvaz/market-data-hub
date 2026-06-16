# -*- coding: utf-8 -*-
"""
stalled_detector.py — detection of stalled series and last-date check.

A series is "stalled" if the number of days since the last observation exceeds
the allowed threshold for its frequency. The thresholds are freq-aware: an
annual series is not flagged as stalled for a normal delay of ~12 months.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd

# Tolerance thresholds before flagging "stalled" (days), per frequency.
# Daily 3d covers a long weekend + holidays. Aligned with checks1_improved.py.
STALLED_THRESHOLD_DAYS = {
    "D": 3,
    "W": 10,
    "M": 45,
    "Q": 120,
    "A": 550,      # WDI/WGI publish with ~18 months of lag (World Bank)
    "UNKNOWN": 30,
}


def _threshold_for(freq: Optional[str]) -> int:
    if not freq:
        return STALLED_THRESHOLD_DAYS["UNKNOWN"]
    if freq.startswith("irregular"):
        return STALLED_THRESHOLD_DAYS["UNKNOWN"]
    return STALLED_THRESHOLD_DAYS.get(freq, STALLED_THRESHOLD_DAYS["UNKNOWN"])


def lag_days(last_date, as_of: Optional[date] = None) -> Optional[int]:
    """Days between the last observation and today (UTC). None if last_date is absent."""
    if last_date is None or pd.isna(last_date):
        return None
    ld = pd.Timestamp(last_date).date()
    ref = as_of or pd.Timestamp.now(tz="UTC").date()
    return (ref - ld).days


def is_stalled(last_date, freq: Optional[str],
               as_of: Optional[date] = None) -> bool:
    """True if the series is stalled beyond the threshold for its frequency."""
    lag = lag_days(last_date, as_of)
    if lag is None:
        return True  # no data = effectively stalled
    return lag > _threshold_for(freq)
