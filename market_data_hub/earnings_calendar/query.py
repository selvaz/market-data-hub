# -*- coding: utf-8 -*-
"""The read surface: what is scheduled, what came out, and how it groups.

Every window is served from what we stored, so a past week answers exactly as
well as a future one -- which is the whole reason the observations accumulate.
"""
from __future__ import annotations

from typing import Optional

import duckdb

_CAMPI = """
    event_id, symbol, exchange, tv_ticker, company_name, country, region,
    sector, industry, theme, market_cap, release_ts_utc,
    release_precision, status, currency, eps_estimate, eps_actual,
    revenue_estimate, revenue_actual
"""
_RAGGRUPPABILI = ("country", "region", "sector", "industry", "theme")


def events_between(
    con: duckdb.DuckDBPyConnection,
    start: str,
    end: str,
    *,
    region: Optional[str] = None,
    sector: Optional[str] = None,
    theme: Optional[str] = None,
    status: Optional[str] = None,
    min_market_cap: Optional[float] = None,
    limit: int = 500,
) -> list[dict]:
    """Releases whose expected or actual instant falls in [start, end)."""
    dove = ["release_ts_utc >= ?", "release_ts_utc < ?"]
    valori: list = [start, end]
    for colonna, valore in (("region", region), ("sector", sector),
                            ("theme", theme), ("status", status)):
        if valore:
            dove.append(f"lower({colonna}) = lower(?)")
            valori.append(valore)
    if min_market_cap is not None:
        dove.append("market_cap >= ?")
        valori.append(min_market_cap)

    righe = con.execute(
        f"SELECT {_CAMPI} FROM earnings_events WHERE {' AND '.join(dove)} "
        f"ORDER BY market_cap DESC NULLS LAST LIMIT {int(limit)}",
        valori,
    )
    colonne = [d[0] for d in righe.description]
    return [dict(zip(colonne, r)) for r in righe.fetchall()]


def aggregate(
    con: duckdb.DuckDBPyConnection,
    start: str,
    end: str,
    *,
    by: str = "country",
    min_market_cap: Optional[float] = None,
) -> list[dict]:
    """Counts and aggregate capitalisation for a window, grouped one way.

    This is what makes a week like a Chinese reporting deadline readable: 165
    releases summarised in a line instead of listed name by name.
    """
    if by not in _RAGGRUPPABILI:
        raise ValueError(f"cannot group by {by!r}; expected one of {_RAGGRUPPABILI}")
    dove = ["release_ts_utc >= ?", "release_ts_utc < ?"]
    valori: list = [start, end]
    if min_market_cap is not None:
        dove.append("market_cap >= ?")
        valori.append(min_market_cap)

    righe = con.execute(
        f"""
        SELECT {by} AS bucket, count(*) AS n,
               sum(market_cap) AS market_cap_total,
               count(*) FILTER (WHERE status = 'occurred') AS occurred
        FROM earnings_events WHERE {' AND '.join(dove)}
        GROUP BY {by} ORDER BY n DESC
        """,
        valori,
    ).fetchall()
    return [{"bucket": b, "n": n, "market_cap_total": tot, "occurred": occ}
            for b, n, tot, occ in righe]


def vocabulary(con: duckdb.DuckDBPyConnection) -> dict:
    """What a caller can filter on, with counts. Empty lists when nothing is stored."""
    def valori(colonna: str) -> list[dict]:
        try:
            righe = con.execute(
                f"SELECT {colonna}, count(*) FROM earnings_events "
                f"WHERE {colonna} IS NOT NULL GROUP BY {colonna} ORDER BY count(*) DESC"
            ).fetchall()
        except duckdb.Error:
            return []
        return [{"value": v, "n": n} for v, n in righe]

    finestra = con.execute(
        "SELECT min(release_ts_utc), max(release_ts_utc) FROM earnings_events"
    ).fetchone()
    return {
        "regions": valori("region"),
        "countries": valori("country"),
        "sectors": valori("sector"),
        "themes": valori("theme"),
        "statuses": valori("status"),
        "stored_from": str(finestra[0]) if finestra and finestra[0] else None,
        "stored_to": str(finestra[1]) if finestra and finestra[1] else None,
    }
