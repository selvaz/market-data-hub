"""Live, issuer-authored edition discovery for Japan tier-1 releases.

The parsers deliberately use dates printed by issuers.  Retrieval time is not
an edition date, except for OECD CLI whose SDMX response has no publication
timestamp; that exception is marked ``observed``.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import calendar
import json
import re
from urllib.parse import urljoin

from .http import FetchError
from .nbs_housing import Page
from .tier1_publications import plain


@dataclass(frozen=True)
class Edition:
    edition_date: str
    period: str
    stage: str
    release_time: str | None
    document_url: str
    precision: str = "exact"


METI = "https://www.meti.go.jp/english/statistics/tyo/iip/index.html"
LFS = "https://www.stat.go.jp/english/data/roudou/results/month/index.html"
LFS_SCHEDULE = "https://www.stat.go.jp/english/data/roudou/1543.html"
FIES = "https://www.stat.go.jp/english/data/kakei/156.html"
CPI = "https://www.stat.go.jp/english/data/cpi/1581-z.html"
CPI_SCHEDULE = "https://www.stat.go.jp/english/data/cpi/1582.html"
GDP = "https://www.esri.cao.go.jp/en/sna/kouhyou/kouhyou_top.html"
TANKAN = "https://www.boj.or.jp/en/statistics/tk/index.htm"
OECD = "https://sdmx.oecd.org/public/rest/v1/data/OECD.SDD.STES,DSD_STES@DF_CLI,4.1/JPN.M.LI.IX._Z.AA.IX._Z.H"


def _get(client, provider, url, params=None):
    return client.get(provider, url, params)


def _text(document):
    return plain(document.body.decode("utf-8", "replace").replace("&nbsp;", " "))


def _month(name, year):
    return f"{year}-{datetime.strptime(name[:3], '%b').month:02d}"


def _date(value, year=None):
    value = re.sub(r"\s+", " ", value.replace(".", "")).strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%A, %B %d, %Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    if year:
        return datetime.strptime(value + f", {year}", "%B %d, %Y").date().isoformat()
    raise FetchError("Japan issuer release date format needs review")


def parse_meti(document):
    text = _text(document)
    match = re.search(r"Preliminary Report for (\w+) (20\d{2}) \(released at (\d{1,2}:\d{2}), (\w+ \d{1,2}, 20\d{2})\)", text)
    if not match:
        raise FetchError("METI IIP latest-release identity needs review")
    links = [urljoin(document.url, a.get("href")) for a in Page(document.body).root.find_all("a")
             if a.get("href") and ("gsm1e.xlsx" in a.get("href") or "b2020_gsm1e.xlsx" in a.get("href"))]
    url = links[0] if links else "https://www.meti.go.jp/english/statistics/tyo/iip/xls/b2020_gsm1e.xlsx"
    return Edition(_date(match[4]), _month(match[1], match[2]), "preliminary", match[3], url)


def parse_fies(document):
    text = _text(document)
    match = re.search(r"(\w+) (20\d{2}) Released on (\d{1,2}) (\w+) (20\d{2})", text)
    if not match:
        raise FetchError("Statistics Bureau FIES latest release needs review")
    return Edition(_date(f"{match[4]} {match[3]}, {match[5]}"), _month(match[1], match[2]),
                   "final", "08:30", urljoin(document.url, "/data/kakei/sokuhou/tsuki/zuhyou/fies_t1.xlsx"), "date")


def _schedule_rows(document):
    for row in Page(document.body).root.find_all("tr"):
        cells = [plain(cell.text()) for cell in row.find_all("td")]
        if cells:
            yield cells


def parse_lfs(document, *, today=None):
    today = today or date.today()
    year = today.year
    found = []
    for cells in _schedule_rows(document):
        if len(cells) < 2:
            continue
        period = re.fullmatch(r"(?:(20\d{2}) )?(January|February|March|April|May|June|July|August|September|October|November|December)", cells[0].split(",")[0])
        if not period:
            continue
        if period[1]:
            year = int(period[1])
        release = cells[1]
        edition_year = int(re.search(r"20\d{2}", release).group()) if re.search(r"20\d{2}", release) else year
        edition = _date(release, edition_year)
        found.append(Edition(edition, _month(period[2], year), "final", "08:30",
                             "https://www.e-stat.go.jp/en/stat-search/files", "date"))
    return found


def parse_cpi_schedule(document):
    found = {"jp_cpi": [], "jp_tokyo_cpi": []}
    year = date.today().year
    for cells in _schedule_rows(document):
        if len(cells) < 4:
            continue
        national, national_date, tokyo, tokyo_date = cells[:4]
        ym = re.search(r"(January|February|March|April|May|June|July|August|September|October|November|December),?\s*(20\d{2})?", national)
        tm = re.search(r"(January|February|March|April|May|June|July|August|September|October|November|December),?\s*(20\d{2})?", tokyo)
        if not ym or not tm:
            continue
        year = int(ym[2] or year)
        for family, match, stamp, stage in (("jp_cpi", ym, national_date, "final"), ("jp_tokyo_cpi", tm, tokyo_date, "preliminary")):
            release_year = int(re.search(r"20\d{2}", stamp).group()) if re.search(r"20\d{2}", stamp) else year
            found[family].append(Edition(_date(stamp, release_year), _month(match[1], match[2] or year), stage,
                                         "08:30", "https://www.e-stat.go.jp/en/stat-search/files", "date"))
    return found


def parse_gdp(document):
    found = []
    for cells in _schedule_rows(document):
        if len(cells) < 3:
            continue
        match = re.search(r"(Jan\.-Mar\.|Apr\.-Jun\.|Jul\.-Sep\.|Oct\.-Dec\.) (20\d{2}).*?\(The\s+(First|Second) Preliminary\)", cells[0], re.I)
        if not match or not re.search(r"20\d{2}", cells[1]):
            continue
        quarter = {"Jan.-Mar.": 1, "Apr.-Jun.": 2, "Jul.-Sep.": 3, "Oct.-Dec.": 4}[match[1]]
        found.append(Edition(_date(cells[1]), f"{match[2]}-Q{quarter}",
                             "preliminary" if match[3].lower() == "first" else "revised",
                             re.search(r"\d{1,2}:\d{2}", cells[2]).group(), GDP, "exact"))
    return found


def parse_tankan(document):
    found = []
    months = {"March": 1, "June": 2, "September": 3, "December": 4}
    for row in Page(document.body).root.find_all("tr"):
        text = plain(row.text())
        match = re.search(r"(\w+) (20\d{2}) Survey", text)
        stamp = re.search(r"(\w+)\s+(\d{1,2}),\s*(20\d{2})", text.replace(".", ""))
        links = row.find_all("a")
        if match and stamp and match[1] in months and links and "Summary" not in text:
            url = urljoin(document.url, links[0].get("href"))
            if "/statistics/tk/gaiyo/" in url and url.endswith((".pdf", ".zip")):
                found.append(Edition(_date(f"{stamp[1]} {stamp[2]}, {stamp[3]}"),
                    f"{match[2]}-Q{months[match[1]]}", "final", "08:50", url, "date"))
    # Summary is represented twice (ZIP/PDF); prefer the PDF as the document.
    unique = {}
    for item in found:
        if item.document_url.endswith(".pdf") or item.edition_date not in unique:
            unique[item.edition_date] = item
    return list(unique.values())


def parse_oecd(document):
    payload = json.loads(document.body)
    observations = payload.get("data", {}).get("dataSets", payload.get("dataSets", []))
    # Discovery only needs the latest monthly coordinate; accept both SDMX-JSON layouts.
    periods = [v.get("id") for v in payload.get("data", {}).get("structure", {})
               .get("dimensions", {}).get("observation", [{}])[0].get("values", [])]
    if not observations or not periods:
        raise FetchError("OECD CLI latest observation needs review")
    observed = datetime.fromisoformat(document.retrieved_at.replace("Z", "+00:00")).date().isoformat()
    return Edition(observed, max(periods), "final", None, document.url, "observed")


def discover(client, family, *, today=None):
    """Return live issuer editions for one Japan family."""
    if family == "jp_industrial_production":
        latest = parse_meti(_get(client, "meti", METI))
        return [latest, lagged_next(family, latest)]
    if family == "jp_household_spending":
        latest = parse_fies(_get(client, "statjp", FIES))
        return [latest, lagged_next(family, latest)]
    if family == "jp_unemployment":
        _get(client, "statjp", LFS)  # live latest page identifies the moving resource
        return parse_lfs(_get(client, "statjp", LFS_SCHEDULE), today=today)
    if family in {"jp_cpi", "jp_tokyo_cpi"}:
        _get(client, "statjp", CPI)  # latest page plus annual issuer schedule
        return parse_cpi_schedule(_get(client, "statjp", CPI_SCHEDULE))[family]
    if family == "jp_gdp":
        return parse_gdp(_get(client, "esri", GDP))
    if family == "jp_tankan":
        return parse_tankan(_get(client, "boj", TANKAN))
    if family == "jp_oecd_cli":
        params = {"lastNObservations": "1", "format": "jsondata"}
        return [parse_oecd(_get(client, "oecd", OECD, params))]
    raise KeyError(f"Japan discovery is not configured for {family}")


FAMILIES = frozenset({"jp_industrial_production", "jp_unemployment", "jp_household_spending",
    "jp_cpi", "jp_tokyo_cpi", "jp_gdp", "jp_tankan", "jp_oecd_cli"})


def moving_status(expected_period, returned_period):
    """Classify a moving resource without assigning it to the wrong edition."""
    if returned_period == expected_period:
        return "published"
    return "not_yet_published" if returned_period < expected_period else "needs_review"


def pending_reason(edition_date, *, today=None, grace_days=1):
    """Make a missed issuer date visible after a small publication grace."""
    today = today or date.today()
    expected = date.fromisoformat(edition_date)
    return "overdue" if today > expected + timedelta(days=grace_days) else "not_yet_published"


def lagged_next(family, latest):
    """Expected next release for latest-page-only monthly publishers."""
    year, month = map(int, latest.period.split("-"))
    month += 1
    if month == 13:
        year, month = year + 1, 1
    if family == "jp_industrial_production":
        # METI normally publishes the preliminary result at month-end.
        release_year, release_month = (year + 1, 1) if month == 12 else (year, month + 1)
        day = calendar.monthrange(release_year, release_month)[1]
        return Edition(f"{release_year:04d}-{release_month:02d}-{day:02d}", f"{year:04d}-{month:02d}",
                       "preliminary", "08:50", latest.document_url, "expected")
    # FIES normally publishes with a two-month lag on the 10th.
    release_month = month + 2
    release_year = year
    if release_month > 12:
        release_year, release_month = year + 1, release_month - 12
    return Edition(f"{release_year:04d}-{release_month:02d}-10", f"{year:04d}-{month:02d}",
                   "final", "08:30", latest.document_url, "expected")
