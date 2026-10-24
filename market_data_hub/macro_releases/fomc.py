"""FOMC target range, decision change and rate vote from Board statements."""
from datetime import datetime
import re

from .http import FetchError
from .policy import due_editions, number, observation, require_origin, reviewed
from .tier1_publications import publication_text, require

HOST = "https://www.federalreserve.gov"
LISTING = HOST + "/newsevents/pressreleases/2026-press-fomc.htm"
RATE = r"(?:\d+(?:-\d+)?/\d+|\d+(?:\.\d+)?)"


def parse(document, family="fomc", since="1900-01"):
    require_origin(document, "www.federalreserve.gov")
    text, method = publication_text(document)
    date = require(r"(\w+ \d{1,2}, 2026) Federal Reserve issues FOMC statement", text)[1]
    edition = datetime.strptime(date, "%B %d, %Y").date().isoformat()
    reviewed(family, edition)
    require(r"For release at 2:00 p\.m\. (?:EDT|EST)", text)
    decision = require(r"The Committee decided to (maintain|raise|lower) the target range for the federal funds rate "
                       r"(?:(?:by (" + RATE + r") percentage point(?:s)? to)|at) (" + RATE + r") to (" + RATE + r") percent", text)
    action, delta, low, high = decision.groups()
    delta = 0 if action.lower() == "maintain" else number(delta) * (100 if action.lower() == "raise" else -100)
    lower, upper = number(low), number(high)
    if not 0 <= lower <= upper or upper - lower > 1:
        raise FetchError("FOMC target range needs review")
    vote = require(r"approved the following statement for release by a (\d+)\s*[^0-9]{1,12}?\s*(\d+) vote", text)
    rates = {"target_range": dict(lower=lower, upper=upper, change_bp=delta,
                                   change_basis="issuer_statement", scalar_representation="upper_bound")}
    return [observation(document, family, edition, rates, {"for": int(vote[1]), "against": int(vote[2])},
                        printed=low + " to " + high + " percent", method=method)] if edition[:7] >= since else []


def fetch(client, family, since):
    listing = client.get("fed", LISTING)
    links = set(re.findall(rb'href=["\'](/newsevents/pressreleases/monetary(2026\d{4})a.htm)["\']', listing.body))
    indexed = {datetime.strptime(d.decode(), "%Y%m%d").date().isoformat(): HOST + path.decode() for path, d in links}
    for edition in indexed:
        reviewed(family, edition)
    out = []
    for edition in due_editions(family, since):
        if edition not in indexed:
            raise FetchError("Due FOMC statement absent from issuer listing; needs review")
        rows = parse(client.get("fed", indexed[edition]), family, since)
        if {o.edition for o in rows} != {edition}:
            raise FetchError("FOMC requested statement edition needs review")
        out += rows
    return out
