"""Compare each symbol's full-history regime fit against a shorter-window
fit (e.g. the last 8 years) to surface STRUCTURAL changes: does restricting
to recent history change how many regimes a symbol shows, or which one
it's currently in?

Both fits already live in ``lazystats_depot`` (see ``estimate.py``'s
``variant`` param) -- this module only reads them back, no re-fitting.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from lazystats.io.depot import ResultDepot

from market_data_hub.regime.daily_payload import (
    PERIODS_PER_YEAR, _annualized_state_stats, _clean_name, classify_vol_tiers,
)
from market_data_hub.regime.estimate import _PROVENANCE_SOURCE, _series_key
from market_data_hub.regime.names import display_names

__all__ = [
    "build_comparison_payload",
    "classify_calm_or_highvol",
    "WINDOW_COMPARISON_KIND",
    "WINDOW_COMPARISON_SERIES_KEY",
]

WINDOW_COMPARISON_KIND = "regime_window_comparison"
WINDOW_COMPARISON_SERIES_KEY = "regime_window_comparison"

# How far back depot.list()'s DESC-by-created_at scan looks for a series_key
# match -- generous enough to comfortably cover ~90 symbols x 2 variants
# worth of history without a dedicated (series_key -> result_id) index.
_SCAN_LIMIT = 5000


def classify_calm_or_highvol(states: List[Dict[str, Any]]) -> Dict[int, str]:
    """Collapse a model's states to exactly 2 groups for cross-window
    comparison: the lowest-volatility state anchors "calm"; every state
    (including that anchor) then joins "calm" if its OWN annualized mean
    return is >= 0, else "highvol". A single-state model has nothing to
    rank against -- "single" for its one state."""
    if len(states) <= 1:
        return {st["state"]: "single" for st in states}
    lowest_vol_state = min(states, key=lambda st: st["annualized_volatility"])["state"]
    groups: Dict[int, str] = {}
    for st in states:
        if st["state"] == lowest_vol_state:
            groups[st["state"]] = "calm"
        else:
            groups[st["state"]] = "calm" if st["annualized_mean_return"] >= 0 else "highvol"
    return groups


def _tier_label(ranked_tiers: List[str], state_index: Optional[int]) -> str:
    """``ranked_tiers[state_index]``, normalized to match
    ``classify_calm_or_highvol``'s vocabulary (calm/mid/highvol/single) --
    ``classify_vol_tiers`` uses "high" for the same concept."""
    if state_index is None or state_index >= len(ranked_tiers):
        return "single"
    label = ranked_tiers[state_index]
    return "highvol" if label == "high" else label


def _latest_regime_row(depot: ResultDepot, series_key: str) -> Optional[dict]:
    """The newest ``kind="regime"`` row for ``series_key``, or None if
    nothing's been fit yet. ``depot.list()`` is already DESC by
    created_at, so the first series_key match is the newest."""
    for entry in depot.list(cadence="stable", limit=_SCAN_LIMIT):
        if entry["series_key"] != series_key or entry["kind"] != "regime":
            continue
        row = depot.load(entry["result_id"])
        if row is not None:
            return row
    return None


def _window_snapshot(depot: ResultDepot, symbol: str, *, variant: Optional[str], db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Everything needed about ONE symbol's ONE window (full-history when
    ``variant`` is None, otherwise e.g. "8y"): per-state annualized stats,
    current state + full probability vector. None if nothing's been fit
    for this window, or the latest fit errored."""
    series_key = _series_key(symbol, db_path, variant=variant)
    regime_row = _latest_regime_row(depot, series_key)
    if regime_row is None or regime_row["payload"].get("status") == "error":
        return None
    payload = regime_row["payload"]
    states = _annualized_state_stats(payload.get("means") or [], payload.get("covars") or [], payload.get("labels") or [])

    points = depot.get_series_latest(series_key)
    if not points:
        return None
    latest_point = points[-1]["value"]

    return {
        "n_states": payload.get("n_states"),
        "states": states,
        "current_state": latest_point["state"],
        "current_state_probs": latest_point["state_probs"],
        "as_of_date": points[-1]["as_of_date"],
        "data_start": payload.get("data_start"),
        "data_end": payload.get("data_end"),
    }


