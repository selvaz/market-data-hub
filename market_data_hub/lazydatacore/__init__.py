# -*- coding: utf-8 -*-
"""lazydatacore — the shared data contract for the ecosystem.

This subpackage is the single, dependency-light vocabulary that every tool
external to the data core (LazyFin, LazyHMM, ...) imports so that *identity*,
*time*, *series* and *analysis results* mean the same thing everywhere.

Design rules (see docs/ECOSYSTEM_RATIONALIZATION.md):

* **Leaf, no heavy deps.** Only ``pydantic`` is required at import time; pandas
  is imported lazily inside the few helpers that need it. This lets the data
  core *and* every downstream tool depend on it without dependency cycles.
* **Storage is never touched.** The canonical identity is namespaced
  (:class:`InstrumentId`), and :mod:`.resolver` translates it to the flat
  DuckDB keys the warehouse already uses.
* **Numeric policy is explicit.** ``float`` for series / analysis / charts;
  :class:`Money` (``Decimal``) only for monetary ledger values.

Nothing here has behaviour beyond validation, so every model round-trips
cleanly through ``model_dump(mode="json")`` / ``model_validate_json`` and
through ``lazybridge.Store``.
"""
from __future__ import annotations

from market_data_hub.lazydatacore.identity import (
    CurrencyCode,
    Domain,
    InstrumentId,
)
from market_data_hub.lazydatacore.resolver import (
    NotResolvableError,
    ResolvedRef,
    to_duckdb,
)
from market_data_hub.lazydatacore.result import (
    AnalysisResult,
    LazyDataModel,
    Money,
    Provenance,
    ResultKind,
    SourceRef,
)
from market_data_hub.lazydatacore.series import (
    OHLCV_COLUMNS,
    Frequency,
    PriceBar,
    ReturnKind,
    validate_long_prices,
    validate_wide_prices,
)
from market_data_hub.lazydatacore.timeutil import (
    ensure_utc,
    now_utc,
    parse_iso,
    to_iso,
)

__all__ = [
    # identity
    "CurrencyCode",
    "Domain",
    "InstrumentId",
    # resolver
    "NotResolvableError",
    "ResolvedRef",
    "to_duckdb",
    # result envelopes
    "AnalysisResult",
    "LazyDataModel",
    "Money",
    "Provenance",
    "ResultKind",
    "SourceRef",
    # series
    "OHLCV_COLUMNS",
    "Frequency",
    "PriceBar",
    "ReturnKind",
    "validate_long_prices",
    "validate_wide_prices",
    # time
    "ensure_utc",
    "now_utc",
    "parse_iso",
    "to_iso",
]
