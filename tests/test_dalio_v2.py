# -*- coding: utf-8 -*-
"""
test_dalio_v2.py — Dalio v2 Phase 1: Sovereign Solvency + Political Execution.

Validates the shared scoring helpers in isolation, then a full pipeline run
(seed macro_panel -> run_dalio_v2 -> engine_scores) with hand-picked, clearly
separated country profiles so the expected score ordering is unambiguous
(see docs/DALIO_5ENGINE_IMPLEMENTATION_PLAN_2026-07.md Fase 1 for the design
this exercises).
"""
from __future__ import annotations

import datetime as dt
import json

import pandas as pd

from market_data_hub.dalio_v2 import scoring
from market_data_hub.dalio_v2.runner import run_dalio_v2
from market_data_hub.db.connection import get_conn
from market_data_hub.db.upsert import upsert


# ---------------------------------------------------------------------------
# scoring.py unit tests
# ---------------------------------------------------------------------------

def test_score_threshold_interpolates_and_flips_orientation():
    assert scoring.score_threshold(50, 90, 110, 130) == 0.0          # below watch
    assert scoring.score_threshold(100, 90, 110, 130) == 25.0        # w..s interpolation
    assert scoring.score_threshold(110, 90, 110, 130) == 50.0        # exactly at stress
    assert scoring.score_threshold(120, 90, 110, 130) == 75.0        # s..c interpolation
    assert scoring.score_threshold(200, 90, 110, 130) == 100.0       # above critical
    assert scoring.score_threshold(None, 90, 110, 130) is None
    # orientation=-1: a LOW value is worse (e.g. primary balance)
    assert scoring.score_threshold(-8, 2, 4, 6, orientation=-1) == 100.0
    assert scoring.score_threshold(8, 2, 4, 6, orientation=-1) == 0.0


def test_weighted_average_drops_missing_and_reports_coverage():
    components = {"a": 80.0, "b": None, "c": 40.0}
    weights = {"a": 1, "b": 1, "c": 1}
    score, n_avail, n_exp = scoring.weighted_average(components, weights)
    assert n_avail == 2 and n_exp == 3
    assert score == 60.0                          # (80+40)/2, b dropped entirely
    assert scoring.coverage_tier(n_avail, n_exp) == "proxy"    # 2/3 = 0.667 -> proxy
    assert scoring.coverage_tier(3, 3) == "full"
    assert scoring.coverage_tier(1, 3) == "insufficient"
    assert scoring.confidence_for("full") == "high"
    assert scoring.confidence_for("insufficient") == "low"


def test_bucket_with_hysteresis_smooths_single_boundary_flutter():
    thresholds = [20, 40, 60, 80]
    labels = ["strong", "stable", "watch", "stressed", "critical"]
    # first-ever computation: plain threshold assignment, no prior label
    assert scoring.bucket_with_hysteresis(41, thresholds, labels, None) == "watch"
    # small move back across the same boundary should NOT flip immediately
    assert scoring.bucket_with_hysteresis(39, thresholds, labels, "watch") == "watch"
    # move that clears threshold + margin (36 = 40 - 10%*(60-20)) flips it
    assert scoring.bucket_with_hysteresis(35, thresholds, labels, "watch") == "stable"
    # a jump spanning more than one bucket always applies immediately
    assert scoring.bucket_with_hysteresis(95, thresholds, labels, "strong") == "critical"


def test_percentile_rank_orders_ascending():
    s = pd.Series({"a": -1.0, "b": 1.0, "c": 2.0})
    ranked = scoring.percentile_rank(s)
    assert ranked["c"] > ranked["b"] > ranked["a"]
    assert ranked["c"] == 100.0


def test_git_short_sha_never_raises():
    sha = scoring.git_short_sha()
    assert isinstance(sha, str) and sha


# ---------------------------------------------------------------------------
# Full pipeline: seed -> run_dalio_v2 -> engine_scores
# ---------------------------------------------------------------------------

