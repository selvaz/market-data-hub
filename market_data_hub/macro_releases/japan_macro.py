"""Narrow official Japan release tables, with independently printed editions."""
from dataclasses import replace
from datetime import datetime
from io import BytesIO, StringIO
import csv
from xml.etree import ElementTree
import re

from .asia import observation, fetch_archives, in_window
from .asia_editions import reviewed
from .http import FetchError
from .tier1_publications import require, plain
from .nbs_housing import Page

ARCHIVES = {
    "jp_trade_balance": {
        "2026-09-29": "https://www.customs.go.jp/toukei/shinbun/trade-st_e/2026/2026085e.xml",
        "2026-10-21": "https://www.customs.go.jp/toukei/shinbun/trade-st_e/2026/2026094e.xml",
    },
    "jp_tankan": {
        "2026-07-01": "https://www.boj.or.jp/en/statistics/tk/gaiyo/2026/tka2606.pdf",
        "2026-10-01": "https://www.boj.or.jp/en/statistics/tk/gaiyo/2026/tka2609.pdf",
        "2026-12-14": "https://www.boj.or.jp/en/statistics/tk/gaiyo/2026/tka2612.pdf",
    },
    "jp_tokyo_cpi": {
        "2026-08-28": "https://www.e-stat.go.jp/en/stat-search/file-download?statInfId=000040491317&fileKind=4",
        "2026-10-02": "https://www.e-stat.go.jp/en/stat-search/file-download?statInfId=000040501413&fileKind=4",
    },
    "jp_cpi": {
        "2026-09-18": "https://www.e-stat.go.jp/en/stat-search/file-download?statInfId=000040501410&fileKind=4",
        "2026-10-23": "https://www.e-stat.go.jp/en/stat-search/file-download?statInfId=000040513539&fileKind=4",
    },
    "jp_industrial_production": {"2026-10-30": "https://www.meti.go.jp/english/statistics/tyo/iip/xls/b2020_gsm1e.xlsx"},
    "jp_oecd_cli": {"2026-11-13": "https://sdmx.oecd.org/public/rest/v1/data/OECD.SDD.STES,DSD_STES@DF_CLI,4.1/JPN.M.LI.IX._Z.AA.IX._Z.H?startPeriod=2026-01&format=csvfilewithlabels"},
    "jp_unemployment": {"2026-10-30": "https://www.e-stat.go.jp/en/stat-search/file-download?statInfId=000031831358&fileKind=0"},
    "jp_household_spending": {"2026-11-10": "https://www.stat.go.jp/data/kakei/sokuhou/tsuki/zuhyou/fies_t1.xlsx"},
    "jp_gdp": {
        "2026-08-17": "https://www.esri.cao.go.jp/jp/sna/data/data_list/sokuhou/files/2026/qe262/tables/ritu-jk2621.csv",
        "2026-09-08": "https://www.esri.cao.go.jp/jp/sna/data/data_list/sokuhou/files/2026/qe262_2/tables/ritu-jk2622.csv",
    },
}
PROVIDERS = {"jp_industrial_production": "meti", "jp_oecd_cli": "oecd", "jp_unemployment": "statjp", "jp_household_spending": "statjp", "jp_gdp": "esri", "jp_cpi": "statjp", "jp_tokyo_cpi": "statjp", "jp_tankan": "boj", "jp_trade_balance": "mof"}
CPI_LISTINGS = {
    "2026-08-28": "https://www.e-stat.go.jp/en/stat-search/files?page=1&layout=datalist&year=20260&month=23070907&toukei=00200573&tstat=000001243876&tclass1=000001243877&cycle=1&tclass2val=0",
    "2026-10-02": "https://www.e-stat.go.jp/en/stat-search/files?page=1&layout=datalist&lid=000001490251",
    "2026-09-18": "https://www.e-stat.go.jp/en/stat-search/files?page=1&layout=datalist&lid=000001490251",
    "2026-10-23": "https://www.e-stat.go.jp/en/stat-search/files?page=1&layout=datalist&lid=000001491870",
}


