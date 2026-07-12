# -*- coding: utf-8 -*-
"""
agent_tools.py — LLM / function-calling layer over catalog.py + extract.py.

Plain ``tool_*`` functions that take primitive arguments and return a JSON
string. They have no third-party dependency and can be called from any agent
framework, an MCP server, or a notebook. This module is the single source of
truth for the hub's tool semantics; the LazyBridge ``ToolProvider`` binding
(``datahub_*`` tool names) lives in LazyTools: ``lazytools.connectors.datahub``
(``pip install lazytoolkit`` + this package).

Typical agent flow: discover first (``tool_list_*`` / ``tool_search`` /
``tool_describe``), then extract (``tool_get_series`` / ``tool_get_returns``).

Deliberately NOT exposed here: vintage / point-in-time reads (``asof``,
revision history). The tool surface serves current values only; PIT work goes
through the Python ``reader`` / ``extract`` layer (docs/EXTRACTION.md §7). If
a tool-only agent ever needs it, add the ``tool_*`` function here AND mirror
it by hand in LazyTools' ``DataHubBackend`` — its methods map 1:1 explicitly,
so new tools do not flow through automatically.
"""
from __future__ import annotations

import json
import threading
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


# Cap on resolve candidates returned to the LLM (plan v3.1 §5.3: discovery 50).
_MAX_CANDIDATES = 50


def tool_resolve_instrument(query: str, exchange: str = "",
                            currency: str = "") -> str:
    """Resolve human input (ticker, alias, or a 'lst_*' listing_id) to listing
    candidates with listing_id/instrument_id. Ambiguous input returns ALL
    candidates so the caller can choose — this tool never guesses and never
    writes. registered=false means the symbol is known to the config universe
    but has no identity rows yet (ensure_price_history registers it)."""
    from market_data_hub.services import prices as _prices
    cands = _prices.resolve_instrument(
        query, exchange=exchange or None, currency=currency or None)
    return _json({"n": len(cands), "ambiguous": len(cands) > 1,
                  "candidates": cands[:_MAX_CANDIDATES]})


def tool_get_price_summary(query: str, start: str = "", end: str = "") -> str:
    """Bounded price metrics for ONE listing (date range, obs, freshness, last
    adjusted close, total return, annualized vol, max drawdown). Reads only
    from the hub DB — no network, no raw OHLCV bars. If the hub has no data,
    the reply says to use tool_ensure_price_history first."""
    from market_data_hub.services import prices as _prices
    try:
        return _json(_prices.get_price_summary(
            query, start=start or None, end=end or None))
    except _prices.AmbiguousInstrumentError as ex:
        return _json({"error": str(ex), "candidates": ex.candidates})
    except _prices.UnknownInstrumentError as ex:
        return _json({"error": str(ex)})


def tool_get_financials_coverage(query: str = "") -> str:
    """SEC coverage in the hub: which issuers have filings/facts ingested,
    how many, which forms, freshness. query: CIK, ticker or issuer_id; empty
    for all covered issuers. Read-only, no network."""
    from market_data_hub.services import financials as _fin
    return _json(_fin.get_financials_coverage(query or None))


def tool_get_financial_facts(query: str, line: str = "", forms: str = "",
                             limit: int = 25) -> str:
    """XBRL company facts for ONE issuer from the hub DB (read-only, no
    network, max 100 rows). Every value carries unit, period, fiscal
    year/period, form, accession and filed date, so it is verifiable.

    query: CIK, ticker or issuer_id (must be ingested already).
    line:  mapped statement line — revenue | net_income | assets |
           liabilities | equity | operating_cash_flow (versioned mapping).
    forms: optional comma-separated filter, e.g. "10-K" or "10-K,10-Q"."""
    from market_data_hub.services import financials as _fin
    return _json(_fin.get_facts(
        query, line=line or None, forms=_split(forms) or None, limit=limit))


