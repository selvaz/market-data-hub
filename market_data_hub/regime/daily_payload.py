"""Build the consolidated daily regime-report payload -- one self-contained,
JSON-serialisable dict per day covering every fitted symbol: display name,
ALL states' annualized mean/vol (not just the current one), the full
current state-probability vector, and change/revision flags.

This is the single source of truth ``daily_render.render_html`` needs --
no live DB access, no re-fitting -- so any saved row can always be
re-rendered from its own JSON alone (see ``render_regime_report.py``).
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Dict, List, Optional

from lazystats.io.depot import ResultDepot

from market_data_hub.regime.estimate import SymbolRunResult, _PROVENANCE_SOURCE
from market_data_hub.regime.names import display_names

PERIODS_PER_YEAR = 252  # the fit is on daily returns

__all__ = ["build_daily_payload", "REGIME_REPORT_KIND", "REGIME_REPORT_SERIES_KEY"]

REGIME_REPORT_KIND = "regime_daily_report"
REGIME_REPORT_SERIES_KEY = "regime_daily_report"


def _annualized_state_stats(means: List[List[float]], covars: List[List[List[float]]], labels: List[str]) -> List[Dict[str, Any]]:
    """Per state: annualized mean return and volatility from the fit's
    (per-period) means_/covars_ -- univariate fit, so means[s] and
    covars[s] each hold exactly one feature. is_high_vol marks the single
    state with the highest annualized volatility, matching the same
    argmax(vol) rule the fit itself uses to classify "high-vol" days."""
    stats = []
    for s, (mean_row, covar_row) in enumerate(zip(means, covars)):
        mean_period = float(mean_row[0])
        var_period = float(covar_row[0][0])
        stats.append(
            {
                "state": s,
                "label": labels[s] if s < len(labels) else str(s),
                "annualized_mean_return": mean_period * PERIODS_PER_YEAR,
                "annualized_volatility": math.sqrt(max(var_period, 0.0) * PERIODS_PER_YEAR),
            }
        )
    if stats:
        high_idx = max(range(len(stats)), key=lambda i: stats[i]["annualized_volatility"])
        for i, st in enumerate(stats):
            st["is_high_vol"] = i == high_idx
    return stats


def classify_vol_tiers(annualized_vols: List[float]) -> List[str]:
    """Rank states by volatility: lowest = "calm", highest = "high", anything
    in between = "mid"; a single state has nothing to rank against, so it's
    "single". Shared by both the JSON-first report (daily_render.py, in JS)
    and the older chart-based report (report.py), so "what counts as mid"
    can't drift between the two."""
    n = len(annualized_vols)
    if n <= 1:
        return ["single"] * n
    order = sorted(range(n), key=lambda i: annualized_vols[i])
    tiers = [""] * n
    for rank, idx in enumerate(order):
        tiers[idx] = "calm" if rank == 0 else "high" if rank == n - 1 else "mid"
    return tiers


def current_state_tier(depot: ResultDepot, result_id: Optional[str], current_state: Optional[int]) -> str:
    """The calm/mid/high/single tier of ``current_state``, read back from
    the symbol's saved fit row -- for callers (like the older chart-based
    report) that only have a ``SymbolRunResult`` + its ``result_id``, not
    the fuller per-state breakdown ``build_daily_payload`` assembles."""
    if result_id is None or current_state is None:
        return "single"
    fit_row = depot.load(result_id)
    if fit_row is None:
        return "single"
    payload = fit_row["payload"]
    states = _annualized_state_stats(payload.get("means") or [], payload.get("covars") or [], payload.get("labels") or [])
    tiers = classify_vol_tiers([st["annualized_volatility"] for st in states])
    if current_state < len(tiers):
        return tiers[current_state]
    return "single"


def _clean_name(raw_name: str | None, symbol: str) -> str:
    """``display_names()`` returns tickers.yaml's name field verbatim, which
    is stored as "CATEGORY | AREA | Proper Name" -- keep only the last
    segment. Falls back to the symbol itself if no name is on file."""
    if not raw_name:
        return symbol
    return raw_name.rsplit("|", 1)[-1].strip() or symbol


def build_daily_payload(
    depot: ResultDepot,
    results: Dict[str, SymbolRunResult],
    *,
    asof: date,
) -> Dict[str, Any]:
    names = {s: _clean_name(n, s) for s, n in display_names().items()}
    symbols_out: List[Dict[str, Any]] = []
    errors_out: List[Dict[str, Any]] = []
    n_changed = 0
    n_revised = 0

    for symbol in sorted(results):
        r = results[symbol]
        if r.status != "ok":
            errors_out.append({"symbol": symbol, "name": names.get(symbol), "error_msg": r.error_msg})
            continue

        fit_row = depot.load(r.result_id) if r.result_id else None
        fit_payload = fit_row["payload"] if fit_row else {}
        means = fit_payload.get("means") or []
        covars = fit_payload.get("covars") or []
        labels = fit_payload.get("labels") or []
        states = _annualized_state_stats(means, covars, labels)

        if r.changed_today:
            n_changed += 1
        if r.revised_last_n_days:
            n_revised += 1

        symbols_out.append(
            {
                "symbol": symbol,
                "name": names.get(symbol),
                "n_states": r.n_states,
                "current_state": r.current_state,
                "current_label": r.current_label,
                "current_state_probs": r.current_state_probs,
                "is_high_vol": r.is_high_vol,
                "changed_today": r.changed_today,
                "revised_last_n_days": r.revised_last_n_days,
                "revised_dates": r.revised_dates or [],
                "states": states,
                "fit": {
                    "bic": fit_payload.get("bic"),
                    "loglik": fit_payload.get("loglik"),
                    "data_start": fit_payload.get("data_start"),
                    "data_end": fit_payload.get("data_end"),
                    "n_obs": fit_payload.get("n_obs"),
                },
            }
        )

    return {
        "as_of": asof.isoformat(),
        "symbols": symbols_out,
        "errors": errors_out,
        "summary": {
            "n_ok": len(symbols_out),
            "n_errors": len(errors_out),
            "n_changed_today": n_changed,
            "n_revised": n_revised,
        },
        "provenance": {
            "source": _PROVENANCE_SOURCE,
            "periods_per_year": PERIODS_PER_YEAR,
            "as_of": asof.isoformat(),
        },
    }
