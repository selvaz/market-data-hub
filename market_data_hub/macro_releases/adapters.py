"""Shared adapter interface and short writes into the M4 store."""
from __future__ import annotations

from dataclasses import dataclass
import base64
import json
from datetime import date
from typing import Optional, Protocol

from .catalogue import INDICATORS
from .http import Document
from .documents import put_document
from .models import Discrepancy, Reading, ReleaseEvent, SourceSeries, ValueVintage, utc

PARSER_VERSION = "official-adapters:m5-v2"


def document_payload(document):
    try:
        return {"encoding": "utf-8", "body": document.body.decode("utf-8")}
    except UnicodeDecodeError:
        return {"encoding": "base64", "body": base64.b64encode(document.body).decode("ascii")}


@dataclass(frozen=True)
class Observation:
    indicator_id: str
    family: str
    period: str
    value: Optional[float]
    printed: str
    series_key: str
    source: str
    document: Document
    edition: str
    published_at: Optional[str]
    stage: str = "regular"
    revision: str = "initial"
    precision: str = "date_only"
    provider_vintage: Optional[str] = None
    method: str = "api"
    published: bool = True
    inputs: tuple = ()
    formula: Optional[str] = None
    event_stage: Optional[str] = None
    composition_id: Optional[str] = None
    issuer_local_time: Optional[str] = None
    issuer_timezone: Optional[str] = None
    needs_review: bool = False
    unit: Optional[str] = None
    policy_decision: Optional[dict] = None
    parser_version: str = PARSER_VERSION


class Adapter(Protocol):
    def fetch(self, families: list[str], since: str) -> list[Observation]: ...


def revision_kind(store, obs, issuer):
    """Classify ordinary editions against earlier published issuer history.

    Adapters distinguish a first publication from a historical/current snapshot.
    Only the writer knows whether that snapshot is a backfill or a routine
    revision. Stages share a measure/period family: flash -> final and advance
    -> second -> third are routine revisions even across different source keys.
    Later publications loaded first must not revise an older archived edition.
    Keep explicit initial, benchmark and provider-history kinds intact.
    """
    if obs.revision not in ("routine", "backfill") or obs.published_at is None:
        return obs.revision
    # ECY2's rounded index is a separate source series from the retired 4dp API.
    source_filter = " AND s.source_key=?" if obs.series_key == "MGDP:ECY2:index-1dp" else ""
    params = (obs.indicator_id, obs.period, utc(obs.published_at), issuer, issuer)
    prior = store.conn.execute(
        "SELECT 1 FROM value_vintages v JOIN source_series s ON s.id=v.source_series_id "
        "WHERE v.indicator_id=? AND v.reference_period=? AND v.issuer_published_at<=? "
        "AND s.issuer_id=? AND s.source_id=? AND s.evidence_role='issuer' "
        "AND s.is_published=1" + source_filter + " LIMIT 1",
        params + ((obs.series_key,) if source_filter else ()),
    ).fetchone()
    if prior:
        return "routine"
    return "backfill"


