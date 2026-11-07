"""Edition-bound official Asian publications, using only issuer-printed rates."""
from dataclasses import replace
from urllib.parse import parse_qsl, urlsplit, urlunsplit
from zipfile import BadZipFile
from .adapters import Observation
from .asia_editions import EDITIONS, ZONES, reviewed
from .catalogue import INDICATORS
from .http import FetchError
from .publication_times import local_release
from .schedule import due, get_edition, mark_pending, publication_grace, run_date


def in_window(period, since):
    end = period[:4] + "-" + f"{int(period[-1]) * 3:02d}" if "-Q" in period else period
    return end >= since


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
    urls = dict(archives)
    cache = {}
    from .asia_discovery import publication_url
    for edition, (period, _, _) in due(EDITIONS[family], family=family).items():
        if not in_window(period, since):
            continue
        try:
            url = publication_url(client, family, edition, urls.get(edition), cache)
            if url is None:
                mark_pending(family, edition, "not_yet_published" if publication_grace(family, edition)
                             else "scheduled_unreviewed")
                continue
            urls[edition] = url
            doc = get_edition(client, provider, url, family, edition)
            if doc is None:
                continue
            rows = parse(doc, family, since, archives=urls)
            if not rows or {r.edition for r in rows} != {edition}:
                raise FetchError("Asia requested publication edition needs review")
        except (FetchError, ValueError, KeyError, TypeError, IndexError, BadZipFile) as exc:
            # Reject this edition without losing successfully validated editions.
            # Transport/authentication failures still stop the issuer family.
            if isinstance(exc, FetchError) and str(exc).startswith(provider + ": ") and str(exc) != provider + ": HTTP 404":
                raise
            mark_pending(family, edition, "not_yet_published" if
                str(exc) == provider + ": HTTP 404" and publication_grace(family, edition) else "needs_review")
            continue
        out.extend(rows)
    return out


class AsiaAdapter:
    def __init__(self, client):
        self.client = client

    def fetch(self, families, since):
        from . import nbs_macro, japan_macro
        client = self.client
        class PublicClient:
            def get(self, provider, url, params=None):
                requested = url
                parts = urlsplit(url)
                if parts.query:
                    params = dict(parse_qsl(parts.query), **(params or {}))
                    url = urlunsplit(parts._replace(query=""))
                doc = client.get(provider, url, params)
                return replace(doc, url=requested) if parts.query else doc
        return [row for family in families for row in (
            japan_macro if family.startswith("jp_") else nbs_macro).fetch(PublicClient(), family, since)]
