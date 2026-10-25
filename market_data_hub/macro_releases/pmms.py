"""Free Freddie Mac PMMS weekly 30-year fixed-rate mortgage publications.

The reference period is the issuer calendar's survey week ending, distinct
from the publication date. Only reviewed editions and the 30-year rate qualify.
"""
from datetime import datetime
import re

from .housing_editions import EDITIONS, reviewed
from .http import FetchError
from .schedule import current_edition_allowed, due
from .tier1_publications import plain, publication_text, require

URL = "https://www.freddiemac.com/pmms/archive"
CURRENT = "https://www.freddiemac.com/pmms"
CALENDAR = "https://www.freddiemac.com/pmms/docs/PMMS_Publication_Calendar.pdf"
FAMILY = "us_pmms"


def rate_row(document, family, edition, printed):
    from .housing import observation
    reviewed(family, edition)
    if not re.fullmatch(r"\d+(?:\.\d+)?", printed) or not 0 <= float(printed) <= 25:
        raise FetchError("PMMS printed rate needs review")
    return observation(document, family, edition, "us_pmms_30y", printed)


def parse_archive(document, family=FAMILY, since="1900-01"):
    raw = document.body.decode("utf-8-sig")
    body = plain(raw)
    year = require(r"Compilation of Weekly Survey Data for (\d{4})", body)[1]
    calendar = EDITIONS[family]
    if year != min(calendar)[:4]:
        raise FetchError("PMMS archive year needs review")
    blocks = re.findall(r'<strong\b[^>]*>(\w+ \d{1,2}, \d{4})</strong>\s*</p>\s*(<table\b.*?</table>)', raw, re.S)
    if not blocks:
        raise FetchError("PMMS dated archive table format needs review")
    out, seen = [], set()
    for label, table in blocks:
        edition = datetime.strptime(label, "%B %d, %Y").date().isoformat()
        if edition < min(calendar):
            continue  # Older unreviewed history is never certified.
        reviewed(family, edition)
        if edition in seen:
            raise FetchError("PMMS duplicate archive edition needs review")
        seen.add(edition)
        rows = [[plain(cell) for cell in re.findall(r'<t[hd]\b[^>]*>(.*?)</t[hd]>', row, re.S)]
                for row in re.findall(r'<tr\b[^>]*>(.*?)</tr>', table, re.S)]
        if (len(rows) != 2 or rows[0] != ["", "30-Yr FRM", "15-Yr FRM"] or
                len(rows[1]) != 3 or rows[1][0] != "Average Rates" or
                any(not re.fullmatch(r"\d+(?:\.\d+)?%", v) for v in rows[1][1:])):
            raise FetchError("PMMS archive term, unit or column format needs review")
        row = rate_row(document, family, edition, rows[1][1][:-1])
        if row.period >= since:
            out.append(row)
    if not seen or seen != {d for d in calendar if d <= max(seen)}:
        raise FetchError("PMMS archive has missing reviewed editions; needs review")
    return sorted(out, key=lambda o: o.edition)


def parse_current(document, family=FAMILY, since="1900-01"):
    body, _ = publication_text(document)
    stamp = require(r"U.S. weekly mortgage rate averages as of (\d{1,2}/\d{1,2}/\d{4})", body)[1]
    edition = datetime.strptime(stamp, "%m/%d/%Y").date().isoformat()
    reviewed(family, edition)
    panel = require(r"30-year Fixed-Rate Mortgage (\d+(?:\.\d+)?)% 15-year Fixed-Rate Mortgage", body)[1]
    headline = require(r"The 30-year fixed-rate mortgage averaged (\d+(?:\.\d+)?)% as of (\w+ \d{1,2}, \d{4})", body)
    require(r"PMMS results are released weekly on Thursdays at 12 p.m. ET", body)
    if (datetime.strptime(headline[2], "%B %d, %Y").date().isoformat() != edition or
            headline[1] != panel):
        raise FetchError("PMMS current headline date or rate needs review")
    row = rate_row(document, family, edition, panel)
    return [row] if row.period >= since else []


def parse(document, family=FAMILY, since="1900-01"):
    if "Compilation of Weekly Survey Data" in plain(document.body.decode("utf-8-sig")):
        return parse_archive(document, family, since)
    return parse_current(document, family, since)


def fetch(client, family, since):
    eligible = due(EDITIONS[family], family=family)
    if not eligible:
        return []
    rows = parse_archive(client.get("freddiemac", URL), family, since)
    rows = [o for o in rows if o.edition in eligible]
    if rows and not current_edition_allowed(family, max(o.edition for o in rows)):
        return []
    return rows
