"""BoE Money and Credit: published net house-purchase approvals, SA count."""
from dataclasses import replace
from datetime import datetime
import re

from .housing_editions import EDITIONS, reviewed
from .http import FetchError
from .schedule import due, get_edition, release_instant, run_date, run_instant
from .tier1_publications import plain, require

FAMILY = "uk_mortgage_approvals"
CALENDAR = "https://www.bankofengland.co.uk/statistics/tables"
CLOCK_SOURCE = "https://www.bankofengland.co.uk/-/media/boe/files/statistics/statistical-code-of-practice.pdf"


def publication_url(edition):
    period = reviewed(FAMILY, edition)[0]
    return ("https://www.bankofengland.co.uk/statistics/money-and-credit/" + period[:4] + "/" +
            datetime.strptime(period, "%Y-%m").strftime("%B-%Y").lower())


def calendar_editions(document):
    body = plain(document.body.decode("utf-8"))
    section = require(r"2026/2027 release dates (.*?) If you have an enquiry regarding Bankstats data", body)[1]
    found = {}
    for day, label in re.findall(r"(\d{1,2} \w+ \d{4}) \((\w+ \d{4}) data\)", section):
        edition = datetime.strptime(day, "%d %B %Y").date().isoformat()
        if not min(EDITIONS[FAMILY]) <= edition <= "2026-12-31":
            continue
        meta = (datetime.strptime(label, "%B %Y").strftime("%Y-%m"), "regular", "09:30")
        if edition in found or EDITIONS[FAMILY].get(edition) != meta:
            raise FetchError("BoE approvals calendar edition needs review")
        found[edition] = meta
    required = {d for d in EDITIONS[FAMILY] if release_instant(d, family=FAMILY) > run_instant()}
    if not required.issubset(found):
        raise FetchError("BoE approvals future calendar edition missing; needs review")
    return found


def parse(document, family=FAMILY, since="1900-01"):
    from .housing import observation
    if family != FAMILY:
        raise FetchError("BoE approvals family needs review")
    raw = document.body.decode("utf-8")
    dates = re.findall(r'<div class="published-date">(.*?)</div>', raw, re.S)
    if len(dates) != 1:
        raise FetchError("BoE approvals publication date needs review")
    date = require(r"Published on (\d{1,2} \w+ \d{4})", plain(dates[0]))[1]
    edition = datetime.strptime(date, "%d %B %Y").date().isoformat()
    period, _, _ = reviewed(family, edition)
    label = datetime.strptime(period, "%Y-%m").strftime("%B %Y")
    month = label.split()[0]
    title = require(r'<meta property="og:title" content="([^"]+)"', raw)[1]
    if title != "Money and Credit - " + label or document.url != publication_url(edition):
        raise FetchError("BoE approvals issuer URL, title or period needs review")
    body = plain(raw)
    summary = require(r"Net mortgage approvals for house purchases (?:increased|decreased|rose|fell|remained).*? (?:to|at) ([\d,]+) in " + month, body)[1]
    narrative = require(r"Net mortgage approvals \(that is, approvals net of cancellations\) for house purchases, which is an indicator of future borrowing, .*? (?:to|at) ([\d,]+) in " + month, body)[1]
    require(r"Chart 2: Mortgage approvals Seasonally adjusted", body)
    if summary != narrative or not re.fullmatch(r"\d{1,3}(?:,\d{3})+", summary) or int(summary.replace(",", "")) % 100:
        raise FetchError("BoE approvals national headline or printed precision needs review")
    row = replace(observation(document, family, edition, "uk_mortgage_approvals", summary),
                  series_key="LPMVTVX:release_headline_rounded_100")
    return [row] if period >= since else []


def fetch(client, family=FAMILY, since="1900-01"):
    if run_date() > "2026-12-31":
        raise FetchError("BoE approvals reviewed calendar horizon exhausted; needs review")
    eligible = {d: m for d, m in due(EDITIONS[family], family=family).items() if m[0] >= since}
    if not eligible:
        return []
    calendar_editions(client.get("boe", CALENDAR))
    out = []
    for edition in eligible:
        try:
            doc = get_edition(client, "boe", publication_url(edition), family, edition)
        except FetchError as exc:
            if str(exc) == "boe: HTTP 404":
                raise FetchError("BoE approvals due publication missing; needs review") from None
            raise
        if doc is None:
            continue
        rows = parse(doc, family, since)
        if len(rows) != 1 or rows[0].edition != edition:
            raise FetchError("BoE approvals requested edition needs review")
        out += rows
    return out
