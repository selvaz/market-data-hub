"""Issuer-only Latin American publications; no keys or index-derived rates."""
from .adapters import Observation
from .catalogue import INDICATORS
from .http import FetchError
from .latam_editions import ZONES, reviewed
from .publication_times import local_release
from .schedule import due, mark_pending, publication_grace, run_date


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
            mark_pending(family, edition, "not_yet_published" if
                str(exc) == provider + ": HTTP 404" and publication_grace(family, edition) else "needs_review")
            continue
        out.extend(rows)
    return out


def observation(doc, family, edition, indicator, printed, *, method, policy=None):
    period, stage, clock = reviewed(family, edition)
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
        if run_date() > "2026-12-31":
            raise FetchError("Latin American edition horizon exhausted; needs review")
        from importlib import import_module
        modules = dict(bcb_policy="bcb_policy", banxico_policy="banxico_policy", mx_cpi="inegi_cpi")
        return [row for family in families for row in import_module(
            "." + modules[family], __package__).fetch(self.client, family, since)]
