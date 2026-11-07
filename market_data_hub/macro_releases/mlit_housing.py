"""MLIT national new residential housing starts, published annual change."""
from datetime import date
import re
import unicodedata
from urllib.parse import urljoin

from .housing_editions import EDITIONS, reviewed
from .http import FetchError
from .schedule import due, get_edition, mark_pending, publication_grace, release_instant, run_date, run_instant
from .tier1_publications import plain, require

FAMILY = "jp_housing_starts"
CALENDAR = "https://www.mlit.go.jp/statistics/details/t-other-2_tk_000214.html"
ARCHIVES = {
    "2026-08-31": "https://www.mlit.go.jp/report/press/joho04_hh_001390.html",
    "2026-09-30": "https://www.mlit.go.jp/report/press/joho04_hh_001395.html",
}


def normalized(raw):
    return unicodedata.normalize("NFKC", plain(raw))


def title_for(period):
    return f"建築着工統計調査報告(令和{int(period[:4]) - 2018}年{int(period[5:])}月分)"


def calendar_editions(document):
    section = require(r"今後の公表予定 <建築着工統計調査> (.*?) <建築工事費調査>", normalized(document.body.decode("utf-8")))[1]
    found = {}
    for year, month, ry, rm, rd, hour, minute in re.findall(r"令和(\d+)年(\d+)月分(?:及び令和\d+年(?:度)?計)?[・.]+令和(\d+)年(\d+)月(\d+)日(\d+)時(\d+)分", section):
        edition = date(2018 + int(ry), int(rm), int(rd)).isoformat()
        if not min(EDITIONS[FAMILY]) <= edition <= "2026-12-31":
            continue
        meta = (f"{2018 + int(year):04d}-{int(month):02d}", "regular", f"{int(hour):02d}:{int(minute):02d}")
        if edition in found or EDITIONS[FAMILY].get(edition) != meta:
            raise FetchError("MLIT housing calendar date, period or clock needs review")
        found[edition] = meta
    required = {d for d in EDITIONS[FAMILY] if release_instant(d, family=FAMILY) > run_instant()}
    if not required.issubset(found):
        raise FetchError("MLIT future housing calendar missing; needs review")
    return found


def index_url(edition):
    return "https://www.mlit.go.jp/report/press/houdou" + edition[:7].replace("-", "") + ".html"


def publication_link(document, edition):
    label = title_for(reviewed(FAMILY, edition)[0])
    urls = set()
    for href, text in re.findall(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', document.body.decode("utf-8"), re.S):
        if normalized(text) == label:
            url = urljoin("https://www.mlit.go.jp", href)
            if not re.fullmatch(r"https://www\.mlit\.go\.jp/report/press/joho04_hh_\d+\.html", url):
                raise FetchError("MLIT housing discovery URL needs review")
            urls.add(url)
    if len(urls) > 1:
        raise FetchError("MLIT housing duplicate publication needs review")
    return next(iter(urls), None)


def parse(document, family=FAMILY, since="1900-01"):
    from .housing import observation
    if family != FAMILY or not re.fullmatch(r"https://www\.mlit\.go\.jp/report/press/joho04_hh_\d+\.html", document.url):
        raise FetchError("MLIT housing issuer identity needs review")
    raw = document.body.decode("utf-8")
    stamp = require(r"令和(\d+)年(\d+)月(\d+)日", normalized(require(r'<p class="date mb20">(.*?)</p>', raw)[1]))
    edition = date(2018 + int(stamp[1]), int(stamp[2]), int(stamp[3])).isoformat()
    period, _, _ = reviewed(family, edition)
    if normalized(require(r'<h2 class="title">(.*?)</h2>', raw)[1]) != title_for(period) or (edition in ARCHIVES and document.url != ARCHIVES[edition]):
        raise FetchError("MLIT housing title, period or dated issuer URL needs review")
    section = require(r"◇住宅着工統計 (.*?) ◇建築物着工統計", normalized(raw))[1]
    headline = require(str(int(period[5:])) + r"月の新設住宅着工は.*?全体で前年同月比(\d+(?:\.\d+)?)%の(増加|減少)となった", section)
    value = float(headline[1]) * (1 if headline[2] == "増加" else -1)
    return [observation(document, family, edition, FAMILY + "_yy", headline[1], value=value)] if period >= since else []


def fetch(client, family=FAMILY, since="1900-01"):
    if run_date() > "2026-12-31":
        raise FetchError("MLIT housing reviewed horizon exhausted; needs review")
    eligible = {d: m for d, m in due(EDITIONS[family], family=family).items() if m[0] >= since}
    if not eligible:
        return []
    calendar_editions(client.get("mlit", CALENDAR))
    out = []
    for edition in eligible:
        url = ARCHIVES.get(edition)
        if url is None:
            url = publication_link(client.get("mlit", index_url(edition)), edition)
        if url is None:
            if publication_grace(family, edition):
                mark_pending(family, edition)
                continue
            raise FetchError("MLIT due housing publication link missing; needs review")
        try:
            doc = get_edition(client, "mlit", url, family, edition)
        except FetchError as exc:
            if str(exc) == "mlit: HTTP 404":
                raise FetchError("MLIT due housing publication missing; needs review") from None
            raise
        if doc is None:
            continue
        rows = parse(doc, family, since)
        if len(rows) != 1 or rows[0].edition != edition:
            raise FetchError("MLIT requested housing edition needs review")
        out += rows
    return out
