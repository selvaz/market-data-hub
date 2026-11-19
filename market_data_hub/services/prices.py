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
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

import duckdb
import pandas as pd

from market_data_hub.config_loader import get_settings, get_yahoo_tickers
from market_data_hub.db.connection import get_conn
from market_data_hub.db import identity as _identity
# Single id scheme shared with the upsert auto-attach (db.identity): two
# writers can never mint different ids for the same (symbol, provider).
from market_data_hub.db.identity import stable_id as _stable_id
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

# Shared with db/upsert.py's auto-attach path -- see db/identity.py for why
# this lives there and not here.
_currency_for_symbol = _identity.currency_for_symbol

_DEFAULT_PROVIDER = "yahoo"

# Yahoo chart labels and common names for the warehouse's operating venues.
_YAHOO_EXCHANGE_MICS = {
    "NMS": "XNAS",
    "NGM": "XNAS",
    "NCM": "XNAS",
    "NASDAQ": "XNAS",
    "NYQ": "XNYS",
    "NYSE": "XNYS",
    "PAR": "XPAR",
    "MIL": "XMIL",
}


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
                "currency": _currency_for_symbol(e["symbol"]),
                "provider": _DEFAULT_PROVIDER,
                "provider_symbol": e["symbol"],
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
        try:
            rows = con.execute(f"""
                SELECT l.listing_id, l.instrument_id, i.issuer_id, l.symbol,
                       i.kind, i.name, l.exchange, l.currency, l.provider,
                       l.provider_symbol
                FROM listings l JOIN instruments i USING (instrument_id)
                WHERE {' AND '.join(where)} AND l.active_to IS NULL
                ORDER BY l.listing_id
            """, params).fetchall()
            if not rows:
                # alias lookup (historic ticker, ISIN, FIGI ...) -> listing
                rows = con.execute("""
                    SELECT l.listing_id, l.instrument_id, i.issuer_id, l.symbol,
                           i.kind, i.name, l.exchange, l.currency, l.provider,
                           l.provider_symbol
                    FROM identifier_aliases a
                    JOIN listings l ON a.target_type = 'listing' AND a.target_id = l.listing_id
                    JOIN instruments i USING (instrument_id)
                    WHERE upper(a.value) = upper(?) AND a.valid_to IS NULL
                    ORDER BY l.listing_id
                """, [q]).fetchall()
        except duckdb.CatalogException:
            # DB file predates schema v5 (identity tables not created yet) and
            # this is a read-only connection, which never runs migrate() --
            # only a writer (e.g. ensure_price_history) upgrades the schema.
            # Degrade to config-only candidates instead of raising.
            rows = []
        cols = ["listing_id", "instrument_id", "issuer_id", "symbol", "kind",
                "name", "exchange", "currency", "provider", "provider_symbol"]
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
            f"{query!r} matches no listing, alias or config-universe symbol; "
            f"register it first with register_listing(symbol, exchange=..., "
            f"currency=...) and then ensure_price_history")
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
          cand.get("provider_symbol") or symbol, now, now, listing_id])
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


def _verify_listing(symbol: str, provider_symbol: str, exchange: str,
                    currency: str, fetch: Optional[FetchFn] = None,
                    sec_resolve=None) -> Dict[str, Any]:
    """Probe the selected provider and (for US securities) the SEC map.

    Provider frames may expose authoritative ``name``, ``exchange`` and
    ``currency`` values through ``DataFrame.attrs``.  Absence of those attrs
    does not invalidate a non-empty quote/history response.
    """
    fetch = fetch or _default_fetch
    end = _now().date()
    start = end - timedelta(days=10)
    checked: List[Dict[str, Any]] = []
    metadata: Dict[str, Any] = {}
    try:
        frame = fetch([provider_symbol], start.isoformat(), end.isoformat()).get(
            provider_symbol)
        confirmed = frame is not None and not frame.empty
        checked.append({"source": "price_provider", "confirmed": confirmed})
        if confirmed:
            metadata.update({k: frame.attrs[k] for k in
                             ("name", "exchange", "currency")
                             if frame.attrs.get(k)})
    except Exception as exc:  # verification failure must never create rows
        checked.append({"source": "price_provider", "confirmed": False,
                        "error": type(exc).__name__})

    is_us = currency.upper() == "USD" or exchange.upper() in {
        "NASDAQ", "NYSE", "XNAS", "XNYS", "ARCX", "AMEX", "XASE"
    }
    if is_us:
        try:
            if sec_resolve is None:
                from market_data_hub.services.financials import _resolve_cik_via_sec
                sec_resolve = _resolve_cik_via_sec
            hit = sec_resolve(symbol)
            confirmed = bool(hit.get("ticker"))
            checked.append({"source": "sec_ticker_map", "confirmed": confirmed})
            if confirmed and hit.get("name"):
                metadata["name"] = hit["name"]
        except Exception as exc:
            checked.append({"source": "sec_ticker_map", "confirmed": False,
                            "error": type(exc).__name__})
    return {"confirmed": any(x["confirmed"] for x in checked),
            "checked": checked, "metadata": metadata}


