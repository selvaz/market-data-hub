"""UK-wide HMLR/ONS annual HPI headlines from dated issuer publications."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo

from .housing_editions import EDITIONS, reviewed
from .http import FetchError
from .publication_times import local_release
from .schedule import due, get_edition, mark_pending, release_instant, run_date, run_instant
from .tier1_publications import plain, require

FAMILY = "uk_hpi"
CALENDAR = "https://www.gov.uk/government/publications/about-the-uk-house-price-index/about-the-uk-house-price-index"


def publication_url(edition):
    period = reviewed(FAMILY, edition)[0]
    label = datetime.strptime(period, "%Y-%m").strftime("%B-%Y").lower()
    return f"https://www.gov.uk/government/statistics/uk-house-price-index-for-{label}/uk-house-price-index-summary-{label}"


def calendar_editions(document):
    raw = document.body.decode("utf-8")
    tables = [plain(t) for t in re.findall(r"<table\b.*?</table>", raw, re.S)
              if "Month and year of the data" in plain(t) and "Publication time and date" in plain(t)]
    if len(tables) != 1:
        raise FetchError("UK HPI release calendar table needs review")
    found = {}
    for label, day, clock in re.findall(r"(\w+ \d{4}) (\d{1,2} \w+ \d{4}) \((\d[.:]\d{2}am)\)", tables[0]):
        edition = datetime.strptime(day, "%d %B %Y").date().isoformat()
        if not min(EDITIONS[FAMILY]) <= edition <= "2026-12-31":
            continue
        meta = (datetime.strptime(label, "%B %Y").strftime("%Y-%m"), "regular", "0" + clock[:-2].replace(".", ":"))
        if edition in found or EDITIONS[FAMILY].get(edition) != meta:
            raise FetchError("UK HPI calendar edition needs review")
        found[edition] = meta
    future = {d for d in EDITIONS[FAMILY] if release_instant(d, family=FAMILY) > run_instant()}
    if not future.issubset(found):
        raise FetchError("UK HPI future calendar edition missing; needs review")
    return found


def parse(document, family=FAMILY, since="1900-01"):
    from .housing import observation
    if family != FAMILY:
        raise FetchError("UK HPI family needs review")
    raw = document.body.decode("utf-8")
    stamps = re.findall(r'<meta name="govuk:first-published-at" content="([^"]+)"', raw)
    if len(stamps) != 1:
        raise FetchError("UK HPI first-publication clock needs review")
    clock = datetime.fromisoformat(stamps[0])
    if clock.tzinfo is None:
        raise FetchError("UK HPI first-publication timezone needs review")
    local = clock.astimezone(ZoneInfo("Europe/London"))
    edition = local.date().isoformat()
    period, _, _ = reviewed(family, edition)
    expected = release_instant(edition, family=family)
    if not expected <= clock < expected + timedelta(hours=6) or document.url != publication_url(edition):
        raise FetchError("UK HPI issuer URL or first-publication time needs review")
    label = datetime.strptime(period, "%Y-%m").strftime("%B %Y")
    title = plain(require(r"(?s)<h1\b[^>]*>(.*?)</h1>", raw)[1])
    if title.lower() != ("UK House Price Index Summary: " + label).lower():
        raise FetchError("UK HPI national title or period needs review")
    body = plain(raw)
    require(r"1\. Headline statistics for " + label, body)
    printed = require(r"The annual price change for a property in the UK was ([+-]?\d+\.\d)%", body)[1]
    narrative = require(r"Average UK house price annual inflation was ([+-]?\d+\.\d)% \(provisional estimate\) in the 12 months to " + label, body)[1]
    require(r"On a non-seasonally adjusted basis, average UK house prices", body)
    require(r"Estimates for the most recent months are provisional", body)
    if printed != narrative:
        raise FetchError("UK HPI national headline and narrative disagree; needs review")
    row = replace(observation(document, family, edition, "uk_hpi_yy", printed),
                  **local_release(edition, local.strftime("%H:%M:%S"), "Europe/London"))
    return [row] if period >= since else []


def fetch(client, family=FAMILY, since="1900-01"):
    if run_date() > "2026-12-31":
        raise FetchError("UK HPI reviewed calendar horizon exhausted; needs review")
    eligible = {d: m for d, m in due(EDITIONS[family], family=family).items() if m[0] >= since}
    if not eligible:
        return []
    calendar_editions(client.get("ons", CALENDAR))
    out = []
    for edition in eligible:
        try:
            doc = get_edition(client, "ons", publication_url(edition), family, edition)
        except FetchError as exc:
            if str(exc) == "ons: HTTP 404":
                raise FetchError("UK HPI due publication missing; needs review") from None
            raise
        if doc is None:
            continue
        rows = parse(doc, family, since)
        if len(rows) != 1 or rows[0].edition != edition:
            raise FetchError("UK HPI requested edition needs review")
        if datetime.fromisoformat(rows[0].published_at).astimezone(timezone.utc) > run_instant():
            mark_pending(family, edition)
            continue
        out += rows
    return out
