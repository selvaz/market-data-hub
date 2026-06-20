# -*- coding: utf-8 -*-
"""Symbol ⇄ :class:`InstrumentId` registry — the inverse of :mod:`.resolver`.

:mod:`.resolver` answers "where does this canonical id live in DuckDB?"
(``InstrumentId → (table, filters)``). This module answers the two questions a
*consumer* has when it holds a flat warehouse symbol instead of a canonical id:

* :func:`from_symbol` — canonicalise a bare symbol (``"AAPL"``) into an
  :class:`InstrumentId` (``ticker:AAPL``), closing the ``AAPL ⇄ ticker:AAPL`` gap
  that previously forced every tool to wrap bare symbols ad-hoc.
* :func:`from_duckdb` — reconstruct the canonical id from a warehouse row's
  ``table`` + flat key (the exact inverse of :func:`resolver.to_duckdb`), so a
  row read out of DuckDB can be re-identified canonically and round-tripped.

Like the rest of ``lazydatacore`` this is a pure, dependency-light leaf: it does
*not* query DuckDB or guess a symbol's domain from its spelling — domain is
explicit (defaulting to ``ticker``, the ``prices_daily`` identity). Heuristic
disambiguation, if ever wanted, belongs in a warehouse-backed layer above this.
"""
from __future__ import annotations

from typing import Dict

from market_data_hub.lazydatacore.identity import Domain, InstrumentId
from market_data_hub.lazydatacore.resolver import (
    NotResolvableError,
    _DEFAULT_TIMEFRAME,
)

# Reverse of resolver.to_duckdb: warehouse table -> canonical Domain. Composite
# tables (macro_panel/factor_returns) keep their natural "set/name" key inside
# InstrumentId.key, split back into columns by the resolver.
_TABLE_DOMAIN: Dict[str, Domain] = {
    "prices_daily": Domain.TICKER,
    "crypto_ohlcv": Domain.CRYPTO,
    "macro_series": Domain.MACRO,
    "macro_panel": Domain.MACRO_PANEL,
    "factor_returns": Domain.FACTOR,
}


def from_symbol(
    symbol: "str | InstrumentId",
    *,
    domain: "Domain | str" = Domain.TICKER,
    qualifier: "str | None" = None,
) -> InstrumentId:
    """Canonicalise a bare warehouse symbol into an :class:`InstrumentId`.

    An already-namespaced string (``"ticker:AAPL"``, ``"crypto:BTCUSDT@1h"``) or
    an :class:`InstrumentId` passes straight through; a bare symbol (``"AAPL"``)
    is wrapped in ``domain`` — defaulting to ``ticker``, the ``prices_daily``
    identity for equities/ETFs/FX/VIX. ``qualifier`` applies only to ``crypto``.
    """
    if isinstance(symbol, InstrumentId):
        return symbol
    if not isinstance(symbol, str) or not symbol:
        raise ValueError(f"symbol must be a non-empty string, got {symbol!r}")
    if ":" in symbol:
        return InstrumentId.parse(symbol)
    dom = domain if isinstance(domain, Domain) else Domain(domain)
    return InstrumentId(domain=dom, key=symbol, qualifier=qualifier)


def to_symbol(instrument: "str | InstrumentId") -> str:
    """Return the flat warehouse key for an instrument (inverse of typical use).

    For single-key domains this is the warehouse ``symbol`` / ``series_id``; for
    composite domains it is the natural ``"set/name"`` key the resolver splits.
    """
    return InstrumentId.parse(instrument).key


def from_duckdb(
    table: str,
    key: str,
    *,
    qualifier: "str | None" = None,
) -> InstrumentId:
    """Reconstruct the canonical :class:`InstrumentId` from a warehouse row.

    The exact inverse of :func:`resolver.to_duckdb`: given the DuckDB ``table``
    and the row's flat key (``symbol`` / ``series_id``, or the ``"set/name"``
    composite for ``macro_panel`` / ``factor_returns``), return the canonical id.
    ``qualifier`` carries the ``crypto`` timeframe (defaulting to the resolver's
    default) and is rejected for every other table.
    """
    domain = _TABLE_DOMAIN.get(table)
    if domain is None:
        allowed = ", ".join(sorted(_TABLE_DOMAIN))
        raise NotResolvableError(
            f"unknown warehouse table {table!r}; expected one of: {allowed}"
        )
    if domain is Domain.CRYPTO:
        return InstrumentId(domain=domain, key=key, qualifier=qualifier or _DEFAULT_TIMEFRAME)
    if qualifier is not None:
        raise ValueError(
            f"a qualifier is only valid for the crypto table, not {table!r}"
        )
    return InstrumentId(domain=domain, key=key)
