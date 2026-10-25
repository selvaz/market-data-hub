"""CMHC dated releases: total monthly housing starts, all Canada, SAAR."""
from dataclasses import replace
from datetime import datetime
import re

from .housing_editions import EDITIONS, reviewed
from .http import FetchError
from .schedule import due, get_edition, release_instant, run_date, run_instant
from .tier1_publications import plain, require

FAMILY = "ca_housing_starts"
CALENDAR = "https://www.cmhc-schl.gc.ca/professionals/housing-markets-data-and-research/news-releases-reports-calendar"
PREFIX = "https://www.cmhc-schl.gc.ca/media-newsroom/news-releases/2026/housing-starts-construction-data-"
MONTHS = "January February March April May June July August September October November December".split()


def publication_url(edition):
    period = reviewed(FAMILY, edition)[0]
    return PREFIX + MONTHS[int(period[5:]) - 1].lower() + "-" + period[:4]


def calendar_editions(document):
    body = plain(document.body.decode("utf-8"))
    section = require(r"Monthly Housing Starts and Other Construction Data .*? 2026 release dates: (.*?) Housing Information Monthly", body)[1]
    found = {}
    for month, day in re.findall(r"(?:Monday|Tuesday|Wednesday|Thursday|Friday), (\w+)\.? (\d{1,2})", section):
        number = next((i for i, m in enumerate(MONTHS, 1) if m.startswith(month)), None)
        if number is None:
            raise FetchError("CMHC calendar month needs review")
        edition = f"2026-{number:02d}-{int(day):02d}"
        if not min(EDITIONS[FAMILY]) <= edition <= "2026-12-31":
            continue
        meta = (f"2026-{number - 1:02d}", "regular", "08:15")
        if edition in found or EDITIONS[FAMILY].get(edition) != meta:
            raise FetchError("CMHC housing calendar edition needs review")
        found[edition] = meta
    required = {d for d in EDITIONS[FAMILY] if release_instant(d, family=FAMILY) > run_instant()}
    if not required.issubset(found):
        raise FetchError("CMHC future housing calendar missing; needs review")
    return found


def parse(document, family=FAMILY, since="1900-01"):
    from .housing import observation
    if family != FAMILY:
        raise FetchError("CMHC housing family needs review")
    raw = document.body.decode("utf-8")
    body = plain(raw)
    stamp = require(r"Ottawa, (\w+ \d{1,2}, \d{4}) The six-month trend in housing starts", body)[1]
    edition = datetime.strptime(stamp, "%B %d, %Y").date().isoformat()
    period, _, _ = reviewed(family, edition)
    label = MONTHS[int(period[5:]) - 1]
    title = plain(require(r"<h1\b[^>]*>(.*?)</h1>", raw)[1])
    if title != "Housing starts and construction data for " + label + " " + period[:4] or document.url != publication_url(edition):
        raise FetchError("CMHC housing issuer URL, title or period needs review")
    require(r"The trend measure is a six-month moving average of the seasonally adjusted annual rate \(SAAR\) of total housing starts for all areas in Canada", body)
    printed = require(r"The total monthly SAAR of housing starts for all areas in Canada was (?:up \d+%|down \d+%|flat) in " + label + r" \((\d{1,3}(?:,\d{3})+) units\) compared to", body)[1]
    next_release = require(r"We will release the (\w+) housing starts data on (\w+ \d{1,2}) at (\d{1,2}:\d{2} [AP]M ET)", body)
    next_month = MONTHS[int(period[5:]) % 12]
    expected = [d for d, m in EDITIONS[family].items() if m[0] == f"2026-{int(period[5:]) + 1:02d}"]
    if next_release[1] != next_month or next_release[3] != "8:15 AM ET" or (expected and datetime.strptime(next_release[2] + " 2026", "%B %d %Y").date().isoformat() != expected[0]):
        raise FetchError("CMHC next release date or clock needs review")
    row = replace(observation(document, family, edition, FAMILY, printed), series_key="CMHC:all_areas:SAAR:release")
    return [row] if period >= since else []


def fetch(client, family=FAMILY, since="1900-01"):
    if run_date() > "2026-12-31":
        raise FetchError("CMHC housing reviewed horizon exhausted; needs review")
    eligible = {d: m for d, m in due(EDITIONS[family], family=family).items() if m[0] >= since}
    if not eligible:
        return []
    calendar_editions(client.get("cmhc", CALENDAR))
    out = []
    for edition in eligible:
        try:
            doc = get_edition(client, "cmhc", publication_url(edition), family, edition)
        except FetchError as exc:
            if str(exc) == "cmhc: HTTP 404":
                raise FetchError("CMHC due housing publication missing; needs review") from None
            raise
        if doc is None:
            continue
        rows = parse(doc, family, since)
        if len(rows) != 1 or rows[0].edition != edition:
            raise FetchError("CMHC requested housing edition needs review")
        out += rows
    return out
