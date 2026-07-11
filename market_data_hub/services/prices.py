# -*- coding: utf-8 -*-
"""
services.prices — single-name price capabilities (plan v3.1, Fase 2).

Public semantics live here, not in agent_tools.py:

  - resolve_instrument()   read-only: human input ('NVDA') -> listing candidates.
                           Ambiguity returns candidates; it never guesses.
  - ensure_price_history() write: idempotent job under the DB writer lock.
                           Registers identity rows (instrument/listing/alias)
                           for symbols known to the config universe, fetches
                           incrementally from the provider, upserts
                           prices_daily and records ingestion_runs.
  - get_price_summary()    read-only: bounded metrics, never raw OHLCV bars.
  - get_job_status()       read-only: job envelope + linked run.

Identity model: prices_daily is untouched (its INSERT OR REPLACE upsert would
NULL any column it does not write); listings.symbol joins prices_daily.symbol.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from market_data_hub.config_loader import get_settings, get_yahoo_tickers
from market_data_hub.db.connection import get_conn
from market_data_hub.db.upsert import upsert
from market_data_hub.lock import db_write_lock

# asset_class in tickers.yaml -> instruments.kind
_KIND_BY_ASSET_CLASS = {
    "EQUITY": "EQUITY",
    "FIXED_INCOME": "ETF",
    "COMMODITIES": "ETF",
    "REAL_ESTATE": "ETF",
    "ALTERNATIVES": "ETF",
    "FX": "FX",
    "VOLATILITY": "INDEX",
}

_DEFAULT_PROVIDER = "yahoo"


class AmbiguousInstrumentError(ValueError):
    """Raised when a write capability receives a query matching >1 listing."""

    def __init__(self, query: str, candidates: List[Dict[str, Any]]):
        super().__init__(
            f"{query!r} matches {len(candidates)} listings; pass an exact "
            f"listing_id or narrow with exchange/currency")
        self.candidates = candidates


class UnknownInstrumentError(ValueError):
    """Raised when a query matches nothing in listings, aliases or config."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stable_id(prefix: str, *parts: str) -> str:
    h = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{h}"


