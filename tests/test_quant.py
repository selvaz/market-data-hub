# -*- coding: utf-8 -*-
"""Tests for lazydatacore.quant — the single float return/risk implementation.

Beyond correctness, two equivalence blocks pin the *other* two historical
implementations to these formulas:

* the pandas/float transforms in ``market_data_hub.extract`` (log_return,
  pct_change), and
* LazyFin's 50-digit ``Decimal`` performance metrics (replicated inline so the
  test needs no LazyFin import).

Any drift in either implementation fails here — that is what makes "one
definition of returns" a contract rather than a hope.
"""
from __future__ import annotations

import math
from decimal import Decimal, localcontext
from typing import Sequence

import pytest

from market_data_hub.lazydatacore import (
    annualized_return,
    annualized_volatility,
    cumulative_return,
    log_returns,
    max_drawdown,
    pct_change,
    performance_summary,
    simple_returns,
)

VALUES = [100.0, 102.0, 99.0, 105.0, 110.0, 104.0, 112.0]


# --------------------------------------------------------------------------- #
# correctness & identities                                                    #
# --------------------------------------------------------------------------- #
def test_simple_returns_and_alias() -> None:
    assert simple_returns([100.0, 110.0, 99.0]) == pytest.approx([0.10, -0.10])
    assert pct_change is simple_returns


def test_log_returns_additive_identity() -> None:
    total = math.fsum(log_returns(VALUES))
    assert total == pytest.approx(math.log(VALUES[-1] / VALUES[0]))
    assert total == pytest.approx(math.log1p(cumulative_return(VALUES)))


def test_max_drawdown_known() -> None:
    assert max_drawdown(VALUES) == pytest.approx(6.0 / 110.0)
    assert max_drawdown([1.0, 2.0, 3.0]) == 0.0


def test_performance_summary_keys() -> None:
    summ = performance_summary(VALUES, periods_per_year=252)
    assert set(summ) == {
        "cumulative_return",
        "annualized_return",
        "annualized_volatility",
        "max_drawdown",
        "periods",
    }


# --------------------------------------------------------------------------- #
# validation — incl. non-finite rejection (NaN / inf)                         #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("fn", [simple_returns, log_returns, cumulative_return, max_drawdown])
def test_too_short_rejected(fn) -> None:
    with pytest.raises(ValueError):
        fn([100.0])


def test_non_positive_rejected() -> None:
    with pytest.raises(ValueError):
        simple_returns([100.0, 0.0])


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_rejected(bad) -> None:
    # would otherwise slip past `v <= 0` and silently produce nan/inf or hide risk
    with pytest.raises(ValueError):
        max_drawdown([100.0, bad, 90.0])
    with pytest.raises(ValueError):
        simple_returns([100.0, bad])


def test_bad_periods_per_year_rejected() -> None:
    with pytest.raises(ValueError):
        annualized_return(VALUES, periods_per_year=0)


# --------------------------------------------------------------------------- #
# equivalence with the pandas transforms in market_data_hub.extract           #
# --------------------------------------------------------------------------- #
def test_equivalence_with_extract_pandas_transforms() -> None:
    pd = pytest.importorskip("pandas")
    from market_data_hub.extract import _apply_transform

    level = pd.DataFrame({"AAPL": VALUES})

    log_df = _apply_transform(level, "log_return")["AAPL"].dropna().tolist()
    assert log_df == pytest.approx(log_returns(VALUES), rel=1e-12)

    pct_df = _apply_transform(level, "pct_change")["AAPL"].dropna().tolist()
    assert pct_df == pytest.approx(simple_returns(VALUES), rel=1e-12)


# --------------------------------------------------------------------------- #
# equivalence with LazyFin's Decimal performance math (replicated inline)     #
# --------------------------------------------------------------------------- #
_PREC = 50


def _dec(values: Sequence[float]) -> list[Decimal]:
    return [Decimal(str(v)) for v in values]


def _cumulative_dec(values: Sequence[float]) -> Decimal:
    s = _dec(values)
    with localcontext() as ctx:
        ctx.prec = _PREC
        return s[-1] / s[0] - 1


def _annualized_return_dec(values: Sequence[float], ppy: int) -> Decimal:
    s = _dec(values)
    with localcontext() as ctx:
        ctx.prec = _PREC
        growth = s[-1] / s[0]
        exponent = Decimal(ppy) / Decimal(len(s) - 1)
        return (growth.ln() * exponent).exp() - 1


def _annualized_vol_dec(values: Sequence[float], ppy: int) -> Decimal:
    s = _dec(values)
    with localcontext() as ctx:
        ctx.prec = _PREC
        rets = [s[i] / s[i - 1] - 1 for i in range(1, len(s))]
        n = Decimal(len(rets))
        mean = sum(rets, Decimal(0)) / n
        variance = sum(((r - mean) ** 2 for r in rets), Decimal(0)) / (n - 1)
        return variance.sqrt() * Decimal(ppy).sqrt()


def _max_drawdown_dec(values: Sequence[float]) -> Decimal:
    s = _dec(values)
    peak = s[0]
    worst = Decimal(0)
    for v in s[1:]:
        if v > peak:
            peak = v
        elif (peak - v) / peak > worst:
            worst = (peak - v) / peak
    return worst


@pytest.mark.parametrize("ppy", [252, 52, 12])
def test_equivalence_with_lazyfin_decimal(ppy: int) -> None:
    rel = 1e-12
    assert cumulative_return(VALUES) == pytest.approx(float(_cumulative_dec(VALUES)), rel=rel)
    assert annualized_return(VALUES, periods_per_year=ppy) == pytest.approx(
        float(_annualized_return_dec(VALUES, ppy)), rel=rel
    )
    assert annualized_volatility(VALUES, periods_per_year=ppy) == pytest.approx(
        float(_annualized_vol_dec(VALUES, ppy)), rel=rel
    )
    assert max_drawdown(VALUES) == pytest.approx(float(_max_drawdown_dec(VALUES)), rel=rel)
