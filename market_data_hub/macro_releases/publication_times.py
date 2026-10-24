"""Issuer-local release times, retaining the zone and local offset as evidence."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def new_york_release(edition, time="08:30"):
    local = datetime.fromisoformat(edition + "T" + time + ":00").replace(tzinfo=ZoneInfo("America/New_York"))
    return {"published_at": local.astimezone(timezone.utc).isoformat(),
            "issuer_local_time": local.isoformat(), "issuer_timezone": "America/New_York"}
