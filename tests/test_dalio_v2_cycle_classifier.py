# -*- coding: utf-8 -*-
"""
test_dalio_v2_cycle_classifier.py — Fase 5 cycle classifier.

Pure-function unit tests for classify_deleveraging()/classify_dalio_stage()
(no DB), plus compute() tests that seed engine_scores rows directly (same
pattern as test_dalio_v2.py's test_hysteresis_end_to_end_through_the_db /
test_prev_label_skips_null_label_gaps) so labels/coverage_tier/audit fields
are fully controlled without fighting the upstream engines' raw threshold
math. The ARG regression case is the reason this module exists: see
market_data_hub/dalio_v2/cycle_classifier.py's docstring.
"""
from __future__ import annotations

import datetime as dt
import json

from market_data_hub.dalio_v2 import cycle_classifier as cc
from market_data_hub.db.connection import get_conn

REF = dt.date(2026, 12, 31)


# ---------------------------------------------------------------------------
# classify_deleveraging — pure function
# ---------------------------------------------------------------------------

def test_deleveraging_none_when_debt_trend_unknown():
    assert cc.classify_deleveraging({}) is None


def test_deleveraging_none_when_debt_not_falling():
    assert cc.classify_deleveraging({"debt_trend_actuals_5y": 0.5}) == "none"


def test_deleveraging_beautiful():
    inputs = {"debt_trend_actuals_5y": -1.5, "real_growth_pct": 2.0,
             "inflation_pct": 3.0, "fx_depreciation_12m_pct": 2.0,
             "funding_label": "normal"}
    assert cc.classify_deleveraging(inputs) == "beautiful"


def test_deleveraging_inflationary_via_inflation():
    inputs = {"debt_trend_actuals_5y": -3.0, "real_growth_pct": 1.0,
             "inflation_pct": 30.0, "fx_depreciation_12m_pct": 2.0,
             "funding_label": "normal"}
    assert cc.classify_deleveraging(inputs) == "inflationary"


def test_deleveraging_inflationary_via_fx_depreciation_alone():
    inputs = {"debt_trend_actuals_5y": -3.0, "real_growth_pct": 1.0,
             "inflation_pct": 4.0, "fx_depreciation_12m_pct": 40.0,
             "funding_label": "normal"}
    assert cc.classify_deleveraging(inputs) == "inflationary"


def test_deleveraging_repressive():
    inputs = {"debt_trend_actuals_5y": -1.0, "real_growth_pct": -1.0,
             "inflation_pct": 4.0, "fx_depreciation_12m_pct": 2.0,
             "funding_label": "watch",
             "real_rate_proxy_pct": -5.0, "fx_debt_share_pct": 5.0}
    assert cc.classify_deleveraging(inputs) == "repressive"


def test_deleveraging_ugly_default():
    # debt falling, none of beautiful/inflationary/repressive conditions met
    # (negative growth, moderate inflation, positive real rate) -- also
    # covers the folded-in restructuring case (no data source exists)
    inputs = {"debt_trend_actuals_5y": -2.0, "real_growth_pct": -3.0,
             "inflation_pct": 5.0, "fx_depreciation_12m_pct": 3.0,
             "funding_label": "stress",
             "real_rate_proxy_pct": 3.0, "fx_debt_share_pct": 80.0}
    assert cc.classify_deleveraging(inputs) == "ugly"


def test_deleveraging_missing_data_cannot_earn_beautiful():
    # growth positive but inflation unknown -- must NOT read as beautiful
    inputs = {"debt_trend_actuals_5y": -2.0, "real_growth_pct": 2.0,
             "inflation_pct": None, "fx_depreciation_12m_pct": None,
             "funding_label": "normal"}
    assert cc.classify_deleveraging(inputs) != "beautiful"
    assert cc.classify_deleveraging(inputs) == "ugly"


def test_deleveraging_arg_regression():
    # THE motivating bug: hyperinflation makes debt/GDP fall via erosion,
    # not fiscal health -- must read as inflationary, never beautiful,
    # regardless of a superficially "favorable" positive real growth print
    inputs = {"debt_trend_actuals_5y": -8.0, "real_growth_pct": 2.0,
             "inflation_pct": 30.0, "fx_depreciation_12m_pct": 35.0,
             "funding_label": "severe"}
    result = cc.classify_deleveraging(inputs)
    assert result == "inflationary"
    assert result != "beautiful"


# ---------------------------------------------------------------------------
# classify_dalio_stage — pure function
# ---------------------------------------------------------------------------

