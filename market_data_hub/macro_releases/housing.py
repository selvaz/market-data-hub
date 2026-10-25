"""Edition-bound free housing publications; no redistributor certification."""
from dataclasses import replace
from datetime import datetime
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit
import re

from .adapters import Observation
from .catalogue import INDICATORS
from .housing_editions import EDITIONS, reviewed
from .http import FetchError
from .publication_times import local_release
from .schedule import due, issuer_zone
from .tier1_publications import publication_text, require
from . import census_construction, pmms, nar, case_shiller, eurostat_hpi, ecb_housing, uk_hpi, boe_housing, destatis_housing, cmhc_housing, mlit_housing, nbs_housing

FHFA_LIST = "https://www.fhfa.gov/reports/house-price-index"
FHFA_ARCHIVES = {
    "2026-08-25": "https://www.fhfa.gov/document/d/hpi/fhfa-house-price-index-report-2026q2",
    "2026-09-29": "https://www.fhfa.gov/document/d/hpi/fhfa-hpi-monthly-september-2026",
}


def observation(doc, family, edition, indicator, printed, method="html", *, period=None, value=None):
    head, stage, time = reviewed(family, edition)
    period = period or head
    if period != head:
        raise FetchError("Housing headline reference period needs review")
    measure = INDICATORS[indicator]
    return Observation(indicator, family, period,
        float(printed.replace(",", "")) if value is None else value, printed,
        indicator + ":published:" + stage, measure.issuer_id, doc, edition,
        **local_release(edition, time, issuer_zone(family)), stage=stage,
        precision="exact", method=method, provider_vintage=edition,
        composition_id=measure.geography if measure.geography.startswith("EA") else None)


def parse_fhfa(doc, family="us_fhfa_hpi", since="1900-01"):
    body, method = publication_text(doc)
    stamp = require(r"For Immediate Release.*?(\w+ \d{1,2}, \d{4})", body)[1]
    edition = datetime.strptime(stamp, "%B %d, %Y").date().isoformat()
    head, _, _ = reviewed(family, edition)
    from io import BytesIO
    from pypdf import PdfReader
    tables = [p.extract_text() for p in PdfReader(BytesIO(doc.body)).pages
              if "Monthly Price Change Estimates for U.S. and Census Divisions" in p.extract_text()]
    if len(tables) != 1:
        raise FetchError("FHFA monthly table needs review")
    table = re.sub(r"\s+", " ", tables[0])
    require(r"Purchase-Only FHFA HPI.*?Seasonally Adjusted, Nominal", table)
    label = datetime.strptime(head, "%Y-%m").strftime("%b %y")
    mm = require(r"1-Month Change:.*?- " + label + r" ([+-]?[\d.]+)%", table)[1]
    yy = require(r"12-Month Change:.*?- " + label + r" ([+-]?[\d.]+)%", table)[1]
    return [observation(doc, family, edition, "us_fhfa_hpi_" + basis, value, method)
            for basis, value in (("mm", mm), ("yy", yy)) if head >= since]


def fetch_fhfa(client, family, since):
    eligible = due(EDITIONS[family], family=family)
    if not eligible:
        return []
    urls = dict(FHFA_ARCHIVES)
    if any(d not in urls for d in eligible):
        listing = client.get("fhfa", FHFA_LIST)
        for row in re.findall(r"<tr\b.*?</tr>", listing.body.decode(), re.S):
            text, _ = publication_text(replace(listing, body=row.encode()))
            stamp = re.search(r"\w+ \d{1,2}, \d{4}", text)
            links = re.findall(r'href="([^"]*/document/d/hpi/[^"]+)"', row)
            if stamp and len(links) == 1:
                urls[datetime.strptime(stamp[0], "%B %d, %Y").date().isoformat()] = urljoin(FHFA_LIST, links[0])
    out = []
    for edition, (period, _, _) in eligible.items():
        if period < since:
            continue
        if edition not in urls:
            raise FetchError("FHFA due publication link needs review")
        rows = parse_fhfa(client.get("fhfa", urls[edition]), family, since)
        if not rows or {o.edition for o in rows} != {edition}:
            raise FetchError("FHFA requested publication edition needs review")
        out += rows
    return out


PARSERS = {"cn_new_home_prices": nbs_housing.parse, "jp_housing_starts": mlit_housing.parse, "ca_housing_starts": cmhc_housing.parse, "de_building_permits": destatis_housing.parse, "uk_mortgage_approvals": boe_housing.parse, "uk_hpi": uk_hpi.parse, ecb_housing.LOANS: ecb_housing.parse, ecb_housing.COST: ecb_housing.parse,
           "ea_hpi": eurostat_hpi.parse, "us_fhfa_hpi": parse_fhfa, "us_construction_spending": census_construction.parse,
           "us_pmms": pmms.parse, "us_existing_home_sales": nar.parse_existing,
           "us_pending_home_sales": nar.parse_pending, "us_case_shiller": case_shiller.parse}
FETCHERS = {"cn_new_home_prices": nbs_housing.fetch, "jp_housing_starts": mlit_housing.fetch, "ca_housing_starts": cmhc_housing.fetch, "de_building_permits": destatis_housing.fetch, "uk_mortgage_approvals": boe_housing.fetch, "uk_hpi": uk_hpi.fetch, ecb_housing.LOANS: ecb_housing.fetch, ecb_housing.COST: ecb_housing.fetch,
            "ea_hpi": eurostat_hpi.fetch, "us_fhfa_hpi": fetch_fhfa, "us_construction_spending": census_construction.fetch,
            "us_pmms": pmms.fetch, "us_existing_home_sales": nar.fetch, "us_pending_home_sales": nar.fetch,
            "us_case_shiller": case_shiller.fetch}


class HousingAdapter:
    def __init__(self, client):
        self.client = client

    def fetch(self, families, since):
        client = self.client
        class PublicClient:
            def get(self, provider, url, params=None):
                parts = urlsplit(url)
                requested = url
                if parts.query:
                    params = dict(parse_qsl(parts.query), **(params or {}))
                    url = urlunsplit(parts._replace(query=""))
                doc = client.get(provider, url, params)
                return replace(doc, url=requested) if parts.query else doc
        return [row for family in families for row in FETCHERS[family](PublicClient(), family, since)]
