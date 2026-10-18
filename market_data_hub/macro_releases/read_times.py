"""Tolerant timestamp boundary for stored evidence; write contracts stay strict."""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .models import utc


def timestamp(value, *, assumed_timezone=None):
    """Return sortable UTC and honest precision, without using the host zone.

    Unknown naive wall times retain only their date. Midnight is a date bucket,
    not a claim about the release instant. Only a documented zone can retain
    the wall time, labelled assumed_tz rather than exact.
    """
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO string")
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if len(value.strip()) == 10 or (parsed.tzinfo is None and not assumed_timezone):
        return utc(parsed.date().isoformat() + "T00:00:00Z"), "date_only"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(assumed_timezone))
        precision = "assumed_tz"
    else:
        precision = "exact"
    return utc(parsed.isoformat()), precision


def normalize(row, fields):
    """Copy a stored row, retaining precision for each non-null timestamp."""
    row = dict(row)
    precisions = {}
    for name, zone in fields.items():
        if row.get(name) is not None:
            try:
                row[name], precisions[name] = timestamp(row[name], assumed_timezone=zone)
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                raise ValueError(f"invalid {name}") from exc
    row["timestamp_precisions"] = precisions
    return row


def known_by(row, field, cutoff):
    """Date-only knowledge is available only once the entire day has elapsed."""
    instant = row[field]
    if row["timestamp_precisions"].get(field) == "date_only":
        day = datetime.fromisoformat(instant).date()
        instant = datetime.combine(day, time.max, tzinfo=timezone.utc).isoformat()
    return instant <= cutoff


def stored_rows(rows, fields, *, known_at, cutoff, limitations, table):
    """Isolate malformed stored metadata as well as malformed evidence."""
    result = []
    for raw in rows:
        try:
            row = normalize(raw, fields)
            if not known_by(row, known_at, cutoff):
                continue
            if table == "schedule_versions":
                if row["timestamp_precisions"]["expected_at"] != "exact":
                    row["precision"] = row["timestamp_precisions"]["expected_at"]
            elif table == "consensus_readings":
                if row["timestamp_precisions"].get("release_cutoff_at", "exact") != "exact":
                    row["cutoff_precision"] = row["timestamp_precisions"]["release_cutoff_at"]
            elif table == "collect_run_families":
                # Fetch bounds are dates/months, never arbitrary wall times.
                row["fetch_start"] = timestamp(row["fetch_since"] + "-01")[0]
                end, _ = timestamp(row["fetch_until"])
                row["fetch_end"] = (datetime.fromisoformat(end) + timedelta(days=1) -
                                    timedelta(microseconds=1)).isoformat(timespec="microseconds")
            result.append(row)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            key = raw.get("id", raw.get("reading_id", raw.get("run_id", "unknown")))
            limitations.append(f"Skipped {table}:{key}: invalid stored timestamp ({exc}).")
    return result
