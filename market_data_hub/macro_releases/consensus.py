"""Offline FF forecasts with exact catalogue mapping and manifest provenance.

An event's release date is never its reference period. Monthly frequency alone
cannot establish publication lag (UK GDP and US CPI have different lags).
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
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
from .store import open_writer
from .worklist_periods import explicit_period

PROVIDER = "ff_forecast"
RULE_VERSION = "ff-consensus-v1"
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


def event_period(event, title, indicator):
    """Only explicit, fully dated periods; strip only a recognized final label."""
    texts = [event[key] for key in ("reference_period", "period") if event.get(key)]
    match = re.fullmatch(r"(.+?)\s*\(([^()]+)\)", title)
    if match and explicit_period(
        match[2], indicator["frequency"], indicator["geography"]
    ):
        texts.append(match[2])
    periods = {
        explicit_period(t, indicator["frequency"], indicator["geography"])
        for t in texts
    }
    if len(periods) != 1 or None in periods:
        return None
    return periods.pop()


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


def event_clock(event, indicator):
    """FF's offset-aware date is a schedule, never an actual publication time."""
    raw = event.get("date")
    if not isinstance(raw, str):
        return None, None, None
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        zone_name = ISSUER_ZONES.get(indicator["issuer_id"])
        zone = ZoneInfo(zone_name) if zone_name else None
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            return (
                (raw, utc(moment.replace(tzinfo=zone).isoformat()), "date_only")
                if zone
                else (raw, None, None)
            )
        if moment.tzinfo is None:
            return None, None, None
        # An explicit offset proves the schedule instant even when its issuer
        # zone is absent. Do not guess the edition's issuer-local date.
        edition = moment.astimezone(zone).date().isoformat() if zone else None
        return edition, utc(raw), "exact"
    except (KeyError, ValueError):
        return None, None, None


def release_event(store, mapping, indicator, edition, period):
    family = mapping.get("family")
    if family and edition is not None:
        event_id = ":".join((indicator["issuer_id"], family, mapping["stage"], edition))
        return ReleaseEvent(
            id=event_id,
            issuer_id=indicator["issuer_id"],
            family=family,
            estimate_stage=mapping["stage"],
            edition=edition,
        )
    # Generic catalogue aliases need an already linked exact edition. Never
    # create a new family or borrow a different reference period's event.
    rows = store.conn.execute(
        "SELECT DISTINCT e.* FROM release_events e JOIN readings r ON r.release_event_id=e.id "
        "WHERE r.indicator_id=? AND r.reference_period=? AND r.estimate_stage=? "
        "AND e.issuer_id=? AND (? IS NULL OR e.edition=?) "
        "AND (? IS NULL OR e.family=?)",
        (
            indicator["id"],
            period,
            mapping["stage"],
            indicator["issuer_id"],
            edition,
            edition,
            family,
            family,
        ),
    ).fetchall()
    return ReleaseEvent(**dict(rows[0])) if len(rows) == 1 else None


def cutoff(store, event_id, captured_at, ff_cutoff, ff_precision):
    # Prefer the event schedule known at capture. Otherwise the FF event date
    # itself is explicit schedule evidence. No actual-time inference from values.
    row = store.conn.execute(
        "SELECT * FROM schedule_versions WHERE release_event_id=? AND recorded_at<=? "
        "ORDER BY recorded_at DESC,id DESC LIMIT 1",
        (event_id, captured_at),
    ).fetchone()
    if row:
        return row["expected_at"], row["precision"], "scheduled"
    return ff_cutoff, ff_precision, "scheduled" if ff_cutoff else "unknown"


def prepare(store, catalogue, event, captured_at, snapshot_hash):
    title = mapping_title(event)
    mapping, reason = catalogue.match(event, title)
    if reason:
        return False, reason, None
    indicator = catalogue.indicators[mapping["indicator_id"]]
    if event.get("forecast") in (None, ""):
        return True, "forecast_missing", None
    period = event_period(event, event.get("title", ""), indicator)
    if period is None:
        return True, "period_unknown", None
    if mapping.get("period_since") and period < mapping["period_since"]:
        return True, "geography_period_mismatch", None
    stage = mapping["stage"]
    if stage in ("unknown", "", None) or event.get("estimate_stage", stage) != stage:
        return True, "stage_unknown_or_conflicting", None
    edition, ff_cutoff, ff_precision = event_clock(event, indicator)
    release = release_event(store, mapping, indicator, edition, period)
    if release is None:
        return (
            True,
            "release_schedule_unknown" if edition is None else "release_event_unlinked",
            None,
        )
    if captured_at is None:
        return True, "capture_time_not_in_manifest", None
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
    bound, precision, basis = cutoff(
        store, release.id, captured_at, ff_cutoff, ff_precision
    )
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
        release_cutoff_at=bound,
        cutoff_precision=precision,
        cutoff_basis=basis,
        content_hash=hashlib.sha256(
            (
                snapshot_hash + json.dumps(event, sort_keys=True, ensure_ascii=True)
            ).encode()
        ).hexdigest(),
    )
    return True, None, (release, reading)


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
                events_total=len(events),
                events_mapped=0,
                events_skipped=0,
            )
            skipped = Counter()
            snapshot_titles = Counter()
            records = []
            for event in events:
                mapped, reason, record = prepare(
                    store, catalogue, event, captured_at, digest
                )
                counts["events_mapped"] += int(mapped)
                if reason:
                    counts["events_skipped"] += 1
                    skipped[reason] += 1
                    snapshot_titles[
                        event.get("country", "?"), event.get("title", "?"), reason
                    ] += 1
                else:
                    records.append(record)
            with store.batch():
                for release, reading in records:
                    store.put(release)
                    before = store.conn.total_changes
                    store.put(reading)
                    counts["readings_added"] += store.conn.total_changes - before
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
        if summary["readings_added"]
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