def tool_get_statement(query: str, statement: str = "", periods: int = 8) -> str:
    """Standardized ANNUAL statement lines for ONE issuer (revenue,
    net_income, assets, liabilities, equity, operating_cash_flow), ready for
    period-over-period comparison — margins, leverage, cash conversion —
    WITHOUT raw XBRL/HTML. Derived from the hub's facts via the versioned
    mapping; each value carries concept, accession and filed date, and
    restatements supersede on read (history preserved). Read-only, no
    network, max 12 periods.

    query:     CIK, ticker or issuer_id (ingested via tool_ensure_financials).
    statement: optional filter — income | balance | cash_flow."""
    from market_data_hub.services import financials as _fin
    return _json(_fin.get_statement(query, statement=statement or None,
                                    periods=periods))


def tool_get_ingestion_health() -> str:
    """Health snapshot of the hub's ingestion: jobs by kind/status, runs per
    provider, recent errors (max 20), stalled price series (max 20 + total),
    SEC coverage freshness. Read-only, no network — use it to decide whether
    an ensure_* capability or a retry is needed."""
    from market_data_hub.services import health as _health
    return _json(_health.get_ingestion_health())


def tool_get_job_status(job_id: str) -> str:
    """Status of an ingestion job created by tool_ensure_price_history:
    queued | running | completed | error, plus the linked run record
    (provider, rows written, timestamps)."""
    from market_data_hub.services import prices as _prices
    job = _prices.get_job_status(job_id)
    if job is None:
        return _json({"error": f"unknown job_id {job_id!r}"})
    return _json(job)


# All read-only tool functions exposed to an agent, in the order an agent
# should prefer. The hub's agent surface is read-only by default (the data is
# kept fresh by a separate downloader, run_daily.py).
TOOL_FUNCTIONS = [
    tool_list_datasets, tool_list_symbols, tool_list_sectors, tool_list_macro,
    tool_list_indicators, tool_list_countries, tool_describe, tool_search,
    tool_get_coverage,
    tool_resolve_instrument, tool_get_price_summary, tool_get_job_status,
    tool_get_financials_coverage, tool_get_financial_facts, tool_get_statement,
    tool_get_ingestion_health,
]

# Raw time-series matrices — NOT in the standard profile (plan v3.1 §5.1: "Le
# matrici raw ... non sono nel profilo LLM standard"). An agent operates on
# symbols/ids and receives bounded results (summary, statement, coverage);
# it never carries a price/return matrix through its own context by default.
# These stay available for verification/spot-checks by callers that opt in
# explicitly (e.g. a technical profile), always under the same 500-row cap.
RAW_SERIES_TOOL_FUNCTIONS = [tool_get_series, tool_get_returns]


# ---------------------------------------------------------------------------
# Write tools — opt-in only (they trigger a network download + DB write)
# ---------------------------------------------------------------------------

# Serialises concurrent tool_refresh_prices calls within the process (the
# cross-process case is covered by the DB writer file lock).
_REFRESH_LOCK = threading.Lock()


