"""Shared machinery for narrow, edition-bound public issuer publications."""
from datetime import datetime
from dataclasses import replace
from html import unescape
from io import BytesIO
import re
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from .adapters import Observation
from .catalogue import INDICATORS
from .http import FetchError
from .publication_times import local_release
from .tier1_editions import EDITIONS


def plain(raw):
    raw = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", raw))).strip()


def publication_text(document):
    if document.body.startswith(b"%PDF"):
        from pypdf import PdfReader
        return re.sub(r"\s+", " ", "\n".join(p.extract_text() for p in PdfReader(BytesIO(document.body)).pages)), "pdf_table"
    return plain(document.body.decode("utf-8-sig")), "html"


def require(pattern, text):
    match = re.search(pattern, text, re.I)
    if match is None:
        raise FetchError("Issuer publication format needs review")
    return match


def reviewed(family, edition):
    if edition not in EDITIONS[family]:
        raise FetchError("Issuer publication edition needs review")
    return EDITIONS[family][edition]


def month(period):
    return datetime.strptime(period, "%Y-%m").strftime("%B %Y")


def observation(document, family, edition, indicator, period, printed, method="html", *, value=None):
    head, stage, time = reviewed(family, edition)
    if period > head:
        raise FetchError("Issuer reference period needs review")
    measure = INDICATORS[indicator]
    zone = "Europe/Luxembourg" if measure.geography == "EA21" else "Europe/London" if measure.geography == "GBR" else "America/New_York"
    return Observation(indicator, family, period, float(printed.replace(",", "")) if value is None else value,
        printed, indicator + ":published:" + stage, measure.issuer_id, document, edition,
        **local_release(edition, time, zone), stage=stage, method=method, precision="exact",
        provider_vintage=edition, composition_id="EA21" if measure.geography == "EA21" else None,
        revision="initial" if period == head and stage not in ("final",) else "routine")


class Tier1Adapter:
    def __init__(self, client):
        self.client = client

    def fetch(self, families, since):
        from . import eurostat_activity, ons_retail, census_sales, ons_pay, ec_surveys, umich
        out = []
        client = self.client
        class PublicClient:
            def get(self, provider, url, params=None):
                requested_url = url
                parts = urlsplit(url)
                if parts.query:
                    params = dict(parse_qsl(parts.query), **(params or {}))
                    url = urlunsplit(parts._replace(query=""))
                doc = client.get(provider, url, params)
                # These public document IDs/filenames are provenance, not keys.
                return replace(doc, url=requested_url) if parts.query else doc
        for family in families:
            adapter = {"uk_retail_sales": ons_retail, "us_new_home_sales": census_sales,
                       "uk_total_pay": ons_pay, "ea_ec_surveys": ec_surveys,
                       "us_umich_sentiment": umich}.get(family, eurostat_activity)
            out += adapter.fetch(PublicClient(), family, since)
        return out
