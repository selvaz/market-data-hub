"""Private NAR issuer: only national headlines in public press releases.

Overview pages discover the current release link; they never supply values.
No licensed tables, redistributors, inferred revisions or calculated history.
"""
from dataclasses import replace
from datetime import datetime, timedelta
from html import unescape
import re
from urllib.parse import urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from .housing_editions import EDITIONS, reviewed
from .http import FetchError
from .schedule import current_edition_allowed, due, mark_pending, publication_grace, run_instant
from .tier1_publications import month, plain, require

CALENDAR = "https://www.nar.realtor/press-releases/nar-releases-2026-statistical-news-release-schedule"
LANDINGS = {
    "us_existing_home_sales": "https://www.nar.realtor/research-and-statistics/housing-statistics/existing-home-sales",
}


def metadata(document, family):
    parts = urlsplit(document.url)
    if parts.scheme != "https" or parts.hostname not in ("www.nar.realtor", "nar.realtor") or not parts.path.startswith("/newsroom/"):
        raise FetchError("NAR public press-release URL needs review")
    raw = document.body.decode("utf-8-sig")
    title = plain(require(r"<h1\b[^>]*>(.*?)</h1>", raw)[1])
    content = raw[require(r"</h1>", raw).end():]
    body = plain(content)
    stamp = require(r'<time\b[^>]*datetime="([^"]+)"[^>]*>(.*?)</time>', content)
    try:
        clock = datetime.fromisoformat(stamp[1])
        if clock.tzinfo is None:
            raise ValueError()
        local = clock.astimezone(ZoneInfo("America/New_York"))
        edition = local.date().isoformat()
        display_date = datetime.strptime(plain(stamp[2]), "%B %d, %Y").date().isoformat()
        dateline = require(r"WASHINGTON \((\w+ \d{1,2}, \d{4})\)", body)[1]
        dateline = datetime.strptime(dateline, "%B %d, %Y").date().isoformat()
    except ValueError:
        raise FetchError("NAR issuer page timestamp needs review") from None
    head, _, time = reviewed(family, edition)
    expected = datetime.fromisoformat(edition + "T" + time).replace(tzinfo=ZoneInfo("America/New_York"))
    if display_date != edition or dateline != edition or not expected <= local < expected + timedelta(hours=6):
        raise FetchError("NAR release date or issuer page clock needs review")
    require(r"National Association of REALTORS", body)
    return body, title, edition, head, local


def headline_row(document, family, edition, indicator, printed, local, *, value=None):
    from .housing import observation
    from .publication_times import local_release
    row = observation(document, family, edition, indicator, printed, value=value)
    # The schedule permits collection at 10:00 ET. The page's own clock may
    # be later (13 October: 10:01); preserve that observed publication instant.
    return replace(row, **local_release(edition, local.strftime("%H:%M:%S"), "America/New_York"))


def parse_existing(document, family="us_existing_home_sales", since="1900-01"):
    body, title, edition, head, local = metadata(document, family)
    require(r"Existing-Home Sales.*? in " + month(head).split()[0] + r"\b", title)
    summary = require(r"Month-Over-Month (.*?)Year-Over-Year", body)[1]
    headline = require(r"(\d+(?:\.\d+)?)% (increase|decrease) in existing-home sales to a "
                       r"seasonally adjusted annual rate of (\d+(?:\.\d+)?) million in (\w+)", summary)
    if headline[4] != month(head).split()[0]:
        raise FetchError("NAR existing-sales headline reference month needs review")
    intro = require(r"WASHINGTON \([^)]*\).*?Existing-home sales (increased|decreased) by "
                    r"(\d+(?:\.\d+)?)% month-over-month", body)
    if (intro[1].lower() != headline[2].lower() + "d" or intro[2] != headline[1] or
            headline[1] + "%" not in title):
        raise FetchError("NAR existing-sales title or national headline disagreement needs review")
    rows = [headline_row(document, family, edition, "us_existing_home_sales", headline[3], local),
            headline_row(document, family, edition, "us_existing_home_sales_mm",
                         headline[1] + "% " + headline[2], local,
                         value=float(headline[1]) * (-1 if headline[2].lower() == "decrease" else 1))]
    return rows if head >= since else []


def release_link(document, family):
    links = set()
    for url, anchor in re.findall(r'href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', document.body.decode("utf-8-sig"), re.S):
        if plain(anchor).rstrip(".").lower() != "read the full news release":
            continue
        parts = urlsplit(urljoin(document.url, unescape(url)))
        term = "existing-home-sales" if family == "us_existing_home_sales" else "pending-home-sales"
        if (parts.scheme != "https" or parts.hostname not in ("nar.realtor", "www.nar.realtor") or
                not parts.path.startswith("/newsroom/") or term not in parts.path or parts.query or parts.fragment):
            raise FetchError("NAR current issuer press-release link needs review")
        # NAR's pending overview links its bare hostname, which redirects to
        # www. Normalize only these two reviewed publisher hostnames.
        links.add(urlunsplit(parts._replace(netloc="www.nar.realtor")))
    if len(links) != 1:
        raise FetchError("NAR current press-release link is missing or ambiguous; needs review")
    return links.pop()


PARSERS = {"us_existing_home_sales": parse_existing}


def fetch(client, family, since):
    if not due(EDITIONS[family], family=family):
        return []
    index = client.get("nar", LANDINGS[family])
    try:
        publication = client.get("nar", release_link(index, family))
    except FetchError as exc:
        if str(exc) in {"nar: HTTP " + str(status) for status in (301, 302, 303, 307, 308)}:
            raise FetchError("NAR public press-release redirect needs review") from None
        raise
    rows = PARSERS[family](publication, family, since)
    if not rows:
        return []
    edition = rows[0].edition
    if not current_edition_allowed(family, edition):
        return []
    if datetime.fromisoformat(rows[0].published_at) > run_instant():
        if not publication_grace(family, edition):
            raise FetchError("NAR future issuer page timestamp needs review")
        mark_pending(family, edition)
        return []
    return rows
