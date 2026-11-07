"""Census total construction spending: the published SA monthly change.

No API key, no rates calculated from rounded levels, and no residential/private
subtotals substituted for the national total. A changed publication fails closed.
"""
from datetime import datetime

from .bls import shift_month
from .housing_editions import EDITIONS, reviewed
from .http import FetchError
from .schedule import current_edition_allowed, due
from .tier1_publications import month, publication_text, require

URL = "https://www.census.gov/construction/c30/pdf/release.pdf"


def parse(document, family="us_construction_spending", since="1900-01"):
    from .housing import observation
    body, method = publication_text(document)
    stamp = require(r"FOR RELEASE AT (\d{1,2}:\d{2}) AM (EDT|EST), \w+, (\w+ \d{1,2}, \d{4})", body)
    edition = datetime.strptime(stamp[3].title(), "%B %d, %Y").date().isoformat()
    head, _, time = reviewed(family, edition)
    if stamp[1] != time:
        raise FetchError("Census construction release time needs review")
    require(r"MONTHLY CONSTRUCTION SPENDING, " + month(head), body)
    section = require(r"Total Construction (.*?)Private Construction", body)[1]
    headline = require(
        r"Construction spending during (\w+ \d{4}) was estimated at a "
        r"seasonally adjusted annual rate of \$[\d,.]+ billion, "
        r"((\d+(?:\.\d+)?) percent \([^)]*percent\)\*? (above|below)) "
        r"the revised (\w+) estimate of \$[\d,.]+ billion", section)
    previous = datetime.strptime(shift_month(head, -1), "%Y-%m").strftime("%B")
    if headline[1] != month(head) or headline[5] != previous:
        raise FetchError("Census construction headline period needs review")
    row = observation(document, family, edition, "us_construction_spending_mm",
                      headline[2], method, value=float(headline[3]) * (-1 if headline[4] == "below" else 1))
    # The issuer's own printed zone must agree with the reviewed local clock.
    expected_zone = "EDT" if row.issuer_local_time.endswith("-04:00") else "EST"
    if stamp[2] != expected_zone:
        raise FetchError("Census construction release timezone needs review")
    return [row] if head >= since else []


def fetch(client, family, since):
    if not due(EDITIONS[family], family=family):
        return []
    rows = parse(client.get("census", URL), family, since)
    if rows and not current_edition_allowed(family, rows[0].edition):
        return []
    return rows