def persist(store, observations):
    """Mirror differences attach to the exact official edition, never replace it.

    M6 owns general resolution. This only compares these exact mapped measures,
    in their declared unit/scale and same event/stage. Mirrors remain unverified.
    """
    ids = []
    for obs in observations:
        indicator = INDICATORS[obs.indicator_id]
        if indicator.frequency == "W":
            if len(obs.period) != 10:
                raise ValueError("Weekly measures require an explicit survey week ending date")
            date.fromisoformat(obs.period)
        if indicator.id == "us_initial_jobless_claims" and date.fromisoformat(obs.period).weekday() != 5:
            raise ValueError("Weekly claims require an explicit Saturday week ending date")
        if indicator.id == "us_pmms_30y":
            from .housing_editions import EDITIONS
            if (obs.family != "us_pmms" or
                    EDITIONS["us_pmms"].get(obs.edition, ())[:1] != (obs.period,)):
                raise ValueError("PMMS requires the reviewed survey week ending date")
        issuer = indicator.issuer_id
        mirror = obs.source != issuer
        verified = not (mirror or not obs.published or obs.method == "archive_text" or obs.needs_review
                        or indicator.geography == "EA_PENDING")
        revision = revision_kind(store, obs, issuer)
        store.put(indicator)
        event_stage = obs.event_stage or obs.stage
        event_id = ":".join((issuer, obs.family, event_stage, obs.edition))
        store.put(ReleaseEvent(id=event_id, issuer_id=issuer, family=obs.family,
                               estimate_stage=event_stage, edition=obs.edition))
        series_id = ":".join((obs.source, obs.indicator_id, obs.series_key,
                              "published" if obs.published else "derived"))
        store.put(SourceSeries(id=series_id, indicator_id=indicator.id,
                               source_id=obs.source, issuer_id=issuer,
                               distributor_id=obs.source, source_key=obs.series_key,
                               is_published=obs.published,
                               evidence_role="mirror" if mirror else "issuer"))
        with store.transaction():
            identity = put_document(store, obs.document)
        store.put(Reading(
            indicator_id=indicator.id, release_event_id=event_id, source_id=obs.source,
            series_key=obs.series_key, period_key=obs.period, reference_period=obs.period,
            edition=obs.edition, estimate_stage=obs.stage, value=obs.value,
            published_as=obs.printed, unit=obs.unit or indicator.unit, scale=indicator.scale,
            issuer_id=issuer, distributor_id=obs.source,
            evidence_origin_id=f"{obs.source}:{obs.series_key}", published_at=obs.published_at,
            publication_precision=obs.precision, first_seen_at=obs.document.retrieved_at,
            retrieved_at=obs.document.retrieved_at, content_hash=identity,
            parser_version=obs.parser_version, extraction_method=obs.method,
            verification_status="verified" if verified and obs.value is not None else "unverified", url=obs.document.url,
            reading_kind="unavailable" if obs.value is None else ("actual" if obs.published else "derived"),
            raw_payload_json=json.dumps({"provider_vintage": obs.provider_vintage,
                                         "policy_decision": obs.policy_decision,
                                         "document_hash": identity}),
            composition_id=obs.composition_id, issuer_local_time=obs.issuer_local_time,
            issuer_timezone=obs.issuer_timezone,
        ))
        if obs.value is None or obs.needs_review or obs.published_at is None:
            continue  # Keep the issuer's nonnumeric evidence, never invent a vintage.
        vintage_id = store.put(ValueVintage(
            indicator_id=indicator.id, reference_period=obs.period, release_event_id=event_id,
            source_series_id=series_id, value=obs.value, published_as=obs.printed,
            unit=indicator.unit, scale=indicator.scale, estimate_stage=obs.stage,
            revision_kind=revision, issuer_published_at=obs.published_at,
            publication_precision=obs.precision, provider_vintage_id=obs.provider_vintage,
            first_seen_at=obs.document.retrieved_at, retrieved_at=obs.document.retrieved_at,
            document_hash=identity, parser_version=obs.parser_version,
            extraction_method=obs.method, verification_status="verified" if verified else "unverified",
            derived_from_json=json.dumps(obs.inputs), formula_version=obs.formula,
            composition_id=obs.composition_id, issuer_local_time=obs.issuer_local_time,
            issuer_timezone=obs.issuer_timezone,
            policy_decision_json=json.dumps(obs.policy_decision) if obs.policy_decision else None,
        ))
        ids.append(vintage_id)
        # Also reconcile mirrors fetched before the issuer, without mutating them.
        official = store.conn.execute(
            "SELECT v.* FROM value_vintages v JOIN source_series s ON s.id=v.source_series_id "
            "WHERE v.indicator_id=? AND v.reference_period=? AND v.release_event_id=? "
            "AND v.estimate_stage=? AND v.verification_status='verified' AND s.evidence_role='issuer' "
            "AND s.is_published=1 ORDER BY v.id DESC LIMIT 1",
            (indicator.id, obs.period, event_id, obs.stage),
        ).fetchone()
        if official is not None:
            mirrors = store.conn.execute(
                "SELECT * FROM readings WHERE indicator_id=? AND reference_period=? "
                "AND release_event_id=? AND estimate_stage=? AND distributor_id != issuer_id",
                (indicator.id, obs.period, event_id, obs.stage),
            ).fetchall()
            for row in mirrors:
                if row["value"] is not None and row["unit"] == official["unit"] and row["scale"] == official["scale"]:
                    # Official API rates are printed to 0.1 pp; counts to 1 thousand.
                    tolerance = 0.05 if indicator.unit == "%" or indicator.id == "us_trade_balance" else 0.5
                    if abs(row["value"] - official["value"]) > tolerance + 1e-10:
                        store.put(Discrepancy(reading_id=row["id"], selected_vintage_id=official["id"],
                                              reason="Exact edition differs from issuer published precision",
                                              recorded_at=obs.document.retrieved_at))
    return ids
