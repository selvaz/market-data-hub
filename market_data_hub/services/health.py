# -*- coding: utf-8 -*-
"""
services.health — ingestion observability (plan v3.1, Fase 7).

One bounded, read-only snapshot answering: are the ensure_* capabilities
healthy? Jobs by status, recent errors, provider distribution, price
coverage staleness and SEC filing lag. No network, no writes; every section
is capped so the answer is always LLM-safe.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import duckdb

from market_data_hub.db.connection import get_conn

_MAX_ERRORS = 20
_MAX_STALLED = 20


def _rows(con, sql: str, params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
    try:
        df = con.execute(sql, params or []).fetch_df()
    except duckdb.CatalogException:
        return []           # table not there yet (pre-migration DB)
    return json.loads(df.to_json(orient="records", date_format="iso"))


def get_ingestion_health(db_path: Optional[str] = None) -> Dict[str, Any]:
    """Bounded health snapshot of the ingestion ledger + coverage."""
    con = get_conn(db_path, read_only=True)
    try:
        jobs = _rows(con, """
            SELECT kind, status, COUNT(*) AS n, MAX(updated_at) AS last_update
            FROM ingestion_jobs GROUP BY kind, status ORDER BY kind, status
        """)
        runs_by_provider = _rows(con, """
            SELECT provider, status, COUNT(*) AS n,
                   SUM(rows_written) AS rows_written
            FROM ingestion_runs GROUP BY provider, status
            ORDER BY provider, status
        """)
        recent_errors = _rows(con, f"""
            SELECT job_id, kind, error_msg, updated_at
            FROM ingestion_jobs WHERE status = 'error'
            ORDER BY updated_at DESC LIMIT {_MAX_ERRORS}
        """)
        stalled = _rows(con, f"""
            SELECT symbol, source, last_date, lag_days, coverage_score
            FROM coverage_report WHERE stalled = TRUE
            ORDER BY lag_days DESC LIMIT {_MAX_STALLED}
        """)
        n_stalled = _rows(con, "SELECT COUNT(*) AS n FROM coverage_report "
                               "WHERE stalled = TRUE")
        sec = _rows(con, """
            SELECT cik, entity_name, n_filings, n_facts, last_filed, lag_days
            FROM sec_coverage ORDER BY lag_days DESC LIMIT 20
        """)
    finally:
        con.close()
    return {
        "jobs": jobs,
        "runs_by_provider": runs_by_provider,
        "recent_errors": recent_errors,
        "recent_errors_truncated_at": _MAX_ERRORS,
        "stalled_prices": stalled,
        "stalled_prices_total": (n_stalled[0]["n"] if n_stalled else 0),
        "sec_coverage": sec,
    }
