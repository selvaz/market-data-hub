"""Archived EA21 HPI rates as printed by Eurostat, never rounded-index ratios."""
from datetime import datetime, timezone
import re

from .http import FetchError, json_payload
from .housing_editions import EDITIONS, reviewed
from .schedule import due, get_edition, release_instant, run_date
from .tier1_publications import plain, require

FAMILY = "ea_hpi"
CALENDAR = "https://ec.europa.eu/eurostat/o/calendars/eventsJson"
CALENDAR_PARAMS = {"start": "2026-07-01T00:00:00Z", "end": "2027-01-01T00:00:00Z"}
DATASET = "prc_hpi_q"


def publication_url(edition):
    reviewed(FAMILY, edition)
    return ("https://ec.europa.eu/eurostat/web/products-euro-indicators/w/2-" +
            datetime.strptime(edition, "%Y-%m-%d").strftime("%d%m%Y") + "-bp")


def quarter_end(period):
    return f"{period[:4]}-{int(period[-1]) * 3:02d}"


def calendar_editions(document):
    found = {}
    for row in json_payload(document):
        if row.get("euroindAuthor") != "estat" or not row.get("euroind"):
            continue
        datasets = row.get("datasetCodes", "").split(",")
        if row.get("title") != "House price index" and DATASET not in datasets:
            continue
        edition = row["start"][:10]
        if not min(EDITIONS[FAMILY]) <= edition <= "2026-12-31":
            continue
        match = re.fullmatch(r"Q([1-4])/(\d{4})", row.get("period", ""))
        if (row.get("preliminary") is not False or row.get("title") != "House price index" or
                DATASET not in datasets or not match or edition in found or
                EDITIONS[FAMILY].get(edition, ())[:2] != (f"{match[2]}-Q{match[1]}", "regular")):
            raise FetchError("Eurostat HPI calendar edition needs review")
        # allDay events use a misleading Z suffix: only their dates are used.
        # The reviewed clock is corroborated by the dated publication metadata.
        found[edition] = EDITIONS[FAMILY][edition]
    if not set(due(EDITIONS[FAMILY], family=FAMILY)).issubset(found):
        raise FetchError("Eurostat HPI due calendar edition missing; needs review")
    return due(found, family=FAMILY)


def parse(document, family=FAMILY, since="1900-01"):
    from .housing import observation
    if family != FAMILY:
        raise FetchError("Eurostat HPI family needs review")
    raw = document.body.decode("utf-8")
    stamps = re.findall(r"<meta\s+property=['\"]article:published_time['\"]\s+content=['\"]([^'\"]+)", raw)
    if len(stamps) != 1:
        raise FetchError("Eurostat HPI publication timestamp needs review")
    edition = stamps[0][:10]
    period, _, _ = reviewed(family, edition)
    stamp = datetime.fromisoformat(stamps[0].replace("Z", "+00:00"))
    if (document.url != publication_url(edition) or stamp.tzinfo is None or
            stamp.astimezone(timezone.utc) != release_instant(edition, family=family)):
        raise FetchError("Eurostat HPI issuer URL or publication clock needs review")
    body = plain(raw)
    require(r"The figures are not seasonally adjusted\.", body)
    require(r"The House Price Index \(HPI\) measures the price changes of all residential properties purchased by households .*?both newly built and existing", body)
    # July lists both EA20 and EA21; October uses an unnumbered table row with
    # an explicit EA21 geographical note. Never substitute the EA20 or EU row.
    if not (re.search(r"Euro area \(EA21\):", body) or
            ("From 1 January 2026, the euro area also includes Bulgaria (EA21)" in body and
             "releases with data for the first quarter of 2026 onwards comment on EA21 series" in body)):
        raise FetchError("Eurostat HPI composition needs review")
    quarter = int(period[-1])
    label = ("first", "second", "third", "fourth")[quarter - 1]
    previous = ("first", "second", "third", "fourth")[(quarter - 2) % 4]
    prior_year = int(period[:4]) - (quarter == 1)
    annual = require(r"In the " + label + r" quarter of " + period[:4] +
        r", house prices, as measured by the House Price Index, (increased|decreased) by ([\d.]+)% in the euro area.*?"
        r"compared with the same quarter of the previous year", body)
    quarterly = require(r"Compared with the " + previous + r" quarter of " + str(prior_year) +
        r", house prices (increased|decreased) by ([\d.]+)% in the euro area.*?in the " + label +
        r" quarter of " + period[:4], body)
    tables = [table for table in re.findall(r"<table\b.*?</table>", raw, re.S)
              if "Quarterly and annual changes in house prices" in plain(table)]
    if len(tables) != 1:
        raise FetchError("Eurostat HPI headline table needs review")
    rows = [[plain(cell) for cell in re.findall(r"<t[hd]\b[^>]*>(.*?)</t[hd]>", row, re.S)]
            for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", tables[0], re.S)]
    labels = [f"Q{(quarter - 4 + offset) % 4 + 1}" for offset in range(4)]
    if (len(rows) < 5 or rows[1] != ["", "Change compared with the previous quarter, %",
                                              "Change compared with the same quarter of the previous year, %"] or
            [cell for cell in rows[2] if cell] != [str(int(period[:4]) - 1), period[:4]] * 2 or
            rows[3] != [""] + labels * 2):
        raise FetchError("Eurostat HPI table column or period metadata needs review")
    area = [row for row in rows[4:] if row and row[0] in ("Euro area 21", "Euro area")]
    if len(area) != 1 or len(area[0]) != 9 or any(
            not re.fullmatch(r"[+-]?\d+\.\d+", cell) for cell in area[0][1:]):
        raise FetchError("Eurostat HPI EA21 table row needs review")
    out = []
    for basis, match, cell in (("qq", quarterly, area[0][4]), ("yy", annual, area[0][8])):
        printed = ("-" if match[1].lower() == "decreased" else "") + match[2]
        if float(printed) != float(cell):
            raise FetchError("Eurostat HPI headline and table disagree; needs review")
        if quarter_end(period) >= since:
            out.append(observation(document, family, edition, "ea21_hpi_" + basis, printed,
                                   method="html_table"))
    return out


def fetch(client, family=FAMILY, since="1900-01"):
    if run_date() > "2026-12-31":
        raise FetchError("Eurostat HPI reviewed calendar horizon exhausted; needs review")
    eligible = {edition: meta for edition, meta in due(EDITIONS[family], family=family).items()
                if quarter_end(meta[0]) >= since}
    if not eligible:
        return []
    calendar = client.get("eurostat", CALENDAR, CALENDAR_PARAMS)
    confirmed = calendar_editions(calendar)
    out = []
    for edition in eligible:
        if edition not in confirmed:
            raise FetchError("Eurostat HPI requested calendar edition needs review")
        try:
            document = get_edition(client, "eurostat", publication_url(edition), family, edition)
        except FetchError as exc:
            if str(exc) == "eurostat: HTTP 404":
                raise FetchError("Eurostat HPI due publication missing; needs review") from None
            raise
        if document is None:
            continue
        observations = parse(document, family, since)
        if len(observations) != 2 or {o.edition for o in observations} != {edition}:
            raise FetchError("Eurostat HPI requested archive edition needs review")
        out += observations
    return out