def _record_registration_hints(con, listing_id: str, hints: Dict[str, Any], now) -> None:
    """Retain distinct caller hints without duplicating an identical registration."""
    con.execute("""
        INSERT INTO listing_registration_hints
            (listing_id, name_hint, exchange_hint, currency_hint, recorded_at)
        SELECT ?, ?, ?, ?, ?
        WHERE NOT EXISTS (
            SELECT 1 FROM listing_registration_hints WHERE listing_id = ?
                AND name_hint IS NOT DISTINCT FROM ?
                AND exchange_hint IS NOT DISTINCT FROM ?
                AND currency_hint IS NOT DISTINCT FROM ?)
    """, [listing_id, hints["name"], hints["exchange"], hints["currency"], now,
          listing_id, hints["name"], hints["exchange"], hints["currency"]])


def register_listing(symbol: str, *, exchange: str, currency: str,
                     kind: str = "EQUITY", name: Optional[str] = None,
                     provider: str = _DEFAULT_PROVIDER,
                     provider_symbol: Optional[str] = None,
                     db_path: Optional[str] = None, verify: bool = True,
                     verification_fetch=None, sec_resolve=None) -> Dict[str, Any]:
    """Register a listing for use by the normal ensure_price_history job.

    Verification prefers provider metadata. For Yahoo, known venue aliases
    are resolved to the warehouse exchange code, and unmapped provider labels
    that are not MIC-shaped leave the supplied exchange in place.

    Idempotent: re-registering the same (symbol, provider, exchange) returns
    the existing listing (created=False). A second venue for the same
    (symbol, provider) gets its own listing_id (dual listing), never an
    overwrite. Ambiguity later is the resolver's job — it will return both
    candidates and force an explicit listing_id.
    """
    symbol = (symbol or "").strip()
    if not symbol:
        raise ValueError("symbol is required")
    if not (exchange or "").strip() or not (currency or "").strip():
        raise ValueError(
            f"registering the unknown symbol {symbol!r} requires explicit "
            f"exchange and currency (identity is never guessed)")
    exchange = exchange.strip().upper()
    currency = currency.strip().upper()
    provider_symbol = provider_symbol or symbol
    hints = {"name": name, "exchange": exchange, "currency": currency}
    exchange_aliases = _YAHOO_EXCHANGE_MICS if provider == _DEFAULT_PROVIDER else {}
    checked: List[Dict[str, Any]] = []
    if verify:
        verification = _verify_listing(
            symbol, provider_symbol, exchange, currency,
            fetch=verification_fetch, sec_resolve=sec_resolve)
        checked = verification["checked"]
        if not verification["confirmed"]:
            return {"registered": False, "reason": "unknown_symbol",
                    "checked": checked}
        authoritative = verification["metadata"]
        name = authoritative.get("name")
        reported_exchange = authoritative.get("exchange", "").strip().upper()
        reported_exchange = exchange_aliases.get(reported_exchange, reported_exchange)
        if reported_exchange and (provider != _DEFAULT_PROVIDER or
                                  (len(reported_exchange) == 4 and reported_exchange.isalnum())):
            exchange = reported_exchange
        currency = authoritative.get("currency", currency).strip().upper()

    # Also look for rows written with a legacy venue label. Prefer an exact
    # match to the selected exchange when both spellings already exist.
    venue = exchange_aliases.get(exchange, exchange)
    equivalent_exchanges = [venue] + [
        label for label, mic in exchange_aliases.items() if mic == venue
    ]
    with db_write_lock(db_path):
        con = get_conn(db_path)
        try:
            now = _now()
            row = con.execute("""
                SELECT listing_id, instrument_id, currency, exchange FROM listings
                WHERE symbol = ? AND provider = ?
                  AND upper(trim(exchange)) = ANY(?) AND active_to IS NULL
                ORDER BY (upper(trim(exchange)) = ?) DESC, listing_id
                LIMIT 1
            """, [symbol, provider, equivalent_exchanges, exchange]).fetchone()
            if row:
                _record_registration_hints(con, row[0], hints, now)
                return {"listing_id": row[0], "instrument_id": row[1],
                        "symbol": symbol, "exchange": row[3],
                        "currency": row[2], "provider": provider,
                        "registered": True, "created": False}

            instrument_id = _stable_id("ins", symbol)
            base = _stable_id("lst", symbol, provider)
            taken = con.execute(
                "SELECT 1 FROM listings WHERE listing_id = ?", [base]).fetchone()
            # second venue for the same (symbol, provider): venue-qualified id
            listing_id = _stable_id("lst", symbol, provider, exchange) if taken else base

            con.execute("""
                INSERT INTO instruments (instrument_id, issuer_id, kind, name,
                                         created_at, updated_at)
                SELECT ?, NULL, ?, ?, ?, ?
                WHERE NOT EXISTS (SELECT 1 FROM instruments WHERE instrument_id = ?)
            """, [instrument_id, kind, name, now, now, instrument_id])
            con.execute("""
                INSERT INTO listings (listing_id, instrument_id, symbol,
                                      exchange, currency, provider,
                                      provider_symbol, active_from, active_to,
                                      created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
            """, [listing_id, instrument_id, symbol, exchange, currency,
                  provider, provider_symbol or symbol, now, now])
            con.execute("""
                INSERT INTO identifier_aliases (namespace, value, target_type,
                                                target_id, valid_from,
                                                valid_to, updated_at)
                SELECT 'ticker', ?, 'listing', ?, NULL, NULL, ?
                WHERE NOT EXISTS (SELECT 1 FROM identifier_aliases
                                  WHERE namespace = 'ticker' AND value = ?
                                    AND target_type = 'listing' AND target_id = ?)
            """, [symbol, listing_id, now, symbol, listing_id])
            _record_registration_hints(con, listing_id, hints, now)
            return {"listing_id": listing_id, "instrument_id": instrument_id,
                    "symbol": symbol, "exchange": exchange,
                    "currency": currency, "provider": provider,
                    "provider_symbol": provider_symbol or symbol,
                    "registered": True, "created": True, "checked": checked,
                    "hints": hints}
        finally:
            con.close()


