"""Dated Destatis residential permits: dwellings, not buildings or YTD totals."""
from datetime import datetime
import re
from urllib.parse import urljoin

from .housing_editions import EDITIONS, reviewed
from .http import FetchError
from .schedule import due, get_edition, mark_pending, publication_grace, release_instant, run_date, run_instant
from .tier1_publications import plain, require

FAMILY = "de_building_permits"
CALENDAR = "https://www.destatis.de/SiteGlobals/Forms/Suche/Termine/EN/Terminsuche_Formular.html?nn=1426424&resourceId=263318&input_=263386&pageLocale=en&templateQueryString=Building+permits"
INDEX = "https://www.destatis.de/SiteGlobals/Forms/Suche/Presse/DE/Pressesuche_Formular_2.html?resourceId=245598&input_=250572&pageLocale=de&templateQueryString=Baugenehmigungen+f%C3%BCr+Wohnungen"
ARCHIVES = {
    "2026-09-18": "https://www.destatis.de/DE/Presse/Pressemitteilungen/2026/09/PD26_330_3111.html",
    "2026-10-16": "https://www.destatis.de/DE/Presse/Pressemitteilungen/2026/10/PD26_370_3111.html",
}
MONTHS = "Januar Februar März April Mai Juni Juli August September Oktober November Dezember".split()


def german_date(day, month, year):
    return f"{int(year):04d}-{MONTHS.index(month) + 1:02d}-{int(day):02d}"


def calendar_editions(document):
    body = plain(document.body.decode("utf-8"))
    require(r"We generally publish our press releases at 8 a\.m\. \(CET/CEST\)", body)
    found = {}
    for label, stamp in re.findall(r"Building permits Reporting period : (\w+ \d{4}) Date of issue : (\d{4}\.\d{2}\.\d{2})", body):
        edition = stamp.replace(".", "-")
        if not min(EDITIONS[FAMILY]) <= edition <= "2026-12-31":
            continue
        meta = (datetime.strptime(label, "%B %Y").strftime("%Y-%m"), "regular", "08:00")
        if edition in found or EDITIONS[FAMILY].get(edition) != meta:
            raise FetchError("Destatis permits calendar edition needs review")
        found[edition] = meta
    required = {d for d in EDITIONS[FAMILY] if release_instant(d, family=FAMILY) > run_instant()}
    if not required.issubset(found):
        raise FetchError("Destatis permits future calendar missing; needs review")
    return found


def publication_links(document):
    urls = {}
    for href, content in re.findall(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', document.body.decode("utf-8"), re.S):
        text = plain(content)
        match = re.search(r"Pressemitteilung Nr\. \d+ vom (\d+)\. (\w+) (\d{4}) Baugenehmigungen für Wohnungen im (\w+) (\d{4}):", text)
        if not match:
            continue
        edition = german_date(*match.group(1, 2, 3))
        if edition not in EDITIONS[FAMILY]:
            continue
        period = f"{int(match[5]):04d}-{MONTHS.index(match[4]) + 1:02d}"
        url = urljoin("https://www.destatis.de/", href)
        if period != reviewed(FAMILY, edition)[0] or not re.fullmatch(r"https://www\.destatis\.de/DE/Presse/Pressemitteilungen/2026/\d{2}/PD26_\d+_3111\.html", url):
            raise FetchError("Destatis permits discovery identity needs review")
        if edition in urls and urls[edition] != url:
            raise FetchError("Destatis permits duplicate publication needs review")
        urls[edition] = url
    return urls


def parse(document, family=FAMILY, since="1900-01"):
    from .housing import observation
    if family != FAMILY or not re.fullmatch(r"https://www\.destatis\.de/DE/Presse/Pressemitteilungen/2026/\d{2}/PD26_\d+_3111\.html", document.url):
        raise FetchError("Destatis permits issuer identity needs review")
    raw = document.body.decode("utf-8")
    body = plain(raw)
    stamp = require(r"Pressemitteilung Nr\. \d+ vom (\d+)\. (\w+) (\d{4})", body)
    edition = german_date(*stamp.group(1, 2, 3))
    period, _, _ = reviewed(family, edition)
    label = MONTHS[int(period[5:]) - 1] + " " + period[:4]
    title = plain(require(r'<meta property="og:title" content="([^"]+)"', raw)[1])
    headline = require(r"^Baugenehmigungen für Wohnungen im " + label + r": ([+-]?\d+,\d) % zum Vorjahresmonat$", title)[1]
    count = require(r"Im " + label + r" wurde in Deutschland der Bau von (\d{1,3}(?: \d{3})+) Wohnungen genehmigt\. Wie das Statistische Bundesamt \(Destatis\) mitteilt, waren das (\d+,\d) % oder [\d ]+ Baugenehmigungen (mehr|weniger) als im " + MONTHS[int(period[5:]) - 1] + " " + str(int(period[:4]) - 1), body)
    yy = float(count[2].replace(",", ".")) * (1 if count[3] == "mehr" else -1)
    if yy != float(headline.replace(",", ".")) or int(count[1].replace(" ", "")) % 100:
        raise FetchError("Destatis permits headline or printed precision needs review")
    if period < since:
        return []
    return [observation(document, family, edition, FAMILY, count[1], value=int(count[1].replace(" ", ""))),
            observation(document, family, edition, FAMILY + "_yy", headline, value=yy)]


def fetch(client, family=FAMILY, since="1900-01"):
    if run_date() > "2026-12-31":
        raise FetchError("Destatis permits reviewed horizon exhausted; needs review")
    eligible = {d: m for d, m in due(EDITIONS[family], family=family).items() if m[0] >= since}
    if not eligible:
        return []
    calendar_editions(client.get("destatis", CALENDAR))
    urls = dict(ARCHIVES)
    if any(d not in urls for d in eligible):
        urls.update(publication_links(client.get("destatis", INDEX)))
    out = []
    for edition in eligible:
        if edition not in urls:
            if publication_grace(family, edition):
                mark_pending(family, edition)
                continue
            raise FetchError("Destatis due permits publication missing; needs review")
        try:
            doc = get_edition(client, "destatis", urls[edition], family, edition)
        except FetchError as exc:
            if str(exc) == "destatis: HTTP 404":
                raise FetchError("Destatis due permits publication missing; needs review") from None
            raise
        if doc is None:
            continue
        rows = parse(doc, family, since)
        if len(rows) != 2 or {r.edition for r in rows} != {edition}:
            raise FetchError("Destatis requested permits edition needs review")
        out += rows
    return out
