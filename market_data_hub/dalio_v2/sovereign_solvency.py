# -*- coding: utf-8 -*-
"""
sovereign_solvency.py — Dalio v2 Engine 1: Sovereign Solvency.

Can the state service its debt without default, extreme financial repression,
high inflation, persistent monetization, or politically destabilizing
austerity? See docs/DALIO_5ENGINE_IMPLEMENTATION_PLAN_2026-07.md Fase 1 for
the full design (7 components, r-g formula, income-group thresholds).

Reads the live v_macro_panel_ext (current known values, not vintage-aware
yet) — components_json marks vintage_safe=False. Fase A of
DALIO_VINTAGE_AND_AUDIT_PLAN_2026-07.md wires asof= point-in-time reads for
historical backtesting in a later pass; this module stays unchanged, only
its data source becomes swappable then.

debt_trend_5y reuses dalio.py's _slope()/_first_avail()/_latest() so the two
systems' debt trajectories are defined identically (same window, same
forecast inclusion), not two subtly different formulas.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

import duckdb
import pandas as pd

from market_data_hub.config_loader import get_countries, get_settings
from market_data_hub.dalio import _first_avail, _latest, _slope
from market_data_hub.dalio_v2.scoring import (
    bucket_with_hysteresis, confidence_for, coverage_tier, git_short_sha,
    prev_label, score_threshold, weighted_average,
)

ENGINE = "sovereign_solvency"

_IND = {
    "debt_gdp": "public_debt_gdp",
    "net_debt_gdp": "govt_net_debt_gdp",
    "interest_gdp": "interest_on_debt_gdp",
    "revenue_gdp": "government_revenue_gdp",
    "primary_balance_gdp": "primary_balance_gdp",
    "growth": ["gdp_growth_weo", "real_gdp_growth"],
    "inflation": ["inflation_avg_weo", "inflation_cpi"],
    "r_effective": "implied_interest_rate",
}

_COLUMNS = ["country_iso3", "ref_date", "engine", "score", "label", "coverage_tier",
           "confidence", "n_components", "n_expected", "components_json", "computed_at"]


def compute(con: duckdb.DuckDBPyConnection, ref_date, cfg: Optional[dict] = None
           ) -> pd.DataFrame:
    """Sovereign Solvency scores for every country in the panel as of
    ref_date. Returns a DataFrame ready to write to engine_scores."""
    settings = get_settings().get("dalio_v2", {})
    cfg = cfg or settings.get("sovereign_solvency", {})
    th = cfg.get("thresholds", {})
    weights = cfg.get("weights", {})
    bucket_thresholds = cfg.get("bucket_thresholds", [20, 40, 60, 80])
    bucket_labels = cfg.get("bucket_labels",
                            ["strong", "stable", "watch", "stressed", "critical"])
    margin_pct = settings.get("hysteresis_margin_pct", 0.10)

    dev = {c["iso3"]: c.get("development", "EM") for c in get_countries()}

    panel = con.execute(
        "SELECT date, country_iso3, indicator_id, value FROM v_macro_panel_ext "
        "WHERE value IS NOT NULL").fetch_df()
    if panel.empty:
        return pd.DataFrame(columns=_COLUMNS)
    panel["date"] = pd.to_datetime(panel["date"])
    ref_ts = pd.Timestamp(ref_date)

    sha = git_short_sha()
    now = datetime.now(timezone.utc)
    rows = []
    for country, cdf_full in panel.groupby("country_iso3"):
        cdf = cdf_full[cdf_full["date"] <= ref_ts]
        if cdf.empty:
            continue
        by_ind = {i: g[["date", "value"]] for i, g in cdf.groupby("indicator_id")}

        debt, _ = _latest(_first_avail(by_ind, _IND["debt_gdp"]))
        net_debt, _ = _latest(_first_avail(by_ind, _IND["net_debt_gdp"]))
        interest_gdp, _ = _latest(_first_avail(by_ind, _IND["interest_gdp"]))
        revenue_gdp, _ = _latest(_first_avail(by_ind, _IND["revenue_gdp"]))
        primary_balance, _ = _latest(_first_avail(by_ind, _IND["primary_balance_gdp"]))
        growth, _ = _latest(_first_avail(by_ind, _IND["growth"]))
        infl, _ = _latest(_first_avail(by_ind, _IND["inflation"]))
        r_eff, _ = _latest(_first_avail(by_ind, _IND["r_effective"]))

        debt_full = cdf_full[cdf_full["indicator_id"] == _IND["debt_gdp"]][["date", "value"]]
        debt_trend = _slope(debt_full, ref_ts.year - 3, ref_ts.year + 5)

        g_nom = (((1 + growth / 100.0) * (1 + infl / 100.0)) - 1) * 100.0 \
            if not (pd.isna(growth) or pd.isna(infl)) else float("nan")
        r_minus_g = (r_eff - g_nom) if not (pd.isna(r_eff) or pd.isna(g_nom)) else float("nan")
        interest_revenue = (interest_gdp / revenue_gdp * 100.0) \
            if not pd.isna(interest_gdp) and not pd.isna(revenue_gdp) and revenue_gdp != 0 \
            else float("nan")
        primary_deficit = -primary_balance if not pd.isna(primary_balance) else float("nan")

        grp = "dm" if dev.get(country, "EM") == "DM" else "em"
        debt_th = th.get(f"debt_gdp_{grp}", [90, 110, 130])
        net_debt_th = th.get(f"net_debt_gdp_{grp}", [90, 110, 130])

        raw_values = {
            "debt_gdp": debt, "net_debt_gdp": net_debt, "interest_revenue": interest_revenue,
            "interest_gdp": interest_gdp, "primary_deficit_gdp": primary_deficit,
            "r_minus_g": r_minus_g, "debt_trend_5y": debt_trend,
        }
        components = {
            "debt_gdp": score_threshold(debt, *debt_th),
            "net_debt_gdp": score_threshold(net_debt, *net_debt_th),
            "interest_revenue": score_threshold(interest_revenue, *th.get("interest_revenue", [10, 15, 25])),
            "interest_gdp": score_threshold(interest_gdp, *th.get("interest_gdp", [3, 5, 7])),
            "primary_deficit_gdp": score_threshold(primary_deficit, *th.get("primary_deficit_gdp", [2, 4, 6])),
            "r_minus_g": score_threshold(r_minus_g, *th.get("r_minus_g", [1, 3, 5])),
            "debt_trend_5y": score_threshold(debt_trend, *th.get("debt_trend_5y", [0.7, 1.5, 3.0])),
        }
        score, n_avail, n_exp = weighted_average(components, weights)
        tier = coverage_tier(n_avail, n_exp)
        conf = confidence_for(tier)
        prev = prev_label(con, country, ENGINE, ref_date)
        label = bucket_with_hysteresis(score, bucket_thresholds, bucket_labels, prev, margin_pct)

        audit = {
            "model_version": sha, "ref_date": str(ref_date), "asof": None,
            "income_group": dev.get(country, "EM"),
            "components": {
                k: {"value": None if pd.isna(raw_values[k]) else round(float(raw_values[k]), 4),
                    "score": components[k], "weight": weights.get(k, 0)}
                for k in components
            },
            "missing_components": [k for k, v in components.items() if v is None],
            "coverage_tier": tier, "vintage_safe": False,
        }
        rows.append((country, ref_date, ENGINE,
                    None if score is None else round(score, 2), label, tier, conf,
                    n_avail, n_exp, json.dumps(audit), now))

    return pd.DataFrame(rows, columns=_COLUMNS)
