"""Statement-only policy decisions. No price feed or news-derived decisions."""
from datetime import datetime, timezone
from decimal import Decimal
from fractions import Fraction
import re
from urllib.parse import urlsplit

from .adapters import Observation
from .catalogue import CENTRAL_BANKS
from .http import FetchError
from .policy_editions import EDITIONS, ZONES
from .publication_times import local_release
from .schedule import RUN_AT, run_date
from .tier1_publications import require


def number(printed):
    """Issuer decimals and mixed fractions; never round a policy range."""
    parts = printed.strip().split("-")
    return float(sum((Fraction(p) for p in parts), Fraction()))


def change(current, previous):
    return float((Decimal(str(current)) - Decimal(str(previous))) * 100)


def reviewed(family, edition):
    if edition not in EDITIONS[family]:
        raise FetchError("Unscheduled or unknown policy decision needs review")
    return EDITIONS[family][edition]


def due_editions(family, since):
    """Gate in issuer time, including DST. Same-day publication is not assumed."""
    instant = RUN_AT.get() or datetime.now(timezone.utc)
    out = []
    for edition, (_, _, clock) in EDITIONS[family].items():
        if edition[:7] < since or edition > run_date():
            continue
        if clock is not None:
            stamp = datetime.fromisoformat(local_release(edition, clock, ZONES[family])["published_at"])
            if stamp > instant:
                continue
        # BOJ has no announced instant: fetching a document is allowed on the
        # meeting date, but its publication must independently identify the day.
        out.append(edition)
    return out


def observation(document, family, edition, rates, vote_split, *, printed=None,
                time=None, time_missing_reason=None, evidence=(), method="html"):
    _, stage, clock = reviewed(family, edition)
    clock = time or clock
    measure = CENTRAL_BANKS[family][0]
    timing = local_release(edition, clock or "00:00", ZONES[family])
    facts = dict(decision_date=edition, rates=rates, vote_split=vote_split,
                 source_url=document.url, published_at=timing["published_at"] if clock else None,
                 publication_precision="exact" if clock else "date_only",
                 time_missing_reason=time_missing_reason, supporting_sources=list(evidence))
    headline = next(iter(rates.values()))
    value = headline.get("rate", headline.get("upper"))
    return Observation(measure.id, family, edition, value, printed or str(value),
        measure.id + ":decision", measure.issuer_id, document, edition, **timing,
        stage=stage, precision="exact" if clock else "date_only", method=method,
        provider_vintage=edition, policy_decision=facts)


def require_origin(document, host):
    if urlsplit(document.url).scheme != "https" or urlsplit(document.url).hostname != host:
        raise FetchError("Policy statement issuer origin needs review")


class PolicyAdapter:
    def __init__(self, client):
        self.client = client

    def fetch(self, families, since):
        from . import fomc, ecb_policy
        adapters = {"fomc": fomc, "ecb_policy": ecb_policy}
        return [o for family in families for o in adapters[family].fetch(self.client, family, since)]
