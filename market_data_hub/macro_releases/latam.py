"""Issuer-only Latin American publications; no keys or index-derived rates."""
from .adapters import Observation
from .catalogue import INDICATORS
from .http import FetchError
from .latam_editions import EDITIONS, ZONES, reviewed
from .publication_times import local_release
from .schedule import due, run_date


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
