"""PBOC's authorized NIFC announcements, both 1Y and over-5Y LPR."""
from datetime import date
import re

from .http import FetchError
from .policy import change, due_editions, observation, require_origin, reviewed
from .policy_editions import EDITIONS
from .tier1_publications import publication_text, require

HOST = "https://www.pbc.gov.cn"
LISTING = HOST + "/zhengcehuobisi/125207/125213/125440/3876551/index.html"
BASELINE = "2025-12-22"


def previous_edition(edition):
    return max(d for d in (BASELINE, *EDITIONS["pboc_lpr"]) if d < edition)


def statement(document):
    require_origin(document, "www.pbc.gov.cn")
    text, method = publication_text(document)
    row = require(r"中国人民银行授权全国银行间同业拆借中心公布，(\d{4})年(\d{1,2})月(\d{1,2})日贷款市场报价利率[（(]LPR[）)]为[：:]"
                  r"1年期LPR为([\d.]+)%，5年期以上LPR为([\d.]+)%", text)
    edition = date(*map(int, row.groups()[:3])).isoformat()
    # Use the visible publication stamp, never CMS createDate or an URL token.
    raw = document.body.decode("utf-8-sig")
    stamp = require(r'<span id="shijian">(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}):00</span>', raw)
    if stamp[1] != edition or stamp[2] != "09:00":
        raise FetchError("PBOC LPR publication time/edition needs review")
    return edition, (float(row[4]), float(row[5])), method


def parse(document, previous, family="pboc_lpr", since="1900-01"):
    edition, values, method = statement(document)
    reviewed(family, edition)
    prior, old, _ = statement(previous)
    if prior != previous_edition(edition):
        raise FetchError("LPR consecutive issuer baseline needs review")
    rates = {key: dict(rate=value, change_bp=change(value, before), change_basis="consecutive_issuer_statements")
             for key, value, before in zip(("one_year", "over_five_year"), values, old)}
    evidence = [dict(source_url=previous.url, decision_date=prior,
                     rates=dict(zip(("one_year", "over_five_year"), old)))]
    return [observation(document, family, edition, rates, None, evidence=evidence, method=method)] if edition[:7] >= since else []


def fetch(client, family, since):
    listing = client.get("pboc", LISTING)
    indexed = {}
    raw = listing.body.decode("utf-8-sig")
    for path, y, m, d in re.findall(r'<a href="([^\"]+)"[^>]*title="(20\d{2})年(\d{1,2})月(\d{1,2})日全国银行间同业拆借中心受权公布贷款市场报价利率[（(]LPR[）)]公告"', raw):
        edition = date(int(y), int(m), int(d)).isoformat()
        if edition.startswith("2026"):
            reviewed(family, edition)
        indexed[edition] = HOST + path
    documents = {}
    def get(edition):
        if edition not in indexed:
            raise FetchError("Due LPR announcement/baseline absent from issuer listing; needs review")
        if edition not in documents:
            documents[edition] = client.get("pboc", indexed[edition])
        return documents[edition]
    out = []
    for edition in due_editions(family, since):
        rows = parse(get(edition), get(previous_edition(edition)), family, since)
        if {o.edition for o in rows} != {edition}:
            raise FetchError("LPR requested announcement edition needs review")
        out += rows
    return out
