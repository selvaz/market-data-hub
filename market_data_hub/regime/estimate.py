# -*- coding: utf-8 -*-
"""
estimate.py — fit a per-symbol HMM regime model and persist it as-of today.

Every call to run_daily_regime_estimation() does a FULL refit per symbol on
the whole available daily-return history (not lazyhmm's fixed-parameter
apply_regime_params()) — that is what lets us observe, day by day, whether
adding one more day's data changes the model's read of the past. Results are
written with record-vintage-style append-on-change semantics (see
market_data_hub/db/upsert.py::record_vintage) so a past estimate is never
overwritten: a new (symbol, trading_date, estimation_date) row is inserted
only when the discretized regime label actually differs from the most recent
prior estimate for that trading_date.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Dict, List, Optional

import duckdb
import pandas as pd

from lazyhmm import MSRegimeEngine, RegimeRun

from market_data_hub import catalog
from market_data_hub.extract import extract_returns

DEFAULT_S_MAX = 3
DEFAULT_N_STARTS = 20
DEFAULT_RANDOM_STATE = 123
DEFAULT_RETRO_DAYS = 30


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


def _existing_count(con: duckdb.DuckDBPyConnection, symbol: str) -> int:
    row = con.execute(
        "SELECT count(*) FROM hmm_regime_estimates WHERE symbol = ?", [symbol]
    ).fetchone()
    return int(row[0]) if row else 0


def write_regime_run(con: duckdb.DuckDBPyConnection, symbol: str, run: RegimeRun,
                     *, estimation_date: date, fit_seconds: float,
                     retro_days: int = DEFAULT_RETRO_DAYS) -> SymbolRunResult:
    panel = run.panel
    m = run.meta[symbol]
    S = int(m["S"])
    labels = m["labels"]

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
        "state_probs_json": [json.dumps([round(float(x), 6) for x in row])
                              for row in gamma.values],
    })

    is_first_run = _existing_count(con, symbol) == 0
    window = src if is_first_run else src.tail(retro_days)

    con.register("_hmm_src", window)
    n0 = con.execute(
        "SELECT count(*) FROM hmm_regime_estimates WHERE symbol = ?", [symbol]
    ).fetchone()[0]
    con.execute(
        """
        INSERT OR REPLACE INTO hmm_regime_estimates
            (symbol, trading_date, estimation_date, n_states, state,
             is_high_vol, prob_high_vol, state_probs_json)
        WITH latest AS (
            SELECT v.* FROM hmm_regime_estimates v
            JOIN (
                SELECT symbol, trading_date, max(estimation_date) AS md
                FROM hmm_regime_estimates WHERE symbol = ? GROUP BY symbol, trading_date
            ) m ON v.symbol = m.symbol AND v.trading_date = m.trading_date
                AND v.estimation_date = m.md
        )
        SELECT s.symbol, s.trading_date, ?::DATE, s.n_states, s.state,
               s.is_high_vol, s.prob_high_vol, s.state_probs_json
        FROM _hmm_src s LEFT JOIN latest l ON s.trading_date = l.trading_date
        WHERE l.trading_date IS NULL
           OR l.state IS DISTINCT FROM s.state
           OR l.n_states IS DISTINCT FROM s.n_states
           OR l.is_high_vol IS DISTINCT FROM s.is_high_vol
        """,
        [symbol, str(estimation_date)],
    )
    n1 = con.execute(
        "SELECT count(*) FROM hmm_regime_estimates WHERE symbol = ?", [symbol]
    ).fetchone()[0]
    con.unregister("_hmm_src")

    rows_written = int(n1 - n0)
    # The newest trading_date always writes (it is new, not a revision); any
    # further rows written are genuine retroactive revisions.
    revised_count = max(0, rows_written - 1) if not is_first_run else 0
    revised_dates: List[str] = []
    if revised_count:
        revised_dates = [
            str(d) for d in window["trading_date"].tolist()[:-1]
        ][-retro_days:][-revised_count:]

    cur_state = int(state.iloc[-1])
    changed_today = len(state) > 1 and int(state.iloc[-1]) != int(state.iloc[-2])

    con.execute(
        """
        INSERT OR REPLACE INTO hmm_model_runs
            (symbol, estimation_date, n_states, criterion, bic, loglik,
             data_start, data_end, n_obs, transmat_json, means_json,
             covars_json, labels_json, fit_seconds, status, error_msg, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ok', NULL, ?)
        """,
        [symbol, str(estimation_date), S, "bic", float(m["bic"]), float(m["loglik"]),
         panel.index.min().date(), panel.index.max().date(), int(len(panel)),
         json.dumps(m["transmat_"]), json.dumps(m["means_"]), json.dumps(m["covars_"]),
         json.dumps(labels), float(fit_seconds), datetime.now(timezone.utc)],
    )

    return SymbolRunResult(
        symbol=symbol, status="ok", n_states=S, current_state=cur_state,
        current_label=labels[cur_state], is_high_vol=bool(highvol.iloc[-1]),
        prob_high_vol=float(prob_hv.iloc[-1]), changed_today=changed_today,
        revised_last_n_days=revised_count, revised_dates=revised_dates, run=run,
    )


def _write_error_run(con: duckdb.DuckDBPyConnection, symbol: str,
                     estimation_date: date, error_msg: str) -> None:
    con.execute(
        """
        INSERT OR REPLACE INTO hmm_model_runs
            (symbol, estimation_date, status, error_msg, created_at)
        VALUES (?, ?, 'error', ?, ?)
        """,
        [symbol, str(estimation_date), error_msg[:500], datetime.now(timezone.utc)],
    )


def run_daily_regime_estimation(*, symbols: Optional[List[str]] = None,
                                priority: int = 1, S_max: int = DEFAULT_S_MAX,
                                n_starts: int = DEFAULT_N_STARTS,
                                retro_days: int = DEFAULT_RETRO_DAYS,
                                asof: Optional[date] = None,
                                db_path: Optional[str] = None) -> Dict[str, SymbolRunResult]:
    """Fit + persist regimes for every requested symbol. Returns {symbol: SymbolRunResult}.

    A single symbol's failure is recorded as an error row and does not stop the run.

    Two phases, deliberately not interleaved: (1) fit every symbol — each fit
    pulls returns via its own short-lived read-only connection (extract_returns
    -> reader.read_prices); (2) open a single writer connection and persist all
    results. DuckDB refuses a second connection to the same file with a
    different read_only configuration while one is already open in-process, so
    phase 1 must finish (and hold no connection) before phase 2 opens the writer.
    """
    from market_data_hub.db.connection import get_conn
    from market_data_hub.regime.schema import ensure_regime_schema

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

    results: Dict[str, SymbolRunResult] = {}
    con = get_conn(db_path)
    try:
        ensure_regime_schema(con)
        for symbol, (run, error_msg, fit_seconds) in fits.items():
            if run is None:
                _write_error_run(con, symbol, asof, error_msg)
                results[symbol] = SymbolRunResult(symbol=symbol, status="error",
                                                  error_msg=error_msg)
            else:
                results[symbol] = write_regime_run(
                    con, symbol, run, estimation_date=asof,
                    fit_seconds=fit_seconds, retro_days=retro_days,
                )
    finally:
        con.close()
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
