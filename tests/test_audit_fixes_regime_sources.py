# -*- coding: utf-8 -*-
"""Regression tests for the regime/sources/dalio audit fixes (P3.x batch).

Covers: retro-window backfill after a pause, full rewrite on a BIC model flip,
error rerun preserving a same-day success, daily points_per_year in the regime
charts, display names in the regime report, empty-universe guard, orientation-0
exclusion from the cross-country z, policy-rate vs implied-rate split,
investor-base lock+vintage, IMF SDMX UNIT_MULT scaling, fetch-failure logging
and strict ECB period parsing.
"""
from __future__ import annotations

import datetime as dt
import html as html_mod
import logging
import sys
from datetime import datetime, timezone

import duckdb
import numpy as np
import pandas as pd
import pytest

from market_data_hub.dalio import classify_cycle_phase, run_dalio
from market_data_hub.db.connection import get_conn
from market_data_hub.db.upsert import upsert
from market_data_hub.regime.estimate import (
    SymbolRunResult, _write_error_run, write_regime_run)
from market_data_hub.regime.schema import ensure_regime_schema
from market_data_hub.sources import ecb
from market_data_hub.sources import imf_sdmx


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


# ---------------------------------------------------------------------------
# dalio.py — orientation-0 exclusion and policy-vs-implied rate split
# ---------------------------------------------------------------------------
def _panel_row(date, iso3, ind, value, pillar, orient, freq):
    return {"date": date, "country_iso3": iso3, "indicator_id": ind,
            "value": value, "indicator_name": ind, "pillar": pillar,
            "orientation": orient, "source": "test", "provider_dataset": "X",
            "provider_code": "Y", "unit": "pct", "frequency": freq}


def test_orientation_zero_not_coerced_to_pos(tmp_db):
    con = get_conn()
    rows = []
    for iso3, g, r in [("USA", 10.0, 10.0), ("ITA", 0.0, 5.0), ("CHE", -10.0, 0.5)]:
        rows.append(_panel_row(dt.date(2025, 12, 31), iso3, "gdp_growth_weo",
                               g, "growth", 1, "A"))
        rows.append(_panel_row(dt.date(2026, 6, 30), iso3, "bis_policy_rate",
                               r, "liquidity", 0, "M"))
    upsert(con, "macro_panel", pd.DataFrame(rows))
    con.commit()
    con.close()

    run_dalio()

    con = get_conn()
    try:
        pol = con.execute(
            "SELECT z_score, signal FROM dalio_signals "
            "WHERE indicator_id = 'bis_policy_rate'").fetch_df()
        # rows stay visible but never scored: no POS 'strength' for the
        # highest policy rate
        assert len(pol) == 3
        assert pol["z_score"].isna().all()
        assert (pol["signal"] == "NEUTRAL").all()

        gro = con.execute(
            "SELECT country_iso3, z_score, signal FROM dalio_signals "
            "WHERE indicator_id = 'gdp_growth_weo'").fetch_df()
        usa = gro.set_index("country_iso3").loc["USA"]
        assert usa["z_score"] > 0 and usa["signal"] == "POS"   # oriented: scored

        pillars = con.execute(
            "SELECT DISTINCT pillar FROM pillar_scores").fetch_df()["pillar"]
        assert "growth" in set(pillars)
        assert "liquidity" not in set(pillars)   # only orientation-0 members
    finally:
        con.close()


_TH = {"credit_gap_bubble": 10.0, "dsr_high": 20.0, "dsr_peak_pct": 0.8,
       "rate_near_zero": 1.0, "credit_gap_late": 5.0, "weak_growth": 1.5,
       "debt_high_level": 100.0, "debt_crisis_level": 130.0,
       "deficit_large": -4.5, "debt_trend_high": 1.5, "debt_trend_moderate": 0.7}


def test_pushing_on_string_reads_policy_rate():
    x = {"growth": 0.5, "credit_gap": 0.0, "nom_growth": 2.0,
         "nom_rate": 4.0,          # implied stock rate, never near zero
         "policy_rate": 0.25, "debt_level": 60.0, "debt_falling": False,
         "debt_trend": 0.0, "fiscal_balance": -2.0, "dsr": 10.0, "dsr_pct": 0.5}
    assert classify_cycle_phase(x, _TH) == "PUSHING_ON_STRING"
    # without a policy rate the branch must not fire on the implied rate
    x2 = dict(x, policy_rate=None)
    assert classify_cycle_phase(x2, _TH) == "EARLY_EXPANSION"


