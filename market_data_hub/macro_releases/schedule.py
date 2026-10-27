"""Reviewed release instants permit collection; they never prove publication."""
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

RUN_INSTANT = ContextVar("macro_release_run_instant", default=None)
PENDING_EDITIONS = ContextVar("macro_release_pending_editions", default=None)


def run_instant(as_of=None):
    value = as_of if as_of is not None else RUN_INSTANT.get()
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, str):
        # A date-only explicit cutoff means the start of that UTC date.
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if len(str(as_of or RUN_INSTANT.get())) == 10:
            value = value.replace(tzinfo=timezone.utc)
    if value.tzinfo is None:
        raise ValueError("Release collection requires a timezone-aware run instant")
    return value.astimezone(timezone.utc)


def run_date():
    return run_instant().date().isoformat()


def issuer_zone(family):
    from .asia_editions import ZONES as ASIA_ZONES
    if family in ASIA_ZONES:
        return ASIA_ZONES[family]
    from .housing_editions import ZONES as HOUSING_ZONES
    if family in HOUSING_ZONES:
        return HOUSING_ZONES[family]
    from .policy_editions import ZONES
    if family in ZONES:
        return ZONES[family]
    return ("Europe/London" if family and family.startswith("uk_") else
            "Europe/Luxembourg" if family and family.startswith("ea_") else "America/New_York")


def release_instant(edition, metadata=None, *, family=None):
    if metadata is None:
        metadata = family_editions(family).get(edition, ())
    time = metadata[2] if len(metadata) >= 3 else "08:30"
    if time is None:
        return None  # Permission on the meeting date is not an exact publication time.
    local = datetime.fromisoformat(edition + "T" + time).replace(tzinfo=ZoneInfo(issuer_zone(family)))
    return local.astimezone(timezone.utc)


def edition_status(edition, as_of=None, *, metadata=None, family=None):
    pending = PENDING_EDITIONS.get() or {}
    instant = run_instant(as_of)
    release = release_instant(edition, metadata, family=family)
    before = (instant < release if release is not None else
              instant.astimezone(ZoneInfo(issuer_zone(family))).date().isoformat() < edition)
    return "scheduled" if (family, edition) in pending or before else "due"


def due(editions, as_of=None, *, family=None):
    return {d: metadata for d, metadata in editions.items()
            if edition_status(d, as_of, metadata=metadata, family=family) == "due"}


def publication_grace(family, edition):
    release = release_instant(edition, family=family)
    if release is None:
        return False
    age = run_instant() - release
    return timedelta(0) <= age < timedelta(hours=6)


def mark_pending(family, edition):
    pending = PENDING_EDITIONS.get()
    if pending is not None:
        pending[family, edition] = "not_yet_published"


def get_edition(client, provider, url, family, edition, params=None):
    """Only an edition-specific, newly due 404 has the publication-delay grace."""
    from .http import FetchError
    try:
        return client.get(provider, url, params)
    except FetchError as exc:
        if str(exc) != provider + ": HTTP 404" or not publication_grace(family, edition):
            raise
        mark_pending(family, edition)
        return None


def current_edition_allowed(family, edition):
    """Rolling pages may retain only the immediately previous edition in grace."""
    from .http import FetchError
    calendar = family_editions(family)
    eligible = due(calendar, family=family)
    if edition not in eligible:
        return False  # A reviewed publication exposed before its release instant.
    expected = max(eligible)
    if edition == expected:
        return True
    previous = max((d for d in eligible if d < expected), default=None)
    if edition == previous and publication_grace(family, expected):
        mark_pending(family, expected)
        return True
    raise FetchError("Issuer current publication is stale; needs review")


def family_editions(family):
    from .asia_editions import EDITIONS as ASIA
    if family in ASIA:
        return ASIA[family]
    from .housing_editions import EDITIONS as HOUSING
    if family in HOUSING:
        return HOUSING[family]
    from .policy_editions import EDITIONS as POLICY
    if family in POLICY:
        return POLICY[family]
    from .tier1_editions import EDITIONS as REMAINING
    if family in REMAINING:
        return REMAINING[family]
    from .us_editions import EDITIONS as MONTHLY
    from .ons_rates import EDITIONS as UK
    from .eurostat_gdp import EDITIONS as GDP
    from .dol import EDITIONS as CLAIMS
    from . import bea, bls, eurostat
    if family in MONTHLY:
        return MONTHLY[family]
    if family in UK:
        return UK[family]
    if family == "ea_gdp":
        return GDP
    if family == "us_jobless_claims":
        return CLAIMS
    if family == "us_gdp":
        return {d: (*meta, "08:30") for d, meta in bea.EDITIONS.items()}
    if family == "ea_hicp":
        # Headline and core HICP share the same reviewed Eurostat editions.
        result = {d: (p, s, "11:00") for (p, s), d in eurostat.PUBLICATIONS.items()}
        result.update(REMAINING["ea_core_hicp"])
        return result
    return {d: (p, "regular", "08:30") for (f, p), d in bls.EDITIONS.items() if f == family}
