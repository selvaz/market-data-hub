"""Pure, read-only authority resolution over a knowledge-filtered store snapshot.

Deterministic rules (m6-v1), in order:
* Match the exact indicator (measure/geography/adjustment/transform), period,
  stage, compatible units and composition. Never infer an edition from a URL.
* Filter knowledge BEFORE selection. Rejected and derived evidence cannot win.
* Within one source series/edition, the latest vintage supersedes corrections;
  raw rereads cannot supersede its vintage. Independent sources keep their votes
  as evidence, never as a majority. Equal values retain all their provenance.
* Choose the latest captured issuer edition, then its verified publication,
  including a verified copy distributed by a mirror. Without issuer evidence,
  prefer a structured mirror, then other mirror/web/legacy evidence.
* Within an authority tier choose the latest publication time. A mirror with a
  strictly later publication of a DIFFERENT edition yields issuer_pending.
  Opaque edition names do not establish chronology. Older editions cannot dispute.
* Dispute only different values in the SAME edition/composition/stage between
  verified issuers, or an unverified issuer HTML/LLM extraction against structured
  evidence. Units/scales are converted explicitly; no rates are derived or rounded.
* Remaining ties: edition, source id, series key, content hash, vintage before
  reading, row id (ascending). These choose a display value, not a truth vote.

No decisions are persisted and no wall clock or network is used here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
import math
from typing import Optional

from .models import utc

RULE_VERSION = "macro-resolver:m6-v1"
END_OF_TIME = "9999-12-31T23:59:59.999999+00:00"
COLLECT_LOG_LIMITATION = (
    "M5 collect summaries/run failures are not persisted in this schema; absence "
    "of evidence cannot establish a collector failure. Imported failed legacy "
    "runs can be identified, but their exact failure time is unavailable."
)


def canonical_unit(unit):
    return {"percent": "%", "percentage": "%", "pct": "%"}.get(unit, unit)


def convert(value, unit, scale, target):
    """Convert only explicit scale and unit aliases; never infer a transform."""
    if unit is None or scale is None or canonical_unit(unit) != canonical_unit(target["unit"]):
        return None
    return value * scale / target["scale"]


def differs(left, right):
    return not math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-10)


def structured(method):
    return method.lower() in {"api", "structured", "csv", "json", "sdmx", "pdf_table"}


@dataclass(frozen=True)
class Evidence:
    id: str
    indicator_id: str
    reference_period: Optional[str]
    stage: str
    event_id: Optional[str]
    edition: str
    value: Optional[float]
    unit: Optional[str]
    scale: Optional[float]
    published_at: Optional[str]
    first_seen_at: str
    source_id: str
    series_key: str
    issuer_id: str
    distributor_id: str
    role: str
    verified: bool
    method: str
    content_hash: str
    precision: Optional[str]
    composition_id: Optional[str]
    url: Optional[str] = None
    event_time: Optional[str] = None
    time_basis: str = "issuer_publication"
    revision_kind: Optional[str] = None
    reading_kind: str = "actual"
    legacy: Optional[dict] = None

    @property
    def official(self):
        return self.role == "issuer" and self.verified

    def provenance(self):
        return asdict(self)


@dataclass(frozen=True)
class Resolution:
    status: str
    indicator_id: str
    reference_period: str
    stage: str
    value: float
    unit: str
    scale: float
    transform: str
    edition: str
    published_at: Optional[str]
    first_seen_at: str
    source: dict
    evidence_ids: tuple[str, ...]
    discrepancies: tuple[dict, ...]
    provenance: tuple[dict, ...]
    reason: str
    rule_version: str = RULE_VERSION

    def to_dict(self):
        return asdict(self)


@dataclass
class Snapshot:
    indicators: dict
    events: dict
    schedules: list
    evidence: list[Evidence]
    consensus: list
    cutoff: str

    @classmethod
    def load(cls, store, as_known_at=None):
        """Materialize one short SQLite read snapshot, including mutable links.

        Legacy links have no version history. A link updated after the cutoff
        is conservatively unavailable, even if its raw reading was seen earlier.
        """
        cutoff = utc(as_known_at) if as_known_at else END_OF_TIME
        conn = store.conn
        owns_transaction = not conn.in_transaction
        if owns_transaction:
            conn.execute("BEGIN")
        try:
            indicators = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM indicators")}
            events = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM release_events")}
            series = [dict(r) for r in conn.execute("SELECT * FROM source_series")]
            vintages = [dict(r) for r in conn.execute("SELECT * FROM value_vintages WHERE first_seen_at<=?", (cutoff,))]
            readings = [dict(r) for r in conn.execute("SELECT * FROM readings WHERE first_seen_at<=?", (cutoff,))]
            links = {r["reading_id"]: dict(r) for r in conn.execute("SELECT * FROM legacy_reading_links WHERE updated_at<=?", (cutoff,))}
            schedules = [dict(r) for r in conn.execute("SELECT * FROM schedule_versions WHERE recorded_at<=? ORDER BY recorded_at,id", (cutoff,))]
            consensus = [dict(r) for r in conn.execute("SELECT * FROM consensus_readings WHERE captured_at<=?", (cutoff,))]
        finally:
            if owns_transaction:
                conn.execute("ROLLBACK")  # finish read transaction; never mutate
        by_id = {s["id"]: s for s in series}
        by_key = {(s["indicator_id"], s["source_id"], s["source_key"]): s for s in series}
        evidence = []
        for row in vintages:
            source = by_id[row["source_series_id"]]
            if not source["is_published"] or row["formula_version"]:
                continue
            evidence.append(cls._evidence(row, source, indicators, events, vintage=True))
        for row in readings:
            link = links.get(row["id"])
            legacy = json.loads(row["raw_payload_json"]) if row["parser_version"].startswith("legacy-calendar-") else None
            if legacy:
                # Do not apply today's mutable mapping to a historical snapshot.
                if not link or not link["indicator_id"]:
                    continue
                row["indicator_id"] = link["indicator_id"]
                row["estimate_stage"] = link["estimate_stage"]
                legacy = dict(legacy, link=link)
            if row["indicator_id"] not in indicators:
                continue
            source = by_key.get((row["indicator_id"], row["source_id"], row["series_key"]))
            if source and not source["is_published"]:
                continue
            evidence.append(cls._evidence(row, source, indicators, events, legacy=legacy))
        evidence = [e for e in evidence if e is not None and
                    (e.published_at is None or e.published_at <= cutoff)]
        return cls(indicators, events, schedules, evidence, consensus, cutoff)

    @staticmethod
    def _evidence(row, source, indicators, events, *, vintage=False, legacy=None):
        if row["verification_status"] == "rejected":
            return None
        indicator = indicators[row["indicator_id"]]
        event = events.get(row["release_event_id"], {})
        issuer = source["issuer_id"] if source else row["issuer_id"]
        role = source["evidence_role"] if source else "web"
        if issuer != indicator["issuer_id"] or event.get("issuer_id", issuer) != indicator["issuer_id"]:
            role = "web"
        # Preserve M5's exclusion of known unverified tool-rendered archives.
        if source and source["source_id"] == "BLS" and source["source_key"] == "CES0000000001:archive-change":
            role = "web"
        published = row["issuer_published_at"] if vintage else row["published_at"]
        event_id, event_time, time_basis = row["release_event_id"], published, "issuer_publication"
        edition = event.get("edition", row.get("edition", ""))
        if not vintage and event and row["edition"] != edition:
            # An inconsistent event link is evidence, not permission to rewrite
            # the observation's edition or assert issuer authority for that event.
            edition, event_id, role = row["edition"], None, "web"
        if role != "issuer":
            time_basis = "source_publication"
        precision = row["publication_precision"]
        if legacy:
            raw, link = legacy["row"], legacy["link"]
            event_id = f"legacy:{row['source_id']}:{link['occurrence_key'] or row['series_key']}:{row['estimate_stage']}"
            event_time = raw.get("release_at_utc") or raw.get("scheduled_at_utc")
            precision = "exact" if event_time else "date_only"
            event_time = utc(event_time or raw["release_date"] + "T00:00:00Z")
            time_basis = "legacy_calendar"
        return Evidence(
            id=f"{'vintage' if vintage else 'reading'}:{row['id']}",
            indicator_id=row["indicator_id"], reference_period=row["reference_period"],
            stage=row["estimate_stage"], event_id=event_id,
            edition=edition, value=row["value"],
            unit=row["unit"], scale=row["scale"], published_at=published,
            first_seen_at=row["first_seen_at"], source_id=source["source_id"] if source else row["source_id"],
            series_key=source["source_key"] if source else row["series_key"], issuer_id=issuer,
            distributor_id=source["distributor_id"] if source else row["distributor_id"],
            role=role, verified=row["verification_status"] == "verified",
            method=row["extraction_method"], content_hash=row["document_hash"] if vintage else row["content_hash"],
            precision=precision, composition_id=row["composition_id"],
            url=None if vintage else row["url"], event_time=event_time, time_basis=time_basis,
            revision_kind=row.get("revision_kind"), reading_kind=row.get("reading_kind", "actual"), legacy=legacy,
        )


def compatible(evidence, indicator):
    return (evidence.value is not None and evidence.reference_period is not None and
            evidence.reading_kind in {"actual", "legacy_merged_actual"} and
            (evidence.composition_id is None or evidence.composition_id == indicator["geography"]) and
            convert(evidence.value, evidence.unit, evidence.scale, indicator) is not None)


def _tier(e):
    return (0 if e.official else 1 if e.role == "issuer" else
            2 if e.role == "mirror" and structured(e.method) else 3)


def _tie(e):
    kind, row_id = e.id.split(":")
    return (e.edition, e.source_id, e.series_key, e.content_hash, kind != "vintage", int(row_id))


def publication_order(e):
    if e.published_at:
        return e.published_at
    if e.legacy:
        return utc(e.legacy["row"].get("observed_at") or e.legacy["legacy_first_seen_at"])
    return ""


def resolve_evidence(snapshot, indicator_id, reference_period, stage, *, event_id=None):
    indicator = snapshot.indicators.get(indicator_id)
    if indicator is None:
        return None
    all_rows = [e for e in snapshot.evidence if e.indicator_id == indicator_id and
                e.reference_period == reference_period and e.stage == stage and compatible(e, indicator)]
    if event_id is not None:
        all_rows = [e for e in all_rows if e.event_id == event_id]
    if not all_rows:
        return None
    streams = {}
    for e in all_rows:
        key = (e.source_id, e.series_key, e.edition, e.event_id)
        streams.setdefault(key, []).append(e)
    rows = []
    for stream in streams.values():
        vintages = [e for e in stream if e.id.startswith("vintage:")]
        pool = vintages or stream
        newest = max(e.first_seen_at for e in pool)
        rows.append(min((e for e in pool if e.first_seen_at == newest), key=_tie))
    issuers = [e for e in rows if e.role == "issuer"]
    if issuers:
        newest_issuer = max(publication_order(e) for e in issuers)
        edition_leader = min((e for e in issuers if publication_order(e) == newest_issuer), key=lambda e: (_tier(e), _tie(e)))
        pool = [e for e in issuers if e.edition == edition_leader.edition]
    else:
        pool = rows
    tier = min(_tier(e) for e in pool)
    pool = [e for e in pool if _tier(e) == tier]
    newest = max(publication_order(e) for e in pool)
    selected = min((e for e in pool if publication_order(e) == newest), key=_tie)
    pending = [e for e in rows if e.role == "mirror" and e.edition != selected.edition and
               e.published_at and (not selected.published_at or e.published_at > selected.published_at)]
    status = "official" if selected.official else "provisional"
    reason = "verified issuer publication" if selected.official else "only unverified or non-issuer evidence"
    if pending and selected.role == "issuer":
        newest = max(e.published_at for e in pending)
        selected = min((e for e in pending if e.published_at == newest), key=_tie)
        status, reason = "issuer_pending", "issuer not yet captured for the newer mirror edition"
    value = convert(selected.value, selected.unit, selected.scale, indicator)
    discrepancies = []
    for e in sorted(rows, key=_tie):
        other = convert(e.value, e.unit, e.scale, indicator)
        if not differs(value, other):
            continue
        same_edition = e.edition == selected.edition
        conflict = same_edition and (
            (selected.official and e.official) or
            (selected.role == "issuer" and not selected.verified and
             any(token in selected.method.lower() for token in ("llm", "html", "archive_text")) and structured(e.method))
        )
        if conflict and status != "issuer_pending":
            status, reason = "disputed", "compatible evidence conflicts at equal authority or with an unverified issuer extraction"
        discrepancies.append({"evidence_id": e.id, "value": other, "unit": indicator["unit"],
                              "scale": indicator["scale"], "source": e.source_id, "edition": e.edition,
                              "published_at": e.published_at, "first_seen_at": e.first_seen_at,
                              "reason": "same_edition_conflict" if conflict else "other_edition" if not same_edition else "lower_authority"})
    return Resolution(
        status=status, indicator_id=indicator_id, reference_period=reference_period, stage=stage,
        value=value, unit=indicator["unit"], scale=indicator["scale"], transform=indicator["transform"],
        edition=selected.edition, published_at=selected.published_at, first_seen_at=selected.first_seen_at,
        source={"source_id": selected.source_id, "issuer_id": selected.issuer_id,
                "distributor_id": selected.distributor_id, "evidence_role": selected.role,
                "selected_evidence_id": selected.id},
        evidence_ids=tuple(e.id for e in sorted(all_rows, key=_tie)),
        discrepancies=tuple(discrepancies), provenance=tuple(e.provenance() for e in sorted(all_rows, key=_tie)),
        reason=reason,
    )


def resolve(store, indicator_id, reference_period, stage="regular", *, as_known_at=None, release_event_id=None):
    """Return Resolution or None; ReadAPI.explain_empty diagnoses absence."""
    return resolve_evidence(Snapshot.load(store, as_known_at), indicator_id, reference_period,
                            stage, event_id=release_event_id)


def subset(snapshot, evidence):
    return replace(snapshot, evidence=list(evidence))
