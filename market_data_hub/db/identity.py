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
# Europe 600 sector sleeves trade on Xetra in EUR and are the only
# exchange-suffix exception (verified against tickers.yaml -- every other
# non-FX symbol is unsuffixed or a plain US ticker).
_CURRENCY_OVERRIDES = {
    "EXSA.DE": "EUR",
    "EXV1.DE": "EUR",
    "EXV3.DE": "EUR",
    "EXV4.DE": "EUR",
    "EXH4.DE": "EUR",
    "EXH1.DE": "EUR",
    "EXH9.DE": "EUR",
}
_DEFAULT_CURRENCY = "USD"

# Yahoo FX pair convention: 'AAABBB=X' quotes 1 AAA in BBB -- the instrument's
# price (and therefore its currency) is the SECOND code, not the first, and
# not the universe default. E.g. 'USDJPY=X' is priced in JPY, 'EURUSD=X' in
# USD. Plain ETFs like 'UUP' (not '=X'-suffixed) fall through to the default.
_FX_PAIR_RE = re.compile(r"^[A-Z]{3}([A-Z]{3})=X$")


def currency_for_symbol(symbol: str) -> str:
    """Listing currency for a config-universe symbol (best-effort, not a
    provider lookup): explicit override, else FX quote-currency derivation,
    else the USD default."""
    if symbol in _CURRENCY_OVERRIDES:
        return _CURRENCY_OVERRIDES[symbol]
    m = _FX_PAIR_RE.match(symbol)
    if m:
        return m.group(1)
    return _DEFAULT_CURRENCY


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
