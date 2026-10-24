"""Issuer-local release times, retaining the zone and local offset as evidence."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def new_york_release(edition, time="08:30"):
    return local_release(edition, time, "America/New_York")


def local_release(edition, time, zone):
    local = datetime.fromisoformat(edition + "T" + time + ":00").replace(tzinfo=ZoneInfo(zone))
    return {"published_at": local.astimezone(timezone.utc).isoformat(),
            "issuer_local_time": local.isoformat(), "issuer_timezone": zone}
