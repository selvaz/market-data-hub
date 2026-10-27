"""Edition-bound official Asian publications, using only issuer-printed rates."""
from .adapters import Observation
from .asia_editions import EDITIONS, ZONES, reviewed
from .catalogue import INDICATORS
from .http import FetchError
from .publication_times import local_release
from .schedule import due, get_edition, run_date


def observation(doc, family, edition, indicator, printed, method="html", *, value=None):
    period, stage, clock = reviewed(family, edition)
    measure = INDICATORS[indicator]
    return Observation(indicator, family, period,
        float(printed.replace(",", "")) if value is None else value, printed,
        indicator + ":published:" + stage, measure.issuer_id, doc, edition,
        **local_release(edition, clock, ZONES[family]), stage=stage,
        precision="exact", method=method, provider_vintage=edition)


def fetch_archives(client, family, since, provider, archives, parse):
    if run_date() > "2026-12-31":
        raise FetchError("Asia issuer reviewed horizon exhausted; needs review")
    out = []
    for edition, (period, _, _) in due(EDITIONS[family], family=family).items():
        if period < since:
            continue
        if edition not in archives:
            raise FetchError("Asia due publication archive missing; needs review")
        doc = get_edition(client, provider, archives[edition], family, edition)
        if doc is None:
            continue
        rows = parse(doc, family, since)
        if not rows or {r.edition for r in rows} != {edition}:
            raise FetchError("Asia requested publication edition needs review")
        out.extend(rows)
    return out


class AsiaAdapter:
    def __init__(self, client):
        self.client = client

    def fetch(self, families, since):
        from . import nbs_macro
        return [row for family in families for row in nbs_macro.fetch(self.client, family, since)]