_DEBT_TRAJECTORY = {           # public_debt_gdp, 2023..2027 (2027 = WEO forecast)
    "USA": [50, 50, 50, 50, 50],           # flat, comfortable
    "SGP": [170, 170, 170, 171, 171],      # high gross debt, ~flat (the "SGP paradox")
    "ARG": [100, 110, 125, 140, 150],      # rising fast
}

_LATEST = {                    # every other component, single snapshot at ref_date
    "USA": dict(govt_net_debt_gdp=50, interest_on_debt_gdp=1.0, government_revenue_gdp=40,
               primary_balance_gdp=2.0, gdp_growth_weo=3.0, inflation_avg_weo=2.0,
               wgi_government_effectiveness=1.0, wgi_rule_of_law=1.0, wgi_control_corruption=1.0,
               wgi_political_stability=1.0, wgi_regulatory_quality=1.0),
    "SGP": dict(govt_net_debt_gdp=20, interest_on_debt_gdp=2.0, government_revenue_gdp=25,
               primary_balance_gdp=2.0, gdp_growth_weo=2.5, inflation_avg_weo=1.5,
               wgi_government_effectiveness=2.0, wgi_rule_of_law=2.0, wgi_control_corruption=2.0,
               wgi_political_stability=2.0, wgi_regulatory_quality=2.0),
    "ARG": dict(govt_net_debt_gdp=150, interest_on_debt_gdp=10.0, government_revenue_gdp=15,
               primary_balance_gdp=-8.0, gdp_growth_weo=-2.0, inflation_avg_weo=5.0,
               wgi_government_effectiveness=-1.0, wgi_rule_of_law=-1.0, wgi_control_corruption=-1.0,
               wgi_political_stability=-1.0, wgi_regulatory_quality=-1.0),
}

_ORIENT = {
    "govt_net_debt_gdp": -1, "interest_on_debt_gdp": -1, "government_revenue_gdp": 0,
    "primary_balance_gdp": 1, "gdp_growth_weo": 1, "inflation_avg_weo": -1,
    "wgi_government_effectiveness": 1, "wgi_rule_of_law": 1, "wgi_control_corruption": 1,
    "wgi_political_stability": 1, "wgi_regulatory_quality": 1,
}
_WGI_IDS = {"wgi_government_effectiveness", "wgi_rule_of_law", "wgi_control_corruption",
           "wgi_political_stability", "wgi_regulatory_quality"}


def _row(date_, iso3, ind, val):
    return {"date": date_, "country_iso3": iso3, "indicator_id": ind, "value": val,
            "indicator_name": ind, "pillar": "governance" if ind in _WGI_IDS else "sovereign",
            "orientation": _ORIENT.get(ind, 0), "source": "test", "provider_dataset": "X",
            "provider_code": "Y", "unit": "pct", "frequency": "A"}


def _seed(con):
    rows = []
    for iso3, traj in _DEBT_TRAJECTORY.items():
        for y, v in zip(range(2023, 2028), traj):
            rows.append(_row(dt.date(y, 12, 31), iso3, "public_debt_gdp", v))
    for iso3, vals in _LATEST.items():
        for ind, v in vals.items():
            rows.append(_row(dt.date(2026, 12, 31), iso3, ind, v))
    upsert(con, "macro_panel", pd.DataFrame(rows))


