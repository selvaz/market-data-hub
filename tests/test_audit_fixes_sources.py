# -*- coding: utf-8 -*-
"""Regression tests for the dalio.py / sources audit fixes (P3.2/P3.3/P3.5/
P3.6/P1.5 batch) — no lazyhmm dependency, always runnable (incl. CI).

Covers: orientation-0 exclusion from the cross-country z, policy-rate vs
implied-rate split, investor-base lock+vintage, IMF SDMX UNIT_MULT scaling,
fetch-failure logging and strict ECB period parsing.
"""
from __future__ import annotations

import datetime as dt
import logging
import sys
from datetime import datetime, timezone

import duckdb
import pandas as pd
import pytest

from market_data_hub.dalio import classify_cycle_phase, run_dalio
from market_data_hub.db.connection import get_conn
from market_data_hub.db.upsert import upsert
from market_data_hub.sources import ecb
from market_data_hub.sources import imf_sdmx

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


def test_ecb_fetch_happy_path_parses_csv(monkeypatch):
    # even the file that already covers ecb.py's failure path and its pure
    # _period_end() helper never exercised a successful CSV->DataFrame fetch
    text = ("REF_AREA,TIME_PERIOD,OBS_VALUE\n"
            "DE,2026-05,4.12\n"
            "FR,2026-05,4.30\n"
            "GB,2026-05,9.99\n")   # GB not in our country list -> dropped
    monkeypatch.setattr(ecb, "_get_csv", lambda *a, **k: text)
    spec = {"dataset": "MIR", "code": "M.{iso2}.B.A2I.AM.R.A.2240.EUR.N",
            "id": "cost_borrowing_corp", "name": "Cost of borrowing, corporations",
            "pillar": "markets", "orientation": 1, "unit": "percent"}
    countries = [{"iso2": "DE", "iso3": "DEU"}, {"iso2": "FR", "iso3": "FRA"}]

    df = ecb.fetch_ecb(spec, countries, start_year=2020)

    assert list(df.columns) == ecb._COLS
    assert set(df["country_iso3"]) == {"DEU", "FRA"}
    assert df[df["country_iso3"] == "DEU"].iloc[0]["value"] == 4.12
    assert (df["date"] == pd.Timestamp("2026-05-31").date()).all()
    assert (df["source"] == "ecb").all()
