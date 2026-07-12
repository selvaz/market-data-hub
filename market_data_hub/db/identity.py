# -*- coding: utf-8 -*-
"""
identity.py — deterministic identity-row primitives shared by every writer.

The stable-id scheme lives HERE so the two producers of identity rows (the
services layer and the prices upsert auto-attach) can never mint two
different listing_ids for the same (symbol, provider).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Optional

DEFAULT_PROVIDER = "yahoo"


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
    from multiple writers."""
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
