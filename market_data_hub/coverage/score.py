# -*- coding: utf-8 -*-
"""
score.py — coverage score 0-100, freq-aware.

Faithfully ports coverage_score() from macro_dashboard_v2_bundle/macro_dashboard.py.
Composition:
    obs_component       (max 40) — observations vs expected minimum per freq
    missing_component   (max 25) — completeness (1 - missing_pct)
    freshness_component (max 25) — how recent the last data point is vs tolerance
    priority_component  (max 10) — importance tier of the symbol
The freshness threshold scales with the frequency, so an annual series is not
penalized for a normal delay of ~12 months.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from market_data_hub.coverage.freq_detector import MIN_OBS_BY_FREQ, LAG_TOLERANCE


def _base_freq(freq: Optional[str]) -> str:
    if not freq:
        return "UNKNOWN"
    if freq.startswith("irregular"):
        return "UNKNOWN"
    return freq


def coverage_score(obs_count: int, missing_pct: float,
                   latest_lag_days: Optional[float], priority: int,
                   freq: str) -> float:
    """0-100. See the module docstring."""
    if obs_count <= 0:
        return 0.0
    f = _base_freq(freq)
    min_obs = MIN_OBS_BY_FREQ.get(f, 10)
    tol = LAG_TOLERANCE.get(f, 500)

    obs_component = min(obs_count / max(min_obs, 1), 1.0) * 40
    missing_component = max(0.0, 1.0 - float(missing_pct or 0.0)) * 25
    lag = tol if latest_lag_days is None or pd.isna(latest_lag_days) else float(latest_lag_days)
    freshness_component = max(0.0, 1.0 - lag / (2 * tol)) * 25
    priority_component = {1: 10, 2: 7, 3: 4, 4: 1}.get(int(priority or 4), 0)

    return round(obs_component + missing_component
                 + freshness_component + priority_component, 2)
