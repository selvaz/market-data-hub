# -*- coding: utf-8 -*-
"""Time helpers — one definition of "a timestamp" for the whole ecosystem.

The rule across every repo is already *de facto* the same (UTC, ISO-8601); this
module makes it explicit and shared so nobody re-implements timezone handling.
Everything is timezone-aware UTC. Naive datetimes are assumed to be UTC and
made aware, never silently localised.
"""
from __future__ import annotations

from datetime import datetime, timezone


def now_utc() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def ensure_utc(dt: datetime) -> datetime:
    """Return ``dt`` as timezone-aware UTC.

    A naive datetime is *assumed* to already be UTC and tagged as such; an aware
    datetime in another zone is converted.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_iso(dt: datetime) -> str:
    """Serialise a datetime to a canonical UTC ISO-8601 string."""
    return ensure_utc(dt).isoformat()


def parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 string into a timezone-aware UTC datetime.

    A trailing ``Z`` (Zulu) is accepted as ``+00:00``.
    """
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return ensure_utc(datetime.fromisoformat(text))
