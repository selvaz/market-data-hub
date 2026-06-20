# -*- coding: utf-8 -*-
"""
agent_tools.py — LLM / function-calling layer over catalog.py + extract.py.

Two surfaces, one implementation:

1. Plain ``tool_*`` functions that take primitive arguments and return a JSON
   string. They have no third-party dependency and can be called from any
   agent framework, an MCP server, or a notebook.

2. ``DataHubTools`` — an optional LazyBridge ``ToolProvider`` (active only when
   the ``agent`` extra / lazybridge is installed) that wraps the same ``tool_*``
   functions with ``Tool.wrap`` so they drop straight into ``Agent(tools=[...])``.

The logic lives in the ``tool_*`` functions, so the planned move to a LazyTools
``connectors/datahub`` package later is a re-wrap, not a rewrite.

Typical agent flow: discover first (``tool_list_*`` / ``tool_search`` /
``tool_describe``), then extract (``tool_get_series`` / ``tool_get_returns``).
"""
from __future__ import annotations

import json
from typing import Any, List, Optional

import pandas as pd

from market_data_hub import catalog, extract

# Cap on the number of rows a single extraction tool returns inline, to avoid
# flooding the LLM context. The full row count is always reported in `meta`.
_MAX_ROWS = 500


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _df_records(df: pd.DataFrame, limit: Optional[int] = None) -> list:
    if df is None or df.empty:
        return []
    if limit is not None and len(df) > limit:
        df = df.head(limit)
    return json.loads(df.to_json(orient="records", date_format="iso"))


def _split(csv_or_list) -> List[str]:
    """Accept either a list or a comma-separated string (LLMs often send strings)."""
    if csv_or_list is None:
        return []
    if isinstance(csv_or_list, str):
        return [s.strip() for s in csv_or_list.split(",") if s.strip()]
    return list(csv_or_list)


# ---------------------------------------------------------------------------
# Discovery tools
# ---------------------------------------------------------------------------
def tool_list_datasets() -> str:
    """List the data domains in the hub (prices, macro, macro_panel, crypto,
    factors) with their table, primary key, frequency and how to discover them."""
    return _json(catalog.list_datasets())


def tool_list_symbols(asset_class: str = "", area: str = "",
                      sector: str = "", group: str = "") -> str:
    """List price-universe symbols, optionally filtered.

    asset_class: EQUITY | FIXED_INCOME | COMMODITIES | REAL_ESTATE | ALTERNATIVES | FX.
    area:        geographic area, e.g. "Emerging Markets", "USA", "Europe", "China".
    sector:      GICS sector for sector ETFs, e.g. "Energy", "Health Care";
                 use "*" to return only the sector ETFs.
    group:       name sub-group, e.g. "EM", "Energy", "Metals".
    Returns a JSON list of symbols with coverage (date range, obs, freshness)."""
    df = catalog.list_symbols(
        asset_class=asset_class or None, area=area or None,
        sector=sector or None, group=group or None)
    return _json({"n": int(len(df)), "symbols": _df_records(df)})


def tool_list_sectors(area: str = "") -> str:
    """List the available equity sectors and their symbols (sector ETFs).
    area="USA" for US sectors, "Europe" for the STOXX sleeves, "" for all."""
    return _json(_df_records(catalog.list_sectors(area=area or None)))


def tool_list_macro(frequency: str = "", category: str = "") -> str:
    """List FRED macro series. category filters the name prefix
    (RATES/MACRO/CREDIT/RISK/LIQ/FX); frequency filters detected D/M/Q/A."""
    df = catalog.list_macro_series(frequency=frequency or None, category=category or None)
    return _json(_df_records(df))


def tool_list_indicators(pillar: str = "") -> str:
    """List cross-country macro_panel indicators, optionally by pillar
    (growth/liquidity/external/debt_cycle/sovereign/banking/governance/geopolitical)."""
    return _json(_df_records(catalog.list_macro_indicators(pillar=pillar or None)))


def tool_list_countries(region: str = "", income: str = "") -> str:
    """List the macro_panel country universe, filterable by region
    (G7/EU/EM or geographic region) and income group."""
    return _json(_df_records(catalog.list_countries(region=region or None,
                                                    income=income or None)))


def tool_describe(symbol_or_id: str) -> str:
    """Describe a single series/symbol/indicator: which domain it belongs to,
    its classification, source/unit and coverage/quality."""
    return _json(catalog.describe_series(symbol_or_id))


def tool_search(query: str) -> str:
    """Free-text search across all domains (symbol/name/sector/area/indicator).
    Use this to resolve a natural-language request into concrete keys."""
    return _json(_df_records(catalog.search(query)))


# ---------------------------------------------------------------------------
# Extraction tools
# ---------------------------------------------------------------------------
def tool_get_series(symbols: str, start: str = "", end: str = "",
                    domain: str = "prices", field: str = "adj_close",
                    transform: str = "level", frequency: str = "") -> str:
    """Extract an analysis-ready time-series matrix as JSON records.

    symbols:   comma-separated (e.g. "SPY,TLT,^VIX").
    domain:    prices | macro | crypto | factors.
    field:     OHLCV field for prices (adj_close default); timeframe for crypto.
    transform: level | log_return | pct_change | diff.
    frequency: ""(native) | D | W | M | Q.
    Long series are truncated to the first rows; meta.n_rows holds the true count."""
    df, meta = extract.extract_series(
        _split(symbols), start=start or None, end=end or None, domain=domain,
        field=field, transform=transform, frequency=frequency or None)
    return _json({"meta": meta, "data": _df_records(df, limit=_MAX_ROWS),
                  "truncated": bool(len(df) > _MAX_ROWS)})


