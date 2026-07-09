# -*- coding: utf-8 -*-
"""
runner.py — orchestrates the Dalio v2 engines and writes engine_scores.

Additive: never touches dalio_signals/pillar_scores/regime_state (dalio.py
keeps producing the current report unchanged). See
docs/DALIO_5ENGINE_IMPLEMENTATION_PLAN_2026-07.md §1 (non-goal).

Usage:
    from market_data_hub.dalio_v2.runner import run_dalio_v2
    run_dalio_v2()                      # both Phase-1 engines, current year
    run_dalio_v2(ref_year=2026)
    run_dalio_v2(engines=["sovereign_solvency"])
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Dict, List, Optional

import pandas as pd

from market_data_hub.dalio_v2 import (
    external_constraint, funding_liquidity, political_execution, private_credit,
    sovereign_solvency,
)
from market_data_hub.db.connection import get_conn
from market_data_hub.lock import db_write_lock

_ENGINES = {
    "sovereign_solvency": sovereign_solvency.compute,
    "political_execution": political_execution.compute,
    "private_credit": private_credit.compute,
    "external_constraint": external_constraint.compute,
    "funding_liquidity": funding_liquidity.compute,
}


def _records_with_real_nulls(df: pd.DataFrame):
    """df.itertuples() straight from a mixed None/str DataFrame is not safe
    to feed to executemany(): pandas' string-dtype inference silently turns
    a column's `None` entries into its own NA sentinel, which itertuples()
    then yields as a bare float('nan') -- and DuckDB writes THAT into a
    VARCHAR column as the literal 3-character text "nan", not SQL NULL. That
    "nan" text then survives every downstream `pd.isna(label)` check (it's a
    normal string, not missing), so it leaks into the report untouched. Scan
    every cell and coerce pandas/NumPy "missing" back to a real None."""
    return [tuple(None if pd.isna(v) else v for v in row)
            for row in df.itertuples(index=False, name=None)]


def run_dalio_v2(engines: Optional[List[str]] = None, ref_year: Optional[int] = None,
                 db_path: Optional[str] = None) -> Dict[str, int]:
    """Compute the requested engines (default: all implemented so far) for
    ref_year (default: current year) and write to engine_scores. Returns
    {engine_name: n_countries_scored}."""
    engines = engines or list(_ENGINES.keys())
    unknown = set(engines) - set(_ENGINES)
    if unknown:
        raise ValueError(f"Unknown engine(s): {sorted(unknown)}. Known: {sorted(_ENGINES)}")

    ref_date = date(ref_year or datetime.now().year, 12, 31)
    with db_write_lock(db_path):
        con = get_conn(db_path)
        try:
            summary: Dict[str, int] = {}
            # One explicit transaction across all engines: DuckDB autocommits
            # each statement otherwise, so a failure at engine 3/5 would leave
            # engine_scores in a mixed-vintage state for this ref_date.
            con.execute("BEGIN TRANSACTION")
            try:
                for name in engines:
                    df = _ENGINES[name](con, ref_date)
                    # Replace this (ref_date, engine) batch wholesale: a country
                    # that dropped out of coverage since the last run must not
                    # survive as a stale row with an old score/model_version.
                    con.execute(
                        "DELETE FROM engine_scores WHERE ref_date = ? AND engine = ?",
                        [ref_date, name])
                    if df.empty:
                        summary[name] = 0
                        continue
                    con.executemany(
                        "INSERT INTO engine_scores VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        _records_with_real_nulls(df))
                    summary[name] = len(df)
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
            return summary
        finally:
            con.close()
