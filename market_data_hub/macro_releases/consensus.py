"""Offline FF forecasts with exact catalogue mapping and manifest provenance.

Reference periods and stages belong to uniquely matched stored issuer events,
never to a lag inferred from FF's release date or its forecast text.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo

from market_data_hub.econ_calendar.collect.forexfactory import (
    DEFAULT_RAW_DIR,
    MANIFEST_NAME,
    load_snapshots,
    paese_iso,
)

from .models import ConsensusReading, ReleaseEvent, utc
from .numbers import parse_number
from .read_api import event_timing, event_timezone, latest_schedule
from .read_times import timestamp
from .resolve import Snapshot
from .store import open_writer
from .worklist_periods import explicit_period

PROVIDER = "ff_forecast"
RULE_VERSION = "ff-consensus-v3"
ALIAS_FILE = Path(__file__).with_name("ff_aliases_v1.json")
GEOGRAPHIES = {
    "US": {"USA"},
    "GB": {"GBR"},
    "EU": {"EMU", "EA20", "EA21"},
    "DE": {"DEU"},
    "FR": {"FRA"},
    "IT": {"ITA"},
    "ES": {"ESP"},
    "JP": {"JPN"},
    "AU": {"AUS"},
    "NZ": {"NZL"},
    "CA": {"CAN"},
    "CH": {"CHE"},
    "CN": {"CHN"},
}
ISSUER_ZONES = {
    "BLS": "America/New_York",
    "BEA": "America/New_York",
    "ONS": "Europe/London",
    "Eurostat": "Europe/Brussels",
}


def name(value):
    # Preserve qualifiers, slash bases and country prefixes. No fuzzy matching.
    return " ".join(value.casefold().split())


class Catalogue:
    def __init__(self, store):
        self.indicators = {
            r["id"]: dict(r) for r in store.conn.execute("SELECT * FROM indicators")
        }
        self.names = defaultdict(set)
        self.reviewed = {}
        self.aliases = json.loads(ALIAS_FILE.read_text(encoding="utf-8"))["aliases"]
        for row in store.conn.execute("SELECT * FROM indicator_aliases"):
            self.names[row["geography"], name(row["alias"])].add(
                (row["indicator_id"], row["estimate_stage"])
            )
        for alias in self.aliases:
            indicator = self.indicators.get(alias["indicator_id"])
            if indicator is None:
                continue
            if (
                indicator["geography"] != alias["geography"]
                or indicator["tier"] != "T1"
            ):
                raise ValueError("reviewed FF alias conflicts with stored catalogue")
            self.reviewed[alias["currency"], name(alias["title"])] = alias
        self.version = (
            RULE_VERSION
            + ":"
            + hashlib.sha256(ALIAS_FILE.read_bytes()).hexdigest()[:16]
        )
        # Stored aliases participate in match() too. Hash their effective,
        # normalized mappings so catalogue expansion retries unmapped captures,
        # independent of row order or duplicate source-specific alias rows.
        stored_aliases = sorted(
            (geography, title, indicator_id, stage)
            for (geography, title), candidates in self.names.items()
            for indicator_id, stage in candidates
        )
        self.version += (
            ":"
            + hashlib.sha256(
                json.dumps(stored_aliases, ensure_ascii=True).encode()
            ).hexdigest()[:16]
        )
        self.releases = IssuerEvents(store)
        # New issuer metadata must revisit earlier not-found/ambiguous skips.
        # Repeated unchanged official fetches leave this fingerprint stable.
        self.version += ":" + self.releases.fingerprint
        # Link retirements change what a snapshot means even when the issuer
        # index returns to an earlier state (event moved away and back): include
        # the journal position so such a snapshot is processed again.
        if store.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='consensus_link_versions'"
        ).fetchone():
            journal = store.conn.execute(
                "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM consensus_link_versions"
            ).fetchone()
            self.version += f":links{journal[0]}-{journal[1]}"

    def match(self, event, title):
        currency = event.get("country")
        if not isinstance(currency, str):
            return None, "country_unknown"
        geos = GEOGRAPHIES.get(paese_iso(currency, title), set())
        reviewed = self.reviewed.get((currency, name(title)))
        if reviewed and reviewed["geography"] in geos:
            return reviewed, None
        candidates = set().union(
            *(self.names.get((geo, name(title)), set()) for geo in geos)
        )
        if len(candidates) != 1:
            return None, "mapping_ambiguous" if candidates else "unmapped"
        indicator_id, stage = candidates.pop()
        indicator = self.indicators[indicator_id]
        if indicator["geography"] not in geos:
            return None, "country_mismatch"
        return {"indicator_id": indicator_id, "stage": stage}, None


def mapping_title(event):
    title = event.get("title")
    if not isinstance(title, str):
        return ""
    match = re.fullmatch(r"(.+?)\s*\(([^()]+)\)", title)
    if match and any(
        explicit_period(match[2], freq) for freq in ("M", "Q", "A", "W", "D")
    ):
        return match[1]
    return title


def event_clock(event, zone):
    """The FF wall clock defaults to US Eastern, never the machine timezone."""
    raw = event.get("date")
    if not isinstance(raw, str):
        return None
    try:
        instant, precision = timestamp(raw, assumed_timezone="America/New_York")
        if precision == "date_only":
            return instant[:10]  # a bare date is a label, not a midnight instant
        return datetime.fromisoformat(instant).astimezone(zone).date().isoformat()
    except (KeyError, ValueError):
        return None


def local_day(instant, precision, zone):
    if precision == "date_only":
        return instant[:10]
    return datetime.fromisoformat(instant).astimezone(zone).date().isoformat()


class IssuerEvents:
    """Exact indicator membership and headline periods from stored issuer rows.

    The base schema has no period/indicator columns on release_events or
    schedule_versions. Issuer readings (including unavailable, scheduled rows)
    supply that membership. As in ReadAPI._cards, earlier periods carried in
    one publication are revisions; the largest stored period is the headline.
    """

    def __init__(self, store):
        snapshot = Snapshot.load(store)
        issuer_rows = [
            e
            for e in snapshot.evidence
            if e.role == "issuer"
            and not e.legacy
            and e.reading_kind in {"actual", "unavailable"}
        ]
        schedules = [
            s
            for s in snapshot.schedules
            if s["release_event_id"] in snapshot.events
            and s["source_id"] == snapshot.events[s["release_event_id"]]["issuer_id"]
        ]
        snapshot = replace(snapshot, evidence=issuer_rows, schedules=schedules)
        latest = latest_schedule(snapshot)
        groups = defaultdict(list)
        for row in issuer_rows:
            if row.event_id in snapshot.events:
                groups[row.indicator_id, row.event_id].append(row)
        self.by_indicator = defaultdict(list)
        for (indicator_id, event_id), rows in sorted(groups.items()):
            event = snapshot.events[event_id]
            info = snapshot.indicators[indicator_id]
            if event["issuer_id"] != info["issuer_id"]:
                continue
            rows = [
                e
                for e in rows
                if e.reference_period
                and e.stage == event["estimate_stage"]
                and explicit_period(
                    e.reference_period, info["frequency"], info["geography"]
                )
            ]
            if not rows:
                continue
            schedule = latest.get(event_id)
            zones = {
                e.issuer_timezone
                for e in snapshot.evidence_by_event.get(event_id, [])
                if e.issuer_timezone
            }
            if schedule and schedule["issuer_timezone"]:
                zones.add(schedule["issuer_timezone"])
            if len(zones) > 1:
                continue  # conflicting issuer metadata cannot certify a day
            zone = event_timezone(
                snapshot,
                event_id,
                None,
                "schedule" if schedule else "issuer_publication",
            )
            if zone is None and not zones:
                zone_name = ISSUER_ZONES.get(info["issuer_id"])
                zone = ZoneInfo(zone_name) if zone_name else None
            if zone is None:
                continue
            actual, actual_precision, basis = event_timing(snapshot, event_id)
            clocks = []
            if actual and basis == "issuer_publication":
                clocks.append((actual, actual_precision, "actual"))
            if schedule:
                clocks.append(
                    (schedule["expected_at"], schedule["precision"], "scheduled")
                )
            if not clocks:
                continue

            def clock_day(clock):
                instant, precision, basis = clock
                # Saved date-only schedules use a UTC issuer-midnight lower
                # bound; a bare stored date remains a calendar label.
                if (
                    basis == "scheduled"
                    and precision == "date_only"
                    and schedule["timestamp_precisions"]["expected_at"] == "exact"
                ):
                    precision = "exact"
                return local_day(instant, precision, zone)

            # First exact issuer publication, then exact official schedule;
            # date-only clocks remain conservative issuer-local lower bounds.
            bound, precision, cutoff_basis = next(
                (clock for clock in clocks if clock[1] == "exact"), clocks[0]
            )
            if precision == "date_only":
                day = clock_day((bound, precision, cutoff_basis))
                bound = utc(
                    datetime.fromisoformat(day).replace(tzinfo=zone).isoformat()
                )
            self.by_indicator[indicator_id].append(
                dict(
                    event=event,
                    period=max(e.reference_period for e in rows),
                    zone=str(zone),
                    dates=sorted({clock_day(clock) for clock in clocks}),
                    cutoff=bound,
                    precision=precision,
                    basis=cutoff_basis,
                )
            )
        self.fingerprint = hashlib.sha256(
            json.dumps(self.by_indicator, sort_keys=True).encode()
        ).hexdigest()[:16]

    def match(self, indicator_id, event):
        candidates = [
            r
            for r in self.by_indicator.get(indicator_id, [])
            if event_clock(event, ZoneInfo(r["zone"])) in r["dates"]
        ]
        if len(candidates) != 1:
            return (
                None,
                "release_event_ambiguous" if candidates else "release_event_not_found",
            )
        return candidates[0], None


def title_stage(title):
    stages = {
        "final": "final",
        "flash": "flash",
        "prelim": "preliminary",
        "preliminary": "preliminary",
        "advance": "advance",
        "second": "second",
        "2nd": "second",
        "third": "third",
        "3rd": "third",
    }
    named = {
        stage
        for word, stage in stages.items()
        if re.search(r"\b" + word + r"\b", title.casefold())
    }
    return named


def prepare(store, catalogue, event, captured_at, snapshot_hash):
    title = mapping_title(event)
    mapping, reason = catalogue.match(event, title)
    if reason:
        return False, reason, None
    indicator = catalogue.indicators[mapping["indicator_id"]]
    if event.get("forecast") in (None, ""):
        return True, "forecast_missing", None
    if captured_at is None:
        return True, "capture_time_not_in_manifest", None
    linked, reason = catalogue.releases.match(indicator["id"], event)
    if reason:
        return True, reason, None
    release = ReleaseEvent(**linked["event"])
    period, stage = linked["period"], release.estimate_stage
    if stage in ("unknown", "", None):
        return True, "stage_unknown", None
    if mapping.get("period_since") and period < mapping["period_since"]:
        return True, "geography_period_mismatch", None
    expected = title_stage(title)
    if mapping["stage"] not in ("unknown", "", None):
        expected.add(mapping["stage"])
    if event.get("estimate_stage"):
        expected.add(event["estimate_stage"])
    if any(s != stage for s in expected):
        return True, "stage_conflict", None
    text = str(event["forecast"]).strip()
    # Forecast cells must contain a complete numeric print in the mapped unit.
    percent = indicator["unit"] in ("%", "percent")
    pattern = r"[+-]?\d+(?:\.\d+)?%" if percent else r"[+-]?\d+(?:\.\d+)?[KMB]?"
    if not re.fullmatch(pattern, text, re.IGNORECASE):
        return True, "forecast_format_or_unit_unknown", None
    try:
        value = parse_number(
            text, target_scale=indicator["scale"], allow_bare_magnitudes=True
        )
    except ValueError:
        return True, "forecast_format_or_unit_unknown", None
    reading = ConsensusReading(
        provider=PROVIDER,
        indicator_id=indicator["id"],
        release_event_id=release.id,
        measure=indicator["measure"],
        reference_period=period,
        estimate_stage=stage,
        value=value,
        unit=indicator["unit"],
        scale=indicator["scale"],
        captured_at=captured_at,
        release_cutoff_at=linked["cutoff"],
        cutoff_precision=linked["precision"],
        cutoff_basis=linked["basis"],
        content_hash=observation_hash(snapshot_hash, event),
    )
    return True, None, (release, reading)


# Reasons that mean the current rules no longer support an observation's
# indicator/event association: an existing live link must be retired.
LINK_REJECTIONS = frozenset(
    {
        "release_event_not_found",
        "release_event_ambiguous",
        "unmapped",
        "mapping_ambiguous",
    }
)


def observation_hash(snapshot_hash, event):
    """The content_hash a ConsensusReading built from this event would carry."""
    return hashlib.sha256(
        (snapshot_hash + json.dumps(event, sort_keys=True, ensure_ascii=True)).encode()
    ).hexdigest()


def latest_link(store, observation_key):
    return store.conn.execute(
        "SELECT v.consensus_reading_id, v.retired FROM consensus_link_versions v "
        "JOIN consensus_readings r ON r.id=v.consensus_reading_id "
        "WHERE r.provider=? AND r.captured_at=? AND r.content_hash=? "
        "ORDER BY v.id DESC LIMIT 1",
        observation_key,
    ).fetchone()


def journal_link(store, reading_id, *, retired, counts=None):
    if counts is not None:
        counts["links_changed"] += 1
    store.conn.execute(
        "INSERT INTO consensus_link_versions (consensus_reading_id,recorded_at,retired) "
        "VALUES (?,?,?)",
        (reading_id, utc(datetime.now(timezone.utc).isoformat()), int(retired)),
    )


def manifest_hashes(directory):
    result = {}
    path = directory / MANIFEST_NAME
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            if row["outcome"] not in ("written", "unchanged"):
                continue
            filename = row["file"].replace("\\", "/").rsplit("/", 1)[-1]
            result[filename, utc(row["captured_at_utc"])] = row["sha256"]
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    return result


def ingest(store, directory=DEFAULT_RAW_DIR, *, before_snapshot=None, reprocess=False):
    """Backfill once, then atomically checkpoint each new snapshot with its rows.

    Missing-manifest files are diagnosed without promoting their mtime. Their
    ledger key uses an empty capture token; later real manifest evidence creates
    a new key. Failed snapshots are not checkpointed and retry on the next run.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise FileNotFoundError("FF archive directory is unavailable")
    catalogue = Catalogue(store)
    hashes = manifest_hashes(directory)
    summary = dict(
        status="no_change",
        snapshots_processed=0,
        snapshots_unchanged=0,
        readings_added=0,
        links_changed=0,
        events_total=0,
        events_mapped=0,
        events_skipped=0,
        snapshots=[],
        skipped_titles=[],
        failures=[],
    )
    titles = Counter()
    reasons = Counter()
    for snapshot in load_snapshots(directory):
        if before_snapshot:
            before_snapshot()
        moment, path = snapshot
        captured_at = (
            utc(moment.isoformat())
            if snapshot.captured_at_source == "manifest"
            else None
        )
        try:
            body = path.read_bytes()
            digest = hashlib.sha256(body).hexdigest()
            if captured_at and hashes.get((path.name, captured_at)) != digest:
                raise ValueError("manifest hash mismatch")
            key = (path.name, captured_at or "", digest, catalogue.version)
            if (
                not reprocess
                and store.conn.execute(
                    "SELECT 1 FROM consensus_snapshots WHERE snapshot_file=? AND captured_at=? "
                    "AND content_hash=? AND rule_version=?",
                    key,
                ).fetchone()
            ):
                summary["snapshots_unchanged"] += 1
                continue
            events = json.loads(body)
            if not isinstance(events, list) or any(
                not isinstance(e, dict) for e in events
            ):
                raise ValueError("FF snapshot is not an event list")
            counts = dict(
                snapshot_file=path.name,
                snapshot_day=path.name[:10],
                captured_at=captured_at,
                capture_source=snapshot.captured_at_source,
                readings_added=0,
                links_changed=0,
                events_total=len(events),
                events_mapped=0,
                events_skipped=0,
            )
            skipped = Counter()
            snapshot_titles = Counter()
            records = []
            rejected = []
            for event in events:
                mapped, reason, record = prepare(
                    store, catalogue, event, captured_at, digest
                )
                counts["events_mapped"] += int(mapped)
                if reason:
                    counts["events_skipped"] += 1
                    skipped[reason] += 1
                    if reason in LINK_REJECTIONS and captured_at is not None:
                        rejected.append((captured_at, observation_hash(digest, event)))
                    snapshot_titles[
                        event.get("country", "?"), event.get("title", "?"), reason
                    ] += 1
                else:
                    records.append(record)
            with store.batch():
                for release, reading in records:
                    # Preserve the first capture's cutoff metadata if a later
                    # issuer correction changes our event index. The read API
                    # reconciles clocks; reprocessing never rewrites a reading.
                    existing = store.conn.execute(
                        "SELECT id FROM consensus_readings WHERE provider=? AND indicator_id=? "
                        "AND reference_period=? AND estimate_stage=? AND release_event_id=? "
                        "AND captured_at=? AND content_hash=?",
                        (
                            reading.provider,
                            reading.indicator_id,
                            reading.reference_period,
                            reading.estimate_stage,
                            reading.release_event_id,
                            reading.captured_at,
                            reading.content_hash,
                        ),
                    ).fetchone()
                    if existing:
                        reading_id = existing["id"]
                    else:
                        before = store.conn.total_changes
                        reading_id = store.put(reading)
                        counts["readings_added"] += store.conn.total_changes - before
                    observation_key = (
                        reading.provider,
                        reading.captured_at,
                        reading.content_hash,
                    )
                    latest = latest_link(store, observation_key)
                    other = store.conn.execute(
                        "SELECT 1 FROM consensus_readings WHERE provider=? AND captured_at=? "
                        "AND content_hash=? AND id<>? LIMIT 1",
                        (*observation_key, reading_id),
                    ).fetchone()
                    # Journal a live link when the observation moved to another
                    # event, or when a retired observation matches again.
                    if (latest is None and other) or (
                        latest is not None and (latest[0] != reading_id or latest[1])
                    ):
                        journal_link(store, reading_id, retired=False, counts=counts)
                for captured, content_hash in rejected:
                    # A linked observation the current rules no longer place on a
                    # unique issuer event: retire its live link, keep the raw row.
                    key_ = (PROVIDER, captured, content_hash)
                    live = store.conn.execute(
                        "SELECT id FROM consensus_readings WHERE provider=? AND captured_at=? "
                        "AND content_hash=? ORDER BY id DESC LIMIT 1",
                        key_,
                    ).fetchone()
                    if live is None:
                        continue
                    latest = latest_link(store, key_)
                    if latest is not None and latest[1]:
                        continue
                    journal_link(
                        store,
                        latest[0] if latest is not None else live["id"],
                        retired=True,
                        counts=counts,
                    )
                counts["skip_reasons"] = dict(sorted(skipped.items()))
                store.conn.execute(
                    "INSERT INTO consensus_snapshots VALUES (?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                    (
                        *key,
                        utc(datetime.now(timezone.utc).isoformat()),
                        json.dumps(counts),
                    ),
                )
            summary["snapshots_processed"] += 1
            summary["snapshots"].append(counts)
            for field in (
                "readings_added",
                "links_changed",
                "events_total",
                "events_mapped",
                "events_skipped",
            ):
                summary[field] += counts[field]
            reasons.update(skipped)
            titles.update(snapshot_titles)
        except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
            summary["failures"].append(
                {"snapshot_file": path.name, "error_type": type(exc).__name__}
            )
    summary["skip_reasons"] = dict(sorted(reasons.items()))
    summary["skipped_titles"] = [
        dict(currency=currency, title=title, reason=reason, count=count)
        for (currency, title, reason), count in sorted(
            titles.items(), key=lambda item: (-item[1], tuple(map(str, item[0])))
        )
    ]
    summary["status"] = (
        "failed"
        if summary["failures"]
        else "ok"
        if summary["readings_added"] or summary["links_changed"]
        else "no_change"
    )
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", type=Path, required=True, help="Explicit writable copy/target"
    )
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument(
        "--reprocess",
        action="store_true",
        help="Re-evaluate skips; row inserts remain idempotent",
    )
    args = parser.parse_args(argv)
    from .lease import RunLease

    with RunLease(args.db.resolve()) as lease:
        with open_writer(args.db) as store:
            result = ingest(
                store,
                args.raw_dir,
                before_snapshot=lease.validate,
                reprocess=args.reprocess,
            )
    print(json.dumps(result, indent=2))
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
