"""Consumer read API for the macro store. Shadow mode; no writes or downloads."""
from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import re

from .catalogue import FAMILIES, INDICATORS
from .models import utc
from .resolve import (
    COLLECT_LOG_LIMITATION, Snapshot, convert, differs,
    publication_order, resolve_evidence, subset,
)

FAMILY_INDICATORS = {
    "us_cpi": {i for i in INDICATORS if i.startswith("us_cpi_")},
    "us_payrolls": {"us_payrolls_change"},
    "us_gdp": {"us_gdp_qq_saar"},
    "ea_hicp": {i for i in INDICATORS if "hicp" in i},
    "uk_gdp": {"uk_gdp_mm", "uk_gdp_index"},
}


def window(since, until):
    """Half-open UTC bounds. A date-only until includes that entire UTC date."""
    start = utc(since + "T00:00:00Z") if len(since) == 10 else utc(since)
    end = (utc((date.fromisoformat(until) + timedelta(days=1)).isoformat() + "T00:00:00Z")
           if len(until) == 10 else utc(until))
    if start >= end:
        raise ValueError("since must precede until")
    return start, end


def prior_period(period):
    if re.fullmatch(r"\d{4}-\d{2}", period):
        first = date.fromisoformat(period + "-01")
        return (first - timedelta(days=1)).strftime("%Y-%m")
    match = re.fullmatch(r"(\d{4})-Q([1-4])", period)
    if match:
        year, quarter = map(int, match.groups())
        return f"{year if quarter > 1 else year - 1}-Q{quarter - 1 if quarter > 1 else 4}"
    if re.fullmatch(r"\d{4}", period):
        return str(int(period) - 1)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", period):
        return (date.fromisoformat(period) - timedelta(days=1)).isoformat()
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
        return {"reason": "source_failed", "family": family, "run_id": row["run_id"],
                "time": row["finished_at"], "time_basis": "collect_family_finished_at",
                "time_precision": row["timestamp_precisions"]["finished_at"],
                "started_at": row["started_at"], "error_type": row["error_type"],
                "detail": row["error_message"], "fetch_since": row["fetch_since"],
                "fetch_until": row["fetch_until"]}
    return None


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
    return event_timing(snapshot, evidence.event_id, evidence)[0] or publication_order(evidence)


def latest_period(snapshot, indicator, period):
    """Resolve each stage separately, then choose the latest publication stage."""
    candidates = [e for e in snapshot.evidence if e.indicator_id == indicator and e.reference_period == period]
    stages = sorted({e.stage for e in candidates})
    resolved = [resolve_evidence(snapshot, indicator, period, s) for s in stages]
    resolved = [r for r in resolved if r is not None]
    if not resolved:
        return None
    by_id = {e.id: e for e in candidates}

    def order(resolution):
        selected = by_id[resolution.source["selected_evidence_id"]]
        return (event_order(snapshot, selected), publication_order(selected),
                resolution.first_seen_at, resolution.stage)

    return max(resolved, key=order).to_dict()


