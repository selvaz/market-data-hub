# -*- coding: utf-8 -*-
"""Canonical instrument identity.

The same instrument is named several ways across the ecosystem: the warehouse
uses flat keys (``AAPL``, ``BTCUSDT``, ``FEDFUNDS``) spread over four columns,
while LazyFin uses namespaced strings (``ticker:AAPL``, ``cik:0000320193``).
:class:`InstrumentId` makes the namespace *explicit* and aligns with LazyFin:

    ``"<domain>:<key>"``                     e.g. ``ticker:AAPL``
    ``"<domain>:<key>@<qualifier>"``         e.g. ``crypto:BTCUSDT@1h``

``ticker:`` is the canonical identity for anything in ``prices_daily`` (equities,
ETFs, FX, VIX indices); ``price:`` is accepted as an input **alias** and
normalised to ``ticker:`` so the two are the *same* id (equal, same hash, same
string). Composite natural keys (a country/indicator pair, a factor-set/factor
pair) are kept inside ``key`` separated by ``/`` and split by :mod:`.resolver`.
Only the ``crypto`` domain takes a qualifier (the timeframe); a qualifier on any
other domain is rejected rather than silently ignored. CIK keys are normalised
to the SEC's 10-digit zero-padded form (``cik:320193`` == ``cik:0000320193``);
ISIN keys are upper-cased and format-checked.

Note (Python 3.9+): we use ``class X(str, Enum)`` rather than ``StrEnum``
(3.11+) because the data core targets 3.9.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Annotated, Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

# A three-letter uppercase ISO-4217 currency code (e.g. "USD"). Constrained
# ``str`` rather than an enum because the currency universe is open. Kept here so
# both identity and :class:`~lazydatacore.result.Money` share one definition.
CurrencyCode = Annotated[str, Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")]

# ISIN: 2-letter country + 9 alphanumerics + 1 check digit (format only, not the
# Luhn check digit — that is a separate, optional validation).
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


class Domain(str, Enum):
    """Namespace of an :class:`InstrumentId`.

    ``ticker/crypto/macro/macro_panel/factor`` map to a warehouse dataset (and a
    ``reader.py`` function); ``cik/isin`` are reference identities (LazyFin /
    EDGAR) that do not live in the DuckDB warehouse. ``price`` is an input alias
    for ``ticker`` (normalised away on construction).
    """

    TICKER = "ticker"          # prices_daily (equity, ETF, FX, VIX); canonical
    PRICE = "price"            # input alias for TICKER (normalised to ticker:)
    CRYPTO = "crypto"          # crypto_ohlcv  (intraday, takes a timeframe)
    MACRO = "macro"            # macro_series  (FRED single-value series)
    MACRO_PANEL = "macro_panel"  # macro_panel (country/indicator pair)
    FACTOR = "factor"          # factor_returns (factor_set/factor pair)
    CIK = "cik"                # SEC company identity (reference only)
    ISIN = "isin"              # ISIN reference identity (reference only)


# Input-alias domains normalised to a canonical domain on construction.
_DOMAIN_ALIASES = {"price": "ticker"}


def _parse_canonical(value: str) -> dict:
    """Split a canonical ``"domain:key[@qualifier]"`` string into field values."""
    if ":" not in value:
        raise ValueError(
            f"not a namespaced instrument id: {value!r} "
            "(expected 'domain:key', e.g. 'ticker:AAPL')"
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


def _normalize_fields(data: dict) -> dict:
    """Apply domain aliasing and per-domain key canonicalisation to raw fields."""
    data = dict(data)
    dom = data.get("domain")
    dom_val = dom.value if isinstance(dom, Domain) else dom
    if dom_val in _DOMAIN_ALIASES:  # price -> ticker
        dom_val = _DOMAIN_ALIASES[dom_val]
        data["domain"] = dom_val
    key = data.get("key")
    if isinstance(key, str):
        if dom_val == Domain.CIK.value and key.isdigit():
            data["key"] = key.zfill(10)  # SEC 10-digit zero-padded form
        elif dom_val == Domain.ISIN.value:
            data["key"] = key.upper()
    return data


class InstrumentId(BaseModel):
    """An immutable, namespaced instrument identifier.

    Construct from parts or parse from the canonical string with :meth:`parse`.
    It *serialises* to that same canonical string, so it round-trips through
    JSON and ``lazybridge.Store`` as a plain string field — the same shape as
    LazyFin's string identity (``ticker:AAPL``, ``cik:0000320193``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    domain: Domain
    key: str = Field(min_length=1)
    qualifier: Optional[str] = None  # crypto timeframe only, e.g. "1h"

    @model_validator(mode="before")
    @classmethod
    def _coerce_and_normalize(cls, data: Any) -> Any:
        # Accept the canonical string anywhere an InstrumentId is expected (e.g.
        # nested in AnalysisResult JSON), then normalise aliases / keys so there
        # is a single canonical representation.
        if isinstance(data, str):
            data = _parse_canonical(data)
        if isinstance(data, dict):
            return _normalize_fields(data)
        return data

    @model_validator(mode="after")
    def _check_parts(self) -> "InstrumentId":
        # ":" and "@" are structural separators; neither may appear in a part,
        # and an empty key/qualifier is malformed.
        for label, part in (("key", self.key), ("qualifier", self.qualifier)):
            if part is None:
                continue
            if part == "":
                raise ValueError(f"{label} must not be empty")
            if "@" in part or ":" in part:
                raise ValueError("key/qualifier must not contain ':' or '@'")
        # Only crypto carries a qualifier; reject it elsewhere rather than drop it.
        if self.qualifier is not None and self.domain is not Domain.CRYPTO:
            raise ValueError(
                f"a qualifier ('@...') is only valid for the 'crypto' domain, "
                f"not {self.domain.value!r}"
            )
        # Per-domain key shape for the reference identities.
        if self.domain is Domain.CIK and (not self.key.isdigit() or len(self.key) > 10):
            raise ValueError(f"cik key must be up to 10 digits, got {self.key!r}")
        if self.domain is Domain.ISIN and not _ISIN_RE.match(self.key):
            raise ValueError(f"isin key must be a 12-char ISIN, got {self.key!r}")
        return self

    @model_serializer
    def _serialize(self) -> str:
        # Serialise to the canonical string so InstrumentId round-trips as a
        # plain string, the same shape LazyFin stores identity in.
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
