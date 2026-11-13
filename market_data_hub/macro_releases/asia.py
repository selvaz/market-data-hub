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
    from .jp_discovery import FAMILIES, GDP, discover
    from .asia_editions import DISCOVERED_EDITIONS
    if family not in FAMILIES and run_date() > "2026-12-31":
        raise FetchError("Asia issuer reviewed horizon exhausted; needs review")
    # Do not hit live pages before the first relevant anchored release instant.
    # Once an anchor is relevant (and after the anchor horizon), discovery owns
    # continuation without a future-date ceiling.
    anchored_due = due(EDITIONS[family], family=family)
    if family in FAMILIES and run_date() <= max(EDITIONS[family]) and not any(
            in_window(meta[0], since) for meta in anchored_due.values()):
        return []
    out = []
    urls = dict(archives)
    editions = dict(EDITIONS[family])
    live = (discover(client, family) if family in FAMILIES and
            not getattr(client, "replay_mode", False) else [])
    discovered = {item.edition_date: item for item in live}
    discovery_document = None
    if family == "jp_industrial_production" and live:
        from .jp_discovery import METI
        discovery_document = client.get(provider, METI)
    for item in live:
        editions[item.edition_date] = (item.period, item.stage, item.release_time or "00:00")
        # A generic issuer listing is discovery evidence, not a data file.  In
        # particular, never replace a reviewed e-Stat workbook with HTML.
        listing_only = "stat-search/files" in item.document_url
        if not listing_only:
            urls[item.edition_date] = item.document_url
    token = DISCOVERED_EDITIONS.set({(family, item.edition_date): editions[item.edition_date] for item in live})
    cache = {}
    from .asia_discovery import publication_url
    try:
      for edition, (period, _, _) in due(editions, family=family).items():
        if in_window(period, since):
          try:
            # The live discovery result is authoritative for moving resources;
            # archive resolvers continue to bind CPI/GDP/MOF edition files.
            known = urls.get(edition)
            if family == "jp_gdp" and known == GDP:
                # The schedule establishes the edition, but it is not a data
                # document and must not select the reviewed-menu shortcut.
                known = None
            discovered_file = (known is not None and edition in {item.edition_date for item in live}
                               and "stat-search/files" not in known and known != GDP)
            url = (known if family in {"jp_industrial_production", "jp_unemployment",
                    "jp_household_spending", "jp_tankan", "jp_oecd_cli"} or discovered_file
                   else publication_url(client, family, edition, known, cache))
            if url is None:
                mark_pending(family, edition, "not_yet_published" if publication_grace(family, edition)
                             else "scheduled_unreviewed", metadata=editions[edition])
                continue
            urls[edition] = url
            doc = get_edition(client, provider, url, family, edition)
            if doc is None:
                continue
            rows = parse(
                doc, family, since, archives=urls, edition=edition,
                discovery=discovered.get(edition), discovery_document=discovery_document,
            )
            if not rows or {r.edition for r in rows} != {edition}:
                raise FetchError("Asia requested publication edition needs review")
          except (FetchError, ValueError, KeyError, TypeError, IndexError, BadZipFile) as exc:
            # Reject this edition without losing successfully validated editions.
            # Transport/authentication failures still stop the issuer family.
            if isinstance(exc, FetchError) and str(exc).startswith(provider + ": ") and str(exc) != provider + ": HTTP 404":
                raise
            delayed = (str(exc) == provider + ": HTTP 404" and publication_grace(family, edition)) or str(exc).endswith("not_yet_published")
            if delayed:
                if family in FAMILIES:
                    from .jp_discovery import pending_reason
                    mark_pending(family, edition, pending_reason(edition), metadata=editions[edition])
                else:
                    mark_pending(family, edition, "not_yet_published", metadata=editions[edition])
            else:
                mark_pending(family, edition, "needs_review", metadata=editions[edition])
            continue
          out.extend(rows)
    finally:
        DISCOVERED_EDITIONS.reset(token)
    return out


class AsiaAdapter:
    def __init__(self, client):
        self.client = client

    def fetch(self, families, since):
        from . import nbs_macro, japan_macro
        client = self.client
        class PublicClient:
            replay_mode = getattr(client, "replay_mode", False)

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
