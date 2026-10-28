"""NBS headlines dated at first official publication, with English-lag checks."""
from datetime import datetime
import re

from .asia import observation, fetch_archives, in_window
from .asia_editions import reviewed, publication_date_allowed
from .http import FetchError
from .nbs_housing import Page, compact
from .tier1_publications import month, plain, require

ARCHIVES = {
    "cn_gdp": {
        "2026-07-16": "https://www.stats.gov.cn/zwfwck/sjfb/202607/t20260716_1964142.html",
        "2026-10-19": "https://www.stats.gov.cn/english/PressRelease/202610/t20261019_1965619.html",
        "2026-10-20": "https://www.stats.gov.cn/sj/zxfb/202610/t20261020_1965658.html",
    },
    "cn_activity": {
        "2026-09-15": "https://www.stats.gov.cn/english/PressRelease/202609/t20260915_1965305.html",
        "2026-10-19": "https://www.stats.gov.cn/english/PressRelease/202610/t20261019_1965619.html",
    },
    "cn_nbs_pmi": {
        "2026-08-31": "https://www.stats.gov.cn/english/PressRelease/202609/t20260901_1965170.html",
        "2026-09-30": "https://www.stats.gov.cn/english/PressRelease/202610/t20261008_1965466.html",
    },
    "cn_cpi": {
        "2026-09-09": "https://www.stats.gov.cn/english/PressRelease/202609/t20260910_1965275.html",
        "2026-10-14": "https://www.stats.gov.cn/english/PressRelease/202610/t20261015_1965500.html",
    },
}


def metadata(document, family, archives):
    page = Page(document.body).root
    stamps = page.find_all("meta", {"name": "PubDate"})
    titles = page.find_all("meta", {"name": "ArticleTitle"})
    if family == "cn_gdp" and document.url == ARCHIVES[family]["2026-07-16"] and not stamps and not titles:
        # NBS's older public-service template prints the date in the body.
        title_nodes = page.find_all("title")
        if len(title_nodes) != 1:
            raise FetchError("NBS GDP archive title needs review")
        stamp = datetime.strptime(require(r"发布时间：\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2})",
            plain(document.body.decode("utf-8")))[1], "%Y-%m-%d %H:%M")
        title = title_nodes[0].text().strip()
    elif len(stamps) != 1 or len(titles) != 1:
        raise FetchError("NBS publication metadata needs review")
    else:
        stamp = datetime.strptime(stamps[0].get("content"), "%Y/%m/%d %H:%M")
        title = titles[0].get("content")
    editions = [d for d, url in archives.items() if url == document.url]
    if len(editions) != 1:
        raise FetchError("NBS issuer archive edition needs review")
    edition = editions[0]
    period, _, clock = reviewed(family, edition)
    if (stamp.strftime("%H:%M") != clock or not publication_date_allowed(
            edition, stamp.date().isoformat(), english="/english/" in document.url)):
        raise FetchError("NBS publication lag or clock needs review")
    return page, edition, period, title


