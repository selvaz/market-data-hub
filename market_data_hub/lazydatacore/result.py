# -*- coding: utf-8 -*-
"""Result envelopes, provenance and the monetary value object.

``Provenance`` and ``SourceRef`` are promoted here from LazyFin so that *every*
analysis output across the ecosystem (regimes, scores, risk reports, ...) can
travel in the same ``source + as_of + tool_version`` envelope. ``Money`` is the
only place ``Decimal`` appears in the shared contract — the boundary of the
"float for analysis, Decimal for money" rule.
"""
from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from market_data_hub.lazydatacore.identity import CurrencyCode, InstrumentId
from market_data_hub.lazydatacore.timeutil import UtcDatetime, now_utc


class LazyDataModel(BaseModel):
    """Base for every shared model.

    ``extra="forbid"`` turns silent typos into validation errors — essential for
    a shared contract — and ``validate_default`` checks constrained defaults
    (such as currency codes) too. Mirrors LazyFin's ``LazyFinModel`` so the two
    contracts are interchangeable.
    """

    model_config = ConfigDict(extra="forbid", validate_default=True)


class SourceRef(LazyDataModel):
    """A pointer back to where a datum came from.

    Web-fetched content is treated as *data, not instructions*
    (``content_is_untrusted`` defaults to ``True``), mirroring LazyCrawler's and
    LazyFin's labelling.
    """

    source: str
    source_id: Optional[str] = None
    url: Optional[str] = None
    retrieved_at: Optional[UtcDatetime] = None
    content_is_untrusted: bool = True


class Provenance(LazyDataModel):
    """Provenance carried by every fact, signal and recommendation.

    ``source`` + ``as_of`` + ``tool_version`` is the invariant the architecture
    requires on everything that flows into a decision.
    """

    source: SourceRef
    as_of: UtcDatetime
    tool_version: Optional[str] = None


class Money(LazyDataModel):
    """A monetary amount in a specific currency.

    The *only* ``Decimal`` in the shared contract: series, returns and chart
    inputs are ``float``; monetary ledger values are ``Money``.
    """

    amount: Decimal
    currency: CurrencyCode = "USD"


class ResultKind(str, Enum):
    """Coarse category of an :class:`AnalysisResult` payload."""

    SIGNAL = "signal"        # e.g. regime label, z-score signal
    SCORE = "score"          # e.g. security score
    FORECAST = "forecast"    # e.g. predicted value / state
    REPORT = "report"        # e.g. risk report, monitor memo
    SERIES = "series"        # a derived series (returns, vol, ...)
    OTHER = "other"


class AnalysisResult(LazyDataModel):
    """Standard envelope for any analysis a tool produces.

    Keeps the payload opaque (a JSON-serialisable dict) so tools stay free to
    model their own outputs, while standardising *how* a result is identified,
    timestamped and attributed. This is what makes results from LazyHMM,
    LazyFin and future tools uniformly storable and comparable.
    """

    kind: ResultKind
    produced_by: str = Field(description="tool/model name, e.g. 'lazyhmm.regime.v1'")
    instruments: List[InstrumentId] = Field(default_factory=list)
    payload: Dict[str, Any] = Field(default_factory=dict)
    provenance: Optional[Provenance] = None
    created_at: UtcDatetime = Field(default_factory=now_utc)
