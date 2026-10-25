"""NBS 70-city new commercial residential sales prices, official city tiers."""
from datetime import datetime
from html import escape
from html.parser import HTMLParser
import re
import unicodedata
from urllib.parse import urljoin

from .housing_editions import EDITIONS, reviewed
from .http import FetchError
from .schedule import due, get_edition, mark_pending, publication_grace, release_instant, run_date, run_instant
from .tier1_publications import plain, require

FAMILY = "cn_new_home_prices"
CALENDAR = "https://www.stats.gov.cn/xw/tjxw/tzgg/202512/t20251224_1962137.html"
INDEX = "https://www.stats.gov.cn/sj/sjjd/"
ARCHIVES = {
    "2026-09-15": "https://www.stats.gov.cn/sj/sjjd/202609/t20260915_1965303.html",
    "2026-10-19": "https://www.stats.gov.cn/sj/sjjd/202610/t20261019_1965610.html",
}
TIERS = ((1, "一", 4), (2, "二", 31), (3, "三", 35))


class Element:
    """Small stdlib tree for the issuer's metadata, tables and editor blocks."""
    def __init__(self, tag="", attrs=(), parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children = []

    def get(self, key, default=""):
        return self.attrs.get(key, default)

    def find_all(self, tag, attrs=None):
        found = []
        for child in self.children:
            if isinstance(child, Element):
                if child.tag == tag and all(child.get(k) == v for k, v in (attrs or {}).items()):
                    found.append(child)
                found.extend(child.find_all(tag, attrs))
        return found

    def next_sibling(self, tag):
        siblings = self.parent.children if self.parent else []
        return next((c for c in siblings[siblings.index(self) + 1:] if isinstance(c, Element) and c.tag == tag), None)

    def text(self):
        if self.tag in {"script", "style"}:
            return ""
        return "".join(c.text() if isinstance(c, Element) else c for c in self.children)

    def html(self):
        body = "".join(c.html() if isinstance(c, Element) else escape(c, quote=False) for c in self.children)
        if not self.tag:
            return body
        attrs = "".join(' ' + k + '="' + escape(v or "", quote=True) + '"' for k, v in self.attrs.items())
        return "<" + self.tag + attrs + ">" + body + ("" if self.tag in Page.VOID else "</" + self.tag + ">")


class Page(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.root = Element()
        self.stack = [self.root]
        self.feed(body.decode("utf-8"))
        self.close()

    def handle_starttag(self, tag, attrs):
        child = Element(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(child)
        if tag not in self.VOID:
            self.stack.append(child)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def editors(page):
    return [e for e in page.find_all("div") if "trs_editor_view" in e.get("class").split()]


def compact(raw):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", plain(raw)))


def calendar_editions(document):
    soup = Page(document.body).root
    require("2026年国家统计局主要统计信息发布日程表", compact(soup.text()))
    tables = set()
    for row in soup.find_all("tr"):
        cells = [compact(c.text()) for c in row.find_all("td")]
        if len(cells) != 14 or cells[1] != "商品住宅销售价格指数月度报告":
            continue
        clock_row = row.next_sibling("tr")
        clocks = tuple(compact(c.text()) for c in clock_row.find_all("td")) if clock_row else ()
        if len(clocks) != 12:
            raise FetchError("NBS housing calendar clock column layout needs review")
        tables.add((tuple(cells[2:]), clocks))
    if len(tables) != 1:
        raise FetchError("NBS housing calendar table needs review")
    days, clocks = tables.pop()
    found = {}
    for month, (day, clock) in enumerate(zip(days, clocks), 1):
        match = require(r"^(\d{1,2})/[一二三四五六日]$", day)
        edition = f"2026-{month:02d}-{int(match[1]):02d}"
        if not min(EDITIONS[FAMILY]) <= edition <= "2026-12-31":
            continue
        meta = (f"2026-{month - 1:02d}", "regular", clock.zfill(5))
        if EDITIONS[FAMILY].get(edition) != meta:
            raise FetchError("NBS housing calendar date or clock needs review")
        found[edition] = meta
    required = {d for d in EDITIONS[FAMILY] if release_instant(d, family=FAMILY) > run_instant()}
    if not required.issubset(found):
        raise FetchError("NBS future housing calendar missing; needs review")
    return found


def title_suffix(period):
    return f"解读{period[:4]}年{int(period[5:])}月份商品住宅销售价格变动情况统计数据"


def url_pattern(edition):
    return r"https://www\.stats\.gov\.cn/sj/sjjd/" + edition[:7].replace("-", "") + "/t" + edition.replace("-", "") + r"_\d+\.html"


def publication_links(document):
    urls = {}
    soup = Page(document.body).root
    for anchor in soup.find_all("a"):
        if not anchor.get("href"):
            continue
        text = compact(anchor.text())
        for edition, (period, _, _) in EDITIONS[FAMILY].items():
            if not text.startswith("国家统计局城市司") or not text.endswith(title_suffix(period)):
                continue
            url = urljoin(INDEX, anchor.get("href"))
            if not re.fullmatch(url_pattern(edition), url) or (edition in urls and urls[edition] != url):
                raise FetchError("NBS housing discovery identity needs review")
            urls[edition] = url
    return urls


def monthly_change(clause):
    clause = clause.split(",")[0]
    transition = re.fullmatch(r"由上月(?:(?:上涨|下降)\d+(?:\.\d+)?%|持平)转为((?:上涨|下降)\d+(?:\.\d+)?%|持平)", clause)
    if transition:
        clause = transition[1]
    if clause in ("持平", "与上月持平"):
        return 0.0, "持平"
    match = require(r"^(上涨|下降)(\d+(?:\.\d+)?)%$", clause)
    return float(match[2]) * (1 if match[1] == "上涨" else -1), match[0]


def parse(document, family=FAMILY, since="1900-01"):
    from .housing import observation
    if family != FAMILY:
        raise FetchError("NBS housing family needs review")
    soup = Page(document.body).root
    stamps = soup.find_all("meta", attrs={"name": "PubDate"})
    titles = soup.find_all("meta", attrs={"name": "ArticleTitle"})
    if len(stamps) != 1 or len(titles) != 1:
        raise FetchError("NBS housing publication metadata needs review")
    stamp = datetime.strptime(stamps[0].get("content", ""), "%Y/%m/%d %H:%M")
    edition = stamp.date().isoformat()
    period, _, clock = reviewed(family, edition)
    title = compact(titles[0].get("content", ""))
    if stamp.strftime("%H:%M") != clock or not title.startswith("国家统计局城市司") or not title.endswith(title_suffix(period)) or not re.fullmatch(url_pattern(edition), document.url):
        raise FetchError("NBS housing date, clock, title or issuer URL needs review")
    bodies = {compact(e.text()) for e in editors(soup)}
    if len(bodies) != 1:
        raise FetchError("NBS housing desktop/mobile publication agreement needs review")
    body = bodies.pop()
    require(period[:4] + "年" + str(int(period[5:])) + "月份,70个大中城市中", body)
    for _, name, count in TIERS:
        require(name + r"线城市指.*?等" + str(count) + "个城市", body)
    first = require(r"一线城市新建商品住宅销售价格同比(上涨|下降)(\d+(?:\.\d+)?)%", body)
    other = require(r"二、三线城市新建商品住宅销售价格同比分别(上涨|下降)(\d+(?:\.\d+)?)%和(\d+(?:\.\d+)?)%", body)
    annual = {1: (first[1], first[2]), 2: (other[1], other[2]), 3: (other[1], other[3])}
    rows = []
    for number, name, _ in TIERS:
        value, printed = monthly_change(require(name + r"线城市新建商品住宅销售价格环比(.*?)。", body)[1])
        rows.append(observation(document, family, edition, f"cn_new_home_prices_tier{number}_mm", printed, value=value))
        direction, printed = annual[number]
        value = float(printed) * (1 if direction == "上涨" else -1)
        rows.append(observation(document, family, edition, f"cn_new_home_prices_tier{number}_yy", direction + printed + "%", value=value))
    return rows if period >= since else []


def fetch(client, family=FAMILY, since="1900-01"):
    if run_date() > "2026-12-31":
        raise FetchError("NBS housing reviewed horizon exhausted; needs review")
    eligible = {d: m for d, m in due(EDITIONS[family], family=family).items() if m[0] >= since}
    if not eligible:
        return []
    calendar_editions(client.get("nbs", CALENDAR))
    urls = dict(ARCHIVES)
    if any(d not in urls for d in eligible):
        urls.update(publication_links(client.get("nbs", INDEX)))
    out = []
    for edition in eligible:
        if edition not in urls:
            if publication_grace(family, edition):
                mark_pending(family, edition)
                continue
            raise FetchError("NBS due housing headline publication missing; needs review")
        try:
            doc = get_edition(client, "nbs", urls[edition], family, edition)
        except FetchError as exc:
            if str(exc) == "nbs: HTTP 404":
                raise FetchError("NBS due housing publication missing; needs review") from None
            raise
        if doc is None:
            continue
        rows = parse(doc, family, since)
        if len(rows) != 6 or {r.edition for r in rows} != {edition}:
            raise FetchError("NBS requested housing headline edition needs review")
        out += rows
    return out
