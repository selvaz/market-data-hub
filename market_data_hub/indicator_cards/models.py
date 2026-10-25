"""Strict, immutable context cards; timing screens never establish lead times."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

CLASSIFICATION_VERSION = "v2.2"


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, populate_by_name=True)


class StoreIds(StrictModel):
    ms: tuple[str, ...] = ()
    es: tuple[str, ...] = ()
    cal: tuple[str, ...] = ()
    cat: tuple[str, ...] = ()
    pan: tuple[str, ...] = ()
    hub: tuple[str, ...] = ()
    # Ingredients of a derived indicator are not aliases for that indicator.
    derived_ms: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


class Evidence(StrictModel):
    measurement: Literal["H", "M", "L"]
    mechanism: Literal["strong", "mixed", "weak", "n.a."]
    local_timing: Literal[
        "v2-pred-pass (US)", "v2-pred-partial (US)",
        "v2-pred-not-supported (US)", "v2-descriptive (US)",
        "lit-local", "lit-US", "analogy", "none", "n.a.",
    ]


class PredictiveScreen(StrictModel):
    relationship_id: str
    pair: str  # Retains candidate, reference and transform, including direction.
    status: Literal["PRED-PASS", "PRED-PARTIAL", "PRED-NOT-SUPPORTED"]
    pass_horizons: tuple[str, ...] = ()
    partial_horizons: tuple[str, ...] = ()
    source_text: str
    vintage_basis: Literal["latest vintage (revised), not real time"] = (
        "latest vintage (revised), not real time"
    )

    @model_validator(mode="after")
    def check_horizons(self):
        if (self.status == "PRED-PASS") != bool(self.pass_horizons):
            raise ValueError("Only PRED-PASS has pass_horizons, and every pass needs horizons")
        if set(self.pass_horizons) & set(self.partial_horizons):
            raise ValueError("A horizon cannot both pass and partially pass")
        if any(h not in {"3m", "6m", "12m"} for h in self.pass_horizons + self.partial_horizons):
            raise ValueError("Horizon absent from the approved monthly screen")
        return self


class DescriptivePeak(StrictModel):
    source_text: str
    meaning: Literal["not a validated lead"] = "not a validated lead"


class Timing(StrictModel):
    timing_class: Literal["LEAD", "COIN", "LAG", "CTX", "UNKNOWN"] = Field(alias="class")
    basis: Optional[Literal["DESCRIPTIVE", "HYPOTHESIS", "DEFINITION"]]
    reference: str
    class_pair: Optional[str]
    relationship_keys: tuple[str, ...]
    predictive_screen: tuple[PredictiveScreen, ...]
    screen_note: str
    descriptive_peak: Optional[DescriptivePeak]
    matched_turning_point_leads: Literal["not estimated"] = "not estimated"


class IndicatorCard(StrictModel):
    key: str
    concept: str
    country: str
    ids: StoreIds
    raw_ids: str
    theme: str
    secondary: tuple[str, ...]
    nature: Literal["HARD", "SOFT", "MARKET", "MARKET-DERIVED", "POLICY"]
    timing: Timing
    evidence: Evidence
    revision_behaviour: str
    known_failure_modes: tuple[str, ...]
    flag: str
    rationale: str
    source: str
    frequency: str


class CardDataset(StrictModel):
    schema_version: Literal[1] = 1
    classification_version: Literal["v2.2"] = CLASSIFICATION_VERSION
    source_table_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    timing_extract_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    aliases_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    cards: tuple[IndicatorCard, ...]

    @model_validator(mode="after")
    def unique_keys(self):
        if len({c.key for c in self.cards}) != len(self.cards):
            raise ValueError("Duplicate card keys")
        return self
