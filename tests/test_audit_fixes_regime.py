# -*- coding: utf-8 -*-
"""Regression tests for the regime-module audit fixes (P3.1/P3.4/P3.6 batch).

Covers: retro-window backfill after a pause, full rewrite on a BIC model flip,
error rerun preserving a same-day success, daily points_per_year in the regime
charts, display names in the regime report, empty-universe guard.

The regime module hard-imports lazyhmm (private, git-installed); the whole
module is skipped where it isn't available (e.g. CI).
"""
from __future__ import annotations

import datetime as dt
import html as html_mod
import sys

import duckdb
import numpy as np
import pandas as pd
import pytest

pytest.importorskip("lazyhmm", reason="regime module hard-imports lazyhmm")

from market_data_hub.regime.estimate import (           # noqa: E402
    SymbolRunResult, _write_error_run, write_regime_run)
from market_data_hub.regime.schema import ensure_regime_schema  # noqa: E402

# ---------------------------------------------------------------------------
# regime/estimate.py — fake RegimeRun so no HMM fit / price history is needed
# ---------------------------------------------------------------------------
class _FakeRun:
    def __init__(self, panel, meta):
        self.panel = panel
        self.meta = meta


def _mk_run(symbol: str, n_days: int, S: int = 2, start: str = "2024-01-01"):
    idx = pd.bdate_range(start, periods=n_days)
    states = np.zeros(n_days, dtype=int)
    data = {
        f"{symbol}_state": states,
        f"{symbol}_highvol": states == (S - 1),
        f"P_{symbol}_HV": np.where(states == S - 1, 0.9, 0.1),
    }
    for s in range(S):
        data[f"P_{symbol}_S{s}"] = np.where(states == s, 0.9, 0.1)
    panel = pd.DataFrame(data, index=idx)
    meta = {symbol: {"S": S, "labels": [f"S{s}" for s in range(S)],
                     "bic": -100.0, "loglik": 50.0,
                     "transmat_": np.full((S, S), 1.0 / S),
                     "means_": np.zeros((S, 1)), "covars_": np.ones((S, 1, 1))}}
    return _FakeRun(panel, meta)


@pytest.fixture()
def regime_con(tmp_path):
    con = duckdb.connect(str(tmp_path / "regime.duckdb"))
    ensure_regime_schema(con)
    yield con
    con.close()


def test_retro_window_backfills_after_pause(regime_con):
    # 100 days fitted, then a 50-trading-day pause (> retro_days=30): every
    # missing date must become eligible, not just the last 30 rows.
    write_regime_run(regime_con, "SPY", _mk_run("SPY", 100),
                     estimation_date=dt.date(2024, 6, 1), fit_seconds=0.1)
    write_regime_run(regime_con, "SPY", _mk_run("SPY", 150),
                     estimation_date=dt.date(2024, 9, 1), fit_seconds=0.1)
    n = regime_con.execute(
        "SELECT count(DISTINCT trading_date) FROM hmm_regime_estimates "
        "WHERE symbol = 'SPY'").fetchone()[0]
    assert n == 150            # tail(30) alone would leave a 20-date hole


def test_model_flip_rewrites_full_history(regime_con):
    d1, d2 = dt.date(2024, 6, 1), dt.date(2024, 6, 2)
    write_regime_run(regime_con, "SPY", _mk_run("SPY", 100, S=2),
                     estimation_date=d1, fit_seconds=0.1)
    res = write_regime_run(regime_con, "SPY", _mk_run("SPY", 100, S=3),
                           estimation_date=d2, fit_seconds=0.1)
    assert res.n_states == 3
    new = regime_con.execute(
        "SELECT count(*) FROM hmm_regime_estimates "
        "WHERE symbol = 'SPY' AND estimation_date = ?", [str(d2)]).fetchone()[0]
    assert new == 100          # full consistent vintage, not a 30-row mix
    old = regime_con.execute(
        "SELECT count(*) FROM hmm_regime_estimates "
        "WHERE symbol = 'SPY' AND estimation_date = ?", [str(d1)]).fetchone()[0]
    assert old == 100          # prior vintage untouched


def test_no_flip_keeps_windowed_insert(regime_con):
    # same model, same states: nothing beyond the (deduplicated) window differs,
    # so the second run must not re-append the whole history
    d2 = dt.date(2024, 6, 2)
    write_regime_run(regime_con, "SPY", _mk_run("SPY", 100, S=2),
                     estimation_date=dt.date(2024, 6, 1), fit_seconds=0.1)
    write_regime_run(regime_con, "SPY", _mk_run("SPY", 100, S=2),
                     estimation_date=d2, fit_seconds=0.1)
    new = regime_con.execute(
        "SELECT count(*) FROM hmm_regime_estimates "
        "WHERE symbol = 'SPY' AND estimation_date = ?", [str(d2)]).fetchone()[0]
    assert new == 0


def test_error_rerun_keeps_same_day_success(regime_con):
    d = dt.date(2024, 6, 1)
    write_regime_run(regime_con, "SPY", _mk_run("SPY", 100),
                     estimation_date=d, fit_seconds=0.1)
    _write_error_run(regime_con, "SPY", d, "evening rerun failed")
    status, bic = regime_con.execute(
        "SELECT status, bic FROM hmm_model_runs "
        "WHERE symbol = 'SPY' AND estimation_date = ?", [str(d)]).fetchone()
    assert status == "ok" and bic is not None       # success preserved
    # a genuinely new (symbol, date) still records the error
    d2 = dt.date(2024, 6, 2)
    _write_error_run(regime_con, "SPY", d2, "boom")
    status2 = regime_con.execute(
        "SELECT status FROM hmm_model_runs "
        "WHERE symbol = 'SPY' AND estimation_date = ?", [str(d2)]).fetchone()[0]
    assert status2 == "error"


# ---------------------------------------------------------------------------
# regime/report.py — daily chart resolution + display names
# ---------------------------------------------------------------------------
def test_chart_uses_daily_points_per_year():
    from market_data_hub.regime import report as rep

    captured = {}

    class _Run:
        def plot_series_with_regimes(self, symbol, **kwargs):
            captured.update(kwargs)

    rep._chart_img(_Run(), "SPY")
    assert captured["last_years"] == 5
    assert captured["points_per_year"] == 252       # daily fit, not weekly


def test_report_shows_display_names(tmp_path):
    from market_data_hub.regime import report as rep

    names = rep._display_names()
    assert names, "tickers.yaml catalog lookup produced no names"
    sym, name = next(iter(sorted(names.items())))
    results = {sym: SymbolRunResult(symbol=sym, status="error", error_msg="x")}
    con = duckdb.connect()
    try:
        out = rep.generate_html_report(con, results, out_dir=tmp_path,
                                       asof=dt.date(2026, 7, 9))
    finally:
        con.close()
    assert html_mod.escape(name) in out.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# run_regime_daily.py — empty universe must exit cleanly, not KeyError
# ---------------------------------------------------------------------------
def test_empty_universe_exits_cleanly(tmp_db, monkeypatch, capsys):
    import run_regime_daily as rrd

    # priority tier 99 does not exist in tickers.yaml -> empty universe
    monkeypatch.setattr(sys, "argv",
                        ["run_regime_daily.py", "--priority", "99", "--dry-run"])
    assert rrd.main() == 0
    assert "No symbols to fit" in capsys.readouterr().out

