"""ECB DFR decision from the Governing Council's own dated press release."""
from datetime import datetime
import re

from .http import FetchError
from .policy import due_editions, observation, require_origin, reviewed
from .tier1_publications import publication_text, require

HOST = "https://www.ecb.europa.eu"
LISTING = HOST + "/press/govcdec/mopo/2026/html/index_include.en.html"


def parse(document, family="ecb_policy", since="1900-01"):
    require_origin(document, "www.ecb.europa.eu")
    text, method = publication_text(document)
    date = require(r"PRESS RELEASE Monetary policy decisions (\d{1,2} \w+ 2026)", text)[1]
    edition = datetime.strptime(date, "%d %B %Y").date().isoformat()
    reviewed(family, edition)
    section = require(r"Key ECB interest rates (The (?:Governing Council|interest rates).*?) Asset purchase programme", text)[1]
    if "will remain unchanged at" in section:
        delta = 0
    else:
        action = require(r"decided to (raise|lower) the three key ECB interest rates by (\d+) basis points", section)
        delta = int(action[2]) * (1 if action[1].lower() == "raise" else -1)
    values = require(r"interest rates on the deposit facility, the main refinancing operations and the marginal lending facility "
                     r"(?:will (?:remain unchanged at|be (?:increased|decreased) to)|remain unchanged at) "
                     r"([\d.]+)%, ([\d.]+)% and ([\d.]+)% respectively", section)
    rates = {key: dict(rate=float(value), change_bp=delta, change_basis="issuer_statement")
             for key, value in zip(("deposit_facility", "main_refinancing", "marginal_lending"), values.groups())}
    return [observation(document, family, edition, rates, None, method=method)] if edition[:7] >= since else []


def fetch(client, family, since):
    listing = client.get("ecb", LISTING)
    indexed = {}
    for date, path in re.findall(rb'<dt isoDate="(2026-\d{2}-\d{2})">(?:(?!<dt\b).)*?<a href="([^\"]+\.en\.html)"[^>]*>Monetary policy decisions</a>', listing.body, re.S):
        edition = date.decode()
        reviewed(family, edition)
        indexed[edition] = HOST + path.decode()
    out = []
    for edition in due_editions(family, since):
        if edition not in indexed:
            raise FetchError("Due ECB statement missing from issuer listing; needs review")
        rows = parse(client.get("ecb", indexed[edition]), family, since)
        if {o.edition for o in rows} != {edition}:
            raise FetchError("ECB requested statement edition needs review")
        out += rows
    return out
