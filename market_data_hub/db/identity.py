# -*- coding: utf-8 -*-
"""
identity.py — deterministic identity-row primitives shared by every writer.

The stable-id scheme lives HERE so the two producers of identity rows (the
services layer and the prices upsert auto-attach) can never mint two
different listing_ids for the same (symbol, provider).
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Optional

DEFAULT_PROVIDER = "yahoo"

# Listing currency by symbol -- lives here (not services/prices.py) so BOTH
# identity-row producers can use it: the services layer (services/prices.py)
# AND the prices-upsert auto-attach path (db/upsert.py's
# _attach_listing_ids -> ensure_listing), which used to hardcode currency to
# NULL for symbols first seen through ordinary price ingestion rather than
# the service/script paths.
#
# Default is USD (the config universe is US-exchange-listed ETFs); the STOXX
# Europe 600 sector sleeves and the Xetra/Amsterdam-listed euro bond UCITS
# ETFs below trade in EUR and are exchange-suffix exceptions (verified
# against tickers.yaml -- every other non-FX symbol is unsuffixed or a plain
# US ticker). EEM is a US-listed (NYSEARCA), USD-denominated ETF that isn't
# in the curated tickers.yaml universe at all, so it would otherwise fall
# through to an unknown (NULL) currency -- this override bypasses the
# "must be in tickers.yaml" gate the same way every other entry here does,
# without pulling it into the curated ingestion universe just to label it.
_CURRENCY_OVERRIDES = {
    "EXSA.DE": "EUR",
    "EXV1.DE": "EUR",
    "EXV3.DE": "EUR",
    "EXV4.DE": "EUR",
    "EXH4.DE": "EUR",
    "EXH1.DE": "EUR",
    "EXH9.DE": "EUR",
    "DBXP.DE": "EUR",
    "IBCL.DE": "EUR",
    "IEAC.AS": "EUR",
    "EEM": "USD",
}
_DEFAULT_CURRENCY = "USD"

# Yahoo FX pair convention: 'AAABBB=X' quotes 1 AAA in BBB -- the instrument's
# price (and therefore its currency) is the SECOND code, not the first, and
# not the universe default. E.g. 'USDJPY=X' is priced in JPY, 'EURUSD=X' in
# USD. Plain ETFs like 'UUP' (not '=X'-suffixed) fall through to the default.
_FX_PAIR_RE = re.compile(r"^[A-Z]{3}([A-Z]{3})=X$")


def _config_symbols() -> set[str]:
    from market_data_hub.config_loader import get_yahoo_tickers
    return {e["symbol"] for e in get_yahoo_tickers()}


def currency_for_symbol(symbol: str) -> Optional[str]:
    """Listing currency for a CONFIG-UNIVERSE symbol (best-effort, not a
    provider lookup): explicit override, else FX quote-currency derivation,
    else the USD default.

    Returns ``None`` for a symbol NOT in ``config/tickers.yaml`` -- an
    ad-hoc/on-demand ticker (e.g. ``7203.T``, ``VOD.L``) is not part of the
    curated, USD-heavy universe this heuristic was derived from, and every
    other override/pattern here is specific to known config symbols. Auto-
    registering an unknown symbol should leave currency unknown (NULL)
    rather than silently guess USD -- and once non-NULL, the backfill script
    would never revisit it to correct a wrong guess (it only fills NULLs).
    """
    if symbol in _CURRENCY_OVERRIDES:
        return _CURRENCY_OVERRIDES[symbol]
    m = _FX_PAIR_RE.match(symbol)
    if m:
        return m.group(1)
    if symbol in _config_symbols():
        return _DEFAULT_CURRENCY
    return None


def sync_currency_overrides(con) -> int:
    """Idempotent migration: force ``listings.currency`` to match
    ``_CURRENCY_OVERRIDES`` for every symbol with an explicit override, even
    on a row that already has a *different* stored value.

    Deploying a new/changed ``_CURRENCY_OVERRIDES`` entry over an
    already-populated database does not, by itself, touch existing rows:
    ``ensure_listing`` skips its insert once the deterministic listing
    already exists, and the general backfill in
    ``scripts/backfill_etf_classification.py`` only fills ``NULL``
    currencies (deliberately -- it must never overwrite a legitimately
    hand-corrected value for a symbol with no override). An override,
    unlike that general USD default, *is* a deliberate, curated correction,
    so forcing it here is safe where the broader backfill's "only fill
    NULLs" caution would not be. Safe to call on every run: a row that
    already matches is left untouched.

    Returns the number of rows corrected.
    """
    now = datetime.now(timezone.utc)
    updated = 0
    for symbol, currency in _CURRENCY_OVERRIDES.items():
        result = con.execute(
            "UPDATE listings SET currency = ?, updated_at = ? "
            "WHERE symbol = ? AND provider = 'yahoo' AND (currency IS NULL OR currency != ?)",
            [currency, now, symbol, currency],
        ).fetchall()
        updated += result[0][0] if result else 0
    return updated


def stable_id(prefix: str, *parts: str) -> str:
    h = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{h}"


def ensure_listing(con, symbol: str, *, kind: str = "OTHER",
                   name: Optional[str] = None,
                   exchange: Optional[str] = None,
                   currency: Optional[str] = None,
                   provider: str = DEFAULT_PROVIDER,
                   provider_symbol: Optional[str] = None) -> str:
    """Idempotently create instrument + listing + ticker alias for a symbol
    and return the listing_id. Deterministic ids: safe to call repeatedly and
    from multiple writers.

    ``currency`` defaults via :func:`currency_for_symbol` when not given, so
    every caller/call site gets the same best-effort currency without having
    to remember to pass it explicitly (a prior per-call-site fix here missed
    the migration path in connection.py's ``_migrate_prices_to_listing_key``,
    which also auto-registers orphan symbols)."""
    if currency is None:
        currency = currency_for_symbol(symbol)
    now = datetime.now(timezone.utc)
    instrument_id = stable_id("ins", symbol)
    listing_id = stable_id("lst", symbol, provider)
    con.execute("""
        INSERT INTO instruments (instrument_id, issuer_id, kind, name,
                                 created_at, updated_at)
        SELECT ?, NULL, ?, ?, ?, ?
        WHERE NOT EXISTS (SELECT 1 FROM instruments WHERE instrument_id = ?)
    """, [instrument_id, kind, name, now, now, instrument_id])
    con.execute("""
        INSERT INTO listings (listing_id, instrument_id, symbol, exchange,
                              currency, provider, provider_symbol,
                              active_from, active_to, created_at, updated_at)
        SELECT ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?
        WHERE NOT EXISTS (SELECT 1 FROM listings WHERE listing_id = ?)
    """, [listing_id, instrument_id, symbol, exchange, currency, provider,
          provider_symbol or symbol, now, now, listing_id])
    con.execute("""
        INSERT INTO identifier_aliases (namespace, value, target_type,
                                        target_id, valid_from, valid_to, updated_at)
        SELECT 'ticker', ?, 'listing', ?, NULL, NULL, ?
        WHERE NOT EXISTS (SELECT 1 FROM identifier_aliases
                          WHERE namespace = 'ticker' AND value = ?
                            AND target_type = 'listing' AND target_id = ?)
    """, [symbol, listing_id, now, symbol, listing_id])
    return listing_id


class AmbiguousSymbolError(RuntimeError):
    """A bare symbol maps to more than one active listing: the caller must
    pass an explicit listing_id (audit CA-01 — collisions must never be
    resolved silently)."""


def duplicate_active_listings(con, symbol: Optional[str] = None):
    """Describe symbols with multiple active listings and their price counts.

    This is an inventory operation only. It deliberately does not decide
    which venue is canonical: real dual listings are valid and must be
    distinguished from accidental empty duplicates by an operator.
    """
    rows = con.execute("""
        WITH duplicated AS (
            SELECT symbol
            FROM listings
            WHERE active_to IS NULL AND (? IS NULL OR symbol = ?)
            GROUP BY symbol
            HAVING COUNT(*) > 1
        )
        SELECT l.symbol, l.listing_id, l.instrument_id, l.exchange,
               l.currency, l.provider, l.provider_symbol,
               COUNT(p.date) AS price_rows, MIN(p.date) AS first_date,
               MAX(p.date) AS last_date
        FROM listings l
        JOIN duplicated d ON d.symbol = l.symbol
        LEFT JOIN prices_daily p ON p.listing_id = l.listing_id
        WHERE l.active_to IS NULL
        GROUP BY l.symbol, l.listing_id, l.instrument_id, l.exchange,
                 l.currency, l.provider, l.provider_symbol
        ORDER BY l.symbol, l.listing_id
    """, [symbol, symbol]).fetchall()
    columns = ("symbol", "listing_id", "instrument_id", "exchange",
               "currency", "provider", "provider_symbol", "price_rows",
               "first_date", "last_date")
    return [dict(zip(columns, row)) for row in rows]


def resolve_empty_duplicate_listing(con, *, symbol: str,
                                    keep_listing_id: str,
                                    deactivate_listing_id: str):
    """Retire an explicitly selected, empty duplicate listing atomically.

    This API never infers which listing to keep. The two IDs must be the only
    active listings for ``symbol`` and must share the same instrument,
    provider, and provider symbol. The listing being retired must have no
    price bars, so history is never moved or discarded. Active aliases on the
    retired row are moved to the keeper (colliding aliases are coalesced), and
    the old listing row is retained with ``active_to`` set for auditability.

    The operation is idempotent: an already inactive empty listing with its
    active aliases moved returns ``changed=False``. Callers that write to a
    shared database should hold the hub's writer lock around this API.
    """
    if not symbol or not keep_listing_id or not deactivate_listing_id:
        raise ValueError("symbol and both listing IDs are required")
    if keep_listing_id == deactivate_listing_id:
        raise ValueError("keeper and duplicate listing IDs must be different")

    rows = con.execute("""
        SELECT listing_id, instrument_id, symbol, provider, provider_symbol,
               active_to
        FROM listings
        WHERE listing_id IN (?, ?)
    """, [keep_listing_id, deactivate_listing_id]).fetchall()
    by_id = {row[0]: row for row in rows}
    if set(by_id) != {keep_listing_id, deactivate_listing_id}:
        raise ValueError("both listing IDs must exist in listings")

    keeper = by_id[keep_listing_id]
    duplicate = by_id[deactivate_listing_id]
    if keeper[2] != symbol or duplicate[2] != symbol:
        raise ValueError("both listings must have the requested symbol")
    if keeper[1] != duplicate[1] or keeper[3] != duplicate[3] \
            or keeper[4] != duplicate[4]:
        raise ValueError(
            "listings must share instrument_id, provider, and provider_symbol")
    if keeper[5] is not None:
        raise ValueError("the keeper listing must be active")

    active_ids = {row[0] for row in con.execute(
        "SELECT listing_id FROM listings "
        "WHERE symbol = ? AND active_to IS NULL", [symbol]).fetchall()}
    expected_active = {keep_listing_id}
    if duplicate[5] is None:
        expected_active.add(deactivate_listing_id)
    if active_ids != expected_active:
        raise ValueError(
            f"expected only the selected listings active for {symbol}; "
            f"found {sorted(active_ids)}")

    price_count_row = con.execute(
        "SELECT COUNT(*) FROM prices_daily WHERE listing_id = ?",
        [deactivate_listing_id]).fetchone()
    price_count = int(price_count_row[0]) if price_count_row else 0
    if price_count:
        raise ValueError(
            f"refusing to retire {deactivate_listing_id}: "
            f"it has {price_count} price rows")

    aliases = con.execute("""
        SELECT namespace, value, target_type
        FROM identifier_aliases
        WHERE target_type = 'listing' AND target_id = ? AND valid_to IS NULL
    """, [deactivate_listing_id]).fetchall()
    already_inactive = duplicate[5] is not None
    if already_inactive and not aliases:
        return {"symbol": symbol, "keep_listing_id": keep_listing_id,
                "deactivated_listing_id": deactivate_listing_id,
                "price_rows": 0, "changed": False}

    now = datetime.now(timezone.utc)
    effective_date = now.date()
    con.execute("BEGIN TRANSACTION")
    try:
        for namespace, value, target_type in aliases:
            collision = con.execute("""
                SELECT 1 FROM identifier_aliases
                WHERE namespace = ? AND value = ? AND target_type = ?
                  AND target_id = ?
            """, [namespace, value, target_type, keep_listing_id]).fetchone()
            if collision:
                con.execute("""
                    DELETE FROM identifier_aliases
                    WHERE namespace = ? AND value = ? AND target_type = ?
                      AND target_id = ?
                """, [namespace, value, target_type, deactivate_listing_id])
            else:
                con.execute("""
                    UPDATE identifier_aliases
                    SET target_id = ?, updated_at = ?
                    WHERE namespace = ? AND value = ? AND target_type = ?
                      AND target_id = ? AND valid_to IS NULL
                """, [keep_listing_id, now, namespace, value, target_type,
                      deactivate_listing_id])

        if not already_inactive:
            con.execute("""
                UPDATE listings SET active_to = ?, updated_at = ?
                WHERE listing_id = ? AND active_to IS NULL
            """, [effective_date, now, deactivate_listing_id])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    return {"symbol": symbol, "keep_listing_id": keep_listing_id,
            "deactivated_listing_id": deactivate_listing_id,
            "price_rows": 0, "changed": True}
