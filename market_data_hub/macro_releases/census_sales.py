"""Free Census New Residential Sales publication; issuer units -> thousands."""
from datetime import datetime

from .bls import shift_month
from .http import FetchError
from .tier1_publications import month, observation, publication_text, require, reviewed

URL = "https://www.census.gov/construction/nrs/pdf/newressales.pdf"


def parse(document, family="us_new_home_sales", since="1900-01"):
    body, method = publication_text(document)
    stamp = require(r"FOR RELEASE AT.*?(\w+ \d{1,2}, \d{4})", body)[1]
    edition = datetime.strptime(stamp.title(), "%B %d, %Y").date().isoformat()
    head, _, _ = reviewed(family, edition)
    if "MONTHLY NEW RESIDENTIAL SALES, " + month(head).upper() not in body:
        raise FetchError("Census sales reference month needs review")
    values = require(r"Sales of new single-family houses in (\w+ \d{4}) were at a seasonally-adjusted annual rate of ([\d,]+),.*?the (\w+ \d{4}) rate of ([\d,]+)", body)
    out = []
    for period, label, printed in ((head, values[1], values[2]), (shift_month(head, -1), values[3], values[4])):
        if label != month(period):
            raise FetchError("Census sales prior reference month needs review")
        out.append(observation(document, family, edition, "us_new_home_sales", period, printed, method,
                               value=float(printed.replace(",", "")) / 1000))
    return [o for o in out if o.period >= since]


def fetch(client, family, since):
    # Rolling issuer PDF identifies its own edition. No history is invented.
    return parse(client.get("census", URL), family, since)
