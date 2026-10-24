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

from dataclasses import asdict, dataclass, field, replace
from functools import cached_property
import json
import math
from typing import Optional

from .models import utc
from .boj_timing import TIME_FIELDS
from .read_times import known_by, normalize, stored_rows, timestamp

RULE_VERSION = "macro-resolver:m6-v1"
END_OF_TIME = "9999-12-31T23:59:59.999999+00:00"
COLLECT_LOG_LIMITATION = (
    "Daily collector family outcomes are persisted from schema v4; older/manual "
    "M5 run failures are not persisted. A logged failure establishes an attempted "
    "fetch failure, not that a release was due. Imported failed legacy runs retain "
    "only their original start time."
)


def policy_payload(row, vintage):
    """Old stores and arbitrary raw readings need not contain policy metadata."""
    if vintage:
        return json.loads(row["policy_decision_json"]) if row.get("policy_decision_json") else None
    try:
        payload = json.loads(row["raw_payload_json"])
    except (ValueError, TypeError):
        return None
    return payload.get("policy_decision") if isinstance(payload, dict) else None


def canonical_unit(unit):
    return {"percent": "%", "percentage": "%", "pct": "%"}.get(unit, unit)


def convert(value, unit, scale, target):
    """Convert only explicit scale and unit aliases; never infer a transform."""
    if (
        unit is None
        or scale is None
        or canonical_unit(unit) != canonical_unit(target["unit"])
    ):
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
    ordering_at: Optional[str] = None
    timestamp_precisions: dict = field(default_factory=dict)
    issuer_timezone: Optional[str] = None
    policy_decision: Optional[dict] = None

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
    policy_decision: Optional[dict] = None

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
    collect_families: list = field(default_factory=list)
    limitations: list = field(default_factory=list)

    @cached_property
    def evidence_by_event(self):
        result = {}
        for evidence in self.evidence:
            result.setdefault(evidence.event_id, []).append(evidence)
        return result

    @cached_property
    def families_by_indicator(self):
        result = {}
        for evidence in self.evidence:
            if evidence.event_id in self.events:
                result.setdefault(evidence.indicator_id, set()).add(
                    self.events[evidence.event_id]["family"]
                )
        return result

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
            indicators = {
                r["id"]: dict(r) for r in conn.execute("SELECT * FROM indicators")
            }
            events = {
                r["id"]: dict(r) for r in conn.execute("SELECT * FROM release_events")
            }
            series = [dict(r) for r in conn.execute("SELECT * FROM source_series")]
            vintages = [dict(r) for r in conn.execute("SELECT * FROM value_vintages")]
            readings = [dict(r) for r in conn.execute("SELECT * FROM readings")]
            links = [
                dict(r) for r in conn.execute("SELECT * FROM legacy_reading_links")
            ]
            schedules = [
                dict(r) for r in conn.execute("SELECT * FROM schedule_versions")
            ]
            consensus = [
                dict(r) for r in conn.execute("SELECT * FROM consensus_readings")
            ]
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            boj_times = ([] if "boj_publication_times" not in tables else
                [dict(r) for r in conn.execute("SELECT * FROM boj_publication_times")])
            link_versions = (
                []
                if "consensus_link_versions" not in tables
                else [
                    dict(r)
                    for r in conn.execute(
                        "SELECT v.*,r.provider,r.captured_at,r.content_hash FROM consensus_link_versions v "
                        "JOIN consensus_readings r ON r.id=v.consensus_reading_id ORDER BY v.id"
                    )
                ]
            )
            worklist = (
                []
                if "worklist_reverts" not in tables
                else [
                    dict(r)
                    for r in conn.execute(
                        "SELECT * FROM (SELECT w.*,ROW_NUMBER() OVER(PARTITION BY source_id,occurrence_key "
                        "ORDER BY audit_id DESC) AS rank FROM worklist_resolutions w WHERE resolved_at<=? "
                        "AND NOT EXISTS(SELECT 1 FROM worklist_reverts r WHERE r.run_id=w.run_id AND r.reverted_at<=?)) "
                        "WHERE rank=1 AND status='complete' AND selected_vintage_id IS NULL",
                        (cutoff, cutoff),
                    )
                ]
            )
            collect_families = (
                []
                if "collect_run_families" not in tables
                else [
                    dict(r)
                    for r in conn.execute(
                        "SELECT f.*,r.started_at FROM collect_run_families f JOIN collect_runs r "
                        "ON r.run_id=f.run_id AND r.status='running' "
                        "ORDER BY f.finished_at DESC,f.rowid DESC"
                    )
                ]
            )
        finally:
            if owns_transaction:
                conn.execute("ROLLBACK")  # finish read transaction; never mutate
        by_id = {s["id"]: s for s in series}
        by_key = {
            (s["indicator_id"], s["source_id"], s["source_key"]): s for s in series
        }
        limitations = []
        # Current link corrections retire every other association for the same
        # raw observation. Before a correction was recorded, conservatively hide
        # that observation rather than revive a known incorrect historical link.
        current_links = {
            (r["provider"], r["captured_at"], r["content_hash"]): r
            for r in link_versions
        }
        live_ids = set()
        for link in current_links.values():
            try:
                link = normalize(link, {"recorded_at": None})
                if known_by(link, "recorded_at", cutoff):
                    # A retired latest link hides the observation entirely.
                    if not link.get("retired"):
                        live_ids.add(link["consensus_reading_id"])
                else:
                    limitations.append(
                        "Consensus observation omitted: issuer link corrected after query cutoff."
                    )
            except (ValueError, TypeError, KeyError, OverflowError):
                limitations.append(
                    "Consensus observation omitted: invalid link correction timestamp."
                )
        consensus = [
            r
            for r in consensus
            if (r["provider"], r["captured_at"], r["content_hash"]) not in current_links
            or r["id"] in live_ids
        ]

        def metadata(rows, fields, known_at, table):
            return stored_rows(
                rows,
                fields,
                known_at=known_at,
                cutoff=cutoff,
                limitations=limitations,
                table=table,
            )

        links = {
            r["reading_id"]: r
            for r in metadata(
                links, {"updated_at": None}, "updated_at", "legacy_reading_links"
            )
        }
        worklist = {
            (r["source_id"], r["occurrence_key"]): r
            for r in metadata(
                worklist, {"resolved_at": None}, "resolved_at", "worklist_resolutions"
            )
        }
        schedules = metadata(
            schedules,
            {"expected_at": None, "recorded_at": None},
            "recorded_at",
            "schedule_versions",
        )
        schedules.sort(key=lambda r: (r["recorded_at"], r["id"]))
        consensus = metadata(
            consensus,
            {"captured_at": None, "published_at": None, "release_cutoff_at": None},
            "captured_at",
            "consensus_readings",
        )
        collect_families = metadata(
            collect_families,
            {"finished_at": None, "started_at": None},
            "finished_at",
            "collect_run_families",
        )
        collect_families.sort(key=lambda r: r["finished_at"], reverse=True)
        evidence = []
        boj_times = {r["value_vintage_id"]: r for r in metadata(
            boj_times, {"recorded_at": None, "issuer_published_at": None},
            "recorded_at", "boj_publication_times")}
        for row in vintages:
            try:
                timing = boj_times.get(row["id"])
                if timing and row["indicator_id"] == "boj_policy_rate_decision":
                    row = dict(row, **{k: timing[k] for k in TIME_FIELDS})
                row = normalize(
                    row, {"first_seen_at": None, "issuer_published_at": None}
                )
                if not known_by(row, "first_seen_at", cutoff):
                    continue
                source = by_id[row["source_series_id"]]
                if not source["is_published"] or row["formula_version"]:
                    continue
                evidence.append(
                    cls._evidence(row, source, indicators, events, vintage=True)
                )
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                limitations.append(
                    f"Skipped vintage:{row['id']}: invalid stored evidence ({exc})."
                )
        for row in readings:
            try:
                row = normalize(row, {"first_seen_at": None, "published_at": None})
                if not known_by(row, "first_seen_at", cutoff):
                    continue
                link = links.get(row["id"])
                legacy = None
                if row["parser_version"].startswith("legacy-calendar-"):
                    # Do not apply today's mutable mapping to a historical snapshot.
                    if not link:
                        continue
                    payload = json.loads(row["raw_payload_json"])
                    if not isinstance(payload, dict) or not isinstance(
                        payload.get("row"), dict
                    ):
                        raise ValueError("invalid legacy envelope")
                    key = (
                        f"sighting:{link['legacy_row_id']}"
                        if link["legacy_table"] == "sightings"
                        else link["occurrence_key"]
                    )
                    mapping = worklist.get((link["source_id"], key))
                    if (
                        mapping is None
                        and link["occurrence_key"]
                        and link["legacy_run_status"] == "published"
                    ):
                        mapping = worklist.get(
                            (link["source_id"], link["occurrence_key"])
                        )
                    if mapping and link["reading_kind"] != "previous":
                        from .worklist_periods import explicit_period, period_evidence

                        indicator = indicators[mapping["indicator_id"]]
                        raw_period = row["reference_period"] or payload["row"].get(
                            "raw_period"
                        )
                        if mapping["rule_version"] == "legacy-worklist-v3":
                            parsed, _ = period_evidence(
                                raw_period,
                                indicator["frequency"],
                                indicator["geography"],
                                indicator["definition"] or "",
                                measure=indicator["measure"],
                            )
                        else:
                            parsed = explicit_period(
                                raw_period,
                                indicator["frequency"],
                                indicator["geography"],
                            )
                        if parsed and parsed != mapping["reference_period"]:
                            limitations.append(
                                f"Skipped worklist overlay for reading:{row['id']}: conflicting reference period"
                            )
                            mapping = None
                    if mapping:
                        link = dict(
                            link,
                            indicator_id=mapping["indicator_id"],
                            estimate_stage=(
                                "unknown"
                                if link["reading_kind"] == "previous"
                                else mapping["estimate_stage"]
                            ),
                            worklist_resolution=mapping,
                        )
                        if link["reading_kind"] != "previous":
                            row["reference_period"] = mapping["reference_period"]
                    if not link["indicator_id"]:
                        continue
                    row["indicator_id"] = link["indicator_id"]
                    row["estimate_stage"] = link["estimate_stage"]
                    legacy = dict(payload, link=link)
                if row["indicator_id"] not in indicators:
                    continue
                source = by_key.get(
                    (row["indicator_id"], row["source_id"], row["series_key"])
                )
                if source and not source["is_published"]:
                    continue
                evidence.append(
                    cls._evidence(row, source, indicators, events, legacy=legacy)
                )
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                limitations.append(
                    f"Skipped reading:{row['id']}: invalid stored evidence ({exc})."
                )
        evidence = [
            e
            for e in evidence
            if e is not None and (e.published_at is None or e.published_at <= cutoff)
        ]
        return cls(
            indicators,
            events,
            schedules,
            evidence,
            consensus,
            cutoff,
            collect_families,
            limitations,
        )

    @staticmethod
    def _evidence(row, source, indicators, events, *, vintage=False, legacy=None):
        if row["verification_status"] == "rejected":
            return None
        indicator = indicators[row["indicator_id"]]
        event = events.get(row["release_event_id"], {})
        issuer = source["issuer_id"] if source else row["issuer_id"]
        role = source["evidence_role"] if source else "web"
        if (
            issuer != indicator["issuer_id"]
            or event.get("issuer_id", issuer) != indicator["issuer_id"]
        ):
            role = "web"
        # Preserve M5's exclusion of known unverified tool-rendered archives.
        if (
            source
            and source["source_id"] == "BLS"
            and source["source_key"] == "CES0000000001:archive-change"
        ):
            role = "web"
        published = row["issuer_published_at"] if vintage else row["published_at"]
        event_id, event_time, time_basis = (
            row["release_event_id"],
            published,
            "issuer_publication",
        )
        edition = event.get("edition", row.get("edition", ""))
        if not vintage and event and row["edition"] != edition:
            # An inconsistent event link is evidence, not permission to rewrite
            # the observation's edition or assert issuer authority for that event.
            edition, event_id, role = row["edition"], None, "web"
        if role != "issuer":
            time_basis = "source_publication"
        precision = row["publication_precision"]
        published_field = "issuer_published_at" if vintage else "published_at"
        precisions = dict(row["timestamp_precisions"])
        if precisions.get(published_field, "exact") != "exact":
            precision = precisions[published_field]
        ordering_at = None
        if legacy:
            raw, link = legacy["row"], legacy["link"]
            event_id = f"legacy:{row['source_id']}:{link['occurrence_key'] or row['series_key']}:{row['estimate_stage']}"
            event_time = raw.get("release_at_utc") or raw.get("scheduled_at_utc")
            # LazyCommittee calendar/prompts.toml specifies UTC YYYY-MM-DDTHH:MM
            # and YYYY-MM-DD. merge.py copies release_at_utc to scheduled_at_utc.
            event_time, precision = timestamp(
                event_time or raw["release_date"], assumed_timezone="UTC"
            )
            precisions["legacy_event_time"] = precision
            run_time, run_precision = timestamp(
                legacy["legacy_first_seen_at"], assumed_timezone="UTC"
            )
            ordering_at, order_precision = timestamp(
                raw.get("observed_at") or legacy["legacy_first_seen_at"],
                assumed_timezone="UTC",
            )
            legacy = dict(legacy, normalized_run_started_at=run_time)
            precisions.update(
                legacy_run_started_at=run_precision,
                legacy_publication_order=order_precision,
            )
            time_basis = "legacy_calendar"
        return Evidence(
            id=f"{'vintage' if vintage else 'reading'}:{row['id']}",
            indicator_id=row["indicator_id"],
            reference_period=row["reference_period"],
            stage=row["estimate_stage"],
            event_id=event_id,
            edition=edition,
            value=row["value"],
            unit=row["unit"],
            scale=row["scale"],
            published_at=published,
            first_seen_at=row["first_seen_at"],
            source_id=source["source_id"] if source else row["source_id"],
            series_key=source["source_key"] if source else row["series_key"],
            issuer_id=issuer,
            distributor_id=source["distributor_id"]
            if source
            else row["distributor_id"],
            role=role,
            verified=row["verification_status"] == "verified",
            method=row["extraction_method"],
            content_hash=row["document_hash"] if vintage else row["content_hash"],
            precision=precision,
            composition_id=row["composition_id"],
            url=None if vintage else row["url"],
            event_time=event_time,
            time_basis=time_basis,
            revision_kind=row.get("revision_kind"),
            reading_kind=row.get("reading_kind", "actual"),
            legacy=legacy,
            ordering_at=ordering_at,
            timestamp_precisions=precisions,
            issuer_timezone=row.get("issuer_timezone"),
            policy_decision=policy_payload(row, vintage),
        )


