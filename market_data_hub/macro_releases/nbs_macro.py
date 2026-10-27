"""NBS public English headlines; translated publication dates stay explicit."""
from datetime import datetime
import re

from .asia import observation, fetch_archives
from .asia_editions import reviewed
from .http import FetchError
from .nbs_housing import Page
from .tier1_publications import month, plain, require

ARCHIVES = {
    "cn_cpi": {
        "2026-09-10": "https://www.stats.gov.cn/english/PressRelease/202609/t20260910_1965275.html",
        "2026-10-15": "https://www.stats.gov.cn/english/PressRelease/202610/t20261015_1965500.html",
    },
}


def metadata(document, family):
    page = Page(document.body).root
    stamps = page.find_all("meta", {"name": "PubDate"})
    titles = page.find_all("meta", {"name": "ArticleTitle"})
    if len(stamps) != 1 or len(titles) != 1:
        raise FetchError("NBS publication metadata needs review")
    stamp = datetime.strptime(stamps[0].get("content"), "%Y/%m/%d %H:%M")
    edition = stamp.date().isoformat()
    period, _, clock = reviewed(family, edition)
    if stamp.strftime("%H:%M") != clock or document.url != ARCHIVES[family].get(edition):
        raise FetchError("NBS publication clock or issuer URL needs review")
    return page, edition, period, titles[0].get("content")


def parse(document, family="cn_cpi", since="1900-01"):
    page, edition, period, title = metadata(document, family)
    if family != "cn_cpi" or title != "Consumer Price Index in " + month(period):
        raise FetchError("NBS publication title or measure needs review")
    body = plain(document.body.decode("utf-8"))
    match = require(r"In " + month(period) + r", China[’']s Consumer Price Index \(CPI\) (increased|decreased) by ([\d.]+)% year on year\.", body)
    printed = ("-" if match[1] == "decreased" else "") + match[2]
    # Independently require the table and its m/m, y/y and cumulative columns.
    table = require(r"Consumer Price Indexes in " + month(period) + r" Growth Rate M/M \(%\) Growth Rate Y/Y \(%\) Jan-\w+ Growth Rate Y/Y \(%\) Consumer Price Index ([+-]?[\d.]+) ([+-]?[\d.]+) ([+-]?[\d.]+)", body)
    if float(table[2]) != float(printed):
        raise FetchError("NBS headline and annual table disagree; needs review")
    return [observation(document, family, edition, "cn_cpi_yy", printed)] if period >= since else []


def fetch(client, family, since):
    return fetch_archives(client, family, since, "nbs", ARCHIVES[family], parse)
