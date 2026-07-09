# -*- coding: utf-8 -*-
"""
political_execution.py — Dalio v2 Engine 5: Political Execution.

Can the country make the fiscal/structural adjustment its debt situation
requires, without a political crisis? See
docs/DALIO_5ENGINE_IMPLEMENTATION_PLAN_2026-07.md Fase 1.

Built entirely on the 5 WGI indicators already wired into macro_panel
(governance pillar) — no new connector needed. voice_accountability is
intentionally excluded (not in the source proposal's §9.3 formula).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

import duckdb
import pandas as pd

from market_data_hub.config_loader import get_settings
from market_data_hub.dalio_v2.scoring import (
    bucket_with_hysteresis, confidence_for, coverage_tier, git_short_sha,
    percentile_rank, prev_label, weighted_average,
)

ENGINE = "political_execution"

_WGI = {
    "government_effectiveness": "wgi_government_effectiveness",
    "rule_of_law": "wgi_rule_of_law",
    "control_corruption": "wgi_control_corruption",
    "political_stability": "wgi_political_stability",
    "regulatory_quality": "wgi_regulatory_quality",
}

_COLUMNS = ["country_iso3", "ref_date", "engine", "score", "label", "coverage_tier",
           "confidence", "n_components", "n_expected", "components_json", "computed_at"]


def compute(con: duckdb.DuckDBPyConnection, ref_date, cfg: Optional[dict] = None
           ) -> pd.DataFrame:
    """Political Execution scores for every country with WGI coverage as of
    ref_date. Returns a DataFrame ready to write to engine_scores."""
    settings = get_settings().get("dalio_v2", {})
    cfg = cfg or settings.get("political_execution", {})
    weights = cfg.get("weights", {})
    bucket_thresholds = cfg.get("bucket_thresholds", [20, 40, 60, 80])
    bucket_labels = cfg.get("bucket_labels",
                            ["strong", "adequate", "watch", "weak", "impaired"])
    margin_pct = settings.get("hysteresis_margin_pct", 0.10)

    ids = list(_WGI.values())
    placeholders = ",".join("?" * len(ids))
    panel = con.execute(
        f"SELECT date, country_iso3, indicator_id, value FROM macro_panel "
        f"WHERE indicator_id IN ({placeholders}) AND value IS NOT NULL AND date <= ?",
        ids + [ref_date]).fetch_df()
    if panel.empty:
        return pd.DataFrame(columns=_COLUMNS)
    panel["date"] = pd.to_datetime(panel["date"])

    latest = (panel.sort_values("date")
                    .groupby(["country_iso3", "indicator_id"]).tail(1))
    wide = latest.pivot(index="country_iso3", columns="indicator_id", values="value")

    # cross-country percentile per indicator (100 = best governance), then
    # inverted so a HIGHER engine score = worse, consistent with the other
    # engines (Sovereign Solvency etc. are all "high score = high risk").
    risk = pd.DataFrame(index=wide.index)
    for key, ind in _WGI.items():
        risk[key] = 100.0 - percentile_rank(wide[ind]) if ind in wide.columns else float("nan")

    sha = git_short_sha()
    now = datetime.now(timezone.utc)
    rows = []
    for country in risk.index:
        components = {k: (None if pd.isna(risk.loc[country, k]) else float(risk.loc[country, k]))
                     for k in _WGI}
        raw_values = {
            k: (None if ind not in wide.columns or pd.isna(wide.loc[country, ind])
                else float(wide.loc[country, ind]))
            for k, ind in _WGI.items()
        }
        score, n_avail, n_exp = weighted_average(components, weights)
        tier = coverage_tier(n_avail, n_exp)
        conf = confidence_for(tier)
        prev = prev_label(con, country, ENGINE, ref_date)
        label = bucket_with_hysteresis(score, bucket_thresholds, bucket_labels, prev, margin_pct)

        audit = {
            "model_version": sha, "ref_date": str(ref_date), "asof": None,
            "components": {
                k: {"wgi_raw": raw_values[k], "risk_percentile": components[k],
                    "weight": weights.get(k, 0)}
                for k in _WGI
            },
            "missing_components": [k for k, v in components.items() if v is None],
            "coverage_tier": tier, "vintage_safe": False,
        }
        rows.append((country, ref_date, ENGINE,
                    None if score is None else round(score, 2), label, tier, conf,
                    n_avail, n_exp, json.dumps(audit), now))

    return pd.DataFrame(rows, columns=_COLUMNS)