def cpi_archive_listing(document, family, edition):
    """Bind the reviewed file ID to the issuer's first monthly report listing."""
    period, _, _ = reviewed(family, edition)
    page = Page(document.body).root
    tokyo = family == "jp_tokyo_cpi"
    label = "Subgroup Index for Ku-area of Tokyo" if tokyo else "Subgroup Index for Japan"
    from .bls import shift_month
    survey = shift_month(period, -1) if tokyo else period
    require(r"20\d{2}-Base Consumer Price Index", plain(document.body.decode("utf-8")))
    url = ARCHIVES[family][edition]
    found = []
    for group in page.find_all("ul"):
        if group.get("class") != "stat-dataset_list-detail":
            continue
        text = plain(group.text())
        for link in group.find_all("a"):
            if "https://www.e-stat.go.jp" + link.get("href") != url:
                continue
            # A later update cannot establish the original edition.
            require(re.escape(label) + r" Monthly Survey date " +
                    datetime.strptime(survey, "%Y-%m").strftime("%Y%b.") +
                    r" Update date " + edition, text)
            # Tokyo's next-month preliminary table is the second publication
            # in this monthly-report slot; this is explicitly reviewed.
            if link.get("data-release_count") != ("2" if tokyo else "1"):
                raise FetchError("e-Stat updated report publication needs review")
            found.append(url)
    if len(found) != 1:
        raise FetchError("e-Stat monthly report archive identity needs review")


def parse_cpi(document, family, since, archives):
    from openpyxl import load_workbook
    editions = [d for d, url in archives.items() if url == document.url]
    if len(editions) != 1 or not document.body.startswith(b"PK"):
        raise FetchError("e-Stat CPI edition or workbook format needs review")
    edition = editions[0]
    period, stage, _ = reviewed(family, edition)
    tokyo = family == "jp_tokyo_cpi"
    workbook = load_workbook(BytesIO(document.body), data_only=True, read_only=True)
    try:
        sheets = [s for s in workbook if s['H8'].value == 'Monthly, % Change over the year']
        if len(sheets) != 1:
            raise FetchError("e-Stat published annual-rate sheet needs review")
        sheet = sheets[0]
        label = 'Table 1-2 Subgroup Index for Ku-area of Tokyo' if tokyo else 'Table 1-1 Subgroup Index for Japan'
        if (sheet['I6'].value.strip() != label or
                sheet['I12'].value != 'All items' or sheet['J12'].value != 'All items, less fresh food'):
            raise FetchError("e-Stat CPI geography or measure headers need review")
        notes = [str(v) for row in sheet.iter_rows(values_only=True) for v in row if v is not None and 'preliminary' in str(v)]
        if tokyo and notes != ['Note) Latest monthly figures are preliminary ones.']:
            raise FetchError("e-Stat Tokyo preliminary stage needs review")
        rows = [r for r in sheet.iter_rows(values_only=True) if len(r) > 9 and
                str(r[1]).strip() == period.replace('-', '') and str(r[2]).strip() == '03']
        if len(rows) != 1 or max(str(r[1]).strip() for r in sheet.iter_rows(values_only=True)
                if len(r) > 2 and re.fullmatch(r'\d{6}', str(r[1]).strip())) != period.replace('-', ''):
            raise FetchError("e-Stat CPI headline reference period needs review")
        result = []
        pairs = [('jp_tokyo_core_cpi_yy', rows[0][9])] if tokyo else list(zip(('jp_cpi_yy', 'jp_core_cpi_yy'), rows[0][8:10]))
        for indicator, value in pairs:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise FetchError("e-Stat CPI annual rate needs review")
            result.append(observation(document, family, edition, indicator, str(value), "xlsx_table"))
        return result if in_window(period, since) else []
    finally:
        workbook.close()


GDP_ARCHIVE = "https://www.esri.cao.go.jp/en/sna/data/sokuhou/files/2026/toukei_2026.html"
GDP_MENUS = {
    "2026-08-17": "https://www.esri.cao.go.jp/en/sna/data/sokuhou/files/2026/qe262/gdemenuea.html",
    "2026-09-08": "https://www.esri.cao.go.jp/en/sna/data/sokuhou/files/2026/qe262_2/gdemenuea.html",
}
def trade_style_url(edition):
    """Return the stylesheet beside the edition's annual trade archive."""
    return f"https://www.customs.go.jp/toukei/shinbun/trade-st_e/{edition[:4]}/d1201e.xsl"


# Historical fixture anchor; live editions use ``trade_style_url``.
TRADE_STYLE = trade_style_url("2026-10-21")