def test_sovereign_solvency_and_political_execution(tmp_db):
    con = get_conn()
    _seed(con)
    con.commit()
    con.close()

    summary = run_dalio_v2(engines=["sovereign_solvency", "political_execution"], ref_year=2026)
    assert summary == {"sovereign_solvency": 3, "political_execution": 3}

    con = get_conn(read_only=True)
    scores = con.execute(
        "SELECT country_iso3, engine, score, label, coverage_tier, confidence, "
        "components_json FROM engine_scores ORDER BY engine, country_iso3").fetch_df()
    con.close()

    ss = scores[scores.engine == "sovereign_solvency"].set_index("country_iso3")
    pe = scores[scores.engine == "political_execution"].set_index("country_iso3")

    # every component was seeded for all 3 countries -> full coverage throughout
    assert (scores["coverage_tier"] == "full").all()
    assert (scores["confidence"] == "high").all()

    # Sovereign Solvency: USA flat/low debt (safest) < SGP high-but-flat gross
    # debt / low net debt (the "Singapore paradox") < ARG rising debt + double
    # digit inflation + primary deficit (clearly worst)
    assert ss.loc["USA", "score"] < ss.loc["SGP", "score"] < ss.loc["ARG", "score"]

    # Political Execution: WGI ordering is consistent across all 5 dimensions
    # (SGP > USA > ARG on every one), so risk score must follow the same order
    assert pe.loc["SGP", "score"] < pe.loc["USA", "score"] < pe.loc["ARG", "score"]
    assert pe.loc["SGP", "score"] == 0.0    # best on every dimension -> zero risk

    # audit trail: components_json is valid JSON with the documented schema
    audit = json.loads(ss.loc["ARG", "components_json"])
    assert audit["coverage_tier"] == "full"
    assert audit["vintage_safe"] is False
    assert set(audit["missing_components"]) == set()
    assert "model_version" in audit and audit["model_version"]


# ---------------------------------------------------------------------------
# Private Credit Cycle: BIS-covered country vs BIS-uncovered (proxy) country
# ---------------------------------------------------------------------------

def _pc_row(date_, iso3, ind, val):
    return {"date": date_, "country_iso3": iso3, "indicator_id": ind, "value": val,
            "indicator_name": ind, "pillar": "debt_cycle", "orientation": -1,
            "source": "test", "provider_dataset": "X", "provider_code": "Y",
            "unit": "pct", "frequency": "A"}


def _seed_private_credit(con):
    rows = []
    # USA: BIS-covered, calm profile
    rows.append(_pc_row(dt.date(2026, 12, 31), "USA", "bis_credit_gap", 1.0))
    dsr_usa = [10, 12, 13, 14, 15, 16, 15, 14, 15, 15]     # latest=15, min=10, max=16 -> pct=0.83? see below
    for y, v in zip(range(2017, 2027), dsr_usa):
        rows.append(_pc_row(dt.date(y, 12, 31), "USA", "bis_dsr_private", v))
    for y, v in zip((2025, 2026), (100, 101)):
        rows.append(_pc_row(dt.date(y, 12, 31), "USA", "private_debt_gdp", v))
    rows.append(_pc_row(dt.date(2026, 12, 31), "USA", "npl_ratio", 2.0))

    # TUR: BIS-covered, textbook private credit boom
    rows.append(_pc_row(dt.date(2026, 12, 31), "TUR", "bis_credit_gap", 15.0))
    dsr_tur = [30, 32, 34, 36, 38, 40, 42, 44, 46, 50]     # rising to a fresh peak
    for y, v in zip(range(2017, 2027), dsr_tur):
        rows.append(_pc_row(dt.date(y, 12, 31), "TUR", "bis_dsr_private", v))
    for y, v in zip((2025, 2026), (100, 130)):
        rows.append(_pc_row(dt.date(y, 12, 31), "TUR", "private_debt_gdp", v))
    rows.append(_pc_row(dt.date(2026, 12, 31), "TUR", "npl_ratio", 12.0))

    # VNM: no BIS coverage at all (one of the 21 countries in the 2026-07
    # coverage audit) -> exercises the private_debt_gdp linear-detrend proxy
    debt_vnm = [60, 62, 64, 66, 68, 72, 78, 86, 96, 110]   # accelerating late
    for y, v in zip(range(2017, 2027), debt_vnm):
        rows.append(_pc_row(dt.date(y, 12, 31), "VNM", "private_debt_gdp", v))
    rows.append(_pc_row(dt.date(2026, 12, 31), "VNM", "npl_ratio", 4.0))

    upsert(con, "macro_panel", pd.DataFrame(rows))