class ReadAPI:
    def __init__(self, store):
        self.store = store

    def release_cards(self, since, until, indicators=None, geography=None, as_known_at=None):
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

    def _cards(self, snapshot, start, end, indicators=None, geography=None):
        requested = ([indicators] if isinstance(indicators, str) else list(dict.fromkeys(indicators))
                     if indicators is not None else sorted(set(INDICATORS) | set(snapshot.indicators)))
        # The snapshot is immutable during a query. Reuse chronology across
        # cards instead of resolving every historical event for every card.
        event_orders = {e.id: event_order(snapshot, e) for e in snapshot.evidence}
        groups = {}
        for e in snapshot.evidence:
            if e.indicator_id not in requested or not e.event_id:
                continue
            info = snapshot.indicators[e.indicator_id]
            if geography and info["geography"] != geography:
                continue
            if e.reference_period is not None and e.reading_kind in {"actual", "unavailable", "legacy_merged_actual"}:
                groups.setdefault((e.event_id, e.indicator_id), []).append(e)
        cards = []
        for (event_id, indicator), evidence in sorted(groups.items()):
            # One headline period per indicator; older periods are revision rows.
            period = max(e.reference_period for e in evidence)
            headline = [e for e in evidence if e.reference_period == period]
            stage = snapshot.events.get(event_id, {}).get("estimate_stage")
            stage = stage if stage in {e.stage for e in headline} else sorted({e.stage for e in headline})[-1]
            resolution = resolve_evidence(snapshot, indicator, period, stage, event_id=event_id)
            if resolution is None:
                continue
            selected = next(e for e in headline if e.id == resolution.source["selected_evidence_id"])
            if selected.role == "mirror" and resolution.status == "provisional" and selected.published_at:
                context = subset(snapshot, (e for e in snapshot.evidence if
                                            e.published_at and e.published_at <= selected.published_at))
                contextual = resolve_evidence(context, indicator, period, stage)
                if contextual and contextual.source["selected_evidence_id"] == selected.id:
                    resolution = contextual
            released_at, precision, time_basis = event_timing(snapshot, event_id, selected)
            if released_at is None or not start <= released_at < end or released_at > snapshot.cutoff:
                continue
            prior = prior_period(period)
            # The previous figure is strictly what we knew by release time.
            before = subset(snapshot, (e for e in snapshot.evidence if e.event_id != event_id and
                                      e.first_seen_at < released_at and
                                      (e.published_at is None or e.published_at < released_at)))
            previous = latest_period(before, indicator, prior) if prior else None
            revised_previous = latest_period(subset(snapshot, evidence), indicator, prior) if prior else None
            if (previous and revised_previous and not differs(previous["value"], revised_previous["value"]) and
                    previous["stage"] == revised_previous["stage"] and previous["status"] == revised_previous["status"]):
                revised_previous = previous
            if revised_previous is None:
                revised_previous = previous
            # Revisions reconstruct editions available at the query's cutoff.
            # Archive captures after release can establish publication history,
            # but cannot backdate the previous-as-known-at-release field above.
            history = subset(snapshot, (e for e in snapshot.evidence if e.event_id != event_id and
                                       event_orders[e.id] and event_orders[e.id] < released_at))
            revisions = []
            for changed_period in sorted({e.reference_period for e in evidence if e.reference_period < period}):
                old = latest_period(history, indicator, changed_period)
                changed = latest_period(subset(snapshot, evidence), indicator, changed_period)
                if old and changed and differs(old["value"], changed["value"]):
                    revisions.append({"reference_period": changed_period, "old": old, "new": changed,
                                      "comparison_basis": "publication_history_as_known_at_query"})
            old_stage = latest_period(history, indicator, period)
            stage_change = None
            if old_stage and old_stage["stage"] != stage:
                stage_change = {"old": old_stage, "new": resolution.to_dict(),
                                "changed": differs(old_stage["value"], resolution.value)}
            consensus, diagnostics = self._consensus(snapshot, resolution, event_id, released_at, precision, time_basis)
            legacy_forecasts = [dict(e.provenance(), eligibility="unverified_provider_or_pre_release_capture")
                                for e in snapshot.evidence if e.event_id == event_id and
                                e.indicator_id == indicator and e.reference_period == period and
                                e.reading_kind in {"consensus", "legacy_merged_consensus"}]
            surprise = ([{"provider": c["provider"], "kind": c["kind"], "value": c["surprise"],
                          "unit": c["surprise_unit"], "scale": resolution.scale,
                          "resolution_status": resolution.status,
                          "issuer_pending": resolution.status == "issuer_pending"} for c in consensus]
                        if consensus and resolution.status != "disputed" else None)
            cards.append({"release_event_id": event_id, "family": snapshot.events.get(event_id, {}).get("family", family_for(snapshot, indicator)),
                          "indicator_id": indicator, "indicator_name": snapshot.indicators[indicator]["measure"],
                          "geography": snapshot.indicators[indicator]["geography"],
                          "reference_period": period, "stage": stage, "released_at": released_at,
                          "time_basis": time_basis, "publication_precision": precision,
                          "resolution": resolution.to_dict(), "previous": previous,
                          "previous_before_release": previous, "previous_revised": revised_previous,
                          "previous_period": prior, "previous_missing_reason": None if previous else "prior period not known at release time",
                          "revisions": revisions, "stage_change": stage_change,
                          "consensus": consensus, "consensus_diagnostics": diagnostics, "surprise": surprise,
                          "legacy_consensus_evidence": legacy_forecasts,
                          "provenance": list({e["id"]: e for e in
                                              [*(e.provenance() for e in evidence), *resolution.provenance]}.values())})
        cards.sort(key=lambda c: (c["released_at"], c["release_event_id"], c["indicator_id"]))
        empty = [self._explain(snapshot, i, (start, end), geography=geography) for i in requested
                 if not any(c["indicator_id"] == i for c in cards)]
        if not requested:
            empty.append({"indicator_id": None, "query": (start, end),
                          "reason": "filtered_out", "detail": "empty indicator selection"})
        return {"since": start, "until": end, "as_known_at": snapshot.cutoff,
                "cards": cards, "empty": empty, "limitations": [COLLECT_LOG_LIMITATION, *snapshot.limitations]}

    @staticmethod
    def _consensus(snapshot, resolution, event_id, released_at, precision, time_basis):
        info = snapshot.indicators[resolution.indicator_id]
        per_provider = {}
        diagnostics = []
        for row in snapshot.consensus:
            if (row["indicator_id"] != resolution.indicator_id or row["release_event_id"] != event_id or
                    row["reference_period"] != resolution.reference_period or row["estimate_stage"] != resolution.stage or
                    row["measure"] != info["measure"]):
                continue
            # The actual time reconciles old date-only/scheduled eligibility.
            # With a date-only publication retain the saved conservative cutoff.
            if precision == "exact" and time_basis in {"issuer_publication", "schedule"}:
                cutoff = released_at
            elif row["cutoff_precision"] == "date_only":
                cutoff = row["release_cutoff_at"]
            elif precision == "date_only":
                cutoff = released_at
            else:
                cutoff = row["release_cutoff_at"]
            if cutoff is None:
                diagnostics.append({"consensus_id": row["id"], "provider": row["provider"],
                                    "reason": "unknown_release_cutoff"})
                continue
            if row["captured_at"] >= cutoff or (row["published_at"] and row["published_at"] >= cutoff):
                continue
            key = row["provider"]
            if key not in per_provider or (row["captured_at"], row["content_hash"], row["id"]) > (
                    per_provider[key]["captured_at"], per_provider[key]["content_hash"], per_provider[key]["id"]):
                per_provider[key] = dict(row, eligibility_cutoff_at=cutoff)
        out = []
        selected = next(e for e in snapshot.evidence if e.id == resolution.source["selected_evidence_id"])
        actual = Decimal(str(selected.value)) * Decimal(str(selected.scale)) / Decimal(str(info["scale"]))
        for provider, row in sorted(per_provider.items()):
            forecast = convert(row["value"], row["unit"], row["scale"], info)
            if forecast is None:
                diagnostics.append({"consensus_id": row["id"], "provider": provider,
                                    "survey_identity": row["survey_identity"], "unit": row["unit"],
                                    "scale": row["scale"], "value": row["value"],
                                    "captured_at": row["captured_at"],
                                    "eligibility_cutoff_at": row["eligibility_cutoff_at"],
                                    "expected_unit": info["unit"], "reason": "unit_not_convertible"})
                continue
            expected = Decimal(str(row["value"])) * Decimal(str(row["scale"])) / Decimal(str(info["scale"]))
            # Subtract decimal inputs at their explicit scale, avoiding binary
            # artifacts such as 3.7 - 3.6 == 0.10000000000000009.
            difference = float(actual - expected) if resolution.status != "disputed" else None
            kind = "ff_forecast" if provider.lower().replace(" ", "_") in {"ff", "forex_factory", "forexfactory", "ff_forecast"} else "survey_consensus"
            out.append(dict(row, kind=kind, normalized_value=forecast,
                            surprise=difference, resolution_status=resolution.status,
                            issuer_pending=resolution.status == "issuer_pending",
                            surprise_unit="percentage_points" if info["unit"] in {"%", "percent"} else info["unit"]))
        return out, diagnostics

    def explain_empty(self, indicator, period_or_window, *, as_known_at=None):
        cutoff = utc(as_known_at or datetime.now(timezone.utc).isoformat())
        return self._explain(Snapshot.load(self.store, cutoff), indicator, period_or_window)

    @staticmethod
    def _explain(snapshot, indicator, period_or_window, *, geography=None):
        info = snapshot.indicators.get(indicator)
        base = {"indicator_id": indicator, "query": period_or_window,
                "limitations": [COLLECT_LOG_LIMITATION, *snapshot.limitations]}
        if info is None and indicator not in INDICATORS:
            return dict(base, reason="not_covered", detail="indicator is not in the stored or adapter catalogue")
        if info is None:
            info = INDICATORS[indicator].model_dump()
        if geography and geography != info["geography"]:
            return dict(base, reason="filtered_out", detail="indicator excluded by geography filter")
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
        family = family_for(snapshot, indicator) if indicator in snapshot.indicators else next(
            f for f, members in FAMILY_INDICATORS.items() if indicator in members)
        family_schedules = [s for event, s in latest_schedule(snapshot).items()
                            if snapshot.events[event]["family"] == family]
        schedules = [s for s in family_schedules if not is_window or
                     start <= event_timing(snapshot, s["release_event_id"])[0] < end]
        family_rows = [e for e in snapshot.evidence if family_for(snapshot, e.indicator_id) == family and matches(e)]
        # Schedules lack reference periods: a family schedule cannot be assigned
        # to an arbitrary period when no evidence links that period to the event.
        if not is_window:
            linked = {e.event_id for e in relevant}
            schedules = [s for s in schedules if s["release_event_id"] in linked]
        query_start = start if is_window else utc(
            (period_or_window[:4] + "-01-01" if len(period_or_window) == 4 else
             period_or_window[:4] + f"-{(int(period_or_window[-1]) - 1) * 3 + 1:02d}-01"
             if "-Q" in period_or_window else
             period_or_window + "-01" if len(period_or_window) == 7 else period_or_window) + "T00:00:00Z")
        failure = collection_failure(snapshot, family, query_start, end)
        if failure:
            return dict(base, **failure)
        failures = [e for e in relevant if e.legacy and e.legacy["link"]["legacy_run_status"] == "failed"]
        if failures:
            failure = max(failures, key=lambda e: e.first_seen_at)
            return dict(base, reason="source_failed", family=family,
                        time=failure.legacy["normalized_run_started_at"], time_basis="legacy_run_started_at",
                        time_precision=failure.timestamp_precisions["legacy_run_started_at"],
                        detail="stored imported run failed; exact failure instant was not retained")
        released = [e for e in family_rows if event_order(snapshot, e) and event_order(snapshot, e) <= snapshot.cutoff]
        if released:
            return dict(base, reason="published_not_resolved", family=family,
                        detail="same-family release evidence exists; requested indicator has no eligible card (check numeric value, period, unit, stage and event/time linkage)")
        if schedules and any(s["expected_at"] <= snapshot.cutoff for s in schedules):
            return dict(base, reason="scheduled_not_captured", family=family,
                        detail="scheduled release is due; publication/value capture is not confirmed")
        if schedules or family_rows or (is_window and start > snapshot.cutoff) or (not is_window and end > snapshot.cutoff):
            return dict(base, reason="not_yet_released", family=family,
                        detail="release/period is in the future as known at the query cutoff")
        if is_window and any(e.event_time is None for e in rows):
            return dict(base, reason="published_not_resolved", family=family,
                        detail="undated evidence cannot be assigned to the requested release window")
        moved = ([s["release_event_id"] for s in family_schedules
                  if not start <= s["expected_at"] < end and
                  any(old["release_event_id"] == s["release_event_id"] and start <= old["expected_at"] < end
                      for old in snapshot.schedules)] if is_window else [])
        if moved:
            return dict(base, reason="no_release_scheduled", family=family,
                        release_event_ids=sorted(moved),
                        detail="stored schedule revisions moved these events outside this window; this is not a complete-calendar guarantee")
        return dict(base, reason="schedule_unknown", family=family,
                    detail="no matching release evidence or period-linked schedule is stored; a release calendar is unavailable")

    def coverage(self, since, until, *, as_known_at=None):
        start, end = window(since, until)
        cutoff = utc(as_known_at or datetime.now(timezone.utc).isoformat())
        snapshot = Snapshot.load(self.store, cutoff)
        result = self._cards(snapshot, start, end)
        families = {f: {"expected": 0, "captured": 0, "official": 0, "provisional": 0,
                        "disputed": 0, "issuer_pending": 0, "missing": 0, "missing_reasons": [],
                        "latest_published_at": None, "latest_first_seen_at": None} for f in FAMILIES}
        events = {}
        for card in result["cards"]:
            events.setdefault((card["family"], card["release_event_id"]), []).append(card)
        event_rows = {}
        for e in snapshot.evidence:
            if e.event_id and e.reading_kind in {"actual", "unavailable", "legacy_merged_actual"}:
                event_rows.setdefault(e.event_id, []).append(e)
        for event in set(latest_schedule(snapshot)) | set(event_rows):
            instant, _, _ = event_timing(snapshot, event)
            if instant and start <= instant < end:
                family = (snapshot.events[event]["family"] if event in snapshot.events else
                          family_for(snapshot, event_rows[event][0].indicator_id))
                events.setdefault((family, event), [])
        priorities = {"official": 0, "provisional": 1, "issuer_pending": 2, "disputed": 3}
        for (family, event), cards in sorted(events.items()):
            counts = families.setdefault(family, {"expected": 0, "captured": 0, "official": 0, "provisional": 0,
                                                   "disputed": 0, "issuer_pending": 0, "missing": 0, "missing_reasons": [],
                                                   "latest_published_at": None, "latest_first_seen_at": None})
            counts["expected"] += 1
            if cards:
                counts["captured"] += 1
                counts[max((c["resolution"]["status"] for c in cards), key=priorities.get)] += 1
                for field in ("published_at", "first_seen_at"):
                    times = [c["resolution"][field] for c in cards if c["resolution"][field]]
                    counts["latest_" + field] = max(times + [counts["latest_" + field] or ""]) or None
            else:
                counts["missing"] += 1
                schedule = latest_schedule(snapshot).get(event)
                failures = [e for e in snapshot.evidence if e.event_id == event and e.legacy and
                            e.legacy["link"]["legacy_run_status"] == "failed"]
                instant, _, _ = event_timing(snapshot, event)
                reason = ("source_failed" if failures else "not_yet_released" if instant and instant > cutoff else
                          "scheduled_not_captured" if schedule and not event_rows.get(event) else "published_not_resolved")
                counts["missing_reasons"].append({"release_event_id": event, "reason": reason,
                                                  "detail": "event has no resolved numeric card"})
        for family, counts in families.items():
            failure = collection_failure(snapshot, family, start, end)
            counts["collection_failures"] = [failure] if failure else []
            if failure:
                for missing in counts["missing_reasons"]:
                    instant, _, _ = event_timing(snapshot, missing["release_event_id"])
                    event_failure = collection_failure(snapshot, family, instant, instant) if instant else None
                    if event_failure:
                        missing.update(event_failure)
        return {"since": start, "until": end, "as_known_at": cutoff, "families": families,
                "count_basis": "distinct release events per family; expected is the union of stored schedules and captured events",
                "limitations": [COLLECT_LOG_LIMITATION, *snapshot.limitations,
                                "No complete release calendar is stored; zero expected does not prove no release was due."]}
