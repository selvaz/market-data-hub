# -*- coding: utf-8 -*-
"""
scoring.py — shared helpers for the Dalio v2 5-engine architecture.

See docs/DALIO_5ENGINE_IMPLEMENTATION_PLAN_2026-07.md (Fase 0/Fase 3 design
decisions) for the rationale: robust (median/MAD) cross-country z-scores
instead of mean/std, linear threshold-to-score interpolation instead of
crisp if/elif buckets, hysteresis on bucket transitions so a score
oscillating near a boundary does not flip label every run, and an explicit
coverage tier per engine score so partial data is never silently treated as
full coverage.
"""
from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

import pandas as pd


def robust_z(values: pd.Series, orientation: int = 1) -> pd.Series:
    """Cross-country z-score via median/MAD (robust to fat tails/outliers),
    oriented so higher = worse when orientation=1, clipped to +/-3.5 (the
    conventional MAD-outlier cutoff). orientation=0 -> all zeros (no signal)."""
    if orientation == 0 or values.dropna().empty:
        return pd.Series(0.0, index=values.index)
    median = values.median()
    mad = (values - median).abs().median()
    if not mad or pd.isna(mad):
        return pd.Series(0.0, index=values.index)
    z = 0.6745 * (values - median) / mad * orientation
    return z.clip(-3.5, 3.5)


def percentile_rank(values: pd.Series) -> pd.Series:
    """0-100 cross-country percentile rank (100 = highest raw value)."""
    if values.dropna().empty:
        return pd.Series(float("nan"), index=values.index)
    return values.rank(pct=True) * 100.0


def score_threshold(value: Optional[float], watch: float, stress: float,
                    critical: float, orientation: int = 1) -> Optional[float]:
    """Map a raw value onto a 0-100 risk score by linear interpolation between
    three named thresholds: 0 at/below `watch`, 50 at `stress`, 100 at/above
    `critical`. orientation=-1 flips the direction (a LOW value is worse);
    pass the thresholds in the same order regardless of orientation (watch is
    always the mildest cut point — so for orientation=-1 the raw thresholds
    are DESCENDING, e.g. reserves_months [4, 3, 2]). Thresholds that are not
    strictly ordered after the flip raise: without this check the function
    silently degenerates into a 0/100 cliff at `watch` and the other two
    thresholds are ignored. Returns None if value is missing."""
    if value is None or pd.isna(value):
        return None
    if orientation >= 0:
        v, w, s, c = value, watch, stress, critical
    else:
        v, w, s, c = -value, -watch, -stress, -critical
    if not (w < s < c):
        raise ValueError(
            f"score_threshold: thresholds ({watch}, {stress}, {critical}) with "
            f"orientation={orientation} are not strictly ordered mildest-to-worst")
    if v <= w:
        return 0.0
    if v <= s:
        return 50.0 * (v - w) / (s - w)
    if v <= c:
        return 50.0 + 50.0 * (v - s) / (c - s)
    return 100.0


def weighted_average(components: Dict[str, Optional[float]],
                     weights: Dict[str, float]) -> Tuple[Optional[float], int, int]:
    """Weighted average of the available (non-None) component scores. Missing
    components are simply dropped from the denominator (no silent
    redistribution beyond that) — caller reports n_available/n_expected via
    the returned tuple so coverage_tier() can classify the row honestly.
    A component with zero/unknown weight is excluded from n_available too:
    it contributes nothing to the score, so counting it as coverage would
    let a weight-key typo in settings.yaml silently inflate the tier."""
    num = den = 0.0
    n_avail = 0
    for name, val in components.items():
        if val is None or pd.isna(val):
            continue
        w = weights.get(name, 0.0)
        if w <= 0:
            continue
        num += w * val
        den += w
        n_avail += 1
    score = (num / den) if den else None
    return score, n_avail, len(components)