def _compare_symbol(full: Optional[Dict[str, Any]], windowed: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if full is None or windowed is None:
        return {
            "status": "missing",
            "full_available": full is not None,
            "windowed_available": windowed is not None,
        }

    same_n = full["n_states"] == windowed["n_states"]

    if same_n:
        # Same state count in both windows -- states are directly comparable
        # index-by-index (both fits reorder_by="vol" ascending, so state 0
        # is the lowest-vol state in BOTH). Use the full 3-tier calm/mid/high
        # ranking rather than collapsing to 2 groups -- collapsing here would
        # hide a genuine mid-vol regime that both windows agree exists.
        full_rank = classify_vol_tiers([st["annualized_volatility"] for st in full["states"]])
        windowed_rank = classify_vol_tiers([st["annualized_volatility"] for st in windowed["states"]])
        full_current_tier = _tier_label(full_rank, full["current_state"])
        windowed_current_tier = _tier_label(windowed_rank, windowed["current_state"])
        comparison_mode = "direct"
    else:
        # Different state count -- not directly comparable index-by-index,
        # so collapse both to exactly 2 groups: the lowest-vol state anchors
        # "calm", every other state (per its own mean-return sign) joins
        # "calm" or "highvol".
        full_tiers = classify_calm_or_highvol(full["states"])
        windowed_tiers = classify_calm_or_highvol(windowed["states"])
        full_current_tier = full_tiers.get(full["current_state"], "single")
        windowed_current_tier = windowed_tiers.get(windowed["current_state"], "single")
        comparison_mode = "collapsed_2group"

    if full_current_tier == "single" or windowed_current_tier == "single":
        agreement = "single_state"
    else:
        agreement = "agree" if full_current_tier == windowed_current_tier else "disagree"

    return {
        "status": "ok",
        "n_states_full": full["n_states"],
        "n_states_windowed": windowed["n_states"],
        "n_states_differ": not same_n,
        "comparison_mode": comparison_mode,
        "current_tier_full": full_current_tier,
        "current_tier_windowed": windowed_current_tier,
        "agreement": agreement,
        "as_of_full": full["as_of_date"],
        "as_of_windowed": windowed["as_of_date"],
        "data_start_full": full["data_start"],
        "data_start_windowed": windowed["data_start"],
    }


def build_comparison_payload(
    depot: ResultDepot,
    symbols: List[str],
    *,
    variant: str,
    asof: date,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    names = {s: _clean_name(n, s) for s, n in display_names().items()}
    symbols_out: List[Dict[str, Any]] = []
    n_disagree = 0
    n_single = 0
    n_missing = 0

    for symbol in sorted(symbols):
        full = _window_snapshot(depot, symbol, variant=None, db_path=db_path)
        windowed = _window_snapshot(depot, symbol, variant=variant, db_path=db_path)
        comparison = _compare_symbol(full, windowed)
        if comparison["status"] == "missing":
            n_missing += 1
        elif comparison["agreement"] == "single_state":
            n_single += 1
        elif comparison["agreement"] == "disagree":
            n_disagree += 1

        symbols_out.append(
            {
                "symbol": symbol,
                "name": names.get(symbol),
                "full": full,
                "windowed": windowed,
                "comparison": comparison,
            }
        )

    return {
        "as_of": asof.isoformat(),
        "variant": variant,
        "symbols": symbols_out,
        "summary": {
            "n_symbols": len(symbols_out),
            "n_disagree": n_disagree,
            "n_single_state": n_single,
            "n_missing": n_missing,
        },
        "provenance": {
            "source": _PROVENANCE_SOURCE,
            "periods_per_year": PERIODS_PER_YEAR,
            "as_of": asof.isoformat(),
            "variant": variant,
            "classification_rule": (
                "When both windows have the SAME state count, states are directly comparable "
                "index-by-index (both fits reorder_by='vol' ascending) -- compare the full "
                "3-tier calm/mid/high volatility ranking directly, no collapsing. Only when "
                "state counts DIFFER, collapse each window's states to exactly 2 groups: the "
                "lowest-volatility state anchors 'calm'; every other state joins 'calm' if its "
                "own annualized mean return is >= 0, else 'highvol'. A single-state model is "
                "flagged 'single' rather than compared."
            ),
        },
    }