def test_private_credit_bis_vs_proxy(tmp_db):
    con = get_conn()
    _seed_private_credit(con)
    con.commit()
    con.close()

    summary = run_dalio_v2(engines=["private_credit"], ref_year=2026)
    assert summary == {"private_credit": 3}

    con = get_conn(read_only=True)
    scores = con.execute(
        "SELECT country_iso3, score, label, coverage_tier, components_json "
        "FROM engine_scores WHERE engine = 'private_credit'").fetch_df()
    con.close()
    pc = scores.set_index("country_iso3")

    # USA calm < TUR textbook boom (credit gap, DSR at a fresh peak, double
    # digit real credit growth, high NPLs all firing at once)
    assert pc.loc["USA", "score"] < pc.loc["TUR", "score"]
    assert pc.loc["TUR", "label"] == "bubble"

    # BIS-covered countries get full coverage; the BIS-blind country never
    # does, even though every other input it has is populated
    assert pc.loc["USA", "coverage_tier"] == "full"
    assert pc.loc["TUR", "coverage_tier"] == "full"
    assert pc.loc["VNM", "coverage_tier"] in ("proxy", "insufficient")

    usa_audit = json.loads(pc.loc["USA", "components_json"])
    vnm_audit = json.loads(pc.loc["VNM", "components_json"])
    assert usa_audit["credit_gap_source"] == "bis"
    assert vnm_audit["credit_gap_source"].startswith("proxy")
    # VNM has no BIS DSR at all -> that component must be reported missing,
    # not silently defaulted to a "safe" score
    assert "private_dsr" in vnm_audit["missing_components"]


# ---------------------------------------------------------------------------
# External Currency Constraint: reserve-currency issuer vs FX-fragile EM
# ---------------------------------------------------------------------------

def _ec_row(date_, iso3, ind, val):
    return {"date": date_, "country_iso3": iso3, "indicator_id": ind, "value": val,
            "indicator_name": ind, "pillar": "markets", "orientation": 0,
            "source": "test", "provider_dataset": "X", "provider_code": "Y",
            "unit": "pct", "frequency": "A"}


def _seed_external_constraint(con):
    rows = []
    # USA: reserve currency (via _EXPLICIT_RESERVE_CURRENCY), calm external
    # position, has fx_debt_usd/ext_debt_nonres_usd -> fx_debt_share = 8%
    # (matches the real-world figure cited throughout the docs)
    usa = dict(current_account_gdp=-2.0, iip_net_position=-1000.0, gdp_current_usd=25000.0,
              short_term_debt_reserves=20.0, debt_service_exports=5.0,
              fx_debt_usd=8.0, ext_debt_nonres_usd=100.0, inflation_avg_weo=2.5,
              fx_reserves_months_imports=5.0)
    for ind, v in usa.items():
        rows.append(_ec_row(dt.date(2026, 12, 31), "USA", ind, v))

    # TUR: not a reserve currency, textbook FX-fragile profile, and
    # deliberately WITHOUT fx_debt_usd/ext_debt_nonres_usd so fx_debt_share
    # stays missing -> exercises the coverage-tier cap even though every
    # other input is present
    tur = dict(current_account_gdp=-6.0, iip_net_position=-500.0, gdp_current_usd=1000.0,
              short_term_debt_reserves=180.0, debt_service_exports=35.0,
              inflation_avg_weo=60.0, fx_reserves_months_imports=2.0)
    for ind, v in tur.items():
        rows.append(_ec_row(dt.date(2026, 12, 31), "TUR", ind, v))

    upsert(con, "macro_panel", pd.DataFrame(rows))


