"""Discover edition URLs only through public issuer links, never guessed IDs."""
from datetime import datetime
import re
from urllib.parse import parse_qs, urljoin, urlsplit

from .asia_editions import reviewed, publication_date_allowed
from .http import FetchError
from .nbs_housing import Page
from .tier1_publications import plain, require

MOF_INDEX = "https://www.customs.go.jp/toukei/shinbun/happyou_e.htm"
NBS_INDEX = "https://www.stats.gov.cn/english/PressRelease/"
CPI_INDEX = "https://www.e-stat.go.jp/en/stat-search/files?page=1&layout=datalist&toukei=00200573&tstat=000001243876&cycle=1&tclass1=000001243877&tclass2val=0"


def unique(urls):
    if len(urls) > 1:
        raise FetchError("Asia issuer publication links disagree; needs review")
    return next(iter(urls), None)


def get(client, cache, provider, url):
    key = provider, url
    if key not in cache:
        cache[key] = client.get(provider, url)
    return cache[key]


def trade_link(document, edition):
    period, stage, _ = reviewed("jp_trade_balance", edition)
    label = datetime.strptime(period, "%Y-%m").strftime("%b.%Y")
    stamp = datetime.strptime(edition, "%Y-%m-%d").strftime("%b.%d.%Y").replace(".0", ".")
    wording = "(Prov.)" if stage == "preliminary" else "(D&P)"
    urls = set()
    for row in Page(document.body).root.find_all("tr"):
        cells = [plain(c.text()) for c in row.find_all("td")]
        if len(cells) < 3 or cells[0] != label or not cells[1].startswith(stamp):
            continue
        if cells[1] != stamp + wording:
            raise FetchError("MOF trade listing stage needs review")
        for link in row.find_all("a"):
            if plain(link.text()) != "XML":
                continue
            url = urljoin(MOF_INDEX, link.get("href"))
            if not re.fullmatch(r"https://www\.customs\.go\.jp/toukei/shinbun/trade-st_e/2026/\d{7}e\.xml", url):
                raise FetchError("MOF trade listing format or issuer URL needs review")
            urls.add(url)
    return unique(urls)


def cpi_link(document, family, edition):
    from .bls import shift_month
    period, _, _ = reviewed(family, edition)
    tokyo = family == "jp_tokyo_cpi"
    survey = shift_month(period, -1) if tokyo else period
    label = "Subgroup Index for Ku-area of Tokyo" if tokyo else "Subgroup Index for Japan"
    require("2025-Base Consumer Price Index", plain(document.body.decode("utf-8")))
    expected = label + " Monthly Survey date " + datetime.strptime(survey, "%Y-%m").strftime("%Y%b.") + " Update date " + edition
    urls = set()
    for group in Page(document.body).root.find_all("ul", {"class": "stat-dataset_list-detail"}):
        if expected not in plain(group.text()):
            continue
        for link in group.find_all("a"):
            url = urljoin(document.url, link.get("href"))
            if "file-download" not in url:
                continue
            parts = urlsplit(url)
            query = parse_qs(parts.query)
            if query.get("fileKind") != ["4"]:
                continue
            if (parts.scheme != "https" or parts.netloc != "www.e-stat.go.jp" or
                    parts.path != "/en/stat-search/file-download" or
                    not re.fullmatch(r"\d+", "".join(query.get("statInfId", []))) or
                    link.get("data-release_count") != ("2" if tokyo else "1")):
                raise FetchError("e-Stat publication format or counter needs review")
            urls.add(url)
    return unique(urls)


def cpi_listing(client, cache, family, edition):
    from .japan_macro import CPI_LISTINGS
    from .bls import shift_month
    if edition in CPI_LISTINGS:
        return get(client, cache, "statjp", CPI_LISTINGS[edition])
    period, _, _ = reviewed(family, edition)
    survey = shift_month(period, -1) if family == "jp_tokyo_cpi" else period
    index = get(client, cache, "statjp", CPI_INDEX)
    urls = set()
    for link in Page(index.body).root.find_all("a"):
        url = urljoin(CPI_INDEX, link.get("href"))
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        if query.get("year") != [survey[:4] + "0"] or plain(link.text()) != datetime.strptime(survey, "%Y-%m").strftime("%b."):
            continue
        if (parts.scheme != "https" or parts.netloc != "www.e-stat.go.jp" or parts.path != "/en/stat-search/files" or
                query.get("toukei") != ["00200573"] or query.get("tstat") != ["000001243876"] or
                not re.fullmatch(r"\d{6}" + survey[5:], "".join(query.get("month", [])))):
            raise FetchError("e-Stat monthly listing URL needs review")
        urls.add(url)
    url = unique(urls)
    return get(client, cache, "statjp", url) if url else None


