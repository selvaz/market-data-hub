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

    summary = run_dalio_v2(ref_year=2026)
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
    assert summary == {"sovereign_solvency": 0, "political_execution": 0}
