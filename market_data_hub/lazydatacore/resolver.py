# -*- coding: utf-8 -*-
"""Resolve a canonical :class:`InstrumentId` to the flat DuckDB keys.

This is the *single place* the namespaced identity is translated to the
warehouse's physical layout. The warehouse schema is never changed: tools speak
``InstrumentId``, the resolver hands back the table, the column filters and the
matching ``reader.py`` function to call.

    >>> to_duckdb(InstrumentId.parse("price:AAPL"))
    ResolvedRef(dataset='prices', table='prices_daily',
                filters={'symbol': 'AAPL'}, reader='read_prices')

Reference-only identities (``cik:``, ``isin:``) raise
:class:`NotResolvableError`: they are entity identities (EDGAR / LazyFin), not
market-data rows in DuckDB.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

from market_data_hub.lazydatacore.identity import Domain, InstrumentId

# Default crypto timeframe when an InstrumentId omits the qualifier.
_DEFAULT_TIMEFRAME = "1h"


class NotResolvableError(ValueError):
    """Raised when an InstrumentId has no row representation in the warehouse."""


@dataclass(frozen=True)
class ResolvedRef:
    """The physical coordinates of an instrument in the DuckDB warehouse."""

    dataset: str                       # logical dataset name (reader vocabulary)
    table: str                         # DuckDB table
    filters: Dict[str, str] = field(default_factory=dict)  # column -> value
    reader: str = ""                   # matching reader.py function name


def to_duckdb(instrument: "str | InstrumentId") -> ResolvedRef:
    """Translate an :class:`InstrumentId` (or its string form) to a warehouse ref."""
    iid = InstrumentId.parse(instrument)
    domain = iid.domain

    # PRICE and TICKER are the same physical dataset: prices_daily keyed by symbol.
    if domain in (Domain.PRICE, Domain.TICKER):
        return ResolvedRef(
            dataset="prices", table="prices_daily",
            filters={"symbol": iid.key}, reader="read_prices",
        )

    if domain is Domain.CRYPTO:
        return ResolvedRef(
            dataset="crypto", table="crypto_ohlcv",
            filters={"symbol": iid.key, "timeframe": iid.qualifier or _DEFAULT_TIMEFRAME},
            reader="read_crypto",
        )

    if domain is Domain.MACRO:
        return ResolvedRef(
            dataset="macro", table="macro_series",
            filters={"series_id": iid.key}, reader="read_macro",
        )

    if domain is Domain.MACRO_PANEL:
        # Composite key "COUNTRY/INDICATOR", e.g. "USA/real_gdp_growth".
        country, indicator = _split_pair(iid.key, "macro_panel", "country/indicator")
        return ResolvedRef(
            dataset="macro_panel", table="macro_panel",
            filters={"country_iso3": country, "indicator_id": indicator},
            reader="read_macro_panel",
        )

    if domain is Domain.FACTOR:
        # Composite key "FACTOR_SET/FACTOR", e.g. "FF5_daily/MKT".
        factor_set, factor = _split_pair(iid.key, "factor", "factor_set/factor")
        return ResolvedRef(
            dataset="factors", table="factor_returns",
            filters={"factor_set": factor_set, "factor": factor},
            reader="read_factors",
        )

    # cik / isin: reference identity, not a warehouse row.
    raise NotResolvableError(
        f"{domain.value!r} is a reference identity with no DuckDB rows; "
        "resolve it via the EDGAR/security layer, not the warehouse."
    )


def _split_pair(key: str, domain: str, shape: str) -> "tuple[str, str]":
    parts = key.split("/")
    if len(parts) != 2 or not all(parts):
        raise NotResolvableError(
            f"{domain} key must be '{shape}', got {key!r}"
        )
    return parts[0], parts[1]