def list_empty_listings(*, older_than_minutes: int = 60,
                        db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return stale listings without prices, SEC facts, or active price ingestion."""
    con = get_conn(db_path, read_only=True)
    try:
        return _empty_listings(con, older_than_minutes)
    finally:
        con.close()


def _empty_listings(con, older_than_minutes: int) -> List[Dict[str, Any]]:
    cutoff = _now() - timedelta(minutes=older_than_minutes)
    rows = con.execute("""
        SELECT l.listing_id, l.instrument_id, l.symbol, l.exchange,
               l.currency, l.provider, l.created_at
        FROM listings l JOIN instruments i USING (instrument_id)
        WHERE l.created_at < ?
          AND NOT EXISTS (SELECT 1 FROM prices_daily p
                          WHERE p.listing_id = l.listing_id)
          AND (i.issuer_id IS NULL OR NOT EXISTS (
              SELECT 1 FROM issuers iss JOIN sec_company_facts f
                ON f.cik = iss.cik WHERE iss.issuer_id = i.issuer_id))
          AND NOT EXISTS (SELECT 1 FROM ingestion_runs r
              WHERE r.kind = 'price_history' AND r.status = 'running'
                AND json_extract_string(try_cast(r.input_json AS JSON),
                                        '$.listing_id') = l.listing_id)
          AND NOT EXISTS (SELECT 1 FROM ingestion_jobs j
              WHERE j.kind = 'price_history' AND j.status IN ('running', 'queued')
                AND json_extract_string(try_cast(j.request_json AS JSON),
                                        '$.listing_id') = l.listing_id)
        ORDER BY l.created_at, l.listing_id
    """, [cutoff]).fetchall()
    cols = ["listing_id", "instrument_id", "symbol", "exchange", "currency",
            "provider", "created_at"]
    return [dict(zip(cols, row)) for row in rows]


def prune_empty_listings(*, dry_run: bool = True, older_than_minutes: int = 60,
                         db_path: Optional[str] = None) -> Dict[str, Any]:
    """Remove stale empty listings, their price ledger, and their orphan instruments.

    Eligibility is selected in the deletion transaction under the writer lock.
    Active ingestion is excluded because its fetch runs without that lock.
    """
    if dry_run:
        candidates = list_empty_listings(older_than_minutes=older_than_minutes,
                                         db_path=db_path)
        return {"dry_run": dry_run, "count": len(candidates),
                "listings": candidates}
    with db_write_lock(db_path):
        con = get_conn(db_path)
        try:
            con.execute("BEGIN TRANSACTION")
            candidates = _empty_listings(con, older_than_minutes)
            ids = [item["listing_id"] for item in candidates]
            if not ids:
                con.execute("COMMIT")
                return {"dry_run": False, "count": 0, "listings": []}
            marks = ",".join("?" for _ in ids)
            for table, column in (("ingestion_jobs", "request_json"),
                                  ("ingestion_runs", "input_json")):
                con.execute(f"DELETE FROM {table} WHERE kind = 'price_history' AND "
                            f"json_extract_string(try_cast({column} AS JSON), '$.listing_id') "
                            f"IN ({marks})", ids)
            con.execute(f"DELETE FROM listing_registration_hints WHERE listing_id IN ({marks})", ids)
            con.execute(f"DELETE FROM identifier_aliases WHERE target_type = 'listing' AND target_id IN ({marks})", ids)
            con.execute(f"DELETE FROM listings WHERE listing_id IN ({marks})", ids)
            instrument_ids = list({item["instrument_id"] for item in candidates})
            instrument_marks = ",".join("?" for _ in instrument_ids)
            con.execute(f"DELETE FROM instruments WHERE instrument_id IN ({instrument_marks}) "
                        "AND NOT EXISTS (SELECT 1 FROM listings l WHERE "
                        "l.instrument_id = instruments.instrument_id)", instrument_ids)
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()
    return {"dry_run": False, "count": len(candidates), "listings": candidates}


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
    with the candidates otherwise) and creates or reuses the ingestion job
    keyed by the normalized request hash.

    Concurrency shape (audit CA-06): the writer lock is held only for two
    SHORT windows — registering the job as 'running', then committing the
    results — while the provider fetch runs with NO lock held, so a slow
    provider never blocks other writers. Payload + run/job completion commit
    in ONE transaction: an error after the upsert rolls everything back (no
    materialized payload with an errored job).
    """
    cand = _resolve_single(query, db_path=db_path)
    settings = get_settings()
    start = start or settings.get("start_date", "2000-01-01")
    end = end or _now().date().isoformat()

    fetch = fetch or _default_fetch
    now = _now()

    # ---- phase 1 (short lock): identity + job/run registered as 'running'
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
        finally:
            con.close()

    # ---- phase 2 (NO lock): provider fetch + normalization
    try:
        # Fetch under the PROVIDER's native symbol (may differ from the
        # warehouse symbol, e.g. class-share / venue suffixes), then
        # normalize the returned frame back to listings.symbol before it
        # ever reaches prices_daily.
        provider_symbol = cand.get("provider_symbol") or symbol
        frames = fetch([provider_symbol], start, end)
        df = frames.get(provider_symbol)
        if df is not None and not df.empty:
            df = df.copy()
            df["symbol"] = symbol
            # explicit listing key (CA-01): never rely on the symbol
            # auto-attach here — this is the dual-listing-safe path
            df["listing_id"] = listing_id
            if "source" not in df.columns:
                df["source"] = provider
    except Exception as exc:
        _mark_job_error(db_path, job_id, run_id, exc)
        raise

    # ---- phase 3 (short lock): payload + ledger in ONE transaction
    with db_write_lock(db_path):
        con = get_conn(db_path)
        try:
            con.execute("BEGIN TRANSACTION")
            try:
                added = updated = 0
                if df is not None and not df.empty:
                    added, updated = upsert(con, "prices_daily", df,
                                            outer_txn=True)
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
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
            return {"job_id": job_id, "run_id": run_id,
                    "status": "completed", "listing_id": listing_id,
                    "symbol": symbol, "reused": False,
                    "rows_added": added, "rows_updated": updated}
        except Exception as exc:
            _mark_job_error(db_path, job_id, run_id, exc, con=con)
            raise
        finally:
            con.close()


def _mark_job_error(db_path: Optional[str], job_id: str, run_id: str,
                    exc: Exception, con=None) -> None:
    """Record the failure on job + run (autocommit, outside any transaction).
    Best effort by design: the original exception must surface either way."""
    fin = _now()

    def _write(c) -> None:
        c.execute(
            "UPDATE ingestion_runs SET status = 'error', error_msg = ?, "
            "finished_at = ? WHERE run_id = ?", [str(exc), fin, run_id])
        c.execute(
            "UPDATE ingestion_jobs SET status = 'error', error_msg = ?, "
            "run_id = ?, updated_at = ? WHERE job_id = ?",
            [str(exc), run_id, fin, job_id])

    if con is not None:
        _write(con)
        return
    with db_write_lock(db_path):
        own = get_conn(db_path)
        try:
            _write(own)
        finally:
            own.close()


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
        # keyed by LISTING (audit CA-01): dual listings sharing a ticker read
        # their own series. Config-only candidates (no identity rows yet)
        # cannot have rows either, so the symbol fallback is safe there.
        if cand.get("listing_id"):
            where, params = ["listing_id = ?"], [cand["listing_id"]]
        else:
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