def gdp_archive_listing(archive, menu, edition):
    """The CSV has no release stamp: bind its exact link to the dated archive."""
    _, stage, _ = reviewed("jp_gdp", edition)
    if archive.url != GDP_ARCHIVE or menu.url != GDP_MENUS.get(edition):
        raise FetchError("ESRI GDP edition listing URL needs review")
    matches = []
    for row in Page(archive.body).root.find_all("tr"):
        for link in row.find_all("a"):
            if "https://www.esri.cao.go.jp" + link.get("href") == menu.url:
                matches.append(plain(row.text()))
    ordinal = "First" if stage == "preliminary" else "Second"
    stamp = datetime.strptime(edition, "%Y-%m-%d").strftime("%b %d, %Y")
    # The issuer omits the leading zero on single-digit days.
    stamp = stamp.replace(" 0", " ")
    if len(matches) != 1:
        raise FetchError("ESRI GDP archive edition needs review")
    period, _, _ = reviewed("jp_gdp", edition)
    year, quarter = period.split("-Q")
    quarter_label = {"1": r"Jan\.-Mar\.", "2": r"Apr\.-Jun\.",
                     "3": r"Jul\.-Sep\.", "4": r"Oct\.-Dec\."}[quarter]
    release_label = quarter_label + re.escape(year)
    require(re.escape(stamp) + r" Quarterly Estimates of GDP for " + release_label + r" \(The " +
            ordinal + r" preliminary Estimates\)", matches[0])
    require(release_label + r" \(The " + ("1st" if stage == "preliminary" else "2nd") +
            r" preliminary\)", plain(menu.body.decode("utf-8")))
    links = [link for link in Page(menu.body).root.find_all("a")
             if "https://www.esri.cao.go.jp" + link.get("href") == ARCHIVES["jp_gdp"][edition]]
    if len(links) != 1 or not plain(links[0].text()).startswith(
            "Real, Seasonally Adjusted Series (Quarter-to-Quarter) (csv:"):
        raise FetchError("ESRI GDP published-rate CSV link needs review")


def parse_gdp(document, since, archives):
    editions = [d for d, url in archives.items() if url == document.url]
    if len(editions) != 1:
        raise FetchError("ESRI GDP CSV archive URL needs review")
    edition = editions[0]
    period, _, _ = reviewed("jp_gdp", edition)
    try:
        rows = list(csv.reader(StringIO(document.body.decode("cp932"))))
    except (UnicodeError, csv.Error) as exc:
        raise FetchError("ESRI GDP CSV format needs review") from exc
    if (len(rows) < 8 or len(rows[1]) < 2 or len(rows[5]) < 2 or rows[1][0] !=
            "Real, Seasonally Adjusted Series (Quarter-to-Quarter Percent Change)" or
            rows[5][1] != "GDP(Expenditure Approach)" or "(%)" not in rows[1]):
        raise FetchError("ESRI GDP published-rate header format needs review")
    year = None
    rates = {}
    for row in rows[7:]:
        if not row:
            continue
        stamp = re.fullmatch(r"(?:(\d{4})/ )?(1- 3|4- 6|7- 9|10-12)\.", row[0].strip())
        if not stamp:
            if row[0].strip():
                raise FetchError("ESRI GDP CSV reference period needs review")
            continue
        year = stamp[1] or year
        key = str(year) + "-Q" + str(("1- 3", "4- 6", "7- 9", "10-12").index(stamp[2]) + 1)
        if year is None or key in rates or len(row) < 2:
            raise FetchError("ESRI GDP CSV reference period needs review")
        rates[key] = row[1].strip()
    if not rates or max(rates) != period or not re.fullmatch(r"[+-]?\d+\.\d+", rates[period]):
        raise FetchError("ESRI GDP current quarter rate needs review")
    return [observation(document, "jp_gdp", edition, "jp_gdp_qq", rates[period], "csv")] if in_window(period, since) else []


def trade_stylesheet(document, edition=None):
    """The publisher's XML presentation defines both units and NSA basis."""
    expected = trade_style_url(edition) if edition else None
    pattern = r"https://www\.customs\.go\.jp/toukei/shinbun/trade-st_e/20\d{2}/d1201e\.xsl"
    if (expected and document.url != expected) or (not expected and not re.fullmatch(pattern, document.url)):
        raise FetchError("MOF trade stylesheet URL needs review")
    text = plain(document.body.decode("utf-8"))
    require(r"Ministry of Finance", text)
    require(r"Total \( Census, not seasonally adjusted \)", text)
    require(r"\(Unit: millions of YEN, % \)", text)


