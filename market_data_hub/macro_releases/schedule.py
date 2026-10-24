"""Reviewed dates are permissions to collect, never evidence of publication."""
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

RUN_DATE = ContextVar("macro_release_run_date", default=None)
RUN_AT = ContextVar("macro_release_run_instant", default=None)
PENDING_EDITIONS = ContextVar("macro_release_pending_editions", default=None)
TIMED_FAMILIES = frozenset(("us_new_home_sales", "us_umich_sentiment", "ea_ec_surveys"))


def run_date():
    return RUN_DATE.get() or date.today().isoformat()


def run_instant(as_of=None):
    value = as_of if as_of is not None else RUN_AT.get()
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if value.tzinfo is None and len(str(as_of)) == 10:
            value = value.replace(tzinfo=timezone.utc)
    if value.tzinfo is None:
        raise ValueError("Release collection requires a timezone-aware run instant")
    return value.astimezone(timezone.utc)


def release_instant(edition, metadata=None, *, family):
    metadata = metadata or family_editions(family)[edition]
    zone = "Europe/Luxembourg" if family.startswith("ea_") else "America/New_York"
    local = datetime.fromisoformat(edition + "T" + metadata[2]).replace(tzinfo=ZoneInfo(zone))
    return local.astimezone(timezone.utc)


def edition_status(edition, as_of=None, *, metadata=None, family=None):
    if family in TIMED_FAMILIES:
        pending = PENDING_EDITIONS.get() or {}
        return "scheduled" if ((family, edition) in pending or
            run_instant(as_of) < release_instant(edition, metadata, family=family)) else "due"
    return "scheduled" if edition > (as_of or run_date()) else "due"


def due(editions, as_of=None, *, family=None):
    return {d: metadata for d, metadata in editions.items()
            if edition_status(d, as_of, metadata=metadata, family=family) == "due"}


def publication_grace(family, edition):
    age = run_instant() - release_instant(edition, family=family)
    return timedelta(0) <= age < timedelta(hours=6)


def mark_pending(family, edition):
    pending = PENDING_EDITIONS.get()
    if pending is not None:
        pending[family, edition] = "not_yet_published"


def get_edition(client, provider, url, family, edition):
    """Match #121: only a newly due edition-specific 404 gets the 6-hour grace."""
    from .http import FetchError
    try:
        return client.get(provider, url)
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
    from . import bea, bls
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
    return {d: (p, "regular", "08:30") for (f, p), d in bls.EDITIONS.items() if f == family}
