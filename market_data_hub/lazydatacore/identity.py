# -*- coding: utf-8 -*-
"""Canonical instrument identity.

The same instrument is named three different ways across the ecosystem today:
the warehouse uses flat keys (``AAPL``, ``BTCUSDT``, ``FEDFUNDS``) spread over
four different columns, while LazyFin uses namespaced strings (``ticker:AAPL``,
``cik:0000320193``). :class:`InstrumentId` makes the namespace *explicit* and
unifies both worlds:

    ``"<domain>:<key>"``                     e.g. ``price:AAPL``
    ``"<domain>:<key>@<qualifier>"``         e.g. ``crypto:BTCUSDT@1h``

Composite natural keys (a country/indicator pair, a factor-set/factor pair) are
kept inside ``key`` separated by ``/`` and split by :mod:`.resolver`, so this
model stays simple (domain, key, qualifier).

Note (Python 3.9+): we use ``class X(str, Enum)`` rather than ``StrEnum``
(3.11+) because the data core targets 3.9.
"""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

# A three-letter uppercase ISO-4217 currency code (e.g. "USD"). Constrained
# ``str`` rather than an enum because the currency universe is open. Kept here so
# both identity and :class:`~lazydatacore.result.Money` share one definition.
CurrencyCode = Annotated[str, Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")]


class Domain(str, Enum):
    """Namespace of an :class:`InstrumentId`.

    The first five map to a warehouse dataset (and a ``reader.py`` function);
    the last three are reference/entity identities (LazyFin / EDGAR) that do not
    live in the DuckDB warehouse.
    """

    PRICE = "price"            # prices_daily  (equity, ETF, FX, VIX, crypto-daily)
    CRYPTO = "crypto"          # crypto_ohlcv  (intraday, needs a timeframe)
    MACRO = "macro"            # macro_series  (FRED single-value series)
    MACRO_PANEL = "macro_panel"  # macro_panel (country/indicator pair)
    FACTOR = "factor"          # factor_returns (factor_set/factor pair)
    TICKER = "ticker"          # LazyFin security identity; alias of PRICE for market data
    CIK = "cik"                # SEC company identity (reference only)
    ISIN = "isin"              # ISIN reference identity (reference only)


def _parse_canonical(value: str) -> dict:
    """Split a canonical ``"domain:key[@qualifier]"`` string into field values."""
    if ":" not in value:
        raise ValueError(
            f"not a namespaced instrument id: {value!r} "
            "(expected 'domain:key', e.g. 'price:AAPL')"
        )
    domain_str, rest = value.split(":", 1)
    qualifier: Optional[str] = None
    if "@" in rest:
        rest, qualifier = rest.split("@", 1)
    try:
        domain = Domain(domain_str)
    except ValueError as exc:
        allowed = ", ".join(d.value for d in Domain)
        raise ValueError(
            f"unknown domain {domain_str!r}; allowed: {allowed}"
        ) from exc
    return {"domain": domain, "key": rest, "qualifier": qualifier}


class InstrumentId(BaseModel):
    """An immutable, namespaced instrument identifier.

    Construct from parts or parse from the canonical string with :meth:`parse`.
    It *serialises* to that same canonical string, so it round-trips through
    JSON and ``lazybridge.Store`` as a plain string field — matching LazyFin's
    string identity (``ticker:AAPL``) rather than a nested object.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    domain: Domain
    key: str = Field(min_length=1)
    qualifier: Optional[str] = None  # e.g. crypto timeframe "1h"

    @model_validator(mode="before")
    @classmethod
    def _coerce_canonical_string(cls, data: Any) -> Any:
        # Accept the canonical string anywhere an InstrumentId is expected (e.g.
        # nested in AnalysisResult JSON), so the string serializer round-trips.
        if isinstance(data, str):
            return _parse_canonical(data)
        return data

    @model_validator(mode="after")
    def _check_parts(self) -> "InstrumentId":
        # "@" is the qualifier separator and ":" the domain separator; neither
        # may appear in key/qualifier, and an empty qualifier is malformed.
        for label, part in (("key", self.key), ("qualifier", self.qualifier)):
            if part is None:
                continue
            if part == "":
                raise ValueError(f"{label} must not be empty")
            if "@" in part or ":" in part:
                raise ValueError("key/qualifier must not contain ':' or '@'")
        return self

    @model_serializer
    def _serialize(self) -> str:
        # Serialise to the canonical string so InstrumentId round-trips as a
        # plain string, interchangeable with LazyFin's string identity.
        return str(self)

    def __str__(self) -> str:
        base = f"{self.domain.value}:{self.key}"
        return f"{base}@{self.qualifier}" if self.qualifier else base

    @classmethod
    def parse(cls, value: "str | InstrumentId") -> "InstrumentId":
        """Parse a canonical ``"domain:key[@qualifier]"`` string.

        Already-constructed :class:`InstrumentId` values pass through, so this
        is safe to call on mixed input.
        """
        if isinstance(value, InstrumentId):
            return value
        return cls(**_parse_canonical(value))
