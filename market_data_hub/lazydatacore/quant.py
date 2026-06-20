# -*- coding: utf-8 -*-
"""Canonical return/risk primitives — the single float implementation.

One definition of the return math that was previously written twice: the
pandas/float log-returns in :mod:`market_data_hub.extract` and the Decimal
performance metrics in LazyFin's ``kernel/returns.py``. Per the ecosystem
numeric policy these live here as **float** (the analysis layer); LazyFin keeps
its ``Decimal`` money math, and both the pandas and the Decimal variants are
pinned to *these* formulas by numeric-equivalence tests.

They live in ``lazydatacore`` — the universal, dependency-light leaf every tool
already imports — so market-data-hub, LazyFin and LazyHMM can share one
implementation **without** any dependency inversion or cycle. Pure-Python and
``math``-only: no numpy/pandas needed at import time, consistent with the rest
of the contract.

Inputs are a value/price series, oldest first, finite and strictly positive, at
a regular periodicity; ``periods_per_year`` says which (252 trading days, 52
weeks, 12 months, ...). External cash flows are not modelled: feed flow-adjusted
values when flows exist.
"""
from __future__ import annotations

import math
from typing import Dict, List, Sequence

__all__ = [
    "log_returns",
    "simple_returns",
    "pct_change",
    "cumulative_return",
    "annualized_return",
    "annualized_volatility",
    "max_drawdown",
    "performance_summary",
]


def _validated(values: Sequence[float], *, minimum: int) -> List[float]:
    series = [float(v) for v in values]
    if len(series) < minimum:
        raise ValueError(
            f"value series needs at least {minimum} points, got {len(series)}"
        )
    for i, v in enumerate(series):
        # Reject NaN/inf up front: they slip past ``v <= 0`` and would otherwise
        # silently produce nan/inf results or understate risk (e.g. a max
        # drawdown of 0 across an inf spike).
        if not math.isfinite(v):
            raise ValueError(
                f"value series must be finite (values[{i}] = {v}); "
                "non-finite market data must be cleaned before return math"
            )
        if v <= 0:
            raise ValueError(
                f"value series must be strictly positive (values[{i}] = {v}); "
                "return math on a zero/negative value is undefined"
            )
    return series


def log_returns(values: Sequence[float]) -> List[float]:
    """Continuously-compounded (log) returns: ``ln(V_t / V_{t-1})``.

    The additive return market-data-hub stores/derives; ``len(values) - 1``
    values, oldest first. Their sum is ``ln(V_n / V_0)`` = ``ln(1 + cumulative)``.
    """
    series = _validated(values, minimum=2)
    return [math.log(series[i] / series[i - 1]) for i in range(1, len(series))]


def simple_returns(values: Sequence[float]) -> List[float]:
    """Period-over-period simple returns: ``V_t / V_{t-1} - 1`` (fractions)."""
    series = _validated(values, minimum=2)
    return [series[i] / series[i - 1] - 1.0 for i in range(1, len(series))]


#: pandas-style alias for :func:`simple_returns`.
pct_change = simple_returns


def cumulative_return(values: Sequence[float]) -> float:
    """Total return over the whole series: ``V_n / V_0 - 1``."""
    series = _validated(values, minimum=2)
    return series[-1] / series[0] - 1.0


def annualized_return(values: Sequence[float], *, periods_per_year: int) -> float:
    """Geometric annualized return: ``(V_n / V_0) ** (ppy / n_periods) - 1``.

    ``n_periods = len(values) - 1``. With external cash flows this is not a
    rate of return on capital — use flow-adjusted values.
    """
    if periods_per_year <= 0:
        raise ValueError(f"periods_per_year must be positive, got {periods_per_year}")
    series = _validated(values, minimum=2)
    n_periods = len(series) - 1
    growth = series[-1] / series[0]
    return growth ** (periods_per_year / n_periods) - 1.0


def annualized_volatility(values: Sequence[float], *, periods_per_year: int) -> float:
    """Sample standard deviation (``ddof=1``) of periodic simple returns,
    annualized by ``sqrt(periods_per_year)``. Needs at least 3 values.
    """
    if periods_per_year <= 0:
        raise ValueError(f"periods_per_year must be positive, got {periods_per_year}")
    series = _validated(values, minimum=3)
    rets = simple_returns(series)
    n = len(rets)
    mean = math.fsum(rets) / n
    variance = math.fsum((r - mean) ** 2 for r in rets) / (n - 1)
    return math.sqrt(variance) * math.sqrt(periods_per_year)


def max_drawdown(values: Sequence[float]) -> float:
    """Largest peak-to-trough decline, as a positive fraction of the peak.

    ``0.0`` for a non-decreasing series; ``0.25`` means a -25% drawdown.
    """
    series = _validated(values, minimum=2)
    peak = series[0]
    worst = 0.0
    for v in series[1:]:
        if v > peak:
            peak = v
        else:
            drawdown = (peak - v) / peak
            if drawdown > worst:
                worst = drawdown
    return worst


def performance_summary(
    values: Sequence[float], *, periods_per_year: int = 252
) -> Dict[str, float]:
    """One-call summary: cumulative/annualized return, volatility, drawdown.

    Volatility needs at least 3 values; with a 2-point series call the
    individual functions instead.
    """
    return {
        "cumulative_return": cumulative_return(values),
        "annualized_return": annualized_return(values, periods_per_year=periods_per_year),
        "annualized_volatility": annualized_volatility(
            values, periods_per_year=periods_per_year
        ),
        "max_drawdown": max_drawdown(values),
        "periods": float(len(values) - 1),
    }
