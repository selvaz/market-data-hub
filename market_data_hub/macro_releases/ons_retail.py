"""ONS bulletin prints the m/m rate; J5EK is only a volume index."""
from .bls import shift_month
from .http import FetchError
from .ons import bulletin_date
from .schedule import due, get_edition
from .tier1_editions import EDITIONS
from .tier1_publications import month, observation, plain, require, reviewed

BASE = "https://www.ons.gov.uk/businessindustryandtrade/retailindustry/bulletins/retailsales/"


def publication_url(period):
    return BASE + month(period).replace(" ", "").lower()


def parse(document, family="uk_retail_sales", since="1900-01"):
    edition = bulletin_date(document)
    head, _, _ = reviewed(family, edition)
    body = plain(document.body.decode("utf-8"))
    if ("Retail sales, Great Britain: " + month(head) not in body or
            "all retailing, seasonally adjusted, Great Britain" not in body):
        raise FetchError("ONS retail measure or adjustment needs review")
    match = require(r"Retail sales volumes are estimated to have (fallen|risen) by ([\d.]+)% in " + month(head), body)
    printed = ("-" if match[1].lower() == "fallen" else "") + match[2]
    out = [observation(document, family, edition, "uk_retail_sales_mm", head, printed)]
    # Keep the issuer's revised previous-month rate as a vintage of this edition.
    previous = require(r"This follows a (rise|fall) of ([\d.]+)% in " + month(shift_month(head, -1)), body)
    printed = ("-" if previous[1].lower() == "fall" else "") + previous[2]
    out.append(observation(document, family, edition, "uk_retail_sales_mm", shift_month(head, -1), printed))
    return [o for o in out if o.period >= since]


def fetch(client, family, since):
    out = []
    for edition, (period, _, _) in due(EDITIONS[family], family=family).items():
        if period < since:
            continue
        doc = get_edition(client, "ons", publication_url(period), family, edition)
        if doc is None:
            continue
        rows = parse(doc, family, since)
        if not rows or {o.edition for o in rows} != {edition}:
            raise FetchError("ONS requested retail archive needs review")
        out += rows
    return out
