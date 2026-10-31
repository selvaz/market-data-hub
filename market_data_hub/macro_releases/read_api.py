"""Consumer read API for the macro store. Shadow mode; no writes or downloads."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import re
from zoneinfo import ZoneInfo

from .catalogue import FAMILIES, INDICATORS, US_MONTHLY, EMPLOYMENT_FAMILIES, UK_FAMILIES, REMAINING_FAMILIES, CENTRAL_BANKS, HOUSING_FAMILIES, ASIA_FAMILIES, LATAM_FAMILIES
from .models import utc
from .read_times import stored_rows
from .source_classes import source_class
from .resolve import (
    COLLECT_LOG_LIMITATION,
    Snapshot,
    convert,
    differs,
    publication_order,
    resolve_evidence,
    subset,
)

FAMILY_INDICATORS = {
    "us_cpi": {i for i in INDICATORS if i.startswith("us_cpi_")},
    "us_payrolls": {"us_payrolls_change"},
    "us_gdp": {"us_gdp_qq_saar"},
    "ea_hicp": {i for i in INDICATORS if "hicp" in i and "core" not in i},
    "uk_gdp": {"uk_gdp_mm", "uk_gdp_index"},
}
FAMILY_INDICATORS.update({f: {i.id for i in measures} for f, measures in {**US_MONTHLY, **EMPLOYMENT_FAMILIES, **UK_FAMILIES}.items()})
FAMILY_INDICATORS["ea_gdp"] = {"ea21_gdp_qq", "ea21_gdp_yy"}
FAMILY_INDICATORS["us_jobless_claims"] = {"us_initial_jobless_claims"}
FAMILY_INDICATORS.update({f: {i.id for i in measures} for f, measures in REMAINING_FAMILIES.items()})
FAMILY_INDICATORS.update({f: {i.id for i in measures} for f, measures in CENTRAL_BANKS.items()})
FAMILY_INDICATORS.update({f: {i.id for i in measures} for f, measures in HOUSING_FAMILIES.items()})
FAMILY_INDICATORS.update({f: {i.id for i in measures} for f, measures in ASIA_FAMILIES.items()})
FAMILY_INDICATORS.update({f: {i.id for i in measures} for f, measures in LATAM_FAMILIES.items()})


def indicators_for_stage(family, stage):
    """Only measures actually published by this family's edition stage."""
    if family in ASIA_FAMILIES:
        from .asia_editions import EDITIONS
        return FAMILY_INDICATORS[family] if stage in {m[1] for m in EDITIONS[family].values()} else set()
    if family in HOUSING_FAMILIES:
        from .housing_editions import EDITIONS
        stages = {metadata[1] for metadata in EDITIONS[family].values()}
        return FAMILY_INDICATORS[family] if stage in stages else set()
    if (family, stage) == ("ea_ec_surveys", "flash"):
        return {"ea_consumer_confidence"}
    if (family, stage) == ("us_durable_goods", "final"):
        return {"us_durable_goods_orders_mm"}
    return FAMILY_INDICATORS[family]


# Explicit comparable composition successors; never infer succession merely
# from a shared issuer or family. Three missing monthly periods is the gate.
SUCCESSORS = {f"ea_hicp_{basis}": f"ea21_hicp_{basis}" for basis in ("mm", "yy")}
STALE_MONTHS = 3


def malformed_edition(evidence):
    return evidence.indicator_id == "uk_gdp_index" and evidence.edition.startswith(
        "time-series:"
    )


def stale_series(snapshot, indicator, period, released_at):
    successor = SUCCESSORS.get(indicator)
    if not successor or not re.fullmatch(r"\d{4}-\d{2}", period):
        return None
    available = [
        e.reference_period
        for e in snapshot.evidence
        if e.indicator_id == successor
        and e.verified_issuer
        and e.value is not None
        and e.published_at
        and e.published_at <= released_at
        and re.fullmatch(r"\d{4}-\d{2}", e.reference_period or "")
    ]
    if not available:
        return None
    head = max(available)
    year, month = map(int, period.split("-"))
    release_year, release_month = map(int, released_at[:7].split("-"))
    if (
        head > period
        and (release_year - year) * 12 + release_month - month >= STALE_MONTHS
    ):
        return {
            "reason": "discontinued_series",
            "successor": successor,
            "last_reference_period": period,
            "successor_reference_period": head,
            "stale_months": STALE_MONTHS,
            "detail": "series has no new monthly period while its explicit composition successor has advanced",
        }
    return None


def window(since, until):
    """Half-open UTC bounds. A date-only until includes that entire UTC date."""
    start = utc(since + "T00:00:00Z") if len(since) == 10 else utc(since)
    end = (
        utc((date.fromisoformat(until) + timedelta(days=1)).isoformat() + "T00:00:00Z")
        if len(until) == 10
        else utc(until)
    )
    if start >= end:
        raise ValueError("since must precede until")
    return start, end


def prior_period(period, frequency=None, *, indicator=None):
    if indicator == "us_pmms_30y":
        from .housing_editions import EDITIONS
        # Holiday survey weeks may end six or eight days apart. Preserve the
        # issuer calendar rather than inventing a seven-day comparison date.
        return max((p for p, _, _ in EDITIONS["us_pmms"].values() if p < period), default=None)
    if re.fullmatch(r"\d{4}-\d{2}", period):
        first = date.fromisoformat(period + "-01")
        return (first - timedelta(days=1)).strftime("%Y-%m")
    match = re.fullmatch(r"(\d{4})-Q([1-4])", period)
    if match:
        year, quarter = map(int, match.groups())
        return (
            f"{year if quarter > 1 else year - 1}-Q{quarter - 1 if quarter > 1 else 4}"
        )
    if re.fullmatch(r"\d{4}", period):
        return str(int(period) - 1)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", period):
        return (date.fromisoformat(period) - timedelta(days=7 if frequency == "W" else 1)).isoformat()
    return None