def test_stage_crisis():
    inputs = {"funding_label": "severe", "external_label": "high",
             "sovereign_label": "watch", "private_label": "moderate",
             "real_growth_pct": 1.0}
    assert cc.classify_dalio_stage(inputs) == "crisis"


def test_stage_late_long_debt_cycle():
    inputs = {"funding_label": "watch", "external_label": "low",
             "sovereign_label": "critical", "private_label": "moderate",
             "real_growth_pct": 1.0}
    assert cc.classify_dalio_stage(inputs) == "late_long_debt_cycle"


def test_stage_private_bubble():
    inputs = {"funding_label": "normal", "external_label": "low",
             "sovereign_label": "stable", "private_label": "bubble",
             "real_growth_pct": 3.0}
    assert cc.classify_dalio_stage(inputs) == "private_bubble"


def test_stage_late_leveraging():
    inputs = {"funding_label": "normal", "external_label": "low",
             "sovereign_label": "stable", "private_label": "high",
             "real_growth_pct": 3.0}
    assert cc.classify_dalio_stage(inputs) == "late_leveraging"


def test_stage_contraction():
    inputs = {"funding_label": "normal", "external_label": "low",
             "sovereign_label": "stable", "private_label": "low",
             "real_growth_pct": -1.5}
    assert cc.classify_dalio_stage(inputs) == "contraction"


def test_stage_early_or_mid_cycle():
    inputs = {"funding_label": "easy", "external_label": "low",
             "sovereign_label": "strong", "private_label": "low",
             "real_growth_pct": 2.0}
    assert cc.classify_dalio_stage(inputs) == "early_or_mid_cycle"


def test_stage_branch_precedence_crisis_wins_over_bubble():
    # satisfies BOTH crisis and private_bubble -- crisis (evaluated first)
    # must win, not "the worse-sounding" or "the last matched" branch
    inputs = {"funding_label": "severe", "external_label": "severe",
             "sovereign_label": "critical", "private_label": "bubble",
             "real_growth_pct": -5.0}
    assert cc.classify_dalio_stage(inputs) == "crisis"


def test_stage_none_labels_do_not_raise_or_win():
    inputs = {"funding_label": None, "external_label": None,
             "sovereign_label": None, "private_label": None,
             "real_growth_pct": None}
    result = cc.classify_dalio_stage(inputs)
    assert result == "early_or_mid_cycle"   # falls through to the safe default


# ---------------------------------------------------------------------------
# compute() -- seeds engine_scores directly, full control over labels/audit
# ---------------------------------------------------------------------------

def _insert_engine_row(con, iso3, engine, score, label, tier, confidence, audit):
    con.execute(
        "INSERT INTO engine_scores VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, now())",
        [iso3, REF, engine, score, label, tier, confidence,
         len(audit.get("components", {})), len(audit.get("components", {})) + 1,
         json.dumps(audit)])


def _seed_arg_shaped(con):
    _insert_engine_row(con, "ARG", "sovereign_solvency", 40.0, "watch", "full", "high", {
        "real_growth_pct": 2.0, "r_effective_pct": 1.5, "debt_trend_actuals_5y": -8.0,
        "components": {"r_minus_g": {"score": 92.0, "raw_value": -18.4, "weight": 1}},
    })
    _insert_engine_row(con, "ARG", "external_constraint", 60.0, "high", "full", "high", {
        "fx_depreciation_12m_pct": 35.0,
        "components": {"inflation": {"score": 88.0, "raw_value": 30.0, "weight": 1},
                       "fx_debt_share": {"score": None, "raw_value": None, "weight": 1}},
    })
    _insert_engine_row(con, "ARG", "funding_liquidity", 90.0, "severe", "proxy", "medium", {
        "components": {"short_term_debt_reserves": {"score": 100.0, "raw_value": 164.0, "weight": 1}},
    })
    _insert_engine_row(con, "ARG", "private_credit", 40.0, "moderate", "proxy", "medium", {
        "components": {"credit_gap": {"score": 45.0, "raw_value": 5.0, "weight": 0.3}},
    })
    _insert_engine_row(con, "ARG", "political_execution", 60.0, "weak", "full", "high", {
        "components": {"rule_of_law": {"score": 65.0, "raw_value": -0.5, "weight": 0.25}},
    })


