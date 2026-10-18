"""Consumer read API for the macro store. Shadow mode; no writes or downloads."""
from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
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
    if re.fullmatch(r"\d{4}-\d{2}", period):
        year, month = map(int, period.split("-"))
        return f"{period}-{monthrange(year, month)[1]}T23:59:59.999999+00:00"
    match = re.fullmatch(r"(\d{4})-Q([1-4])", period)
    if match:
        year, quarter = map(int, match.groups())
        return period_end(f"{year}-{quarter * 3:02}")
    if re.fullmatch(r"\d{4}", period):
        return f"{period}-12-31T23:59:59.999999+00:00"
    return window(period, period)[1]


def family_for(snapshot, indicator):
    for family, members in FAMILY_INDICATORS.items():
        if indicator in members:
            return family
    families = sorted({snapshot.events[e.event_id]["family"] for e in snapshot.evidence
                       if e.indicator_id == indicator and e.event_id in snapshot.events})
    if len(families) == 1:
        return families[0]
    info = snapshot.indicators[indicator]
    return f"{info['issuer_id']}:{info['measure']}"


def latest_schedule(snapshot):
    return {r["release_event_id"]: r for r in snapshot.schedules}


def latest_period(snapshot, indicator, period):
    """Resolve each stage separately, then choose the latest publication stage."""
    candidates = [e for e in snapshot.evidence if e.indicator_id == indicator and e.reference_period == period]
    stages = sorted({e.stage for e in candidates})
    resolved = [resolve_evidence(snapshot, indicator, period, s) for s in stages]
    resolved = [r for r in resolved if r is not None]
    if not resolved:
        return None
    return max(resolved, key=lambda r: (r.published_at or "", r.first_seen_at, r.stage)).to_dict()


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
        schedules = latest_schedule(snapshot)
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
            schedule = schedules.get(event_id)
            released_at = selected.event_time or (schedule["expected_at"] if schedule else None)
            if released_at is None or not start <= released_at < end or released_at > snapshot.cutoff:
                continue
            prior = prior_period(period)
            # The previous figure is strictly what we knew by release time.
            before = subset(snapshot, (e for e in snapshot.evidence if e.event_id != event_id and
                                      e.first_seen_at <= released_at and
                                      (e.published_at is None or e.published_at < released_at)))
            previous = latest_period(before, indicator, prior) if prior else None
            # Revisions reconstruct editions available at the query's cutoff.
            # Archive captures after release can establish publication history,
            # but cannot backdate the previous-as-known-at-release field above.
            history = subset(snapshot, (e for e in snapshot.evidence if e.event_id != event_id and
                                       publication_order(e) and publication_order(e) < publication_order(selected)))
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
            consensus = self._consensus(snapshot, resolution, event_id, released_at, selected.precision)
            legacy_forecasts = [dict(e.provenance(), eligibility="unverified_provider_or_pre_release_capture")
                                for e in snapshot.evidence if e.event_id == event_id and
                                e.indicator_id == indicator and e.reference_period == period and
                                e.reading_kind in {"consensus", "legacy_merged_consensus"}]
            surprise = ([{"provider": c["provider"], "kind": c["kind"], "value": c["surprise"],
                          "unit": c["surprise_unit"], "scale": resolution.scale} for c in consensus]
                        if consensus else None)
            cards.append({"release_event_id": event_id, "family": snapshot.events.get(event_id, {}).get("family", family_for(snapshot, indicator)),
                          "indicator_id": indicator, "indicator_name": snapshot.indicators[indicator]["measure"],
                          "geography": snapshot.indicators[indicator]["geography"],
                          "reference_period": period, "stage": stage, "released_at": released_at,
                          "time_basis": selected.time_basis if selected.event_time else "schedule",
                          "publication_precision": selected.precision or (schedule["precision"] if schedule else None),
                          "resolution": resolution.to_dict(), "previous": previous,
                          "previous_period": prior, "previous_missing_reason": None if previous else "prior period not known at release time",
                          "revisions": revisions, "stage_change": stage_change,
                          "consensus": consensus, "surprise": surprise,
                          "legacy_consensus_evidence": legacy_forecasts,
                          "provenance": list({e["id"]: e for e in
                                              [*(e.provenance() for e in evidence), *resolution.provenance]}.values())})
        cards.sort(key=lambda c: (c["released_at"], c["release_event_id"], c["indicator_id"]))
        empty = [self._explain(snapshot, i, (start, end), geography=geography) for i in requested
                 if not any(c["indicator_id"] == i for c in cards)]
        if not requested:
            empty.append({"indicator_id": None, "query": (start, end),
                          "reason": "no_release_scheduled", "detail": "empty indicator selection"})
        return {"since": start, "until": end, "as_known_at": snapshot.cutoff,
                "cards": cards, "empty": empty, "limitations": [COLLECT_LOG_LIMITATION]}

    @staticmethod
    def _consensus(snapshot, resolution, event_id, released_at, precision):
        info = snapshot.indicators[resolution.indicator_id]
        per_provider = {}
        for row in snapshot.consensus:
            if (row["indicator_id"] != resolution.indicator_id or row["release_event_id"] != event_id or
                    row["reference_period"] != resolution.reference_period or row["estimate_stage"] != resolution.stage or
                    row["measure"] != info["measure"]):
                continue
            # The actual time reconciles old date-only/scheduled eligibility.
            # With a date-only publication retain the saved conservative cutoff.
            cutoff = released_at if precision == "exact" else min(
                released_at, row["release_cutoff_at"] or released_at)
            if row["captured_at"] >= cutoff or (row["published_at"] and row["published_at"] >= cutoff):
                continue
            value = convert(row["value"], row["unit"], row["scale"], info)
            if value is None:
                continue
            key = row["provider"]
            if key not in per_provider or (row["captured_at"], row["content_hash"], row["id"]) > (
                    per_provider[key]["captured_at"], per_provider[key]["content_hash"], per_provider[key]["id"]):
                per_provider[key] = row
        out = []
        for provider, row in sorted(per_provider.items()):
            forecast = convert(row["value"], row["unit"], row["scale"], info)
            kind = "ff_forecast" if provider.lower().replace(" ", "_") in {"ff", "forex_factory", "forexfactory", "ff_forecast"} else "survey_consensus"
            out.append(dict(row, kind=kind, normalized_value=forecast,
                            surprise=resolution.value - forecast,
                            surprise_unit="percentage_points" if info["unit"] in {"%", "percent"} else info["unit"],
                            eligibility_cutoff_at=released_at if precision == "exact" else min(released_at, row["release_cutoff_at"] or released_at)))
        return out

    def explain_empty(self, indicator, period_or_window, *, as_known_at=None):
        cutoff = utc(as_known_at or datetime.now(timezone.utc).isoformat())
        return self._explain(Snapshot.load(self.store, cutoff), indicator, period_or_window)

    @staticmethod
    def _explain(snapshot, indicator, period_or_window, *, geography=None):
        info = snapshot.indicators.get(indicator)
        base = {"indicator_id": indicator, "query": period_or_window,
                "limitations": [COLLECT_LOG_LIMITATION]}
        if info is None and indicator not in INDICATORS:
            return dict(base, reason="not_covered", detail="indicator is not in the stored or adapter catalogue")
        if info is None:
            info = INDICATORS[indicator].model_dump()
        if geography and geography != info["geography"]:
            return dict(base, reason="no_release_scheduled", detail="indicator excluded by geography filter")
        rows = [e for e in snapshot.evidence if e.indicator_id == indicator]
        is_window = isinstance(period_or_window, (tuple, list))
        if is_window:
            start, end = window(*period_or_window)
            relevant = [e for e in rows if e.event_time and start <= e.event_time < end]
        else:
            relevant = [e for e in rows if e.reference_period == period_or_window]
            start, end = None, period_end(period_or_window)
        family = family_for(snapshot, indicator) if indicator in snapshot.indicators else next(
            f for f, members in FAMILY_INDICATORS.items() if indicator in members)
        schedules = [s for event, s in latest_schedule(snapshot).items()
                     if snapshot.events[event]["family"] == family and
                     (not is_window or start <= s["expected_at"] < end)]
        # Schedules lack reference periods: a family schedule cannot be assigned
        # to an arbitrary period when no evidence links that period to the event.
        if not is_window:
            linked = {e.event_id for e in relevant}
            schedules = [s for s in schedules if s["release_event_id"] in linked]
        failures = [e for e in relevant if e.legacy and e.legacy["link"]["legacy_run_status"] == "failed"]
        if failures:
            failure = max(failures, key=lambda e: e.first_seen_at)
            return dict(base, reason="source_failed", family=family,
                        time=failure.legacy["legacy_first_seen_at"], time_basis="legacy_run_started_at",
                        detail="stored imported run failed; exact failure instant was not retained")
        released = [e for e in relevant if (e.published_at and e.published_at <= snapshot.cutoff) or
                    (e.event_time and e.event_time <= snapshot.cutoff)]
        if released:
            return dict(base, reason="published_not_resolved", family=family,
                        detail="publication evidence exists; no eligible card (check numeric value, period, unit, stage and event/time linkage)")
        if schedules and any(s["expected_at"] <= snapshot.cutoff for s in schedules):
            return dict(base, reason="published_not_resolved", family=family,
                        detail="scheduled release is due; publication/value capture is not confirmed")
        if schedules or any(e.event_time and e.event_time > snapshot.cutoff for e in relevant) or (is_window and start > snapshot.cutoff) or (not is_window and end > snapshot.cutoff):
            return dict(base, reason="not_yet_released", family=family,
                        detail="release/period is in the future as known at the query cutoff")
        if is_window and any(e.event_time is None for e in rows):
            return dict(base, reason="published_not_resolved", family=family,
                        detail="undated evidence cannot be assigned to the requested release window")
        return dict(base, reason="no_release_scheduled", family=family,
                    detail="no matching schedule or publication evidence is stored; schedule coverage is incomplete")

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
        for event, schedule in latest_schedule(snapshot).items():
            if start <= schedule["expected_at"] < end:
                key = (snapshot.events[event]["family"], event)
                # Actual publication wins over a superseded planned window.
                known = [e.event_time for e in snapshot.evidence if e.event_id == event and e.event_time]
                if not known or any(start <= t < end for t in known):
                    events.setdefault(key, [])
        for e in snapshot.evidence:
            if e.event_id and e.event_time and start <= e.event_time < end and e.reading_kind in {"actual", "unavailable", "legacy_merged_actual"}:
                family = snapshot.events.get(e.event_id, {}).get("family", family_for(snapshot, e.indicator_id))
                events.setdefault((family, e.event_id), [])
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
                reason = ("source_failed" if failures else "not_yet_released" if schedule and
                          schedule["expected_at"] > cutoff else "published_not_resolved")
                counts["missing_reasons"].append({"release_event_id": event, "reason": reason,
                                                  "detail": "event has no resolved numeric card"})
        return {"since": start, "until": end, "as_known_at": cutoff, "families": families,
                "count_basis": "distinct release events per family; expected is the union of stored schedules and captured events",
                "limitations": [COLLECT_LOG_LIMITATION, "No complete release calendar is stored; zero expected does not prove no release was due."]}
