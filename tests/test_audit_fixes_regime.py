# -*- coding: utf-8 -*-
"""Regression tests for the regime-module audit fixes (P3.1/P3.4/P3.6 batch)
plus the persistence-layer swap from DuckDB to LazyStats' ``ResultDepot``.

Covers: first-run behavior, retro-window backfill after a pause, full rewrite
on a BIC model flip, error rerun preserving a same-day success, changed_today/
revised_last_n_days reporting, result_id linkage from save_stable_point back
to the fit-diagnostics row, run_daily_regime_estimation's depot resolution via
LAZYSTATS_RESULT_DEPOT_DB (including the required-DB RuntimeError), daily
points_per_year in the regime charts, display names in the regime report, and
the empty-universe guard.

The regime module hard-imports lazystats.regimes (and, since the persistence
swap, lazystats.io.depot + lazytools.registry); the whole module is skipped
where any of those aren't available (e.g. CI -- see test_regime_daily_lock_skip.py
for the stubbed-import wiring test that runs there instead).
"""
from __future__ import annotations

import datetime as dt
import html as html_mod
import sys

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("lazystats.regimes", reason="regime module hard-imports lazystats.regimes")
pytest.importorskip("lazystats.io.depot", reason="regime persistence hard-imports lazystats.io.depot")
pytest.importorskip("lazytools.registry", reason="regime persistence hard-imports lazytools.registry")

from lazystats.io.depot import ResultDepot                       # noqa: E402

from market_data_hub.regime.estimate import (                    # noqa: E402
    SymbolRunResult, _has_ok_run, _write_error_run, write_regime_run)

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
def depot(tmp_path):
    """A real (not mocked) temporary ResultDepot, backed by an on-disk sqlite
    file so behavior matches production exactly (":memory:" would too, but a
    file is closer to how run_daily_regime_estimation actually uses it)."""
    d = ResultDepot(str(tmp_path / "depot.sqlite"))
    yield d
    d.close()


def _series_entries(depot: ResultDepot, series_key: str):
    """Every ``analysis_results`` index entry for one series_key, newest first."""
    return [e for e in depot.list(cadence="stable", limit=1000) if e["series_key"] == series_key]


def test_first_run_writes_full_history(depot):
    res = write_regime_run(depot, "SPY", _mk_run("SPY", 40),
                           estimation_date=dt.date(2024, 6, 1), fit_seconds=0.1)
    assert res.status == "ok" and res.n_states == 2
    latest = depot.get_series_latest("regime:SPY")
    assert len(latest) == 40
    assert all(p["estimation_date"] == "2024-06-01" for p in latest)
    # first run: nothing counts as a "revision" (there is no prior vintage)
    assert res.revised_last_n_days == 0
    assert res.revised_dates == []


def test_retro_window_backfills_after_pause(depot):
    # 100 days fitted, then a 50-trading-day pause (> retro_days=30): every
    # missing date must become eligible, not just the last 30 rows.
    write_regime_run(depot, "SPY", _mk_run("SPY", 100),
                     estimation_date=dt.date(2024, 6, 1), fit_seconds=0.1)
    write_regime_run(depot, "SPY", _mk_run("SPY", 150),
                     estimation_date=dt.date(2024, 9, 1), fit_seconds=0.1)
    latest = depot.get_series_latest("regime:SPY")
    assert len(latest) == 150   # tail(30) alone would leave a 20-date hole


def test_model_flip_rewrites_full_history(depot):
    d1, d2 = dt.date(2024, 6, 1), dt.date(2024, 6, 2)
    write_regime_run(depot, "SPY", _mk_run("SPY", 100, S=2),
                     estimation_date=d1, fit_seconds=0.1)
    res = write_regime_run(depot, "SPY", _mk_run("SPY", 100, S=3),
                           estimation_date=d2, fit_seconds=0.1)
    assert res.n_states == 3

    latest = depot.get_series_latest("regime:SPY")
    assert len(latest) == 100                                    # full consistent vintage, not a 30-row mix
    assert all(p["estimation_date"] == str(d2) for p in latest)  # every date got a d2 vintage

    # prior vintage untouched: pick any date and check both vintages survive
    sample_date = latest[0]["as_of_date"]
    vintages = depot.list_series_vintages("regime:SPY", sample_date)
    assert [v["estimation_date"] for v in vintages] == [str(d1), str(d2)]
    assert vintages[0]["value"]["n_states"] == 2   # old vintage's own reading, unchanged
    assert vintages[1]["value"]["n_states"] == 3


def test_no_flip_keeps_windowed_insert(depot):
    # same model, same states: nothing beyond the (deduplicated) window differs,
    # so the second run must not re-append the whole history
    d1, d2 = dt.date(2024, 6, 1), dt.date(2024, 6, 2)
    write_regime_run(depot, "SPY", _mk_run("SPY", 100, S=2),
                     estimation_date=d1, fit_seconds=0.1)
    res = write_regime_run(depot, "SPY", _mk_run("SPY", 100, S=2),
                           estimation_date=d2, fit_seconds=0.1)
    latest = depot.get_series_latest("regime:SPY")
    assert all(p["estimation_date"] != str(d2) for p in latest)
    assert res.revised_last_n_days == 0
    assert res.revised_dates == []