def _request_hash(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


# --------------------------------------------------------------------- resolve
def _config_candidates(query: str) -> List[Dict[str, Any]]:
    """Candidates from the static config universe (not yet registered)."""
    q = query.strip().upper()
    out = []
    for e in get_yahoo_tickers():
        if e["symbol"].upper() == q:
            out.append({
                "listing_id": None,
                "instrument_id": None,
                "issuer_id": None,
                "symbol": e["symbol"],
                "kind": _KIND_BY_ASSET_CLASS.get(e.get("asset_class", ""), "OTHER"),
                "name": e.get("name"),
                "exchange": None,
                "currency": None,
                "provider": _DEFAULT_PROVIDER,
                "registered": False,
            })
    return out


def resolve_instrument(query: str, exchange: Optional[str] = None,
                       currency: Optional[str] = None,
                       db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Resolve human input (symbol, alias, or a 'lst_*' listing_id) to listing
    candidates. Read-only: never registers anything and never guesses — the
    caller decides what to do when more than one candidate comes back."""
    q = query.strip()
    con = get_conn(db_path, read_only=True)
    try:
        where, params = ["(l.listing_id = ? OR upper(l.symbol) = upper(?))"], [q, q]
        if exchange:
            where.append("l.exchange = ?")
            params.append(exchange)
        if currency:
            where.append("l.currency = ?")
            params.append(currency)
        rows = con.execute(f"""
            SELECT l.listing_id, l.instrument_id, i.issuer_id, l.symbol,
                   i.kind, i.name, l.exchange, l.currency, l.provider
            FROM listings l JOIN instruments i USING (instrument_id)
            WHERE {' AND '.join(where)} AND l.active_to IS NULL
            ORDER BY l.listing_id
        """, params).fetchall()
        if not rows:
            # alias lookup (historic ticker, ISIN, FIGI ...) -> listing
            rows = con.execute("""
                SELECT l.listing_id, l.instrument_id, i.issuer_id, l.symbol,
                       i.kind, i.name, l.exchange, l.currency, l.provider
                FROM identifier_aliases a
                JOIN listings l ON a.target_type = 'listing' AND a.target_id = l.listing_id
                JOIN instruments i USING (instrument_id)
                WHERE upper(a.value) = upper(?) AND a.valid_to IS NULL
                ORDER BY l.listing_id
            """, [q]).fetchall()
        cols = ["listing_id", "instrument_id", "issuer_id", "symbol", "kind",
                "name", "exchange", "currency", "provider"]
        found = [dict(zip(cols, r), registered=True) for r in rows]
    finally:
        con.close()
    if found:
        return found
    return _config_candidates(q)


def _resolve_single(query: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    candidates = resolve_instrument(query, db_path=db_path)
    if not candidates:
        raise UnknownInstrumentError(
            f"{query!r} matches no listing, alias or config-universe symbol")
    if len(candidates) > 1:
        raise AmbiguousInstrumentError(query, candidates)
    return candidates[0]


# -------------------------------------------------------------------- registry
def _register_listing(con, cand: Dict[str, Any]) -> Dict[str, Any]:
    """Create instrument + listing + ticker alias rows for a config-universe
    candidate. Deterministic ids -> idempotent under re-registration."""
    now = _now()
    symbol = cand["symbol"]
    instrument_id = _stable_id("ins", symbol)
    listing_id = _stable_id("lst", symbol, cand.get("provider") or _DEFAULT_PROVIDER)
    con.execute("""
        INSERT INTO instruments (instrument_id, issuer_id, kind, name,
                                 created_at, updated_at)
        SELECT ?, NULL, ?, ?, ?, ?
        WHERE NOT EXISTS (SELECT 1 FROM instruments WHERE instrument_id = ?)
    """, [instrument_id, cand.get("kind") or "OTHER", cand.get("name"),
          now, now, instrument_id])
    con.execute("""
        INSERT INTO listings (listing_id, instrument_id, symbol, exchange,
                              currency, provider, provider_symbol,
                              active_from, active_to, created_at, updated_at)
        SELECT ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?
        WHERE NOT EXISTS (SELECT 1 FROM listings WHERE listing_id = ?)
    """, [listing_id, instrument_id, symbol, cand.get("exchange"),
          cand.get("currency"), cand.get("provider") or _DEFAULT_PROVIDER,
          symbol, now, now, listing_id])
    con.execute("""
        INSERT INTO identifier_aliases (namespace, value, target_type,
                                        target_id, valid_from, valid_to, updated_at)
        SELECT 'ticker', ?, 'listing', ?, NULL, NULL, ?
        WHERE NOT EXISTS (SELECT 1 FROM identifier_aliases
                          WHERE namespace = 'ticker' AND value = ?
                            AND target_type = 'listing' AND target_id = ?)
    """, [symbol, listing_id, now, symbol, listing_id])
    out = dict(cand)
    out.update(listing_id=listing_id, instrument_id=instrument_id, registered=True)
    return out


# --------------------------------------------------------------------- ensure
FetchFn = Callable[[List[str], str, str], Dict[str, pd.DataFrame]]


def _default_fetch(symbols: List[str], start: str, end: str
                   ) -> Dict[str, pd.DataFrame]:
    from market_data_hub.sources.yahoo import yahoo_batch
    return yahoo_batch(symbols, start, end)


def ensure_price_history(query: str, start: Optional[str] = None,
                         end: Optional[str] = None, *,
                         requester: str = "python",
                         db_path: Optional[str] = None,
                         fetch: Optional[FetchFn] = None,
                         force: bool = False) -> Dict[str, Any]:
    """Idempotent ingestion capability (plan v3.1 §4.1 / §5.2).

    Resolves `query` to exactly one listing (raising AmbiguousInstrumentError
    with the candidates otherwise), creates or reuses the ingestion job keyed
    by the normalized request hash, and — unless the job is already completed
    and force is False — fetches and upserts the history under the writer lock.

    Returns the job envelope: {job_id, run_id, status, listing_id, symbol,
    reused, rows_added, rows_updated}.
    """
    cand = _resolve_single(query, db_path=db_path)
    settings = get_settings()
    start = start or settings.get("start_date", "2000-01-01")
    end = end or _now().date().isoformat()

    fetch = fetch or _default_fetch
    now = _now()

    with db_write_lock(db_path):
        con = get_conn(db_path)
        try:
            if not cand.get("registered"):
                cand = _register_listing(con, cand)
            listing_id, symbol = cand["listing_id"], cand["symbol"]

            req = {"kind": "price_history", "listing_id": listing_id,
                   "start": start, "end": end}
            request_hash = _request_hash(req)

            row = con.execute(
                "SELECT job_id, status, run_id FROM ingestion_jobs "
                "WHERE request_hash = ?", [request_hash]).fetchone()
            if row and row[1] == "completed" and not force:
                return {"job_id": row[0], "run_id": row[2],
                        "status": "completed", "listing_id": listing_id,
                        "symbol": symbol, "reused": True,
                        "rows_added": 0, "rows_updated": 0}

            if row:
                job_id = row[0]
                con.execute(
                    "UPDATE ingestion_jobs SET status = 'running', "
                    "updated_at = ? WHERE job_id = ?", [now, job_id])
            else:
                job_id = f"job_{uuid.uuid4().hex[:12]}"
                con.execute("""
                    INSERT INTO ingestion_jobs (job_id, request_hash, kind,
                        request_json, status, run_id, requester, created_at,
                        updated_at)
                    VALUES (?, ?, 'price_history', ?, 'running', NULL, ?, ?, ?)
                """, [job_id, request_hash, json.dumps(req), requester, now, now])

            run_id = f"run_{uuid.uuid4().hex[:12]}"
            provider = cand.get("provider") or _DEFAULT_PROVIDER
            con.execute("""
                INSERT INTO ingestion_runs (run_id, kind, input_json, provider,
                    provider_reason, status, attempts, started_at)
                VALUES (?, 'price_history', ?, ?, 'primary provider for listing',
                        'running', 1, ?)
            """, [run_id, json.dumps(req), provider, now])

            try:
                frames = fetch([symbol], start, end)
                df = frames.get(symbol)
                added = updated = 0
                if df is not None and not df.empty:
                    df = df.copy()
                    if "symbol" not in df.columns:
                        df["symbol"] = symbol
                    if "source" not in df.columns:
                        df["source"] = provider
                    added, updated = upsert(con, "prices_daily", df)
                payload_hash = (
                    hashlib.sha256(
                        pd.util.hash_pandas_object(df).values.tobytes()
                    ).hexdigest() if df is not None and not df.empty else None)
                fin = _now()
                con.execute("""
                    UPDATE ingestion_runs SET status = 'completed',
                        payload_hash = ?, rows_written = ?, finished_at = ?
                    WHERE run_id = ?
                """, [payload_hash, added + updated, fin, run_id])
                con.execute("""
                    UPDATE ingestion_jobs SET status = 'completed', run_id = ?,
                        error_msg = NULL, updated_at = ? WHERE job_id = ?
                """, [run_id, fin, job_id])
                return {"job_id": job_id, "run_id": run_id,
                        "status": "completed", "listing_id": listing_id,
                        "symbol": symbol, "reused": False,
                        "rows_added": added, "rows_updated": updated}
            except Exception as exc:
                fin = _now()
                con.execute(
                    "UPDATE ingestion_runs SET status = 'error', error_msg = ?, "
                    "finished_at = ? WHERE run_id = ?", [str(exc), fin, run_id])
                con.execute(
                    "UPDATE ingestion_jobs SET status = 'error', error_msg = ?, "
                    "run_id = ?, updated_at = ? WHERE job_id = ?",
                    [str(exc), run_id, fin, job_id])
                raise
        finally:
            con.close()


# --------------------------------------------------------------------- readers
def get_job_status(job_id: str, db_path: Optional[str] = None
                   ) -> Optional[Dict[str, Any]]:
    """Job envelope + linked run record. Read-only, no network."""
    con = get_conn(db_path, read_only=True)
    try:
        row = con.execute("""
            SELECT j.job_id, j.kind, j.status, j.run_id, j.requester,
                   j.error_msg, j.created_at, j.updated_at,
                   r.provider, r.provider_reason, r.rows_written,
                   r.started_at, r.finished_at
            FROM ingestion_jobs j
            LEFT JOIN ingestion_runs r ON j.run_id = r.run_id
            WHERE j.job_id = ?
        """, [job_id]).fetchone()
        if row is None:
            return None
        cols = ["job_id", "kind", "status", "run_id", "requester", "error_msg",
                "created_at", "updated_at", "provider", "provider_reason",
                "rows_written", "started_at", "finished_at"]
        return dict(zip(cols, row))
    finally:
        con.close()


def get_price_summary(query: str, start: Optional[str] = None,
                      end: Optional[str] = None,
                      db_path: Optional[str] = None) -> Dict[str, Any]:
    """Bounded price metrics for one listing. Read-only, no network, and no
    raw OHLCV bars in the output (plan v3.1 go/no-go)."""
    cand = _resolve_single(query, db_path=db_path)
    symbol = cand["symbol"]
    con = get_conn(db_path, read_only=True)
    try:
        where, params = ["symbol = ?"], [symbol]
        if start:
            where.append("date >= ?")
            params.append(start)
        if end:
            where.append("date <= ?")
            params.append(end)
        df = con.execute(f"""
            SELECT date, adj_close FROM prices_daily
            WHERE {' AND '.join(where)} AND adj_close IS NOT NULL
            ORDER BY date
        """, params).fetch_df()
    finally:
        con.close()
    if df.empty:
        return {"listing_id": cand.get("listing_id"), "symbol": symbol,
                "n_obs": 0, "note": "no data in the hub for this range; "
                "use ensure_price_history to ingest"}
    px = df["adj_close"].astype(float)
    rets = px.pct_change().dropna()
    last_date = pd.Timestamp(df["date"].iloc[-1]).date()
    return {
        "listing_id": cand.get("listing_id"),
        "instrument_id": cand.get("instrument_id"),
        "symbol": symbol,
        "first_date": pd.Timestamp(df["date"].iloc[0]).date().isoformat(),
        "last_date": last_date.isoformat(),
        "n_obs": int(len(df)),
        "lag_days": (datetime.now(timezone.utc).date() - last_date).days,
        "last_adj_close": float(px.iloc[-1]),
        "total_return_pct": float((px.iloc[-1] / px.iloc[0] - 1) * 100),
        "ann_vol_pct": float(rets.std() * (252 ** 0.5) * 100) if len(rets) > 1 else None,
        "max_drawdown_pct": float(((px / px.cummax()) - 1).min() * 100),
    }