def gdp_link(client, cache, edition, known):
    from .japan_macro import GDP_ARCHIVE, GDP_MENUS, gdp_archive_listing
    archive = get(client, cache, "esri", GDP_ARCHIVE)
    if known:
        gdp_archive_listing(archive, get(client, cache, "esri", GDP_MENUS[edition]), edition)
        return known
    period, stage, _ = reviewed("jp_gdp", edition)
    stamp = datetime.strptime(edition, "%Y-%m-%d").strftime("%b %d, %Y").replace(" 0", " ")
    label = {"Q2": "Apr.-Jun.", "Q3": "Jul.-Sep."}[period[-2:]] + period[:4]
    ordinal = "First" if stage == "preliminary" else "Second"
    menus = set()
    for row in Page(archive.body).root.find_all("tr"):
        if not plain(row.text()).startswith(stamp + " "):
            continue
        for link in row.find_all("a"):
            if plain(link.text()) != "Quarterly Estimates of GDP for " + label + " (The " + ordinal + " preliminary Estimates)":
                continue
            url = urljoin(GDP_ARCHIVE, link.get("href"))
            if not re.fullmatch(r"https://www\.esri\.cao\.go\.jp/en/sna/data/sokuhou/files/2026/qe\d+(?:_2)?/gdemenuea\.html", url):
                raise FetchError("ESRI GDP edition menu URL needs review")
            menus.add(url)
    menu = unique(menus)
    if not menu:
        return None
    urls = set()
    for link in Page(get(client, cache, "esri", menu).body).root.find_all("a"):
        if not plain(link.text()).startswith("Real, Seasonally Adjusted Series (Quarter-to-Quarter) (csv:"):
            continue
        url = urljoin(menu, link.get("href"))
        directory = menu.split("/2026/")[1].split("/")[0]
        if not re.fullmatch(r"https://www\.esri\.cao\.go\.jp/jp/sna/data/data_list/sokuhou/files/2026/" + re.escape(directory) + r"/tables/ritu-jk\d+\.csv", url):
            raise FetchError("ESRI GDP rate CSV URL needs review")
        urls.add(url)
    return unique(urls)


def nbs_link(document, family, edition):
    period, _, _ = reviewed(family, edition)
    label = datetime.strptime(period, "%Y-%m").strftime("%B %Y") if "-Q" not in period else None
    expected = {"cn_cpi": "Consumer Price Index in " + (label or ""),
                "cn_nbs_pmi": "Purchasing Managers’ Index for " + (label or "")}.get(family)
    urls = set()
    for row in Page(document.body).root.find_all("li"):
        for link in row.find_all("a"):
            title = link.get("title") or re.sub(r"^\d+\.", "", plain(link.text()))
            activity = family == "cn_activity" and title.startswith("National Economy ") and re.search(
                r"\bin (?:the )?" + datetime.strptime(period, "%Y-%m").strftime("%B") + r"(?: 2026)?$", title)
            if not activity and (expected is None or title != expected):
                continue
            stamps = [plain(s.text()) for s in row.find_all("span")]
            if not any(publication_date_allowed(edition, stamp) for stamp in stamps):
                continue
            url = urljoin(NBS_INDEX, link.get("href"))
            if not re.fullmatch(r"https://www\.stats\.gov\.cn/english/PressRelease/\d{6}/t\d{8}_\d+\.html", url):
                raise FetchError("NBS publication listing URL needs review")
            urls.add(url)
    return unique(urls)


def publication_url(client, family, edition, known, cache):
    from . import japan_macro
    if family in {"jp_cpi", "jp_tokyo_cpi"}:
        listing = cpi_listing(client, cache, family, edition)
        url = cpi_link(listing, family, edition) if listing else None
        if known and url != known:
            raise FetchError("e-Stat reviewed archive identity needs review")
        return url
    if family == "jp_gdp":
        return gdp_link(client, cache, edition, known)
    if family == "jp_trade_balance":
        url = known or trade_link(get(client, cache, "mof", MOF_INDEX), edition)
        if url:
            japan_macro.trade_stylesheet(get(client, cache, "mof", japan_macro.TRADE_STYLE))
        return url
    if known:
        return known
    if family.startswith("cn_"):
        return nbs_link(get(client, cache, "nbs", NBS_INDEX), family, edition)
    return None
