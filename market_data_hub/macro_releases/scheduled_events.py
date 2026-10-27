"""Reviewed calendars as nonnumeric membership, never publications.

The schema represents event/indicator/period membership with readings. Calendar
readings are unavailable, unverified, and have no publication timestamp or value.
They cannot produce a vintage or resolve an actual. Normal adapter identities
ensure later official prints land on exactly the same events.
"""
from datetime import datetime, timezone
import hashlib
import json

from .catalogue import FAMILIES, INDICATORS, hicp_indicator
from .models import Reading, ReleaseEvent, ScheduleVersion, SourceSeries, utc
from .read_api import indicators_for_stage
from .publication_times import local_release
from .schedule import family_editions, issuer_zone, release_instant

PARSER_VERSION = "reviewed-calendar:m2b-v1"


def calendar_source(family):
    from .asia_editions import SCHEDULES
    if family in SCHEDULES:
        return SCHEDULES[family]
    from . import dol, housing_editions, policy_editions, tier1_editions, us_editions
    from .ons_rates import EDITIONS as UK
    if family in housing_editions.SCHEDULES:
        return housing_editions.SCHEDULES[family]
    if family in policy_editions.SCHEDULES:
        return policy_editions.SCHEDULES[family]
    if family in tier1_editions.SCHEDULES:
        return tier1_editions.SCHEDULES[family]
    if family in us_editions.SCHEDULES:
        return us_editions.SCHEDULES[family]
    if family in UK:
        keyword = "consumer+price+inflation" if family == "uk_cpi" else "labour+market"
        return "https://www.ons.gov.uk/releasecalendar?keywords=" + keyword + "&release-type=type-upcoming"
    if family == "us_jobless_claims":
        return dol.SCHEDULE
    if family.startswith("ea_"):
        return tier1_editions.EUROSTAT_SCHEDULE
    return us_editions.BEA_SCHEDULE if family == "us_gdp" else us_editions.BLS_SCHEDULE


def persist_scheduled_events(store, *, recorded_at=None):
    """Seed listed editions, preserving existing issuer schedules and rows.

    recorded_at is when this process learned the calendar, never a backdated
    capture or publication time. Missing calendar entries remain missing.
    """
    now = utc(recorded_at or datetime.now(timezone.utc).isoformat())
    counts = dict(events_added=0, schedules_added=0, memberships_added=0)
    schedules = {r[0] for r in store.conn.execute(
        "SELECT s.release_event_id FROM schedule_versions s JOIN release_events e "
        "ON e.id=s.release_event_id WHERE s.source_id=e.issuer_id")}
    memberships = {(r[0], r[1]) for r in store.conn.execute(
        "SELECT release_event_id, indicator_id FROM readings WHERE source_id=issuer_id "
        "AND reading_kind IN ('actual','unavailable')")}
    events = {r[0] for r in store.conn.execute("SELECT id FROM release_events")}
    with store.batch():
        for family in FAMILIES:
            for edition, (period, stage, clock) in sorted(family_editions(family).items()):
                zone = issuer_zone(family)
                measures = ([hicp_indicator(period, basis) for basis in ("mm", "yy")]
                    if family == "ea_hicp" else
                    [INDICATORS[i] for i in sorted(indicators_for_stage(family, stage))])
                issuer = measures[0].issuer_id
                event_id = ":".join((issuer, family, stage, edition))
                store.put(ReleaseEvent(id=event_id, issuer_id=issuer, family=family,
                                       estimate_stage=stage, edition=edition))
                counts["events_added"] += int(event_id not in events)
                if event_id not in schedules:
                    # Use an already stored issuer zone where available. Zone
                    # aliases such as Brussels/Luxembourg must not conflict.
                    zones = {r[0] for r in store.conn.execute(
                        "SELECT DISTINCT issuer_timezone FROM readings WHERE release_event_id=? "
                        "AND source_id=issuer_id AND issuer_timezone IS NOT NULL", (event_id,))}
                    if len(zones) == 1:
                        zone = zones.pop()
                    elif len(zones) > 1:
                        continue  # conflicting metadata cannot certify a schedule
                    instant = release_instant(edition, family=family)
                    # Unknown clocks retain only a local-day lower bound, never
                    # an exact release permission or evidence of publication.
                    expected_at = (instant.isoformat() if instant is not None else
                                   local_release(edition, "00:00", zone)["published_at"])
                    store.put(ScheduleVersion(release_event_id=event_id,
                        expected_at=expected_at, issuer_timezone=zone,
                        precision="exact" if clock else "date_only", source_id=issuer, recorded_at=now))
                    counts["schedules_added"] += 1
                payload = dict(family=family, edition=edition, reference_period=period,
                               estimate_stage=stage, clock=clock, issuer_timezone=zone,
                               calendar_url=calendar_source(family))
                body = json.dumps(payload, sort_keys=True)
                digest = hashlib.sha256(body.encode()).hexdigest()
                for measure in measures:
                    store.put(measure)
                    if (event_id, measure.id) in memberships:
                        continue
                    series_key = "reviewed-calendar:" + measure.id
                    store.put(SourceSeries(id=issuer + ":" + series_key,
                        indicator_id=measure.id, source_id=issuer, issuer_id=issuer,
                        distributor_id=issuer, source_key=series_key, evidence_role="issuer"))
                    store.put(Reading(indicator_id=measure.id, release_event_id=event_id,
                        source_id=issuer, series_key=series_key,
                        period_key=period, reference_period=period, edition=edition,
                        estimate_stage=stage, reading_kind="unavailable", unit=measure.unit,
                        scale=measure.scale, issuer_id=issuer, distributor_id=issuer,
                        evidence_origin_id=issuer + ":reviewed-calendar", first_seen_at=now,
                        retrieved_at=now, content_hash=digest, parser_version=PARSER_VERSION,
                        extraction_method="reviewed_calendar", verification_status="unverified",
                        url=payload["calendar_url"], raw_payload_json=body))
                    counts["memberships_added"] += 1
    return counts