def period_end(period):
    if not isinstance(period, str):
        raise ValueError("period must be a string or a two-bound window")
    if re.fullmatch(r"\d{4}-\d{2}", period):
        year, month = map(int, period.split("-"))
        last = date(year, month, monthrange(year, month)[1])
        return last.isoformat() + "T23:59:59.999999+00:00"
    match = re.fullmatch(r"(\d{4})-Q([1-4])", period)
    if match:
        year, quarter = map(int, match.groups())
        return period_end(f"{year}-{quarter * 3:02}")
    if re.fullmatch(r"\d{4}", period):
        return date(int(period), 12, 31).isoformat() + "T23:59:59.999999+00:00"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", period):
        return window(period, period)[1]
    raise ValueError("period must be YYYY, YYYY-MM, YYYY-Q1..Q4, or YYYY-MM-DD")


def family_for(snapshot, indicator):
    for family, members in FAMILY_INDICATORS.items():
        if indicator in members:
            return family
    families = sorted(snapshot.families_by_indicator.get(indicator, ()))
    if len(families) == 1:
        return families[0]
    info = snapshot.indicators[indicator]
    return f"{info['issuer_id']}:{info['measure']}"


def collection_failure(snapshot, family, start, end):
    """Latest covering outcome wins; successful later fetches clear old failures.

    Fetch windows describe reference months, not a complete publication calendar.
    Report the attempted fetch time explicitly, never as a publication time.
    """
    for row in snapshot.collect_families:
        if row["family"] != family:
            continue
        fetch_start, fetch_end = row["fetch_start"], row["fetch_end"]
        if start > row["finished_at"] or end <= fetch_start or start > fetch_end:
            continue
        if row["status"] != "failed":
            return None
        return {
            "reason": "source_failed",
            "family": family,
            "run_id": row["run_id"],
            "time": row["finished_at"],
            "time_basis": "collect_family_finished_at",
            "time_precision": row["timestamp_precisions"]["finished_at"],
            "started_at": row["started_at"],
            "error_type": row["error_type"],
            "detail": row["error_message"],
            "fetch_since": row["fetch_since"],
            "fetch_until": row["fetch_until"],
        }
    return None


def consensus_provider(provider):
    """Provider policy stays attached to each reading through card assembly."""
    if provider.lower().replace(" ", "_") in {
        "ff",
        "forex_factory",
        "forexfactory",
        "ff_forecast",
    }:
        return {"kind": "ff_forecast", "surprise_eligible": False}
    return {"kind": "survey_consensus", "surprise_eligible": True}


def latest_schedule(snapshot):
    return {r["release_event_id"]: r for r in snapshot.schedules}


def event_timing(snapshot, event_id, selected=None):
    """Keep the event clock separate from the selected correction's timestamp.

    First exact issuer publication wins across the entire event/edition. With
    no issuer publication, use the saved schedule before mirror/web timestamps.
    Date-only issuer dates remain date-only; consensus retains its saved local
    midnight lower bound. No retrieval time is promoted to a release instant.
    """
    rows = snapshot.evidence_by_event.get(event_id, []) if event_id is not None else []
    issuers = [e for e in rows if e.role == "issuer" and e.published_at]
    if issuers:
        exact = [e for e in issuers if e.precision == "exact"]
        first = min(exact or issuers, key=lambda e: (e.published_at, e.id))
        return first.published_at, first.precision, "issuer_publication"
    schedule = latest_schedule(snapshot).get(event_id)
    if schedule:
        return schedule["expected_at"], schedule["precision"], "schedule"
    if selected is None and rows:
        selected = min(rows, key=lambda e: (publication_order(e), e.id))
    if selected:
        return selected.event_time, selected.precision, selected.time_basis
    return None, None, None


def event_order(snapshot, evidence):
    return event_timing(snapshot, evidence.event_id, evidence)[0] or publication_order(
        evidence
    )


def event_timezone(snapshot, event_id, selected, time_basis):
    """Use stored issuer/schedule zones; an unknown zone cannot certify a date."""
    schedule = latest_schedule(snapshot).get(event_id)
    zones = {
        e.issuer_timezone
        for e in snapshot.evidence_by_event.get(event_id, [])
        if e.role == "issuer" and e.issuer_timezone
    }
    if time_basis == "schedule" and schedule:
        name = schedule["issuer_timezone"]
    elif len(zones) == 1:
        name = next(iter(zones))
    elif zones:
        name = None
    else:
        name = (
            schedule["issuer_timezone"]
            if schedule
            else selected.issuer_timezone
            if selected
            else None
        )
    try:
        return ZoneInfo(name) if name else None
    except (KeyError, ValueError, TypeError):
        return None


def latest_period(snapshot, indicator, period):
    """Resolve each stage separately, then choose the latest publication stage."""
    candidates = [
        e
        for e in snapshot.evidence
        if e.indicator_id == indicator and e.reference_period == period
    ]
    stages = sorted({e.stage for e in candidates})
    resolved = [resolve_evidence(snapshot, indicator, period, s) for s in stages]
    resolved = [r for r in resolved if r is not None]
    if not resolved:
        return None
    by_id = {e.id: e for e in candidates}

    def order(resolution):
        selected = by_id[resolution.source["selected_evidence_id"]]
        return (
            event_order(snapshot, selected),
            publication_order(selected),
            resolution.first_seen_at,
            resolution.stage,
        )

    return max(resolved, key=order).to_dict()


