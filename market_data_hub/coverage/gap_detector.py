# -*- coding: utf-8 -*-
"""
gap_detector.py — detection of gaps and the percentage of missing observations.

Concept ported from:
  - crypto_ml_features :: identify_missing_periods()
  - macro_dashboard    :: _missing_pct_inside_span()

For daily series the computation is trading-day-aware: weekends do not count as
gaps (business days are used as the expected baseline).
"""
from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd

from market_data_hub.coverage.freq_detector import EXPECTED_SPACING_DAYS


def missing_pct(dates: pd.Series, freq: str) -> float:
    """
    Percentage of missing observations between the first and last date, given
    the expected step for the frequency. 0.0 = no gaps, 1.0 = all missing.
    """
    d = pd.to_datetime(pd.Series(dates), errors="coerce").dropna().sort_values()
    d = pd.DatetimeIndex(d.unique())
    if len(d) < 2:
        return 0.0

    if freq == "D":
        # baseline: business days in the interval
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
    Number of gaps (consecutive missing sequences) in the series.
    For daily, a gap is an interval > 4 calendar days between consecutive
    observations (tolerates a long weekend + holidays); for the other
    frequencies, a delta > 2x the expected step.
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
    """Returns (first_date, last_date, obs_count), handling empty series."""
    d = pd.to_datetime(pd.Series(dates), errors="coerce").dropna().sort_values()
    if d.empty:
        return None, None, 0
    return d.iloc[0].date(), d.iloc[-1].date(), int(len(d.unique()))