def tool_refresh_prices(symbols: str, start: str = "2010-01-01",
                        allow_write: bool = False) -> str:
    """Download price series from Yahoo and WRITE them into the hub DB, then
    rebuild coverage. WRITE capability gated by allow_write=True (plan v3.1
    §5.2 — every write is explicit). Use this when the hub has no (or
    insufficient) data for a symbol: afterwards tool_get_series /
    tool_get_returns will see it.

    symbols: comma-separated (e.g. "SPY,QQQ,NVDA").
    start:   history start date "YYYY-MM-DD".
    Returns JSON with the refreshed symbols and the rebuilt coverage count.

    Writes are serialised: an in-process lock covers the temporary narrowing
    of the Yahoo universe, and the cross-process DB writer lock (the same one
    the scheduled runner takes) covers the DuckDB write. If another writer
    holds the DB, a JSON error is returned instead of racing it.
    Yahoo needs no API key."""
    if not allow_write:
        return _json({"error": "write capability requires allow_write=true"})
    import uuid

    from market_data_hub import runner
    from market_data_hub.config_loader import get_settings
    from market_data_hub.coverage.report import rebuild_coverage
    from market_data_hub.db.connection import get_conn
    from market_data_hub.lock import DBLockTimeout, db_write_lock

    syms = [s.upper() for s in _split(symbols)]
    if not syms:
        return _json({"error": "no symbols provided"})

    tickers = [{"symbol": s, "asset_class": "EQUITY", "area": "",
                "name": s, "priority": 1} for s in syms]
    run_id = "refresh_" + uuid.uuid4().hex[:8]
    # run_yahoo reads the universe via runner.get_yahoo_tickers(); narrow it to
    # the requested symbols, then restore so a later full run is unaffected.
    # The monkeypatch is process-global, hence the in-process lock around it.
    with _REFRESH_LOCK:
        try:
            with db_write_lock():
                _orig = runner.get_yahoo_tickers
                runner.get_yahoo_tickers = lambda: tickers
                con = get_conn()
                try:
                    runner.run_yahoo(con, get_settings(), run_id,
                                     start_override=start)
                    n = rebuild_coverage(con, run_id)
                finally:
                    runner.get_yahoo_tickers = _orig
                    con.close()
        except DBLockTimeout as ex:
            return _json({"error": f"another writer holds the DB lock; "
                                   f"retry later ({ex})"})
    return _json({"refreshed": syms, "start": start,
                  "coverage_series": int(n), "run_id": run_id})


def tool_ensure_price_history(query: str, start: str = "", end: str = "",
                              allow_write: bool = False) -> str:
    """Ensure the hub holds price history for ONE listing, ingesting it from
    the primary provider if needed. WRITE capability (plan v3.1 §5.2): it is
    gated by allow_write=True, runs as an idempotent persistent job under the
    DB writer lock, and records an auditable ingestion run. Repeating the same
    request reuses the completed job instead of re-downloading.

    query: ticker, alias or 'lst_*' listing_id. Ambiguity returns candidates.
    Returns JSON: job_id, run_id, status, listing_id, rows added/updated."""
    if not allow_write:
        return _json({"error": "write capability requires allow_write=true"})
    from market_data_hub.lock import DBLockTimeout
    from market_data_hub.services import prices as _prices
    try:
        with _REFRESH_LOCK:
            res = _prices.ensure_price_history(
                query, start=start or None, end=end or None, requester="llm")
        return _json(res)
    except _prices.AmbiguousInstrumentError as ex:
        return _json({"error": str(ex), "candidates": ex.candidates})
    except _prices.UnknownInstrumentError as ex:
        return _json({"error": str(ex)})
    except DBLockTimeout as ex:
        return _json({"error": f"another writer holds the DB lock; "
                               f"retry later ({ex})"})


def tool_ensure_financials(query: str, allow_write: bool = False) -> str:
    """Ensure the hub holds SEC filings metadata + XBRL company facts for ONE
    issuer, ingesting from EDGAR if needed. WRITE capability gated by
    allow_write=True; idempotent per (issuer, day) — repeating the call the
    same day reuses the completed job. Facts are stored append-only.

    query: CIK (digits) or US ticker (resolved via SEC's official map).
    Returns JSON: job_id, run_id, status, issuer_id, cik, filings, new_facts."""
    if not allow_write:
        return _json({"error": "write capability requires allow_write=true"})
    from market_data_hub.lock import DBLockTimeout
    from market_data_hub.services import financials as _fin
    try:
        with _REFRESH_LOCK:
            return _json(_fin.ensure_filings_and_facts(query, requester="llm"))
    except _fin.UnknownIssuerError as ex:
        return _json({"error": str(ex)})
    except DBLockTimeout as ex:
        return _json({"error": f"another writer holds the DB lock; "
                               f"retry later ({ex})"})


WRITE_TOOL_FUNCTIONS = [tool_refresh_prices, tool_ensure_price_history,
                        tool_ensure_financials]
