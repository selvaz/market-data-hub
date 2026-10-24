"""Bank Rate and rate vote; QT votes and dissenters' rates are separate."""
from datetime import datetime
import re

from .http import FetchError
from .policy import due_editions, observation, require_origin, reviewed
from .policy_editions import SCHEDULES
from .tier1_publications import publication_text, require

HOST = "https://www.bankofengland.co.uk"


def parse(document, family="boe_policy", since="1900-01"):
    require_origin(document, "www.bankofengland.co.uk")
    text, method = publication_text(document)
    date = require(r"Published on (\d{1,2} \w+ 2026) Monetary Policy Summary", text)[1]
    edition = datetime.strptime(date, "%d %B %Y").date().isoformat()
    reviewed(family, edition)
    decision = require(r"At its meeting ending on (\d{1,2} \w+ 2026), the Monetary Policy Committee \(MPC\) "
                       r"voted (?:by a majority of (\d+)[–-](\d+)|unanimously) to "
                       r"(maintain|reduce|increase) Bank Rate (?:by ([\d.]+) percentage points?, )?(?:at|to) ([\d.]+)%", text)
    meeting_date, yes, no, action, delta, value = decision.groups()
    if action != "maintain" and delta is None:
        raise FetchError("BoE change amount needs review")
    delta = 0 if action == "maintain" else float(delta) * (100 if action == "increase" else -100)
    rates = {"bank_rate": dict(rate=float(value), change_bp=delta, change_basis="issuer_statement")}
    obs = observation(document, family, edition, rates,
                      {"for": int(yes or 9), "against": int(no or 0)}, method=method,
                      decision_date=datetime.strptime(meeting_date, "%d %B %Y").date().isoformat())
    return [obs] if edition[:7] >= since else []


def fetch(client, family, since):
    calendar = client.get("boe", SCHEDULES[family])
    paths = set(re.findall(rb'href="(/monetary-policy-summary-and-minutes/2026/[^\"]+)"', calendar.body))
    allowed = {datetime.fromisoformat(d).strftime("%B").lower() + "-2026" for d in __import__("market_data_hub.macro_releases.policy_editions", fromlist=["EDITIONS"]).EDITIONS[family]}
    if any(p.decode().rsplit("/", 1)[-1] not in allowed for p in paths):
        raise FetchError("Unscheduled MPC publication needs review")
    out = []
    for edition in due_editions(family, since):
        month = datetime.fromisoformat(edition).strftime("%B").lower()
        path = f"/monetary-policy-summary-and-minutes/2026/{month}-2026"
        if path.encode() not in paths:
            raise FetchError("Due MPC statement absent from issuer calendar; needs review")
        rows = parse(client.get("boe", HOST + path), family, since)
        if {o.edition for o in rows} != {edition}:
            raise FetchError("MPC requested publication edition needs review")
        out += rows
    return out