def test_external_constraint_reserve_currency_vs_fragile_em(tmp_db):
    con = get_conn()
    _seed_external_constraint(con)
    con.commit()
    con.close()

    summary = run_dalio_v2(engines=["external_constraint"], ref_year=2026)
    assert summary == {"external_constraint": 2}

    con = get_conn(read_only=True)
    scores = con.execute(
        "SELECT country_iso3, score, label, coverage_tier, components_json "
        "FROM engine_scores WHERE engine = 'external_constraint'").fetch_df()
    con.close()
    ec = scores.set_index("country_iso3")

    assert ec.loc["USA", "score"] < ec.loc["TUR", "score"]
    assert ec.loc["TUR", "label"] in ("high", "severe")

    usa_audit = json.loads(ec.loc["USA", "components_json"])
    tur_audit = json.loads(ec.loc["TUR", "components_json"])
    assert usa_audit["is_reserve_currency"] is True
    assert usa_audit["caveats"]                       # discount caveat recorded
    assert tur_audit["is_reserve_currency"] is False
    assert tur_audit["caveats"] == []

    # USA has fx_debt_share (the highest-quality input) -> full coverage;
    # TUR is missing exactly that input -> capped to proxy even though every
    # other component is present
    assert ec.loc["USA", "coverage_tier"] == "full"
    assert ec.loc["TUR", "coverage_tier"] == "proxy"
    assert "fx_debt_share" in tur_audit["missing_components"]


# ---------------------------------------------------------------------------
# Funding Liquidity: always proxy-tier, never full, regardless of coverage
# ---------------------------------------------------------------------------

def _fl_row(date_, iso3, ind, val):
    return {"date": date_, "country_iso3": iso3, "indicator_id": ind, "value": val,
            "indicator_name": ind, "pillar": "markets", "orientation": 0,
            "source": "test", "provider_dataset": "X", "provider_code": "Y",
            "unit": "pct", "frequency": "M" if ind == "bond_yield_10y" else "A"}


def _seed_funding_liquidity(con):
    rows = [
        _fl_row(dt.date(2026, 12, 31), "DEU", "short_term_debt_reserves", 20.0),
        _fl_row(dt.date(2025, 12, 31), "DEU", "bond_yield_10y", 2.0),
        _fl_row(dt.date(2026, 12, 31), "DEU", "bond_yield_10y", 2.5),      # +50bp, mild
        _fl_row(dt.date(2026, 12, 31), "ITA", "short_term_debt_reserves", 160.0),
        _fl_row(dt.date(2025, 12, 31), "ITA", "bond_yield_10y", 4.0),
        _fl_row(dt.date(2026, 12, 31), "ITA", "bond_yield_10y", 8.0),      # +400bp, spread blowout
    ]
    upsert(con, "macro_panel", pd.DataFrame(rows))


def test_funding_liquidity_always_proxy_tier(tmp_db):
    con = get_conn()
    _seed_funding_liquidity(con)
    con.commit()
    con.close()

    summary = run_dalio_v2(engines=["funding_liquidity"], ref_year=2026)
    assert summary == {"funding_liquidity": 2}

    con = get_conn(read_only=True)
    scores = con.execute(
        "SELECT country_iso3, score, label, coverage_tier, components_json "
        "FROM engine_scores WHERE engine = 'funding_liquidity'").fetch_df()
    con.close()
    fl = scores.set_index("country_iso3")

    assert fl.loc["DEU", "score"] < fl.loc["ITA", "score"]
    assert fl.loc["ITA", "label"] in ("stress", "severe")
    # both countries have full data for the 2 available proxy inputs, but
    # this engine can never report 'full' -- it's a structural cap, not a
    # per-country gap
    assert fl.loc["DEU", "coverage_tier"] == "proxy"
    assert fl.loc["ITA", "coverage_tier"] == "proxy"

    audit = json.loads(fl.loc["ITA", "components_json"])
    assert audit["scope"].startswith("proxy tier only")


def test_run_dalio_v2_rejects_unknown_engine(tmp_db):
    con = get_conn()
    con.close()
    try:
        run_dalio_v2(engines=["not_a_real_engine"])
        assert False, "expected ValueError"
    except ValueError as e:
        assert "not_a_real_engine" in str(e)


def test_run_dalio_v2_empty_panel_returns_zero_counts(tmp_db):
    con = get_conn()
    con.close()
    summary = run_dalio_v2(ref_year=2026)
    assert summary == {"sovereign_solvency": 0, "political_execution": 0,
                       "private_credit": 0, "external_constraint": 0,
                       "funding_liquidity": 0}