def parse(document, family="cn_cpi", since="1900-01", *, archives=None):
    archives = ARCHIVES[family] if archives is None else archives
    page, edition, period, title = metadata(document, family, archives)
    if family == "cn_gdp":
        if edition == "2026-10-19":
            if title != "National Economy was Generally Stable with New Achievement Made in High-Quality Development in the First Three Quarters":
                raise FetchError("NBS first GDP announcement title needs review")
            body = plain(document.body.decode("utf-8"))
            require("National Bureau of Statistics of China 19 October, 2026", body)
            printed = require(r"According to preliminary estimates, the gross domestic product \(GDP\) in the first three quarters of 2026 reached [\d,.]+ billion yuan, up by ([\d.]+) percent year on year at constant prices", body)[1]
            return [observation(document, family, edition, "cn_gdp_yy", printed)] if in_window(period, since) else []
        quarter, cumulative = {"2026-Q2": ("二季度", "上半年"), "2026-Q3": ("三季度", "前三季度")}[period]
        expected_title = "2026年" + ("二季度和上半年" if period.endswith("Q2") else quarter) + "国内生产总值初步核算结果"
        if title != expected_title:
            raise FetchError("NBS preliminary GDP title needs review")
        tables = [t for t in page.find_all("table") if compact(t.text()).startswith(
            "绝对额(亿元)比上年同期增长(%)" + quarter + cumulative + quarter + cumulative)]
        values = set()
        for table in tables:
            require("增长速度按不变价计算", compact(table.text()))
            for row in table.find_all("tr"):
                cells = tuple(compact(c.text()) for c in row.find_all("td"))
                if len(cells) == 5 and cells[0] == "GDP":
                    if not all(re.fullmatch(r"[+-]?\d+(?:\.\d+)?", v) for v in cells[1:]):
                        raise FetchError("NBS GDP numeric cells need review")
                    values.add(cells)
        if len(values) != 1:
            raise FetchError("NBS GDP cumulative column layout needs review")
        printed = values.pop()[4]  # printed cumulative real growth, not the quarterly rate in column 3
        return [observation(document, family, edition, "cn_gdp_yy", printed, "html_table")] if in_window(period, since) else []
    if family == "cn_activity":
        expected = {"2026-08": "National Economy Remained Steady with Innovation-Led and High-Quality Development in August",
                    "2026-09": "National Economy was Generally Stable with New Achievement Made in High-Quality Development in the First Three Quarters"}
        if period in expected:
            if title != expected[period]:
                raise FetchError("NBS national economy title needs review")
        else:
            require(r"^National Economy .*(?:in |in the )" +
                    datetime.strptime(period, "%Y-%m").strftime("%B") + r"(?: 2026)?$", title)
        body = plain(document.body.decode("utf-8"))
        require("National Bureau of Statistics of China", body)
        require(datetime.strptime(edition, "%Y-%m-%d").strftime("%d %B, %Y").lstrip("0"), body)
        label = datetime.strptime(period, "%Y-%m").strftime("%B")
        ip = require(r"In " + label + r", the (?:total )?value added of industrial enterprises above the designated size (grew|went up|increased|decreased) by ([\d.]+) percent year on year", body)
        retail = require(r"In " + label + r", the total retail sales of consumer goods reached [\d,.]+ billion yuan, (up|down) by ([\d.]+) percent year on year", body)
        return [observation(document, family, edition, indicator,
                    ("-" if match[1] in {"decreased", "down"} else "") + match[2])
                for indicator, match in (("cn_industrial_production_yy", ip), ("cn_retail_sales_yy", retail))] if in_window(period, since) else []
    if family == "cn_nbs_pmi":
        if title != "Purchasing Managers’ Index for " + month(period):
            raise FetchError("NBS PMI publication title needs review")
        body = plain(document.body.decode("utf-8"))
        require(r"Department of Service Statistics of NBS of China China Federation of Logistics & Purchasing \(CFLP\)", body)
        require(r"China[’']s Manufacturing PMI and Sub-indexes \(Seasonally Adjusted\)", body)
        require(r"All released indexes have been seasonally adjusted", body)
        label = datetime.strptime(period, "%Y-%m").strftime("%B")
        printed = require(r"In " + label + r", the purchasing managers[’'] index \(PMI\) of China[’']s manufacturing industry was ([\d.]+)%", body)[1]
        return [observation(document, family, edition, "cn_nbs_manufacturing_pmi", printed)] if in_window(period, since) else []
    if family != "cn_cpi" or title != "Consumer Price Index in " + month(period):
        raise FetchError("NBS publication title or measure needs review")
    body = plain(document.body.decode("utf-8"))
    match = require(r"In " + month(period) + r", China[’']s Consumer Price Index \(CPI\) (increased|decreased) by ([\d.]+)% year on year\.", body)
    printed = ("-" if match[1] == "decreased" else "") + match[2]
    # Independently require the table and its m/m, y/y and cumulative columns.
    table = require(r"Consumer Price Indexes in " + month(period) + r" Growth Rate M/M \(%\) Growth Rate Y/Y \(%\) Jan-\w+ Growth Rate Y/Y \(%\) Consumer Price Index ([+-]?[\d.]+) ([+-]?[\d.]+) ([+-]?[\d.]+)", body)
    if float(table[2]) != float(printed):
        raise FetchError("NBS headline and annual table disagree; needs review")
    return [observation(document, family, edition, "cn_cpi_yy", printed)] if in_window(period, since) else []


def fetch(client, family, since):
    return fetch_archives(client, family, since, "nbs", ARCHIVES[family], parse)
