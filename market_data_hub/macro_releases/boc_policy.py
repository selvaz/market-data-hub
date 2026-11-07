"""Bank of Canada target overnight rate from fixed-announcement releases."""
from datetime import datetime
import re

from .http import FetchError
from .policy import due_editions, observation, require_origin, reviewed
from .tier1_publications import publication_text, require

HOST = "https://www.bankofcanada.ca"
LISTING = HOST + "/core-functions/monetary-policy/key-interest-rate/"


def parse(document, family="boc_policy", since="1900-01"):
    require_origin(document, "www.bankofcanada.ca")
    text, method = publication_text(document)
    date = require(r"FOR IMMEDIATE RELEASE Media Relations Ottawa, Ontario (\w+ \d{1,2}, 2026)", text)[1]
    edition = datetime.strptime(date, "%B %d, %Y").date().isoformat()
    reviewed(family, edition)
    decision = require(r"The Bank of Canada today (held|maintained|reduced|increased|raised|lowered) its target for the overnight rate "
                       r"(?:by (\d+) basis points? to|at) ([\d.]+)%", text)
    action, bp, value = decision.groups()
    if action in ("held", "maintained"):
        if bp:
            raise FetchError("Bank of Canada hold/change association needs review")
        delta = 0
    elif bp:
        delta = int(bp) * (1 if action in ("increased", "raised") else -1)
    else:
        raise FetchError("Bank of Canada change amount needs review")
    rates = {"overnight_target": dict(rate=float(value), change_bp=delta, change_basis="issuer_statement")}
    return [observation(document, family, edition, rates, None, method=method)] if edition[:7] >= since else []


def fetch(client, family, since):
    listing = client.get("boc", LISTING)
    indexed = {}
    for path, edition in re.findall(rb'href="((?:https://www.bankofcanada.ca)?/2026/\d{2}/fad-press-release-(2026-\d{2}-\d{2})/)"', listing.body):
        date = edition.decode()
        reviewed(family, date)
        path = path.decode()
        indexed[date] = path if path.startswith("https://") else HOST + path
    out = []
    for edition in due_editions(family, since):
        if edition not in indexed:
            raise FetchError("Due Bank of Canada announcement absent from issuer listing; needs review")
        rows = parse(client.get("boc", indexed[edition]), family, since)
        if {o.edition for o in rows} != {edition}:
            raise FetchError("Bank of Canada requested edition needs review")
        out += rows
    return out
