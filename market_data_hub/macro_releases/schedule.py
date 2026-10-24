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