def test_deleveraging_phase_keeps_implied_rate():
    # the r-vs-g debt-dynamics test stays on the stock rate: gn < rn -> UGLY
    # even when the policy rate is far below nominal growth
    x = {"growth": 2.0, "credit_gap": 0.0, "nom_growth": 5.0,
         "nom_rate": 7.0, "policy_rate": 1.5, "debt_level": 90.0,
         "debt_falling": True, "debt_trend": -3.0, "fiscal_balance": -2.0,
         "dsr": 10.0, "dsr_pct": 0.5}
    assert classify_cycle_phase(x, _TH) == "UGLY_DELEVERAGING"
    assert classify_cycle_phase(dict(x, nom_rate=4.0), _TH) == "BEAUTIFUL_DELEVERAGING"


def test_deleveraging_quality_uses_policy_rate(tmp_db):
    # One country, debt falling; nominal growth 5%: implied stock rate ~7.2%
    # (UGLY r-vs-g phase) but policy rate 1.5% (BEAUTIFUL deleveraging quality).
    con = get_conn()
    rows = []
    for y in range(2023, 2032):                       # ry-3 .. ry+5 window
        rows.append(_panel_row(dt.date(y, 12, 31), "USA", "public_debt_gdp",
                               120.0 - 3.0 * (y - 2023), "sovereign", -1, "A"))
    for y in (2024, 2025, 2026):
        rows.append(_panel_row(dt.date(y, 12, 31), "USA", "gdp_growth_weo",
                               2.0, "growth", 1, "A"))
        rows.append(_panel_row(dt.date(y, 12, 31), "USA", "inflation_avg_weo",
                               3.0, "liquidity", -1, "A"))
    # implied_interest_rate is DERIVED by v_macro_panel_ext: ie / debt * 100
    rows.append(_panel_row(dt.date(2026, 12, 31), "USA", "interest_on_debt_gdp",
                           8.0, "markets", -1, "A"))
    rows.append(_panel_row(dt.date(2026, 6, 30), "USA", "bis_policy_rate",
                           1.5, "liquidity", 0, "M"))
    upsert(con, "macro_panel", pd.DataFrame(rows))
    con.commit()
    con.close()

    run_dalio(ref_year=2026)

    con = get_conn()
    try:
        phase, delev, nom_rate = con.execute(
            "SELECT debt_cycle_phase, deleveraging_quality, nom_rate "
            "FROM regime_state WHERE country_iso3 = 'USA'").fetchone()
    finally:
        con.close()
    assert phase == "UGLY_DELEVERAGING"          # r-vs-g on the implied rate
    assert delev == "BEAUTIFUL"                  # quality on the policy rate
    assert nom_rate == pytest.approx(8.0 / 111.0 * 100.0, rel=1e-6)


# ---------------------------------------------------------------------------
# import_investor_base.py — writer lock + macro_panel_vintage
# ---------------------------------------------------------------------------
def test_investor_base_records_vintage(tmp_db, monkeypatch, capsys):
    import import_investor_base as iib

    df = pd.DataFrame([{
        "date": dt.date(2024, 12, 31), "country_iso3": "USA",
        "indicator_id": iib.INDICATOR_ID, "value": 33.5,
        "indicator_name": iib.INDICATOR_NAME, "pillar": "markets",
        "orientation": -1, "source": "arslanalp_tsuda",
        "provider_dataset": "AT_investor_base", "provider_code": "manual_xlsx",
        "unit": "percent", "frequency": "Q",
        "updated_at": datetime.now(timezone.utc)}])
    monkeypatch.setattr(iib, "load", lambda p: df)
    monkeypatch.setattr(sys, "argv", ["import_investor_base.py", "dummy.xlsx"])
    assert iib.main() == 0

    con = duckdb.connect(tmp_db, read_only=True)
    try:
        assert con.execute(
            "SELECT count(*) FROM macro_panel "
            "WHERE indicator_id = 'nonresident_debt_share'").fetchone()[0] == 1
        vintage = con.execute(
            "SELECT value FROM macro_panel_vintage "
            "WHERE indicator_id = 'nonresident_debt_share'").fetchall()
        assert vintage == [(33.5,)]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# sources/imf_sdmx.py — UNIT_MULT scaling + failure logging