def parse_trade(document, since, archives):
    styles = re.findall(rb'<\?xml-stylesheet\s+([^?]+)\?>', document.body)
    if len(styles) != 1 or not re.search(rb'href=["\']d1201e\.xsl["\']', styles[0]):
        raise FetchError("MOF trade XML stylesheet needs review")
    if b"<!DOCTYPE" in document.body or b"<!ENTITY" in document.body:
        raise FetchError("MOF trade XML format needs review")
    try:
        root = ElementTree.fromstring(document.body)
    except ElementTree.ParseError as exc:
        raise FetchError("MOF trade XML format needs review") from exc
    pages = root.findall("sogakutsuki")
    if root.tag != "hodoxml" or len(pages) != 1 or pages[0].get("name") != "pg1":
        raise FetchError("MOF trade XML headline table needs review")
    page = pages[0]
    try:
        edition = datetime.strptime(page.findtext("kohyoymd", ""), "%B %d, %Y").date().isoformat()
    except ValueError as exc:
        raise FetchError("MOF trade publication date needs review") from exc
    period, stage, _ = reviewed("jp_trade_balance", edition)
    if document.url != archives.get(edition):
        raise FetchError("MOF trade XML archive URL needs review")
    label = datetime.strptime(period, "%Y-%m").strftime("%B %Y")
    wording = "(Provisional)" if stage == "preliminary" else "(Exports:Detailed Data;Imports:Provisional(HS-code Basis))"
    if page.findtext("title") != "Value of Exports and Imports " + label + " " + wording or page.findtext("taishoymtonen") != label:
        raise FetchError("MOF trade reference period or stage needs review")
    tables = root.findall("chiikikunisogaku")
    if len(tables) != 1 or tables[0].get("name") != "pg2" or tables[0].findtext("taishokikan") != label:
        raise FetchError("MOF trade country table needs review")
    totals = [r for r in tables[0].findall("chiikikunisogakuinfo") if r.findtext("chiikikunikbn") == "0"]
    if len(totals) != 1 or totals[0].findtext("chiikikuni") != "Grand Total":
        raise FetchError("MOF trade national balance needs review")
    printed = totals[0].findtext("sashihikikagakue", "")
    headline = page.findtext("sashihiki/sogakutonen", "").replace("△", "-")
    if not re.fullmatch(r"[+-]?[\d,]+", printed) or printed != headline:
        raise FetchError("MOF trade printed balance disagreement needs review")
    return [observation(document, "jp_trade_balance", edition, "jp_trade_balance", printed, "structured",
                        value=float(printed.replace(',', '')) / 1000)] if in_window(period, since) else []



def parse_meti_iip_workbook(body):
    """Return METI mining/manufacturing SA m/m with its preliminary status."""
    from openpyxl import load_workbook
    try:
        workbook = load_workbook(BytesIO(body), data_only=True, read_only=True)
    except Exception as exc:
        raise FetchError("METI IIP workbook format needs review") from exc
    try:
        if workbook.sheetnames != ["Production", "Shipments", "Inventory", "Inventory Ratio"]:
            raise FetchError("METI IIP workbook sheets need review")
        sheet = workbook["Production"]
        if sheet.cell(1, 1).value != "Seasonally adjusted Index by Industry : Industrial Production (2020=100.0)" or sheet.cell(3, 1).value != "Item_Number":
            raise FetchError("METI IIP workbook header needs review")
        rows = [row for row in sheet.iter_rows(values_only=True)
                if row[0] == 1000000000 and row[1] == "Mining and manufacturing"]
        if len(rows) != 1:
            raise FetchError("METI IIP mining and manufacturing row needs review")
        headers = [sheet.cell(3, col).value for col in range(1, sheet.max_column + 1)]
        months = [(i, str(value)) for i, value in enumerate(headers)
                  if (isinstance(value, int) and 200000 <= value <= 209912) or
                  (isinstance(value, str) and re.fullmatch(r"p 20\d{4}", value))]
        if len(months) < 2:
            raise FetchError("METI IIP monthly columns need review")
        current_i, current = months[-1]
        previous_i, previous = months[-2]
        if current.removeprefix("p ") <= previous.removeprefix("p ") or not current.startswith("p "):
            raise FetchError("METI IIP preliminary period needs review")
        values = rows[0]
        latest, prior = values[current_i], values[previous_i]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (latest, prior)):
            raise FetchError("METI IIP index values need review")
        period = current.removeprefix("p ")
        return period[:4] + "-" + period[4:], round(100 * (latest / prior - 1), 1), True
    finally:
        workbook.close()


