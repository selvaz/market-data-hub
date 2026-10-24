"""Typed M4 write contracts. Unknown stages are explicit, never inferred as final."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Precision = Literal["exact", "date_only"]
Verification = Literal["unverified", "verified", "rejected"]


def utc(value: str) -> str:
    """Canonical sortable UTC, rejecting naive timestamps (including bare dates)."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone-aware timestamp required")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    @field_validator("*", mode="after")
    @classmethod
    def normalize_time(cls, value, info):
        if value is not None and (
            info.field_name.endswith("_at") or info.field_name == "news_cutoff"
        ):
            return utc(value)
        return value


class Indicator(Contract):
    id: str
    measure: str
    geography: str
    seasonal_adjustment: str
    transform: Literal["level", "mm", "yy", "qq", "qq_saar", "change", "unknown"]
    unit: str
    scale: float = Field(default=1, gt=0)
    frequency: str
    issuer_id: str
    tier: Optional[Literal["T1", "T2", "T3"]] = None
    definition: str
    measure_kind: str = "statistic"
    catalogue_status: Literal["supported", "candidate", "duplicate", "out_of_scope"] = (
        "candidate"
    )
    active: bool = False


class SourceSeries(Contract):
    id: str
    indicator_id: str
    source_id: str
    issuer_id: str
    distributor_id: str
    source_key: str
    expected_lag_seconds: Optional[int] = Field(default=None, ge=0)
    is_published: bool = True
    evidence_role: Literal["issuer", "mirror", "web"]


class ReleaseEvent(Contract):
    id: str
    issuer_id: str
    family: str
    estimate_stage: str = Field(min_length=1)
    edition: str


class ScheduleVersion(Contract):
    release_event_id: str
    expected_at: str
    issuer_timezone: str
    precision: Precision
    source_id: str
    recorded_at: str


class ValueVintage(Contract):
    indicator_id: str
    reference_period: str = Field(min_length=1)
    release_event_id: str
    source_series_id: str
    value: float
    published_as: str
    unit: str
    scale: float = Field(default=1, gt=0)
    estimate_stage: str = Field(min_length=1)
    revision_kind: str
    issuer_published_at: str
    publication_precision: Precision
    provider_vintage_id: Optional[str] = None
    first_seen_at: str
    retrieved_at: str
    document_hash: str
    parser_version: str
    extraction_method: str
    verification_status: Verification
    derived_from_json: str = "[]"
    formula_version: Optional[str] = None
    composition_id: Optional[str] = None
    issuer_local_time: Optional[str] = None
    issuer_timezone: Optional[str] = None
    policy_decision_json: Optional[str] = None

    @field_validator("reference_period")
    @classmethod
    def known_period(cls, value):
        if value.startswith("d:"):
            raise ValueError("release date is not a reference period")
        return value


class Reading(Contract):
    indicator_id: Optional[str] = None
    release_event_id: Optional[str] = None
    source_id: str
    series_key: str
    period_key: str
    reference_period: Optional[str] = None
    period_unknown_reason: Optional[str] = None
    edition: str
    estimate_stage: str = Field(min_length=1)
    reading_kind: str = "actual"
    value: Optional[float] = None
    published_as: Optional[str] = None
    unit: Optional[str] = None
    scale: Optional[float] = Field(default=None, gt=0)
    issuer_id: str
    distributor_id: str
    evidence_origin_id: str
    published_at: Optional[str] = None
    publication_precision: Optional[Precision] = None
    first_seen_at: str
    retrieved_at: str
    content_hash: str
    parser_version: str
    extraction_method: str
    verification_status: Verification
    url: Optional[str] = None
    raw_payload_json: str
    composition_id: Optional[str] = None
    issuer_local_time: Optional[str] = None
    issuer_timezone: Optional[str] = None

    @model_validator(mode="after")
    def period_is_honest(self):
        if self.reference_period is None:
            if not self.period_unknown_reason:
                raise ValueError("unknown period requires a reason")
        elif (
            self.reference_period.startswith("d:")
            or self.period_unknown_reason is not None
        ):
            raise ValueError(
                "known reference period required, without an unknown reason"
            )
        return self


class ResolutionVersion(Contract):
    idempotency_key: str = Field(min_length=1)
    indicator_id: str
    reference_period: str
    estimate_stage: str
    selected_reading_id: Optional[int] = None
    selected_vintage_id: Optional[int] = None
    rejected_alternatives_json: str
    rule_version: str
    reason: str
    decided_at: str


class ConsensusReading(Contract):
    """A date-only cutoff is the START of the issuer-local release date in UTC.

    Captures before that lower bound are pre-release. Same-day captures have
    uncertain ordering: neither eligible nor post_release. Date-only precision
    never proves post-release here; M6 can reconcile a subsequently known time.
    If 08:30 is known, use precision='exact' and the full offset-aware 08:30
    timestamp, not date_only with midnight (e.g. 08:30-04:00 = 12:30Z).
    """

    provider: str
    survey_identity: Optional[str] = None
    indicator_id: str
    release_event_id: str
    measure: str
    reference_period: str
    estimate_stage: str
    value: float
    unit: str
    scale: float = Field(default=1, gt=0)
    published_at: Optional[str] = None
    captured_at: str
    release_cutoff_at: Optional[str] = None
    cutoff_precision: Optional[Precision] = None
    cutoff_basis: Literal["scheduled", "actual", "unknown"] = "unknown"
    content_hash: str

    @model_validator(mode="after")
    def cutoff_metadata(self):
        if self.release_cutoff_at is None:
            if self.cutoff_basis != "unknown" or self.cutoff_precision is not None:
                raise ValueError(
                    "unknown cutoff requires unknown basis and no precision"
                )
        elif self.cutoff_basis == "unknown" or self.cutoff_precision is None:
            raise ValueError("release cutoff requires its basis and precision")
        return self


class Claim(Contract):
    idempotency_key: str = Field(min_length=1)
    release_event_id: str
    claim_type: Literal["release_fact", "attributed_interpretation", "market_response"]
    text: str
    attributed_to: Optional[str] = None
    news_cutoff: str
    created_at: str
    supersedes_id: Optional[int] = None


class ClaimEvidence(Contract):
    idempotency_key: str = Field(min_length=1)
    claim_id: int
    verbatim_passage: str
    content_hash: str
    url: str
    page_id: Optional[str] = None
    evidence_origin_id: str
    published_at: Optional[str] = None
    retrieved_at: str
    news_cutoff: str


class Discrepancy(Contract):
    reading_id: int
    selected_vintage_id: int
    reason: str
    recorded_at: str
