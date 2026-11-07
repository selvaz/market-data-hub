"""Private S&P issuer: national and Composite-20 public y/y headlines only.

The press archive is free and machine-readable. The index site's 403 is not
bypassed. Detailed index tables and FRED history never certify a headline.
"""
from datetime import datetime
from html import unescape
import re
from urllib.parse import urlsplit

from .housing_editions import EDITIONS, reviewed
from .http import FetchError
from .schedule import due, mark_pending, publication_grace
from .tier1_publications import month, plain, require

FAMILY = "us_case_shiller"
INDEX = "https://press.spglobal.com/index.php?s=2429&keywords=Case-Shiller&titles_only=1&l=25"
CALENDAR = "https://press.spglobal.com/2026-09-29-S-P-Cotality-Case-Shiller-Index-Reports-Annual-Gain-in-July-2026"


def release_date(url):
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.netloc != "press.spglobal.com" or parts.query or parts.fragment):
        raise FetchError("S&P issuer press-release URL needs review")
    return require(r"^/(\d{4}-\d{2}-\d{2})-S-P-Cotality-Case-Shiller-Index-Reports-", parts.path)[1]


def parse(document, family=FAMILY, since="1900-01"):
    from .housing import observation
    edition = release_date(document.url)
    head, _, time = reviewed(family, edition)
    raw = document.body.decode("utf-8-sig")
    title = plain(require(r'<div class="wd_title wd_language_left">(.*?)</div>', raw)[1])
    title_change = require(r"^S&P Cotality Case-Shiller Index Reports Annual (Gain|Decline|Loss) in " + month(head) + "$", title)[1]
    body = plain(raw)
    clock = require(r"The S&P Cotality Case-Shiller Indices are published on the last Tuesday of each month at (\d{1,2}):00 am ET", body)
    if time != clock[1].zfill(2) + ":00":
        raise FetchError("S&P issuer release clock needs review")
    stamp = require(r"NEW YORK , (\w+\.? \d{1,2}, \d{4}) / PRNewswire / -- S&P Dow Jones Indices "
                    r"\(S&P DJI\) today released the (\w+ \d{4}) results for the S&P Cotality Case-Shiller Indices", body)
    date_text = stamp[1].replace(".", "").replace("Sept ", "Sep ")
    try:
        dateline = datetime.strptime(date_text, "%b %d, %Y").date().isoformat()
    except ValueError:
        raise FetchError("S&P issuer dateline needs review") from None
    if dateline != edition or stamp[2] != month(head):
        raise FetchError("S&P issuer release date or reference period needs review")
    require(r"non-seasonally adjusted data set used in the headline indices", body)
    # Isolate the printed narrative before supporting data; do not use tables,
    # city results, SA monthly rates or a later edition's revised prior figure.
    headline = require(r"Index Reports.*?The S&P Cotality Case-Shiller U\.S\. National Home Price NSA Index "
                       r"posted a (\d+(?:\.\d+)?)% annual (gain|decline|loss) for (\w+ \d{4})", body)
    annual = require(r"YEAR-OVER-YEAR (The S&P Cotality.*?)MONTH-OVER-MONTH", body)[1]
    national = require(r"^The S&P Cotality Case-Shiller U\.S\. National Home Price NSA Index, covering all nine "
                       r"U\.S\. census divisions, reported a (\d+(?:\.\d+)?)% annual (gain|decline|loss) for (\w+)\.", annual)
    twenty = require(r"The 20-City Composite posted a year-over-year (increase|decrease) of (\d+(?:\.\d+)?)%", annual)
    if ((headline[1], headline[2]) != (national[1], national[2]) or headline[3] != month(head) or
            national[3] != month(head).split()[0] or
            (title_change.lower() == "gain") != (national[2].lower() == "gain")):
        raise FetchError("S&P national headline or reference month disagreement needs review")
    rows = [observation(document, family, edition, "us_case_shiller_national_yy",
                        national[1] + "% annual " + national[2],
                        value=float(national[1]) * (1 if national[2].lower() == "gain" else -1)),
            observation(document, family, edition, "us_case_shiller_20_yy",
                        twenty[2] + "% year-over-year " + twenty[1],
                        value=float(twenty[2]) * (1 if twenty[1].lower() == "increase" else -1))]
    return rows if head >= since else []


def release_links(document):
    links = {}
    for url, label in re.findall(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', document.body.decode("utf-8-sig"), re.S):
        if not re.search(r"Case-Shiller Index Reports", plain(label), re.I):
            continue
        url = unescape(url)
        edition = release_date(url)
        if edition not in EDITIONS[FAMILY]:
            continue
        if edition in links and links[edition] != url:
            raise FetchError("S&P issuer press-release edition links are ambiguous; needs review")
        links[edition] = url
    return links


def fetch(client, family=FAMILY, since="1900-01"):
    eligible = {d: meta for d, meta in due(EDITIONS[family], family=family).items() if meta[0] >= since}
    if not eligible:
        return []
    links = release_links(client.get("spglobal", INDEX))
    out = []
    for edition in eligible:
        if edition not in links:
            if edition == max(eligible) and publication_grace(family, edition):
                mark_pending(family, edition)
                continue
            raise FetchError("S&P due issuer press-release link is missing; needs review")
        try:
            document = client.get("spglobal", links[edition])
        except FetchError as exc:
            if str(exc) in {"spglobal: HTTP " + str(s) for s in (301, 302, 303, 307, 308, 404, 410)}:
                raise FetchError("S&P issuer press-release URL retired or redirected; needs review") from None
            raise
        rows = parse(document, family, since)
        if not rows or {o.edition for o in rows} != {edition}:
            raise FetchError("S&P requested issuer press edition needs review")
        out += rows
    return out