def parse_oecd_sdmx(body, expected):
    """Parse one exact OECD SDMX-CSV series, rejecting dimension drift."""
    try:
        rows = list(csv.DictReader(StringIO(body.decode("utf-8-sig"))))
    except (UnicodeError, csv.Error) as exc:
        raise FetchError("OECD SDMX CSV format needs review") from exc
    dimensions = {key: value for key, value in expected.items() if key != "STRUCTURE_ID"}
    if not rows or any(row.get(key) != value for row in rows for key, value in dimensions.items()):
        raise FetchError("OECD SDMX series identity needs review")
    if expected.get("STRUCTURE_ID") and any(expected["STRUCTURE_ID"] not in row.get("STRUCTURE_ID", "") for row in rows):
        raise FetchError("OECD SDMX flow version needs review")
    periods = [row.get("TIME_PERIOD", "") for row in rows]
    if len(set(periods)) != len(periods) or not all(re.fullmatch(r"\d{4}-\d{2}", p) for p in periods):
        raise FetchError("OECD SDMX periods need review")
    try:
        values = [float(row["OBS_VALUE"]) for row in rows]
    except (KeyError, TypeError, ValueError) as exc:
        raise FetchError("OECD SDMX observation needs review") from exc
    latest = max(range(len(periods)), key=periods.__getitem__)
    return periods[latest], values[latest]



def parse_statjp_lfs_workbook(body):
    """Parse the Statistics Bureau SA unemployment rate, both sexes, age 15+."""
    from openpyxl import load_workbook
    try:
        workbook = load_workbook(BytesIO(body), data_only=True, read_only=True)
    except Exception as exc:
        raise FetchError("Statistics Bureau LFS workbook format needs review") from exc
    try:
        sheet = workbook["季節調整値"]
        # The public historical table is bilingual only in its notes. Lock the
        # selected national/both-sexes unemployment-rate column and latest row.
        if sheet.max_column != 22 or sheet.cell(7, 20).value != "Unemployment rate  (percent)" or sheet.cell(9, 20).value != "Both sexes":
            raise FetchError("Statistics Bureau LFS measure header needs review")
        candidates = []
        year = None
        for values in sheet.iter_rows(min_row=11, values_only=True):
            y, month, value = values[0], values[1], values[19]
            if isinstance(y, int) and 1900 <= y <= 2100:
                year = y
            match = re.fullmatch(r"(\d{1,2})月", str(month))
            if year and match and isinstance(value, (int, float)) and not isinstance(value, bool):
                candidates.append((f"{year:04d}-{int(match[1]):02d}", float(value)))
        if not candidates or candidates[-1][0] != max(p for p, _ in candidates):
            raise FetchError("Statistics Bureau LFS latest period needs review")
        return candidates[-1]
    finally:
        workbook.close()


def parse_statjp_fies_workbook(body):
    """Parse FIES Table 1 real y/y spending for two-or-more-person households."""
    from openpyxl import load_workbook
    try:
        workbook = load_workbook(BytesIO(body), data_only=True, read_only=True)
    except Exception as exc:
        raise FetchError("Statistics Bureau FIES workbook format needs review") from exc
    try:
        if len(workbook.sheetnames) != 1:
            raise FetchError("Statistics Bureau FIES Table 1 sheet needs review")
        sheet = workbook.worksheets[0]
        text = " ".join(str(v) for row in sheet.iter_rows(min_row=1, max_row=25, values_only=True) for v in row if v is not None)
        if "Two-or-more-person Households" not in text or "Consumption expenditures" not in text:
            raise FetchError("Statistics Bureau FIES population or measure needs review")
        period, value = sheet["I5"].value, sheet["I91"].value
        printed_period = (f"{period:.2f}" if isinstance(period, (int, float))
                          and not isinstance(period, bool) else str(period))
        if not re.fullmatch(r"20\d{4}", printed_period.replace(".", "")) or isinstance(value, bool) or not isinstance(value, (int, float)):
            raise FetchError("Statistics Bureau FIES period or real annual rate needs review")
        stamp = printed_period.replace(".", "")
        return stamp[:4] + "-" + stamp[4:], float(value)
    finally:
        workbook.close()