def tool_get_returns(symbols: str, start: str = "", end: str = "",
                     frequency: str = "W") -> str:
    """Extract log-returns (default weekly W-FRI) ready for regime/HMM analysis.
    symbols: comma-separated. Returns JSON records + meta (incl. coverage)."""
    df, meta = extract.extract_returns(
        _split(symbols), start=start or None, end=end or None,
        frequency=frequency or None)
    return _json({"meta": meta, "data": _df_records(df, limit=_MAX_ROWS),
                  "truncated": bool(len(df) > _MAX_ROWS)})


def tool_get_coverage(symbols: str = "") -> str:
    """Data-quality report (coverage_score, lag_days, stalled, date range) for
    the given symbols, or the whole universe when symbols is empty."""
    from market_data_hub import reader
    df = reader.get_coverage(symbols=_split(symbols) or None)
    return _json(_df_records(df))


# All read-only tool functions exposed to an agent, in the order an agent
# should prefer. The hub's agent surface is read-only by default (the data is
# kept fresh by a separate downloader, run_daily.py).
TOOL_FUNCTIONS = [
    tool_list_datasets, tool_list_symbols, tool_list_sectors, tool_list_macro,
    tool_list_indicators, tool_list_countries, tool_describe, tool_search,
    tool_get_series, tool_get_returns, tool_get_coverage,
]


# ---------------------------------------------------------------------------
# Write tools — opt-in only (they trigger a network download + DB write)
# ---------------------------------------------------------------------------
def tool_refresh_prices(symbols: str, start: str = "2010-01-01") -> str:
    """Download price series from Yahoo and WRITE them into the hub DB, then
    rebuild coverage. Use this when the hub has no (or insufficient) data for a
    symbol: afterwards tool_get_series / tool_get_returns will see it.

    symbols: comma-separated (e.g. "SPY,QQQ,NVDA").
    start:   history start date "YYYY-MM-DD".
    Returns JSON with the refreshed symbols and the rebuilt coverage count.

    This is a thin wrapper over the official downloader (runner.run_yahoo); it
    is NOT concurrency-safe (it temporarily narrows the Yahoo universe to the
    requested symbols), so serialise calls. Yahoo needs no API key."""
    import uuid

    from market_data_hub import runner
    from market_data_hub.config_loader import get_settings
    from market_data_hub.coverage.report import rebuild_coverage
    from market_data_hub.db.connection import get_conn

    syms = [s.upper() for s in _split(symbols)]
    if not syms:
        return _json({"error": "no symbols provided"})

    tickers = [{"symbol": s, "asset_class": "EQUITY", "area": "",
                "name": s, "priority": 1} for s in syms]
    # run_yahoo reads the universe via runner.get_yahoo_tickers(); narrow it to
    # the requested symbols, then restore so a later full run is unaffected.
    _orig = runner.get_yahoo_tickers
    runner.get_yahoo_tickers = lambda: tickers
    con = get_conn()
    run_id = "refresh_" + uuid.uuid4().hex[:8]
    try:
        runner.run_yahoo(con, get_settings(), run_id, start_override=start)
        n = rebuild_coverage(con, run_id)
    finally:
        runner.get_yahoo_tickers = _orig
        con.close()
    return _json({"refreshed": syms, "start": start,
                  "coverage_series": int(n), "run_id": run_id})


WRITE_TOOL_FUNCTIONS = [tool_refresh_prices]


# ---------------------------------------------------------------------------
# Optional LazyBridge binding
# ---------------------------------------------------------------------------
class DataHubTools:
    """LazyBridge ToolProvider exposing the market-data-hub discovery + extraction
    tools. Requires the ``agent`` extra (lazybridge). Drop into ``Agent(tools=[...])``.

        from market_data_hub.agent_tools import DataHubTools
        agent = Agent("claude-opus-4-8", tools=[DataHubTools()])

    The surface is **read-only by default** (the data is kept fresh by a
    separate downloader). Pass ``allow_refresh=True`` to additionally expose the
    write tool ``datahub_refresh_prices`` so an agent can download+persist
    missing series on demand:

        agent = Agent("claude-opus-4-8", tools=[DataHubTools(allow_refresh=True)])
    """

    _is_lazy_tool_provider = True

    def __init__(self, *, allow_refresh: bool = False) -> None:
        self._allow_refresh = allow_refresh

    def as_tools(self) -> list:
        from lazybridge import Tool  # lazy: only needed when actually used
        fns = list(TOOL_FUNCTIONS)
        if self._allow_refresh:
            fns += WRITE_TOOL_FUNCTIONS
        return [Tool.wrap(fn, name=fn.__name__.replace("tool_", "datahub_"),
                          description=(fn.__doc__ or "").strip())
                for fn in fns]