def coverage_tier(n_available: int, n_expected: int) -> str:
    """'full' if >=80% of expected inputs are present, 'proxy' if >=40%,
    otherwise 'insufficient'."""
    if n_expected <= 0 or n_available <= 0:
        return "insufficient"
    ratio = n_available / n_expected
    if ratio >= 0.8:
        return "full"
    if ratio >= 0.4:
        return "proxy"
    return "insufficient"


def suppress_insufficient(score: Optional[float], tier: str) -> Optional[float]:
    """Enforce the coverage-tier discipline end to end: a row with
    'insufficient' coverage must never carry a numeric score, no matter how
    that score was computed. Without this, a country with e.g. 1 of 7
    components available reads as a confident '0.0 / strong' if that one
    lucky component happens to be safe -- the coverage badge would flag it,
    but the number alone looks authoritative and isn't. Call this AFTER any
    tier adjustment (proxy caps, discounts, etc.), right before bucket
    assignment, in every engine."""
    return None if tier == "insufficient" else score


def confidence_for(tier: str) -> str:
    return {"full": "high", "proxy": "medium", "insufficient": "low"}.get(tier, "low")


def _bucket_index(score: float, thresholds: Sequence[float]) -> int:
    idx = 0
    for t in thresholds:
        if score >= t:
            idx += 1
        else:
            break
    return idx


def bucket_with_hysteresis(score: Optional[float], thresholds: Sequence[float],
                           labels: Sequence[str], prev_label: Optional[str] = None,
                           margin_pct: float = 0.10) -> Optional[str]:
    """Assign `score` (0-100) to one of len(thresholds)+1 buckets defined by
    ascending cut points, with a dead-band around the boundary adjacent to
    `prev_label` so single-boundary flutter does not flip the label every
    run. A move spanning more than one bucket always applies immediately
    (hysteresis smooths flutter, it never suppresses a genuine large move).
    `prev_label` = the label previously assigned to this (country, engine)
    pair, or None for a first-ever computation (plain threshold assignment)."""
    if score is None or pd.isna(score):
        return None
    plain_idx = min(_bucket_index(score, thresholds), len(labels) - 1)
    if prev_label is None or prev_label not in labels:
        return labels[plain_idx]
    prev_idx = labels.index(prev_label)
    if abs(plain_idx - prev_idx) != 1:
        return labels[plain_idx]
    boundary_idx = min(plain_idx, prev_idx)
    boundary = thresholds[boundary_idx]
    lo = thresholds[boundary_idx - 1] if boundary_idx - 1 >= 0 else 0.0
    hi = thresholds[boundary_idx + 1] if boundary_idx + 1 < len(thresholds) else 100.0
    margin = margin_pct * (hi - lo)
    if plain_idx > prev_idx:      # moving to a worse (higher) bucket
        return labels[plain_idx] if score >= boundary + margin else prev_label
    return labels[plain_idx] if score <= boundary - margin else prev_label  # moving to a better bucket


@lru_cache(maxsize=1)
def git_short_sha() -> str:
    """Short git SHA of the running checkout, for the components_json
    model_version field. 'unknown' outside a git checkout (never raises)."""
    try:
        repo_root = Path(__file__).resolve().parents[2]
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=repo_root,
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return "unknown"


def prev_label(con, country_iso3: str, engine: str, before_date) -> Optional[str]:
    """Last non-NULL label assigned to (country, engine) strictly before
    `before_date`, for bucket_with_hysteresis(). NULL-label rows (periods of
    insufficient coverage) are skipped, so hysteresis survives a data outage
    instead of silently resetting to plain assignment. None if the pair has
    never carried a label."""
    row = con.execute(
        "SELECT label FROM engine_scores WHERE country_iso3 = ? AND engine = ? "
        "AND ref_date < ? AND label IS NOT NULL "
        "ORDER BY ref_date DESC LIMIT 1",
        [country_iso3, engine, before_date]).fetchone()
    return row[0] if row else None
