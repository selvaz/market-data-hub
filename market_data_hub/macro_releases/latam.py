"""Issuer-only Latin American publications; no keys or index-derived rates."""
import re
from datetime import date, timedelta

from .adapters import Observation
from .catalogue import INDICATORS
from .http import FetchError
from .latam_editions import ZONES, reviewed
from .publication_times import local_release
from .schedule import due, mark_pending, note_review, publication_grace, run_date



def fetch_editions(family, since, provider, load):
    """Keep validated editions when another publication is missing or malformed."""
    from .latam_editions import EDITIONS
    out = []
    for edition, (period, _, _) in due(EDITIONS[family], family=family).items():
        if period < since:
            continue
        try:
            rows = load(edition)
            if rows is None:  # The loader has recorded a publication-delay notice.
                continue
            if not rows or {r.edition for r in rows} != {edition}:
                raise FetchError("Latin American requested publication edition needs review")
        except (FetchError, ValueError, KeyError, TypeError, IndexError) as exc:
            # Match Asia's contract: transport/auth failures stop the issuer;
            # edition-specific 404s and schema failures retain other editions.
            if isinstance(exc, FetchError) and str(exc).startswith(provider + ": ") and str(exc) != provider + ": HTTP 404":
                raise
            http_404 = isinstance(exc, FetchError) and str(exc) == provider + ": HTTP 404"
            if http_404 and publication_grace(family, edition):
                mark_pending(family, edition, "not_yet_published")
                continue
            mark_pending(family, edition, "needs_review")
            note_review(family, edition, *review_detail(exc, http_404))
            continue
        out.extend(rows)
    return out


def review_detail(exc, http_404=False):
    """A controlled category and message; never a URL or response body."""
    if http_404:
        return "issuer_http_error", str(exc)
    if isinstance(exc, FetchError):
        return "parser_rejected", re.sub(r"https?://\S+", "<url>", str(exc))[:200]
    return "parser_rejected", type(exc).__name__ + ": issuer document failed validation"


def next_expected_edition(editions):
    """The earliest date the edition after the listed ones can fall due.

    The issuer's own cadence: the last listed edition plus the shortest gap
    between consecutive listed editions. With a single listed edition there
    is no cadence, so the next one is treated as due right after it.
    """
    dates = sorted(date.fromisoformat(e) for e in editions)
    gaps = [b - a for a, b in zip(dates, dates[1:])]
    return dates[-1] + (min(gaps) if gaps else timedelta(days=1))


def calendar_exhausted(family):
    """No listed edition covers the next expected edition date, which is past."""
    from .latam_editions import EDITIONS
    last = max(EDITIONS[family])
    expected = next_expected_edition(EDITIONS[family])
    if date.fromisoformat(run_date()) > expected:
        edition = "after:" + last
        mark_pending(family, edition, "needs_review")
        note_review(family, edition, "calendar_exhausted",
                    f"No reviewed {family} edition after {last}; the next was expected by "
                    f"{expected.isoformat()}; add the issuer calendar")
        return True
    return False


def observation(doc, family, edition, indicator, printed, *, method, policy=None):
    period, stage, clock = reviewed(family, edition)
    if family == "bcb_policy":
        # Its calendar clock gates fetching; the API still prints only a date.
        clock = None
    measure = INDICATORS[indicator]
    timing = local_release(edition, clock or "00:00", ZONES[family])
    if policy is not None:
        policy = dict(policy, decision_date=edition, announcement_date=edition,
            source_url=doc.url, published_at=timing["published_at"] if clock else None,
            publication_precision="exact" if clock else "date_only",
            time_missing_reason=None if clock else "BCB API prints decision date; actual publication clock is absent",
            supporting_sources=[])
    return Observation(indicator, family, period, float(printed.replace(",", ".")), printed,
        indicator + ":published", measure.issuer_id, doc, edition, **timing,
        stage=stage, precision="exact" if clock else "date_only", method=method,
        provider_vintage=edition, policy_decision=policy)


class LatamAdapter:
    def __init__(self, client):
        self.client = client

    def fetch(self, families, since):
        # Year-agnostic: the horizon is each family's reviewed calendar, not a
        # fixed year. Past it, reviewed editions still collect and the missing
        # calendar is reported as a needs-review notice.
        for family in families:
            calendar_exhausted(family)
        from importlib import import_module
        modules = dict(bcb_policy="bcb_policy", banxico_policy="banxico_policy", mx_cpi="inegi_cpi")
        return [row for family in families for row in import_module(
            "." + modules[family], __package__).fetch(self.client, family, since)]