class ReadAPI:
    def __init__(self, store):
        self.store = store

    def release_documents(
        self, release_event_id=None, *, indicator=None, reference_period=None,
        as_known_at=None, max_chars=20000,
    ):
        """Saved issuer bodies linked by content hash; never fetch a URL.

        Supply an event id OR an indicator and reference period. The latter
        includes all captured estimate stages/editions for that period.
        max_chars is a shared text budget across documents, oldest retrieval
        first. Content types are inferred: the collector stores no HTTP headers.
        """
        if (release_event_id is not None) == (indicator is not None or reference_period is not None):
            raise ValueError("supply release_event_id OR indicator and reference_period")
        if release_event_id is None and (indicator is None or reference_period is None):
            raise ValueError("indicator and reference_period are both required")
        if type(max_chars) is not int or max_chars < 1:
            raise ValueError("max_chars must be a positive integer")
        cutoff = utc(as_known_at or datetime.now(timezone.utc).isoformat())
        conn = self.store.conn
        owns_transaction = not conn.in_transaction
        if owns_transaction:
            conn.execute("BEGIN")
        try:
            snapshot = Snapshot.load(self.store, cutoff)
            hashes = {
                e.content_hash for e in snapshot.evidence
                if e.role == "issuer" and e.source_class != "redistributor"
                and (e.event_id == release_event_id if release_event_id is not None else
                     e.indicator_id == indicator and e.reference_period == reference_period)
            }
            rows = []
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='documents' AND type='table'").fetchone():
                for digest in sorted(hashes):
                    row = conn.execute("SELECT * FROM documents WHERE document_hash=?", (digest,)).fetchone()
                    if row is not None:
                        rows.append(dict(row))
        finally:
            if owns_transaction:
                conn.execute("ROLLBACK")
        limitations = list(snapshot.limitations)
        rows = stored_rows(rows, {"retrieved_at": None}, known_at="retrieved_at",
                           cutoff=cutoff, limitations=limitations, table="documents")
        documents = []
        remaining = max_chars
        from .documents import extract_document_text
        for row in sorted(rows, key=lambda r: (r["retrieved_at"], r["document_hash"])):
            text, content_type, reason = extract_document_text(row)
            documents.append({
                "url": row["url"], "retrieved_at": row["retrieved_at"],
                "document_hash": row["document_hash"], "content_type": content_type,
                "content_type_basis": "inferred_from_saved_body",
                "text": text[:remaining], "text_chars": len(text),
                "truncated": len(text) > remaining, "text_missing_reason": reason,
                "timestamp_precisions": row["timestamp_precisions"],
            })
            remaining -= len(documents[-1]["text"])
        return {
            "release_event_id": release_event_id, "indicator_id": indicator,
            "reference_period": reference_period, "as_known_at": cutoff,
            "max_chars": max_chars, "documents": documents,
            "empty": [] if documents else [{
                "reason": "document_not_found",
                "detail": "no linked saved issuer document was available at as_known_at; later retrievals and mirror documents are excluded",
            }],
            "limitations": limitations,
        }

    def decision_history(self, indicator, *, as_known_at=None, limit=100):
        """Latest official vintage per decision, newest decision date first.

        Rates are percent levels (Fed scalar: upper bound); change_bp is the
        stored issuer decision change, never a difference across collected rows.
        Full rates preserve ranges and multi-rate decisions. No FRED mapping.
        """
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        cutoff = utc(as_known_at or datetime.now(timezone.utc).isoformat())
        snapshot = Snapshot.load(self.store, cutoff)
        result = {"indicator_id": indicator, "as_known_at": cutoff, "limit": limit,
                  "decisions": [], "empty": [], "has_more": False,
                  "limitations": [*snapshot.limitations,
                      "Stored decisions only; missing meetings or a truncated history cannot establish a complete hold streak or 12-month total."]}
        if indicator not in INDICATORS or not indicator.endswith("_decision"):
            result["empty"] = [{"reason": "not_covered", "detail": "indicator is not a supported policy decision"}]
            return result
        groups = {(e.event_id, e.reference_period, e.stage) for e in snapshot.evidence
                  if e.indicator_id == indicator and e.event_id and e.reference_period}
        decisions = []
        for event_id, period, stage in sorted(groups):
            resolution = resolve_evidence(snapshot, indicator, period, stage, event_id=event_id)
            if resolution is None or resolution.status != "official":
                continue
            facts = resolution.policy_decision
            if not facts or not facts.get("rates"):
                result["limitations"].append(f"Skipped {event_id}: stored policy facts are missing.")
                continue
            headline = next(iter(facts["rates"].values()))
            decisions.append({
                "release_event_id": event_id, "decision_date": facts["decision_date"],
                "rate": resolution.value, "unit": resolution.unit,
                "change_bp": headline.get("change_bp"), "rates": facts["rates"],
                "vote_split": facts.get("vote_split"),
                "published_at": resolution.published_at,
                "first_seen_at": resolution.first_seen_at,
                "policy_decision": facts, "resolution": resolution.to_dict(),
            })
        decisions.sort(key=lambda d: (d["decision_date"], d["published_at"] or "", d["release_event_id"]), reverse=True)
        result["decisions"] = decisions[:limit]
        result["has_more"] = len(decisions) > limit
        if not decisions:
            result["empty"] = [{"reason": "decision_history_not_found",
                                "detail": "no official resolved decision with stored policy facts was available at as_known_at"}]
        return result

    def release_cards(
        self, since, until, indicators=None, geography=None, as_known_at=None
    ):
        """Return {cards, empty, limitations}; each requested absence is explained.

        One card per (event, indicator). Revisions do not create extra cards.
        Date bounds include entire UTC dates; timestamp bounds are half-open.
        Actual issuer times drive the window, then schedules, then explicitly
        labelled legacy calendar dates. No missing time is invented.
        """
        start, end = window(since, until)
        cutoff = utc(as_known_at or datetime.now(timezone.utc).isoformat())
        snapshot = Snapshot.load(self.store, cutoff)
        return self._cards(snapshot, start, end, indicators, geography)

    def first_seen_release_cards(self, since, until, *, as_known_at=None):
        """Issuer actuals first captured in [since, until), known at the cutoff.

        Reuse publication-card resolution, previous values and forecast policy.
        Repeated fetches/corrections of an already captured headline do not make
        it a new release. Private issuers retain their explicit private status;
        redistributors, provisional and disputed readings are excluded.
        Collection issues are the latest saved outcome per family at the cutoff,
        independent of publication/fetch windows. No-change is a healthy outcome.
        """
        start, end = window(since, until)
        cutoff = utc(as_known_at or datetime.now(timezone.utc).isoformat())
        snapshot = Snapshot.load(self.store, cutoff)
        result = self._cards(snapshot, start, end, first_seen_window=True)
        latest = {}
        for row in snapshot.collect_families:
            latest.setdefault(row["family"], row)
        result["collection_issues"] = [
            latest[family] for family in sorted(latest)
            if latest[family]["status"] not in {"ok", "no_change"}
        ]
        result["window_basis"] = "first_official_seen_at"
        return result

    def _cards(self, snapshot, start, end, indicators=None, geography=None, *, first_seen_window=False):
        requested = (
            [indicators]
            if isinstance(indicators, str)
            else list(dict.fromkeys(indicators))
            if indicators is not None
            else sorted(set(INDICATORS) | set(snapshot.indicators))
        )
        # The snapshot is immutable during a query. Reuse chronology across
        # cards instead of resolving every historical event for every card.
        event_orders = {e.id: event_order(snapshot, e) for e in snapshot.evidence}
        heads = {}
        for e in snapshot.evidence:
            if (
                e.event_id
                and e.reference_period
                and e.verified_issuer
                and e.reading_kind == "actual"
                and not malformed_edition(e)
            ):
                key = (e.event_id, snapshot.indicators[e.indicator_id]["frequency"])
                heads[key] = max(heads.get(key, e.reference_period), e.reference_period)
        groups = {}
        for e in snapshot.evidence:
            if (
                e.indicator_id not in requested
                or not e.event_id
                or malformed_edition(e)
            ):
                continue
            info = snapshot.indicators[e.indicator_id]
            if geography and info["geography"] != geography:
                continue
            if e.reference_period is not None and e.reading_kind in {
                "actual",
                "unavailable",
                "legacy_merged_actual",
            }:
                groups.setdefault((e.event_id, e.indicator_id), []).append(e)
        cards = []
        for (event_id, indicator), evidence in sorted(groups.items()):
            # One headline period per indicator; older periods are revision rows.
            period = max(e.reference_period for e in evidence)
            # An edition republishes history, including earlier compositions.
            # Determine its head before applying indicator/geography filters.
            if period < heads.get(
                (event_id, snapshot.indicators[indicator]["frequency"]), period
            ):
                continue
            headline = [e for e in evidence if e.reference_period == period]
            stage = snapshot.events.get(event_id, {}).get("estimate_stage")
            stage = (
                stage
                if stage in {e.stage for e in headline}
                else sorted({e.stage for e in headline})[-1]
            )
            first_official_seen = min((
                e.first_seen_at for e in headline
                if e.verified_issuer and e.value is not None
                and e.reading_kind == "actual" and e.stage == stage
            ), default=None)
            if first_seen_window and (
                first_official_seen is None or not start <= first_official_seen < end
            ):
                continue
            resolution = resolve_evidence(
                snapshot, indicator, period, stage, event_id=event_id
            )
            if resolution is None:
                continue
            selected = next(
                e for e in headline if e.id == resolution.source["selected_evidence_id"]
            )
            if (
                selected.role == "mirror"
                and resolution.status == "provisional"
                and selected.published_at
            ):
                context = subset(
                    snapshot,
                    (
                        e
                        for e in snapshot.evidence
                        if e.published_at and e.published_at <= selected.published_at
                    ),
                )
                contextual = resolve_evidence(context, indicator, period, stage)
                if (
                    contextual
                    and contextual.source["selected_evidence_id"] == selected.id
                ):
                    resolution = contextual
            released_at, precision, time_basis = event_timing(
                snapshot, event_id, selected
            )
            window_time = first_official_seen if first_seen_window else released_at
            if (
                released_at is None
                or window_time is None
                or not start <= window_time < end
                or released_at > snapshot.cutoff
                or first_seen_window and resolution.status not in {"official", "private"}
            ):
                continue
            if stale_series(snapshot, indicator, period, released_at):
                continue
            frequency = snapshot.indicators[indicator]["frequency"]
            prior = prior_period(period, frequency, indicator=indicator) if frequency != "E" else None
            decisions = None
            missing_decision = None
            if frequency == "E":
                # Event periods are decision dates, not a daily calendar. Only
                # stored actuals of this indicator can establish a predecessor;
                # unavailable calendar memberships and other banks cannot.
                decisions = subset(snapshot, (
                    e for e in snapshot.evidence
                    if e.indicator_id == indicator and e.event_id != event_id
                    and e.reading_kind == "actual" and e.value is not None
                    and e.reference_period and e.reference_period < period
                    and event_order(snapshot, e) and event_order(snapshot, e) < released_at
                ))
                prior = max((e.reference_period for e in decisions.evidence), default=None)
                if prior is not None:
                    from .schedule import family_editions
                    family = snapshot.events.get(event_id, {}).get("family", family_for(snapshot, indicator))
                    # A reviewed meeting between two saved decisions is a gap,
                    # not permission to call the older decision the previous one.
                    missing_decision = max((p for p, _, _ in family_editions(family).values()
                        if prior < p < period and not any(
                            e.reference_period == p for e in decisions.evidence)), default=None)
                    if missing_decision is not None:
                        prior = missing_decision
            # The previous figure is strictly what we knew by release time.
            before = subset(
                snapshot,
                (
                    e
                    for e in snapshot.evidence
                    if e.event_id != event_id
                    and e.first_seen_at < released_at
                    and (e.published_at is None or e.published_at < released_at)
                ),
            )
            previous_before_release = latest_period(before, indicator, prior) if prior else None
            # A late archive capture can establish the preceding decision at the
            # query cutoff without backdating what we knew before this release.
            previous = (latest_period(decisions, indicator, prior) if prior else None
                        ) if decisions is not None else previous_before_release
            revised_previous = (
                latest_period(snapshot if frequency == "E" else subset(snapshot, evidence), indicator, prior)
                if prior
                else None
            )
            if (
                previous
                and revised_previous
                and not differs(previous["value"], revised_previous["value"])
                and previous["stage"] == revised_previous["stage"]
                and previous["status"] == revised_previous["status"]
            ):
                revised_previous = previous
            if revised_previous is None:
                revised_previous = previous
            # Revisions reconstruct editions available at the query's cutoff.
            # Archive captures after release can establish publication history,
            # but cannot backdate the previous-as-known-at-release field above.
            history = subset(
                snapshot,
                (
                    e
                    for e in snapshot.evidence
                    if e.event_id != event_id
                    and event_orders[e.id]
                    and event_orders[e.id] < released_at
                    and (indicator != "uk_gdp_index" or e.series_key == selected.series_key)
                ),
            )
            revisions = []
            for changed_period in sorted(
                {e.reference_period for e in evidence if e.reference_period < period}
            ):
                old = latest_period(history, indicator, changed_period)
                changed = latest_period(
                    subset(snapshot, evidence), indicator, changed_period
                )
                if old and changed and differs(old["value"], changed["value"]):
                    revisions.append(
                        {
                            "reference_period": changed_period,
                            "old": old,
                            "new": changed,
                            "comparison_basis": "publication_history_as_known_at_query",
                        }
                    )
            old_stage = latest_period(history, indicator, period)
            stage_change = None
            if old_stage and old_stage["stage"] != stage:
                stage_change = {
                    "old": old_stage,
                    "new": resolution.to_dict(),
                    "changed": differs(old_stage["value"], resolution.value),
                }
            consensus, diagnostics = self._consensus(
                snapshot, resolution, event_id, released_at, precision, time_basis
            )
            legacy_forecasts = [
                dict(
                    e.provenance(),
                    eligibility="unverified_provider_or_pre_release_capture",
                )
                for e in snapshot.evidence
                if e.event_id == event_id
                and e.indicator_id == indicator
                and e.reference_period == period
                and e.reading_kind in {"consensus", "legacy_merged_consensus"}
            ]
            surprise = (
                [
                    {
                        "provider": c["provider"],
                        "kind": c["kind"],
                        "value": c["surprise"],
                        "unit": c["surprise_unit"],
                        "scale": resolution.scale,
                        "resolution_status": resolution.status,
                        "issuer_pending": resolution.status == "issuer_pending",
                    }
                    for c in consensus
                    if c["surprise_eligible"]
                ]
                if consensus and resolution.status != "disputed"
                else None
            )
            cards.append(
                {
                    "release_event_id": event_id,
                    "family": snapshot.events.get(event_id, {}).get(
                        "family", family_for(snapshot, indicator)
                    ),
                    "indicator_id": indicator,
                    "indicator_name": snapshot.indicators[indicator]["measure"],
                    "geography": snapshot.indicators[indicator]["geography"],
                    "reference_period": period,
                    "stage": stage,
                    "released_at": released_at,
                    **({"first_official_seen_at": first_official_seen} if first_seen_window else {}),
                    "time_basis": time_basis,
                    "publication_precision": precision,
                    "resolution": resolution.to_dict(),
                    "issuer_id": resolution.issuer_id,
                    "source_class": resolution.source_class,
                    "policy_decision": resolution.policy_decision,
                    "previous": previous,
                    "previous_before_release": previous_before_release,
                    "previous_revised": revised_previous,
                    "previous_period": prior,
                    "previous_missing_reason": None
                    if previous
                    else (f"reviewed decision not collected: {missing_decision}" if missing_decision else
                          "no preceding stored decision" if frequency == "E" else
                          "prior period not known at release time"),
                    "revisions": revisions,
                    "stage_change": stage_change,
                    "consensus": consensus,
                    "consensus_diagnostics": diagnostics,
                    "surprise": surprise,
                    "legacy_consensus_evidence": legacy_forecasts,
                    "provenance": list(
                        {
                            e["id"]: e
                            for e in [
                                *(e.provenance() for e in evidence),
                                *resolution.provenance,
                            ]
                        }.values()
                    ),
                }
            )
        if not first_seen_window:
            cards.extend(
                self._scheduled_cards(
                    snapshot,
                    start,
                    end,
                    requested,
                    geography,
                    {(c["release_event_id"], c["indicator_id"]) for c in cards},
                )
            )
        cards.sort(
            key=lambda c: (c["released_at"], c["release_event_id"], c["indicator_id"])
        )
        empty = [
            self._explain(snapshot, i, (start, end), geography=geography)
            for i in (requested if not first_seen_window else [])
            if not any(c["indicator_id"] == i for c in cards)
        ]
        if not requested:
            empty.append(
                {
                    "indicator_id": None,
                    "query": (start, end),
                    "reason": "filtered_out",
                    "detail": "empty indicator selection",
                }
            )
        return {
            "since": start,
            "until": end,
            "as_known_at": snapshot.cutoff,
            "cards": cards,
            "empty": empty,
            "limitations": [COLLECT_LOG_LIMITATION, *snapshot.limitations],
        }

    def _scheduled_cards(
        self, snapshot, start, end, requested, geography, numeric_pairs
    ):
        """Forecast-only cards require stored issuer period membership and schedule.

        They never create a numeric resolution or count as captured in coverage.
        No FF forecast can establish a schedule or an official actual by itself.
        """
        schedules = latest_schedule(snapshot)
        groups = {}
        for row in snapshot.consensus:
            pair = row["release_event_id"], row["indicator_id"]
            if row["indicator_id"] in requested and pair not in numeric_pairs:
                groups.setdefault(pair, []).append(row)
        cards = []
        for (event_id, indicator_id), rows in sorted(groups.items()):
            schedule = schedules.get(event_id)
            event = snapshot.events.get(event_id)
            info = snapshot.indicators[indicator_id]
            if (
                not schedule
                or not event
                or schedule["source_id"] != info["issuer_id"]
                or event["issuer_id"] != info["issuer_id"]
                or geography
                and info["geography"] != geography
            ):
                continue
            # A published issuer clock already establishes the release, even
            # when its numeric value is unavailable. A later saved schedule
            # must never revive a post-release forecast as a pending card.
            if event_timing(snapshot, event_id)[2] != "schedule":
                continue
            released_at, precision = schedule["expected_at"], schedule["precision"]
            if not start <= released_at < end:
                continue
            evidence = [
                e
                for e in snapshot.evidence_by_event.get(event_id, [])
                if e.indicator_id == indicator_id
                and e.role == "issuer"
                and not e.legacy
                and e.reading_kind in {"actual", "unavailable"}
            ]
            if not evidence or any(e.value is not None for e in evidence):
                continue
            periods = {e.reference_period for e in evidence if e.reference_period}
            if not periods:
                continue
            period, stage = max(periods), event["estimate_stage"]
            key = dict(
                indicator_id=indicator_id, reference_period=period, estimate_stage=stage
            )
            consensus, diagnostics = self._consensus(
                snapshot,
                None,
                event_id,
                released_at,
                precision,
                "schedule",
                pending=key,
            )
            if not consensus:
                continue
            cards.append(
                dict(
                    release_event_id=event_id,
                    family=event["family"],
                    indicator_id=indicator_id,
                    indicator_name=info["measure"],
                    geography=info["geography"],
                    reference_period=period,
                    stage=stage,
                    released_at=released_at,
                    time_basis="schedule",
                    publication_precision=precision,
                    status="scheduled",
                    resolution=None,
                    issuer_id=info["issuer_id"],
                    source_class=source_class(info["issuer_id"], info["issuer_id"]),
                    actual=None,
                    surprise=None,
                    actual_missing_reason="issuer_actual_not_available",
                    consensus=consensus,
                    consensus_diagnostics=diagnostics,
                    provenance=[e.provenance() for e in evidence],
                )
            )
        return cards

    @staticmethod
    def _consensus(
        snapshot,
        resolution,
        event_id,
        released_at,
        precision,
        time_basis,
        *,
        pending=None,
    ):
        indicator_id = (
            resolution.indicator_id if resolution else pending["indicator_id"]
        )
        period = (
            resolution.reference_period if resolution else pending["reference_period"]
        )
        stage = resolution.stage if resolution else pending["estimate_stage"]
        status = resolution.status if resolution else "scheduled"
        info = snapshot.indicators[indicator_id]
        per_provider = {}
        diagnostics = []
        selected = (
            next(
                e
                for e in snapshot.evidence
                if e.id == resolution.source["selected_evidence_id"]
            )
            if resolution
            else None
        )
        for row in snapshot.consensus:
            if (
                row["indicator_id"] != indicator_id
                or row["release_event_id"] != event_id
                or row["reference_period"] != period
                or row["estimate_stage"] != stage
                or row["measure"] != info["measure"]
            ):
                continue
            # The actual time reconciles old date-only/scheduled eligibility.
            # With a date-only publication retain the saved conservative cutoff.
            if precision == "exact" and time_basis in {
                "issuer_publication",
                "schedule",
            }:
                cutoff = released_at
            elif row["cutoff_precision"] == "date_only":
                cutoff = row["release_cutoff_at"]
            elif precision == "date_only":
                cutoff = released_at
            else:
                cutoff = row["release_cutoff_at"]
            if cutoff is None:
                diagnostics.append(
                    {
                        "consensus_id": row["id"],
                        "provider": row["provider"],
                        "reason": "unknown_release_cutoff",
                    }
                )
                continue
            date_fields = [
                field
                for field in ("captured_at", "published_at")
                if row["timestamp_precisions"].get(field) == "date_only"
            ]
            if date_fields:
                zone = event_timezone(snapshot, event_id, selected, time_basis)
                if zone is None:
                    diagnostics.append(
                        {
                            "consensus_id": row["id"],
                            "provider": row["provider"],
                            "reason": "unknown_issuer_timezone",
                            "fields": date_fields,
                        }
                    )
                    continue
                # A normalized date is a calendar label, not a UTC instant.
                # For date-only releases preserve #103's saved local-midnight
                # bound; without one, released_at itself is a date bucket.
                if precision == "date_only":
                    release_date = (
                        datetime.fromisoformat(cutoff).astimezone(zone).date()
                        if row["cutoff_precision"] == "date_only"
                        else date.fromisoformat(released_at[:10])
                    )
                else:
                    release_date = (
                        datetime.fromisoformat(released_at).astimezone(zone).date()
                    )
                uncertain = [
                    field
                    for field in date_fields
                    if date.fromisoformat(row[field][:10]) >= release_date
                ]
                if uncertain:
                    diagnostics.append(
                        {
                            "consensus_id": row["id"],
                            "provider": row["provider"],
                            "reason": "date_only_consensus_not_pre_release",
                            "fields": uncertain,
                            "release_date": release_date.isoformat(),
                            "issuer_timezone": str(zone),
                        }
                    )
                    continue
            if row["captured_at"] >= cutoff or (
                row["published_at"] and row["published_at"] >= cutoff
            ):
                continue
            key = row["provider"]
            if key not in per_provider or (
                row["captured_at"],
                row["content_hash"],
                row["id"],
            ) > (
                per_provider[key]["captured_at"],
                per_provider[key]["content_hash"],
                per_provider[key]["id"],
            ):
                per_provider[key] = dict(row, eligibility_cutoff_at=cutoff)
        out = []
        actual = (
            Decimal(str(selected.value))
            * Decimal(str(selected.scale))
            / Decimal(str(info["scale"]))
            if selected
            else None
        )
        for provider, row in sorted(per_provider.items()):
            properties = consensus_provider(provider)
            forecast = convert(row["value"], row["unit"], row["scale"], info)
            if forecast is None:
                diagnostics.append(
                    {
                        "consensus_id": row["id"],
                        "provider": provider,
                        "survey_identity": row["survey_identity"],
                        "unit": row["unit"],
                        "scale": row["scale"],
                        "value": row["value"],
                        "captured_at": row["captured_at"],
                        "eligibility_cutoff_at": row["eligibility_cutoff_at"],
                        "expected_unit": info["unit"],
                        "reason": "unit_not_convertible",
                    }
                )
                continue
            expected = (
                Decimal(str(row["value"]))
                * Decimal(str(row["scale"]))
                / Decimal(str(info["scale"]))
            )
            # Subtract decimal inputs at their explicit scale, avoiding binary
            # artifacts such as 3.7 - 3.6 == 0.10000000000000009.
            difference = (
                float(actual - expected)
                if properties["surprise_eligible"]
                and actual is not None
                and status != "disputed"
                else None
            )
            out.append(
                dict(
                    row,
                    **properties,
                    normalized_value=forecast,
                    surprise=difference,
                    resolution_status=status,
                    issuer_pending=status == "issuer_pending",
                    surprise_unit="percentage_points"
                    if info["unit"] in {"%", "percent"}
                    else info["unit"],
                )
            )
        return out, diagnostics

    def explain_empty(self, indicator, period_or_window, *, as_known_at=None):
        cutoff = utc(as_known_at or datetime.now(timezone.utc).isoformat())
        return self._explain(
            Snapshot.load(self.store, cutoff), indicator, period_or_window
        )

    @staticmethod
    def _explain(snapshot, indicator, period_or_window, *, geography=None):
        info = snapshot.indicators.get(indicator)
        base = {
            "indicator_id": indicator,
            "query": period_or_window,
            "limitations": [COLLECT_LOG_LIMITATION, *snapshot.limitations],
        }
        if info is None and indicator not in INDICATORS:
            return dict(
                base,
                reason="not_covered",
                detail="indicator is not in the stored or adapter catalogue",
            )
        if info is None:
            info = INDICATORS[indicator].model_dump()
        if geography and geography != info["geography"]:
            return dict(
                base,
                reason="filtered_out",
                detail="indicator excluded by geography filter",
            )
        rows = [e for e in snapshot.evidence if e.indicator_id == indicator]
        is_window = isinstance(period_or_window, (tuple, list))
        try:
            if is_window:
                if len(period_or_window) != 2:
                    raise ValueError("a window requires exactly two bounds")
                start, end = window(*period_or_window)
            else:
                start, end = None, period_end(period_or_window)
        except (ValueError, TypeError, OverflowError) as exc:
            return dict(base, reason="invalid_query", detail=str(exc))

        def matches(e):
            if not is_window:
                return e.reference_period == period_or_window
            instant = event_order(snapshot, e)
            return bool(instant and start <= instant < end)

        relevant = [e for e in rows if matches(e)]
        valid_rows = [e for e in rows if not malformed_edition(e)]
        if rows and not valid_rows:
            return dict(
                base,
                reason="invalid_publication_edition",
                detail="ONS index time-series version is not a publication edition; recollect ECY2 using the UK calendar date of updateDate",
            )
        if indicator == "uk_gdp_index" and is_window and valid_rows:
            published = [e for e in valid_rows if e.published_at]
            if published and max(e.published_at for e in published) < start:
                latest = max(
                    published, key=lambda e: (e.published_at, e.reference_period or "")
                )
                return dict(
                    base,
                    reason="no_release_in_window",
                    last_published_at=latest.published_at,
                    last_reference_period=latest.reference_period,
                    detail="ONS index has no publication in this window; ECY2 editions use the UK calendar date of updateDate",
                )
        monthly = [
            e.reference_period
            for e in valid_rows
            if e.verified_issuer and re.fullmatch(r"\d{4}-\d{2}", e.reference_period or "")
        ]
        if monthly and is_window:
            discontinued = stale_series(
                snapshot, indicator, max(monthly), min(end, snapshot.cutoff)
            )
            if discontinued:
                return dict(base, **discontinued)
        family = (
            family_for(snapshot, indicator)
            if indicator in snapshot.indicators
            else next(
                f for f, members in FAMILY_INDICATORS.items() if indicator in members
            )
        )
        family_schedules = [
            s
            for event, s in latest_schedule(snapshot).items()
            if snapshot.events[event]["family"] == family
        ]
        schedules = [
            s
            for s in family_schedules
            if not is_window
            or start <= event_timing(snapshot, s["release_event_id"])[0] < end
        ]
        if family in FAMILY_INDICATORS and is_window and schedules and not relevant and all(
            indicator not in indicators_for_stage(family, snapshot.events[s["release_event_id"]]["estimate_stage"])
            for s in schedules
        ):
            return dict(base, reason="no_release_scheduled", family=family,
                detail="reviewed editions in this window do not publish the requested indicator")
        if family in FAMILY_INDICATORS:
            schedules = [s for s in schedules if indicator in indicators_for_stage(
                family, snapshot.events[s["release_event_id"]]["estimate_stage"])]
        family_rows = [
            e
            for e in snapshot.evidence
            if family_for(snapshot, e.indicator_id) == family and matches(e)
            and (family not in FAMILY_INDICATORS or indicator in indicators_for_stage(family, e.stage))
        ]
        # Schedules lack reference periods: a family schedule cannot be assigned
        # to an arbitrary period when no evidence links that period to the event.
        if not is_window:
            linked = {e.event_id for e in relevant}
            schedules = [s for s in schedules if s["release_event_id"] in linked]
        query_start = (
            start
            if is_window
            else utc(
                (
                    period_or_window[:4] + "-01-01"
                    if len(period_or_window) == 4
                    else period_or_window[:4]
                    + f"-{(int(period_or_window[-1]) - 1) * 3 + 1:02d}-01"
                    if "-Q" in period_or_window
                    else period_or_window + "-01"
                    if len(period_or_window) == 7
                    else period_or_window
                )
                + "T00:00:00Z"
            )
        )
        failure = collection_failure(snapshot, family, query_start, end)
        if failure:
            return dict(base, **failure)
        failures = [
            e
            for e in relevant
            if e.legacy and e.legacy["link"]["legacy_run_status"] == "failed"
        ]
        if failures:
            failure = max(failures, key=lambda e: e.first_seen_at)
            return dict(
                base,
                reason="source_failed",
                family=family,
                time=failure.legacy["normalized_run_started_at"],
                time_basis="legacy_run_started_at",
                time_precision=failure.timestamp_precisions["legacy_run_started_at"],
                detail="stored imported run failed; exact failure instant was not retained",
            )
        released = [
            e
            for e in family_rows
            if event_order(snapshot, e) and event_order(snapshot, e) <= snapshot.cutoff
        ]
        if released:
            return dict(
                base,
                reason="published_not_resolved",
                family=family,
                detail="same-family release evidence exists; requested indicator has no eligible card (check numeric value, period, unit, stage and event/time linkage)",
            )
        if schedules and any(s["expected_at"] <= snapshot.cutoff for s in schedules):
            return dict(
                base,
                reason="scheduled_not_captured",
                family=family,
                detail="scheduled release is due; publication/value capture is not confirmed",
            )
        if (
            schedules
            or family_rows
            or (is_window and start > snapshot.cutoff)
            or (not is_window and end > snapshot.cutoff)
        ):
            return dict(
                base,
                reason="not_yet_released",
                family=family,
                detail="release/period is in the future as known at the query cutoff",
            )
        if is_window and any(e.event_time is None for e in rows):
            return dict(
                base,
                reason="published_not_resolved",
                family=family,
                detail="undated evidence cannot be assigned to the requested release window",
            )
        moved = (
            [
                s["release_event_id"]
                for s in family_schedules
                if not start <= s["expected_at"] < end
                and any(
                    old["release_event_id"] == s["release_event_id"]
                    and start <= old["expected_at"] < end
                    for old in snapshot.schedules
                )
            ]
            if is_window
            else []
        )
        if moved:
            return dict(
                base,
                reason="no_release_scheduled",
                family=family,
                release_event_ids=sorted(moved),
                detail="stored schedule revisions moved these events outside this window; this is not a complete-calendar guarantee",
            )
        return dict(
            base,
            reason="schedule_unknown",
            family=family,
            detail="no matching release evidence or period-linked schedule is stored; a release calendar is unavailable",
        )

    def coverage(self, since, until, *, as_known_at=None):
        start, end = window(since, until)
        cutoff = utc(as_known_at or datetime.now(timezone.utc).isoformat())
        snapshot = Snapshot.load(self.store, cutoff)
        result = self._cards(snapshot, start, end)
        families = {
            f: {
                "expected": 0,
                "captured": 0,
                "official": 0,
                "private": 0,
                "provisional": 0,
                "disputed": 0,
                "issuer_pending": 0,
                "missing": 0,
                "missing_reasons": [],
                "latest_published_at": None,
                "latest_first_seen_at": None,
            }
            for f in FAMILIES
        }
        events = {}
        for card in result["cards"]:
            if card["resolution"] is not None:
                events.setdefault(
                    (card["family"], card["release_event_id"]), []
                ).append(card)
        event_rows = {}
        for e in snapshot.evidence:
            if e.event_id and e.reading_kind in {
                "actual",
                "unavailable",
                "legacy_merged_actual",
            }:
                event_rows.setdefault(e.event_id, []).append(e)
        for event in set(latest_schedule(snapshot)) | set(event_rows):
            instant, _, _ = event_timing(snapshot, event)
            if instant and start <= instant < end:
                family = (
                    snapshot.events[event]["family"]
                    if event in snapshot.events
                    else family_for(snapshot, event_rows[event][0].indicator_id)
                )
                events.setdefault((family, event), [])
        priorities = {
            "official": 0,
            "private": 0,
            "provisional": 1,
            "issuer_pending": 2,
            "disputed": 3,
        }
        for (family, event), cards in sorted(events.items()):
            counts = families.setdefault(
                family,
                {
                    "expected": 0,
                    "captured": 0,
                    "official": 0,
                    "private": 0,
                    "provisional": 0,
                    "disputed": 0,
                    "issuer_pending": 0,
                    "missing": 0,
                    "missing_reasons": [],
                    "latest_published_at": None,
                    "latest_first_seen_at": None,
                },
            )
            counts["expected"] += 1
            if cards:
                counts["captured"] += 1
                counts[
                    max((c["resolution"]["status"] for c in cards), key=priorities.get)
                ] += 1
                for field in ("published_at", "first_seen_at"):
                    times = [
                        c["resolution"][field] for c in cards if c["resolution"][field]
                    ]
                    counts["latest_" + field] = (
                        max(times + [counts["latest_" + field] or ""]) or None
                    )
            else:
                counts["missing"] += 1
                schedule = latest_schedule(snapshot).get(event)
                failures = [
                    e
                    for e in snapshot.evidence
                    if e.event_id == event
                    and e.legacy
                    and e.legacy["link"]["legacy_run_status"] == "failed"
                ]
                instant, _, _ = event_timing(snapshot, event)
                reason = (
                    "source_failed"
                    if failures
                    else "not_yet_released"
                    if instant and instant > cutoff
                    else "scheduled_not_captured"
                    if schedule and not event_rows.get(event)
                    else "published_not_resolved"
                )
                counts["missing_reasons"].append(
                    {
                        "release_event_id": event,
                        "reason": reason,
                        "detail": "event has no resolved numeric card",
                    }
                )
        for family, counts in families.items():
            failure = collection_failure(snapshot, family, start, end)
            counts["collection_failures"] = [failure] if failure else []
            if failure:
                for missing in counts["missing_reasons"]:
                    instant, _, _ = event_timing(snapshot, missing["release_event_id"])
                    event_failure = (
                        collection_failure(snapshot, family, instant, instant)
                        if instant
                        else None
                    )
                    if event_failure:
                        missing.update(event_failure)
        return {
            "since": start,
            "until": end,
            "as_known_at": cutoff,
            "families": families,
            "count_basis": "distinct release events per family; expected is the union of stored schedules and captured events",
            "limitations": [
                COLLECT_LOG_LIMITATION,
                *snapshot.limitations,
                "No complete release calendar is stored; zero expected does not prove no release was due.",
            ],
        }
