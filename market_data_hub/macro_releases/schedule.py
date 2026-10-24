"""Reviewed dates are permissions to collect, never evidence of publication."""
from contextvars import ContextVar
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

RUN_DATE = ContextVar("macro_release_run_date", default=None)
RUN_AT = ContextVar("macro_release_run_instant", default=None)


def run_date():
    return RUN_DATE.get() or date.today().isoformat()


def edition_status(edition, as_of=None):
    return "scheduled" if edition > (as_of or run_date()) else "due"


def due(editions, as_of=None):
    return {d: metadata for d, metadata in editions.items()
            if edition_status(d, as_of) == "due"}


def release_instant(edition, clock, zone):
    """A reviewed wall clock is exact; an unknown clock is a local-day bound."""
    from .publication_times import local_release
    return local_release(edition, clock or "00:00", zone)["published_at"]


def edition_due(family, edition, *, as_of=None, at=None):
    """Known issuer release instants, including DST, must have arrived.

    Without an announced clock (BOJ), the issuer date permits fetching only;
    a document still has to prove publication independently.
    """
    metadata = family_editions(family).get(edition)
    if metadata is None:
        return True  # unlisted discoveries still require publication evidence
    zone = family_zone(family)
    moment = at or RUN_AT.get() or datetime.now(timezone.utc)
    if edition > (as_of or run_date()):
        return False
    if metadata[2] is None:
        return edition <= moment.astimezone(ZoneInfo(zone)).date().isoformat()
    return datetime.fromisoformat(release_instant(edition, metadata[2], zone)) <= moment


def family_zone(family):
    from .policy_editions import ZONES
    if family in ZONES:
        return ZONES[family]
    if family.startswith("uk_"):
        return "Europe/London"
    if family.startswith("ea_"):
        return "Europe/Brussels" if family in {"ea_hicp", "ea_gdp"} else "Europe/Luxembourg"
    return "America/New_York"


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