# ---------------------------------------------------------------------------
_SPEC = {"dataset": "IIP", "code": "{iso3}.NETAL_P.NIIP.USD.A", "id": "niip_usd",
         "name": "Net IIP (USD)", "pillar": "markets"}


def test_unit_mult_scales_obs_value(monkeypatch):
    text = ("COUNTRY,TIME_PERIOD,OBS_VALUE,UNIT_MULT\n"
            "USA,2024,5,6\n"          # millions -> x 10^6
            "USA,2023,7,\n"           # blank -> unscaled
            "USA,2022,3,N/A\n")       # non-numeric -> unscaled
    monkeypatch.setattr(imf_sdmx, "_get_csv", lambda *a, **k: text)
    df = imf_sdmx.fetch_imf_sdmx(_SPEC, [{"iso3": "USA"}], base_sleep=0)
    by_year = {d.year: v for d, v in zip(df["date"], df["value"])}
    assert by_year == {2024: 5e6, 2023: 7.0, 2022: 3.0}


def test_missing_unit_mult_column_unchanged(monkeypatch):
    text = "COUNTRY,TIME_PERIOD,OBS_VALUE\nUSA,2024,5\n"
    monkeypatch.setattr(imf_sdmx, "_get_csv", lambda *a, **k: text)
    df = imf_sdmx.fetch_imf_sdmx(_SPEC, [{"iso3": "USA"}], base_sleep=0)
    assert df["value"].tolist() == [5.0]


def test_imf_fetch_failure_is_logged(monkeypatch, caplog):
    def _boom(*a, **k):
        raise ConnectionError("dns exploded")

    monkeypatch.setattr(imf_sdmx, "_get_csv", _boom)
    with caplog.at_level(logging.WARNING, logger="market_data_hub.sources.imf_sdmx"):
        df = imf_sdmx.fetch_imf_sdmx(_SPEC, [{"iso3": "USA"}], base_sleep=0)
    assert df.empty
    msgs = [r.getMessage() for r in caplog.records]
    assert any("niip_usd" in m and "USA" in m and "dns exploded" in m for m in msgs)
    assert any("Net IIP (USD)" in m for m in msgs)   # human-readable name too


# ---------------------------------------------------------------------------
# sources/ecb.py — strict period parsing + failure logging
# ---------------------------------------------------------------------------
def test_ecb_period_end_recognized_formats():
    assert ecb._period_end("2026-05") == pd.Timestamp("2026-05-31")
    assert ecb._period_end("2026-Q1") == pd.Timestamp("2026-03-31")
    assert ecb._period_end("2026") == pd.Timestamp("2026-12-31")


def test_ecb_sub_monthly_period_not_binned_to_month_end(caplog):
    # a daily period keeps its own day instead of silently becoming month-end
    assert ecb._period_end("2026-05-04") == pd.Timestamp("2026-05-04")
    with caplog.at_level(logging.WARNING, logger="market_data_hub.sources.ecb"):
        assert ecb._period_end("2026-W18") is None
    assert any("2026-W18" in r.getMessage() for r in caplog.records)


def test_ecb_fetch_failure_is_logged(monkeypatch, caplog):
    def _boom(*a, **k):
        raise ConnectionError("proxy down")

    monkeypatch.setattr(ecb, "_get_csv", _boom)
    spec = {"dataset": "MIR", "code": "M.{iso2}.B.A2I.AM.R.A.2240.EUR.N",
            "id": "cost_borrowing_corp", "name": "Cost of borrowing, corporations",
            "pillar": "markets"}
    with caplog.at_level(logging.WARNING, logger="market_data_hub.sources.ecb"):
        df = ecb.fetch_ecb(spec, [{"iso2": "DE", "iso3": "DEU"}])
    assert df.empty
    msgs = [r.getMessage() for r in caplog.records]
    assert any("cost_borrowing_corp" in m and "proxy down" in m for m in msgs)
    assert any("Cost of borrowing, corporations" in m for m in msgs)