def compatible(evidence, indicator):
    return (
        evidence.value is not None
        and evidence.reference_period is not None
        and evidence.reading_kind in {"actual", "legacy_merged_actual"}
        and (
            evidence.composition_id is None
            or evidence.composition_id == indicator["geography"]
        )
        and convert(evidence.value, evidence.unit, evidence.scale, indicator)
        is not None
    )


def _tier(e):
    return (
        0
        if e.official
        else 1
        if e.role == "issuer"
        else 2
        if e.role == "mirror" and structured(e.method)
        else 3
    )


def _tie(e):
    kind, row_id = e.id.split(":")
    return (
        e.edition,
        e.source_id,
        e.series_key,
        e.content_hash,
        kind != "vintage",
        int(row_id),
    )


def publication_order(e):
    if e.published_at:
        return e.published_at
    return e.ordering_at or ""


def resolve_evidence(snapshot, indicator_id, reference_period, stage, *, event_id=None):
    indicator = snapshot.indicators.get(indicator_id)
    if indicator is None:
        return None
    all_rows = [
        e
        for e in snapshot.evidence
        if e.indicator_id == indicator_id
        and e.reference_period == reference_period
        and e.stage == stage
        and compatible(e, indicator)
    ]
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
        edition_leader = min(
            (e for e in issuers if publication_order(e) == newest_issuer),
            key=lambda e: (_tier(e), _tie(e)),
        )
        pool = [e for e in issuers if e.edition == edition_leader.edition]
    else:
        pool = rows
    tier = min(_tier(e) for e in pool)
    pool = [e for e in pool if _tier(e) == tier]
    newest = max(publication_order(e) for e in pool)
    selected = min((e for e in pool if publication_order(e) == newest), key=_tie)
    pending = [
        e
        for e in rows
        if e.role == "mirror"
        and e.edition != selected.edition
        and e.published_at
        and (not selected.published_at or e.published_at > selected.published_at)
    ]
    status = "official" if selected.official else "provisional"
    reason = (
        "verified issuer publication"
        if selected.official
        else "only unverified or non-issuer evidence"
    )
    if pending and selected.role == "issuer":
        newest = max(e.published_at for e in pending)
        selected = min((e for e in pending if e.published_at == newest), key=_tie)
        status, reason = (
            "issuer_pending",
            "issuer not yet captured for the newer mirror edition",
        )
    value = convert(selected.value, selected.unit, selected.scale, indicator)
    discrepancies = []
    for e in sorted(rows, key=_tie):
        other = convert(e.value, e.unit, e.scale, indicator)
        if not differs(value, other):
            continue
        same_edition = e.edition == selected.edition
        conflict = same_edition and (
            (selected.official and e.official)
            or (
                selected.role == "issuer"
                and not selected.verified
                and any(
                    token in selected.method.lower()
                    for token in ("llm", "html", "archive_text")
                )
                and structured(e.method)
            )
        )
        if conflict and status != "issuer_pending":
            status, reason = (
                "disputed",
                "compatible evidence conflicts at equal authority or with an unverified issuer extraction",
            )
        discrepancies.append(
            {
                "evidence_id": e.id,
                "value": other,
                "unit": indicator["unit"],
                "scale": indicator["scale"],
                "source": e.source_id,
                "edition": e.edition,
                "published_at": e.published_at,
                "first_seen_at": e.first_seen_at,
                "reason": "same_edition_conflict"
                if conflict
                else "other_edition"
                if not same_edition
                else "lower_authority",
            }
        )
    return Resolution(
        status=status,
        indicator_id=indicator_id,
        reference_period=reference_period,
        stage=stage,
        value=value,
        unit=indicator["unit"],
        scale=indicator["scale"],
        transform=indicator["transform"],
        edition=selected.edition,
        published_at=selected.published_at,
        first_seen_at=selected.first_seen_at,
        source={
            "source_id": selected.source_id,
            "issuer_id": selected.issuer_id,
            "distributor_id": selected.distributor_id,
            "evidence_role": selected.role,
            "selected_evidence_id": selected.id,
        },
        evidence_ids=tuple(e.id for e in sorted(all_rows, key=_tie)),
        discrepancies=tuple(discrepancies),
        provenance=tuple(e.provenance() for e in sorted(all_rows, key=_tie)),
        reason=reason,
        policy_decision=selected.policy_decision,
    )


def resolve(
    store,
    indicator_id,
    reference_period,
    stage="regular",
    *,
    as_known_at=None,
    release_event_id=None,
):
    """Return Resolution or None; ReadAPI.explain_empty diagnoses absence."""
    return resolve_evidence(
        Snapshot.load(store, as_known_at),
        indicator_id,
        reference_period,
        stage,
        event_id=release_event_id,
    )


def subset(snapshot, evidence):
    return replace(snapshot, evidence=list(evidence))