def _seed_sgp_shaped(con):
    _insert_engine_row(con, "SGP", "sovereign_solvency", 5.0, "strong", "full", "high", {
        "real_growth_pct": 3.0, "r_effective_pct": 1.0, "debt_trend_actuals_5y": 0.05,
        "components": {},
    })
    _insert_engine_row(con, "SGP", "external_constraint", 2.0, "low", "full", "high", {
        "fx_depreciation_12m_pct": -2.0,
        "components": {"inflation": {"score": 5.0, "raw_value": 1.5, "weight": 1}},
    })
    _insert_engine_row(con, "SGP", "funding_liquidity", 3.0, "easy", "proxy", "high", {
        "components": {},
    })
    _insert_engine_row(con, "SGP", "private_credit", 15.0, "low", "proxy", "medium", {
        "components": {},
    })
    _insert_engine_row(con, "SGP", "political_execution", 0.0, "strong", "full", "high", {
        "components": {},
    })


def test_compute_arg_shaped_is_inflationary_crisis(tmp_db):
    con = get_conn()
    _seed_arg_shaped(con)
    con.commit()

    df = cc.compute(con, REF)
    con.close()

    row = df.set_index("country_iso3").loc["ARG"]
    assert row["deleveraging_type"] == "inflationary"
    assert row["dalio_stage"] in ("crisis", "late_long_debt_cycle")
    drivers = json.loads(row["top_risk_drivers_json"])
    assert drivers and drivers[0]["score"] >= drivers[-1]["score"]  # sorted desc
    audit = json.loads(row["audit_json"])
    assert audit["unclassifiable_reason"]["deleveraging_type"] is None


def test_compute_sgp_shaped_is_none_early_cycle(tmp_db):
    con = get_conn()
    _seed_sgp_shaped(con)
    con.commit()

    df = cc.compute(con, REF)
    con.close()

    row = df.set_index("country_iso3").loc["SGP"]
    assert row["deleveraging_type"] == "none"
    assert row["dalio_stage"] == "early_or_mid_cycle"


def test_compute_thin_data_country_is_unclassifiable(tmp_db):
    con = get_conn()
    # only political_execution present -- none of the 4 gating engines exist
    _insert_engine_row(con, "XXX", "political_execution", 20.0, "adequate", "full", "high", {
        "components": {},
    })
    con.commit()

    df = cc.compute(con, REF)
    con.close()

    row = df.set_index("country_iso3").loc["XXX"]
    assert row["dalio_stage"] is None
    assert row["deleveraging_type"] is None
    audit = json.loads(row["audit_json"])
    assert set(audit["unclassifiable_reason"]["dalio_stage"]) == {
        "sovereign_solvency", "funding_liquidity", "private_credit", "external_constraint"}
    assert set(audit["unclassifiable_reason"]["deleveraging_type"]) == {
        "sovereign_solvency", "funding_liquidity", "external_constraint"}


def test_compute_insufficient_tier_gates_like_missing_row(tmp_db):
    # a row that EXISTS but is coverage_tier='insufficient' must gate exactly
    # like a missing row -- the gate checks tier, not row presence
    con = get_conn()
    _seed_arg_shaped(con)
    con.execute(
        "UPDATE engine_scores SET coverage_tier = 'insufficient', score = NULL, label = NULL "
        "WHERE country_iso3 = 'ARG' AND engine = 'external_constraint'")
    con.commit()

    df = cc.compute(con, REF)
    con.close()

    row = df.set_index("country_iso3").loc["ARG"]
    assert row["deleveraging_type"] is None
    assert row["dalio_stage"] is None


def test_compute_ignores_raw_score_uses_only_label(tmp_db):
    # proves the "no second hysteresis mechanism" design: changing the raw
    # score while holding the label fixed must not change the classification
    con = get_conn()
    _seed_arg_shaped(con)
    con.commit()
    df1 = cc.compute(con, REF)

    con = get_conn()
    con.execute("UPDATE engine_scores SET score = 61.0 WHERE country_iso3 = 'ARG' "
               "AND engine = 'funding_liquidity'")   # label stays 'severe'
    con.commit()
    df2 = cc.compute(con, REF)
    con.close()

    r1 = df1.set_index("country_iso3").loc["ARG"]
    r2 = df2.set_index("country_iso3").loc["ARG"]
    assert r1["dalio_stage"] == r2["dalio_stage"]
    assert r1["deleveraging_type"] == r2["deleveraging_type"]


def test_compute_empty_engine_scores_returns_empty_frame(tmp_db):
    con = get_conn()
    df = cc.compute(con, REF)
    con.close()
    assert df.empty
    assert list(df.columns) == cc._COLUMNS