def parse(document, family, since="1900-01", *, archives=None, edition=None,
          discovery=None, discovery_document=None):
    archives = ARCHIVES[family] if archives is None else archives
    if family == "jp_trade_balance":
        return parse_trade(document, since, archives)
    if family == "jp_tankan":
        from pypdf import PdfReader
        if not document.body.startswith(b"%PDF"):
            raise FetchError("BOJ Tankan PDF format needs review")
        body = re.sub(r"\s+", " ", PdfReader(BytesIO(document.body)).pages[0].extract_text())
        stamp = require(
            r"Tankan Summary \((March|June|September|December) (20\d{2})\) "
            r"\d+th Short-Term Economic Survey of Enterprises in Japan "
            r"(\w+ \d{1,2}, 20\d{2})", body,
        )
        edition = datetime.strptime(stamp[3], "%B %d, %Y").date().isoformat()
        period, _, _ = reviewed(family, edition)
        survey_year, quarter = period.split("-Q")
        survey_month = {"1": "March", "2": "June", "3": "September", "4": "December"}[quarter]
        if (document.url != archives.get(edition) or stamp[1] != survey_month or
                stamp[2] != survey_year):
            raise FetchError("BOJ Tankan survey period or URL needs review")
        require(r'1\. Business Conditions DI \("favorable" - "unfavorable"\), % points Large Enterprises Medium-sized Enterprises Small Enterprises', body)
        # Large enterprises occupy the first six columns: previous actual,
        # previous forecast, current actual, change, forecast, change.
        row = require(r"Changes Changes Changes Changes Changes Changes Manufacturing ([+-]?\d+) ([+-]?\d+) ([+-]?\d+) ([+-]?\d+) ([+-]?\d+) ([+-]?\d+)", body)
        return [observation(document, family, edition, "jp_tankan_large_manufacturers_di", row[3], "pdf_table")] if in_window(period, since) else []
    if family == "jp_industrial_production":
        period, calculated, preliminary = parse_meti_iip_workbook(document.body)
        edition = edition or next((d for d, u in archives.items() if u == document.url), None)
        expected, stage, _ = reviewed(family, edition)
        if period != expected:
            from .jp_discovery import moving_status
            raise FetchError("moving publication " + moving_status(expected, period))
        if stage != ("preliminary" if preliminary else "final"):
            raise FetchError("METI IIP edition needs review")
        if (discovery is None or discovery_document is None or
                discovery.edition_date != edition or discovery.published_value is None):
            raise FetchError("METI IIP published monthly rate evidence needs review")
        published = discovery.published_value
        if abs(float(published) - calculated) > 0.1:
            raise FetchError("METI IIP workbook validation disagrees with published rate")
        return [observation(
            discovery_document, family, edition, "jp_industrial_production_mm",
            published, "html_table",
        )]
    if family == "jp_oecd_cli":
        period, value = parse_oecd_sdmx(document.body, {"STRUCTURE_ID": "DSD_STES@DF_CLI(4.1)", "REF_AREA": "JPN", "FREQ": "M", "MEASURE": "LI", "UNIT_MEASURE": "IX", "ACTIVITY": "_Z", "ADJUSTMENT": "AA", "TRANSFORMATION": "IX", "TIME_HORIZ": "_Z", "METHODOLOGY": "H"})
        edition = edition or next((d for d, u in archives.items() if u == document.url), None)
        expected = reviewed(family, edition)[0]
        if expected != period:
            from .jp_discovery import moving_status
            raise FetchError("moving publication " + moving_status(expected, period))
        row = observation(document, family, edition, "JP_OECD_CLI", str(value), "sdmx_csv")
        # OECD's SDMX payload carries the observation period but no issuer
        # publication timestamp.  The discovery edition is collector-observed.
        return [replace(row, precision="observed", published_at=document.retrieved_at)]
    if family == "jp_unemployment":
        period, value = parse_statjp_lfs_workbook(document.body)
        edition = edition or next((d for d, u in archives.items() if u == document.url), None)
        expected = reviewed(family, edition)[0]
        if expected != period:
            from .jp_discovery import moving_status
            raise FetchError("moving publication " + moving_status(expected, period))
        return [observation(document, family, edition, "jp_unemployment_rate", str(value), "xlsx_table")]
    if family == "jp_household_spending":
        period, value = parse_statjp_fies_workbook(document.body)
        edition = edition or next((d for d, u in archives.items() if u == document.url), None)
        expected = reviewed(family, edition)[0]
        if expected != period:
            from .jp_discovery import moving_status
            raise FetchError("moving publication " + moving_status(expected, period))
        return [observation(document, family, edition, "jp_household_spending_yy", str(value), "xlsx_table")]
    if family in {"jp_cpi", "jp_tokyo_cpi"}:
        return parse_cpi(document, family, since, archives)
    if family == "jp_gdp":
        return parse_gdp(document, since, archives)
    raise FetchError("Japan issuer family needs review")


def fetch(client, family, since):
    return fetch_archives(client, family, since, PROVIDERS[family], ARCHIVES[family], parse)
