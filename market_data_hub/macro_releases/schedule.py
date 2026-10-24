"""Reviewed dates are permissions to collect, never evidence of publication."""
from contextvars import ContextVar
from datetime import date

RUN_DATE = ContextVar("macro_release_run_date", default=None)


def run_date():
    return RUN_DATE.get() or date.today().isoformat()


def edition_status(edition, as_of=None):
    return "scheduled" if edition > (as_of or run_date()) else "due"


def due(editions, as_of=None):
    return {d: metadata for d, metadata in editions.items()
            if edition_status(d, as_of) == "due"}


def family_editions(family):
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
