"""FOMC target range, decision change and rate vote from Board statements."""
from datetime import datetime
import re

from .http import FetchError
from .policy import due_editions, number, observation, require_origin, reviewed
from .tier1_publications import publication_text, require

HOST = "https://www.federalreserve.gov"
LISTING = HOST + "/newsevents/pressreleases/2026-press-fomc.htm"
RATE = r"(?:\d+(?:-\d+)?/\d+|\d+(?:\.\d+)?)"


def voter_names(text):
    """Consume the complete printed list, excluding only the two chair roles."""
    text = re.sub(r", (?:Vice )?Chair\b", "", text)
    text = re.sub(r"^and\s+", "", text.strip())
    names = re.split(r"\s*;\s*(?:and\s+)?|\s*,\s*(?:and\s+)?|\s+and\s+", text)
    if any(re.fullmatch(r"(?:[A-Z][A-Za-z.'-]*\s+){1,4}[A-Z][A-Za-z.'-]*", n) is None for n in names):
        raise FetchError("FOMC voter list needs review")
    if len(names) != len(set(names)):
        raise FetchError("FOMC duplicate voter needs review")
    return names


def vote_split(text):
    numeric = re.search(r"approved the following statement for release by a (\d+)\s*[^0-9]{1,12}?\s*(\d+) vote", text)
    favour = re.search(r"Voting for the monetary policy action were (.*?)(?:\. Voting against|\. For media)", text)
    against = re.search(r"Voting against (?:this|the monetary policy) action (?:was|were) (.*?)\. For media inquiries", text)
    if "Voting for the monetary policy action" in text and favour is None:
        raise FetchError("FOMC favourable voter list needs review")
    if "Voting against" in text and against is None:
        raise FetchError("FOMC dissent voter list needs review")
    yes = voter_names(favour[1]) if favour else []
    no = []
    if against:
        for clause in re.split(r";\s*(?:and\s+)?", against[1]):
            group = require(r"^(.+?), who .+$", clause)
            no.extend(voter_names(group[1]))
    electorate = yes + no
    # Every named voter belongs to exactly one side. Never silently discard a
    # later dissenter clause or count an Oxford-comma list as a single person.
    if len(electorate) != len(set(electorate)):
        raise FetchError("FOMC overlapping voter list needs review")
    if numeric:
        votes = {"for": int(numeric[1]), "against": int(numeric[2])}
        if ((favour and votes["for"] != len(yes)) or
                (against and votes["against"] != len(no)) or
                (favour and sum(votes.values()) != len(electorate))):
            raise FetchError("FOMC numerical vote/list agreement needs review")
    elif favour:
        votes = {"for": len(yes), "against": len(no)}
        if sum(votes.values()) != len(set(electorate)):
            raise FetchError("FOMC rate voter total needs review")
    else:
        raise FetchError("FOMC rate vote needs review")
    if not 1 <= sum(votes.values()) <= 12 or votes["for"] == 0:
        raise FetchError("FOMC rate voter count needs review")
    return votes


def parse(document, family="fomc", since="1900-01"):
    require_origin(document, "www.federalreserve.gov")
    text, method = publication_text(document)
    text = text.replace("\u2011", "-").replace("\u2010", "-")
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
    votes = vote_split(text)
    rates = {"target_range": dict(lower=lower, upper=upper, change_bp=delta,
                                   change_basis="issuer_statement", scalar_representation="upper_bound")}
    return [observation(document, family, edition, rates, votes,
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
