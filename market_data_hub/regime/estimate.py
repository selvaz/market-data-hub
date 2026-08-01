# -*- coding: utf-8 -*-
"""
estimate.py — fit a per-symbol HMM regime model and persist it as-of today.

Every call to run_daily_regime_estimation() does a FULL refit per symbol on
the whole available daily-return history (not lazystats.regimes' fixed-parameter
apply_regime_params()) — that is what lets us observe, day by day, whether
adding one more day's data changes the model's read of the past. Results are
persisted to LazyStats' shared ``ResultDepot`` (a SQLite store, resolved via
``lazytools.registry.resolve_db("lazystats_depot")``) using its stable-series
append-on-change semantics (``ResultDepot.save_stable_point``): a past
(symbol, trading_date, estimation_date) reading is never overwritten, only
superseded by a new row once the discretized regime label actually differs
from the most recent prior estimate for that trading_date.

This module previously wrote directly into this repo's own DuckDB tables
(``hmm_regime_estimates`` / ``hmm_model_runs``) with a hand-rolled
``INSERT OR REPLACE`` CTE reimplementing this same change-detection logic.
That migration is complete: the historical rows were migrated into
ResultDepot and the old tables (and ``market_data_hub/regime/schema.py``)
were dropped/removed.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

import lazytools.registry as lazytools_registry
from lazystats.io.depot import ResultDepot
from lazystats.regimes import MSRegimeEngine, RegimeRun

from market_data_hub import catalog
from market_data_hub.db.connection import _resolve_db_path
from market_data_hub.extract import extract_returns

DEFAULT_S_MAX = 3
DEFAULT_N_STARTS = 20
DEFAULT_RANDOM_STATE = 123
DEFAULT_RETRO_DAYS = 30

# ResultDepot bookkeeping for this producer's writes.
_PRODUCED_BY = "scheduled:run_regime_daily"
_PROVENANCE_SOURCE = "market_data_hub.regime.estimate"

# _find_run_result_id()'s scan has no dedicated (series_key, estimation_date)
# query on ResultDepot (see its docstring) so it scans the most recent
# ``cadence="stable"`` index entries client-side instead. This caps how far
# back that scan looks; same-day reruns (the only case it needs to catch) are
# always near the front of the DESC-by-created_at list, so this comfortably
# covers even a large priority-1 universe.
_ERROR_GUARD_SCAN_LIMIT = 2000


@dataclass
class SymbolRunResult:
    symbol: str
    status: str                     # "ok" | "error" | "empty"
    error_msg: Optional[str] = None
    n_states: Optional[int] = None
    current_state: Optional[int] = None
    current_label: Optional[str] = None
    is_high_vol: Optional[bool] = None
    prob_high_vol: Optional[float] = None
    changed_today: bool = False
    revised_last_n_days: int = 0
    revised_dates: Optional[List[str]] = None
    run: Optional[RegimeRun] = None


def priority_symbols(priority: int = 1, db_path: Optional[str] = None) -> List[str]:
    """Symbols from tickers.yaml at the given priority tier (default: priority 1)."""
    df = catalog.list_symbols(with_coverage=False, db_path=db_path)
    if "priority" not in df.columns:
        return sorted(df["symbol"].tolist())
    return sorted(df.loc[df["priority"] == priority, "symbol"].tolist())


def fit_symbol_regime(symbol: str, *, db_path: Optional[str] = None,
                      S_max: int = DEFAULT_S_MAX, n_starts: int = DEFAULT_N_STARTS,
                      random_state: int = DEFAULT_RANDOM_STATE) -> RegimeRun:
    """Full refit of a 1-3 state Gaussian HMM on the symbol's whole daily-return
    history. Raises ValueError if there is not enough return history to fit."""
    df, meta = extract_returns([symbol], frequency="D", db_path=db_path)
    if df.empty or symbol not in df.columns:
        raise ValueError(f"No return history available for {symbol!r}")
    df = df[[symbol]].dropna()
    if len(df) < 30:
        raise ValueError(f"Not enough observations to fit {symbol!r} ({len(df)} rows)")

    engine = MSRegimeEngine(S_max=S_max, S_min=1, criterion="bic", n_starts=n_starts,
                            reorder_by="vol", reorder_ascending=True,
                            random_state=random_state)
    return engine.fit(df, model="panel", dropna="all")


def _series_key(symbol: str, db_path: Optional[str] = None) -> str:
    """``regime:<symbol>`` for the production DuckDB (the key format the
    405,313-row migration already used) -- but namespaced by the resolved
    input database's identity for any other one (e.g. ``--db test.duckdb``
    for a test/staging run), so an alternate-DB run can never supersede
    production vintages/diagnostics in the single, shared ResultDepot (which
    has no per-DuckDB isolation of its own, unlike the DuckDB file itself).
    """
    resolved = _resolve_db_path(db_path)
    if resolved == _resolve_db_path(None):
        return f"regime:{symbol}"
    db_id = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]
    return f"regime:{symbol}@{db_id}"


def _last_estimate(depot: ResultDepot, symbol: str, db_path: Optional[str] = None):
    """(max stored trading_date, n_states of the newest vintage) or (None, None).

    ``get_series_latest`` already returns, per as_of_date, only the value from
    that date's most recent estimation_date -- so the last entry (as_of_date
    is ascending) is both the max stored trading_date AND (since n_states is
    constant within one estimation_date, one fit per day, and every run's
    window always includes that day's newest trading_date -- see
    write_regime_run below) the n_states of the newest vintage overall."""
    latest = depot.get_series_latest(_series_key(symbol, db_path))
    if not latest:
        return None, None
    last_point = latest[-1]
    last_td = datetime.strptime(last_point["as_of_date"], "%Y-%m-%d").date()
    last_S = last_point["value"].get("n_states")
    return last_td, last_S


def _find_run_result_id(
    depot: ResultDepot, series_key: str, estimation_date_str: str
) -> tuple[Optional[str], Optional[str]]:
    """(result_id, status) of the existing fit-diagnostics result for this
    (series_key, estimation_date), or (None, None) if none exists.

    ``ResultDepot`` has no query keyed directly on (series_key,
    estimation_date), so this scans the most recent ``cadence="stable"``
    index entries (``depot.list``) and loads each candidate's full payload
    (``depot.load``) to check its ``series_key``/``estimation_date``. See
    ``_ERROR_GUARD_SCAN_LIMIT`` for the scan's bound.

    Used both to guard against a failed rerun clobbering a same-day success,
    and to upsert (rather than duplicate) a symbol's fit-diagnostics result
    when it's fitted more than once for the same day.
    """
    for entry in depot.list(cadence="stable", limit=_ERROR_GUARD_SCAN_LIMIT):
        if entry["series_key"] != series_key:
            continue
        full = depot.load(entry["result_id"])
        if full is None:
            continue
        payload = full["payload"]
        if payload.get("estimation_date") == estimation_date_str:
            return entry["result_id"], payload.get("status")
    return None, None


def write_regime_run(depot: ResultDepot, symbol: str, run: RegimeRun,
                     *, estimation_date: date, fit_seconds: float,
                     retro_days: int = DEFAULT_RETRO_DAYS,
                     db_path: Optional[str] = None) -> SymbolRunResult:
    panel = run.panel
    m = run.meta[symbol]
    S = int(m["S"])
    labels = m["labels"]
    series_key = _series_key(symbol, db_path)
    estimation_date_str = str(estimation_date)

    state = panel[f"{symbol}_state"].astype(int)
    highvol = panel[f"{symbol}_highvol"].astype(bool)
    prob_hv = panel[f"P_{symbol}_HV"].astype(float)
    prob_cols = [f"P_{symbol}_S{s}" for s in range(S)]
    gamma = panel[prob_cols]

    src = pd.DataFrame({
        "symbol": symbol,
        "trading_date": [d.date() for d in panel.index],
        "n_states": S,
        "state": state.values,
        "is_high_vol": highvol.values,
        "prob_high_vol": prob_hv.values,
        "state_probs": [[round(float(x), 6) for x in row] for row in gamma.values],
    })

    last_td, last_S = _last_estimate(depot, symbol, db_path)
    is_first_run = last_td is None
    # A BIC flip (e.g. 2 -> 3 states) renumbers every state: rewriting only the
    # retro window would leave the latest vintage mixing two incompatible
    # indexations, so the whole history is re-appended as one consistent
    # vintage (old vintages stay, per the append-on-change semantics above).
    model_flip = not is_first_run and last_S is not None and int(last_S) != S
    if is_first_run or model_flip:
        window = src
    else:
        # Eligible for insert: the retro window PLUS every row after the last
        # stored trading_date, so a pause longer than retro_days trading days
        # no longer leaves a permanent never-backfilled gap.
        mask = src["trading_date"] > last_td
        if retro_days > 0:
            mask.iloc[-retro_days:] = True
        window = src[mask]

    # One fit-diagnostics result per symbol per day; each per-date point below
    # is linked back to it via result_id (this replaces the hmm_model_runs row).
    # A symbol fitted more than once for the same estimation_date (a rerun)
    # upserts that existing result in place instead of accumulating a
    # duplicate -- mirroring hmm_model_runs' old PK(symbol, estimation_date).
    existing_result_id, _existing_status = _find_run_result_id(
        depot, series_key, estimation_date_str
    )
    result_id = depot.save(
        kind="regime",
        produced_by=_PRODUCED_BY,
        instruments=[symbol],
        payload={
            "n_states": S, "criterion": "bic", "bic": float(m["bic"]),
            "loglik": float(m["loglik"]),
            "data_start": str(panel.index.min().date()),
            "data_end": str(panel.index.max().date()),
            "n_obs": int(len(panel)),
            "transmat": np.asarray(m["transmat_"]).tolist(),
            "means": np.asarray(m["means_"]).tolist(),
            "covars": np.asarray(m["covars_"]).tolist(),
            "labels": labels, "fit_seconds": float(fit_seconds),
            "status": "ok", "error_msg": None,
            "estimation_date": estimation_date_str,
        },
        provenance={"source": _PROVENANCE_SOURCE, "fit_seconds": fit_seconds},
        cadence="stable", series_key=series_key,
        result_id=existing_result_id,
    )

    # save_stable_point() does its own per-date change-detection (append only
    # if the value actually differs from the last stored one for that
    # as_of_date) -- so, unlike the old hand-rolled DuckDB CTE, nothing here
    # needs to recompute "did this change"; we only track which dates it
    # reports as changed, to preserve the existing revision-reporting below.
    changed_map: Dict[str, bool] = {}
    for row in window.itertuples(index=False):
        d_str = str(row.trading_date)
        changed_map[d_str] = depot.save_stable_point(
            series_key=series_key, as_of_date=d_str, estimation_date=estimation_date_str,
            value={
                "state": int(row.state), "is_high_vol": bool(row.is_high_vol),
                "prob_high_vol": float(row.prob_high_vol), "n_states": S,
                "state_probs": row.state_probs,
            },
            result_id=result_id,
            # Label-only change detection, matching the original DuckDB CTE
            # (`state`/`n_states`/`is_high_vol` only): prob_high_vol/
            # state_probs shift on every refit merely from adding one more
            # day's observation, even when the discrete regime read is
            # unchanged -- comparing the full value would flag ~retro_days
            # false revisions per symbol on every single run.
            compare_keys=["state", "n_states", "is_high_vol"],
        )

    # The newest trading_date always writes (it is new, not a revision); any
    # other trading_date save_stable_point() actually changed is a genuine
    # retroactive revision.
    revised_dates: List[str] = []
    if not is_first_run and changed_map:
        newest_date = str(window["trading_date"].iloc[-1])
        revised_dates = sorted(
            d for d, changed in changed_map.items() if changed and d != newest_date
        )
    revised_count = len(revised_dates)

    cur_state = int(state.iloc[-1])
    changed_today = len(state) > 1 and int(state.iloc[-1]) != int(state.iloc[-2])

    return SymbolRunResult(
        symbol=symbol, status="ok", n_states=S, current_state=cur_state,
        current_label=labels[cur_state], is_high_vol=bool(highvol.iloc[-1]),
        prob_high_vol=float(prob_hv.iloc[-1]), changed_today=changed_today,
        revised_last_n_days=revised_count, revised_dates=revised_dates, run=run,
    )


def _write_error_run(depot: ResultDepot, symbol: str,
                     estimation_date: date, error_msg: str,
                     db_path: Optional[str] = None) -> None:
    series_key = _series_key(symbol, db_path)
    estimation_date_str = str(estimation_date)
    # A failed evening rerun must not clobber the BIC/params of a successful
    # same-day run (equivalent of the old hmm_model_runs PK (symbol,
    # estimation_date) guard against INSERT OR REPLACE).
    existing_result_id, existing_status = _find_run_result_id(
        depot, series_key, estimation_date_str
    )
    if existing_status == "ok":
        return
    depot.save(
        kind="regime",
        produced_by=_PRODUCED_BY,
        instruments=[symbol],
        payload={
            "status": "error", "error_msg": error_msg[:500],
            "estimation_date": estimation_date_str,
        },
        provenance={"source": _PROVENANCE_SOURCE},
        cadence="stable", series_key=series_key,
        result_id=existing_result_id,
    )


def run_daily_regime_estimation(*, symbols: Optional[List[str]] = None,
                                priority: int = 1, S_max: int = DEFAULT_S_MAX,
                                n_starts: int = DEFAULT_N_STARTS,
                                retro_days: int = DEFAULT_RETRO_DAYS,
                                asof: Optional[date] = None,
                                db_path: Optional[str] = None) -> Dict[str, SymbolRunResult]:
    """Fit + persist regimes for every requested symbol. Returns {symbol: SymbolRunResult}.

    A single symbol's failure is recorded as an error result and does not stop
    the run.

    Two phases, deliberately not interleaved: (1) fit every symbol — each fit
    pulls returns via its own short-lived read-only connection (extract_returns
    -> reader.read_prices); (2) construct one ``ResultDepot`` and persist all
    results through it. Mirrors the previous DuckDB writer's lifecycle (opened
    once, reused across symbols, closed at the end) even though the depot's
    own SQLite connection has none of DuckDB's single-writer-per-file
    restriction that motivated keeping phase 1's readers short-lived.
    """
    asof = asof or datetime.now().date()
    symbols = symbols or priority_symbols(priority, db_path=db_path)

    fits: Dict[str, tuple] = {}  # symbol -> (RegimeRun | None, error | None, fit_seconds)
    for symbol in symbols:
        t0 = time.time()
        try:
            run = fit_symbol_regime(symbol, db_path=db_path, S_max=S_max,
                                    n_starts=n_starts)
            fits[symbol] = (run, None, time.time() - t0)
        except Exception as exc:  # noqa: BLE001 - one bad symbol must not abort the run
            fits[symbol] = (None, str(exc), time.time() - t0)

    # Required core persistence, not the artifact-store's best-effort optional
    # pattern (see market_data_hub/artifact_registry.py): resolve_db raises
    # RuntimeError if LAZYSTATS_RESULT_DEPOT_DB is unset, and that is left to
    # propagate -- a regime run that cannot persist its results must fail loudly.
    depot_path = lazytools_registry.resolve_db("lazystats_depot")
    # resolve_db's return type is Optional[str] for the general (optional-DB)
    # case, but "lazystats_depot" is declared required=True in KNOWN_DBS, so
    # it always either returns a real path or raises above -- never None.
    assert depot_path is not None
    depot = ResultDepot(depot_path)
    try:
        results: Dict[str, SymbolRunResult] = {}
        for symbol, (run, error_msg, fit_seconds) in fits.items():
            if run is None:
                _write_error_run(depot, symbol, asof, error_msg, db_path)
                results[symbol] = SymbolRunResult(symbol=symbol, status="error",
                                                  error_msg=error_msg)
            else:
                results[symbol] = write_regime_run(
                    depot, symbol, run, estimation_date=asof,
                    fit_seconds=fit_seconds, retro_days=retro_days,
                    db_path=db_path,
                )
    finally:
        depot.close()
    return results


def summary_dataframe(results: Dict[str, SymbolRunResult]) -> pd.DataFrame:
    rows = []
    for symbol, r in results.items():
        rows.append({
            "symbol": symbol, "status": r.status, "error_msg": r.error_msg,
            "n_states": r.n_states, "current_state": r.current_state,
            "current_label": r.current_label, "is_high_vol": r.is_high_vol,
            "prob_high_vol": r.prob_high_vol, "changed_today": r.changed_today,
            "revised_last_n_days": r.revised_last_n_days,
        })
    return pd.DataFrame(rows)