def test_error_rerun_keeps_same_day_success(depot):
    d = dt.date(2024, 6, 1)
    write_regime_run(depot, "SPY", _mk_run("SPY", 100),
                     estimation_date=d, fit_seconds=0.1)
    _write_error_run(depot, "SPY", d, "evening rerun failed")

    entries = _series_entries(depot, "regime:SPY")
    payloads = [depot.load(e["result_id"])["payload"] for e in entries]
    same_day = [p for p in payloads if p["estimation_date"] == str(d)]
    assert len(same_day) == 1                    # error rerun did not add a second row
    assert same_day[0]["status"] == "ok"          # success preserved
    assert same_day[0]["bic"] is not None
    assert _has_ok_run(depot, "regime:SPY", str(d)) is True

    # a genuinely new (symbol, date) still records the error
    d2 = dt.date(2024, 6, 2)
    _write_error_run(depot, "SPY", d2, "boom")
    entries2 = _series_entries(depot, "regime:SPY")
    payloads2 = [depot.load(e["result_id"])["payload"] for e in entries2]
    day2 = [p for p in payloads2 if p["estimation_date"] == str(d2)]
    assert len(day2) == 1
    assert day2[0]["status"] == "error"
    assert day2[0]["error_msg"] == "boom"


def test_stable_points_link_back_to_result_id(depot):
    """New coverage for the persistence-layer swap: every stable_series_points
    row written for a run's dates carries that run's analysis_results
    result_id, so a reading is traceable back to the fit diagnostics that
    produced it."""
    d = dt.date(2024, 6, 1)
    write_regime_run(depot, "SPY", _mk_run("SPY", 40),
                     estimation_date=d, fit_seconds=0.1)

    entries = _series_entries(depot, "regime:SPY")
    assert len(entries) == 1
    result_id = entries[0]["result_id"]

    latest = depot.get_series_latest("regime:SPY")
    assert latest
    sample_date = latest[0]["as_of_date"]
    vintages = depot.list_series_vintages("regime:SPY", sample_date)
    assert vintages[-1]["result_id"] == result_id


def test_run_daily_regime_estimation_resolves_depot_from_env(tmp_path, monkeypatch):
    """run_daily_regime_estimation must resolve its depot via
    lazytools.registry.resolve_db("lazystats_depot") (LAZYSTATS_RESULT_DEPOT_DB),
    not any other mechanism, and persist through it end to end."""
    from market_data_hub.regime import estimate as est_mod

    depot_path = tmp_path / "depot.sqlite"
    monkeypatch.setenv("LAZYSTATS_RESULT_DEPOT_DB", str(depot_path))
    monkeypatch.setattr(est_mod, "fit_symbol_regime",
                        lambda symbol, **kw: _mk_run(symbol, 40))

    results = est_mod.run_daily_regime_estimation(
        symbols=["SPY"], asof=dt.date(2024, 6, 1),
    )
    assert results["SPY"].status == "ok"

    d = ResultDepot(str(depot_path))
    try:
        latest = d.get_series_latest("regime:SPY")
        assert len(latest) == 40
    finally:
        d.close()


def test_run_daily_regime_estimation_raises_if_depot_unset(monkeypatch):
    """resolve_db raises RuntimeError for the required lazystats_depot DB when
    unset -- run_daily_regime_estimation must let that propagate rather than
    swallow it (this is required core persistence, not the artifact-store's
    best-effort optional pattern)."""
    from market_data_hub.regime import estimate as est_mod

    monkeypatch.delenv("LAZYSTATS_RESULT_DEPOT_DB", raising=False)
    monkeypatch.setattr(est_mod, "fit_symbol_regime",
                        lambda symbol, **kw: _mk_run(symbol, 40))

    with pytest.raises(RuntimeError):
        est_mod.run_daily_regime_estimation(symbols=["SPY"], asof=dt.date(2024, 6, 1))


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


def test_revision_table_renders_old_new_transition(depot):
    """_revision_table reads vintages from the depot (not DuckDB) and shows
    the old->new state/prob_high_vol/estimation_date transition -- the gap
    flagged after the persistence-layer swap (report.py silently rendering
    an empty revision section)."""
    from market_data_hub.regime import report as rep

    depot.save_stable_point(
        series_key="regime:SPY", as_of_date="2024-06-01", estimation_date="2024-06-01",
        value={"state": 0, "is_high_vol": False, "prob_high_vol": 0.1,
               "n_states": 2, "state_probs": [0.9, 0.1]},
    )
    depot.save_stable_point(
        series_key="regime:SPY", as_of_date="2024-06-01", estimation_date="2024-06-05",
        value={"state": 1, "is_high_vol": True, "prob_high_vol": 0.87,
               "n_states": 2, "state_probs": [0.13, 0.87]},
    )

    out = rep._revision_table(depot, "SPY", [dt.date(2024, 6, 1)])

    assert "0 &rarr; 1" in out
    assert "0.100 &rarr; 0.870" in out
    assert "2024-06-01 &rarr; 2024-06-05" in out


def test_revision_table_skips_dates_with_no_second_vintage(depot):
    from market_data_hub.regime import report as rep

    depot.save_stable_point(
        series_key="regime:SPY", as_of_date="2024-06-01", estimation_date="2024-06-01",
        value={"state": 0, "is_high_vol": False, "prob_high_vol": 0.1,
               "n_states": 2, "state_probs": [0.9, 0.1]},
    )
    assert rep._revision_table(depot, "SPY", [dt.date(2024, 6, 1)]) == ""
    assert rep._revision_table(depot, "SPY", []) == ""


def test_report_shows_display_names(tmp_path):
    from market_data_hub.regime import report as rep

    names = rep._display_names()
    assert names, "tickers.yaml catalog lookup produced no names"
    sym, name = next(iter(sorted(names.items())))
    results = {sym: SymbolRunResult(symbol=sym, status="error", error_msg="x")}
    depot = ResultDepot()
    try:
        out = rep.generate_html_report(depot, results, out_dir=tmp_path,
                                       asof=dt.date(2026, 7, 9))
    finally:
        depot.close()
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
