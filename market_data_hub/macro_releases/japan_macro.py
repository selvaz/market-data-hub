"""Narrow official Japan release tables, with independently printed editions."""
from datetime import datetime
from io import BytesIO
import re
import unicodedata

from .asia import observation, fetch_archives
from .asia_editions import EDITIONS, reviewed
from .http import FetchError
from .tier1_publications import require, publication_text, plain
from .nbs_housing import Page
from .schedule import due

ARCHIVES = {
    "jp_tokyo_cpi": {
        "2026-08-28": "https://www.e-stat.go.jp/en/stat-search/file-download?statInfId=000040491317&fileKind=4",
        "2026-10-02": "https://www.e-stat.go.jp/en/stat-search/file-download?statInfId=000040501413&fileKind=4",
    },
    "jp_cpi": {
        "2026-09-18": "https://www.e-stat.go.jp/en/stat-search/file-download?statInfId=000040501410&fileKind=4",
        "2026-10-23": "https://www.e-stat.go.jp/en/stat-search/file-download?statInfId=000040513539&fileKind=4",
    },
    "jp_gdp": {
        "2026-08-17": "https://www.esri.cao.go.jp/en/sna/data/sokuhou/files/2026/qe262/pdf/gaiyou2621_e.pdf",
        "2026-09-08": "https://www.esri.cao.go.jp/en/sna/data/sokuhou/files/2026/qe262_2/pdf/gaiyou2622_e.pdf",
    },
}
PROVIDERS = {"jp_gdp": "esri", "jp_cpi": "statjp", "jp_tokyo_cpi": "statjp"}
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
    require("2025-Base Consumer Price Index", plain(document.body.decode("utf-8")))
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


def parse_cpi(document, family, since):
    from openpyxl import load_workbook
    editions = [d for d, url in ARCHIVES[family].items() if url == document.url]
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
        return result if period >= since else []
    finally:
        workbook.close()


def parse_gdp(document, since):
    from pypdf import PdfReader
    if not document.body.startswith(b"%PDF"):
        raise FetchError("ESRI GDP PDF format needs review")
    page = PdfReader(BytesIO(document.body)).pages[0].extract_text(extraction_mode="layout")
    body = re.sub(r"\s+", " ", page)
    date = require(r"Released:\s*(\d{4}\.\d{1,2}\.\d{1,2})", body)[1]
    edition = datetime.strptime(date, "%Y.%m.%d").date().isoformat()
    period, stage, _ = reviewed("jp_gdp", edition)
    if document.url != ARCHIVES["jp_gdp"].get(edition):
        raise FetchError("ESRI GDP archive URL needs review")
    label = {"2026-Q2": "April - June 2026", "2026-Q3": "July - September 2026"}[period]
    require("Quarterly Estimates of GDP for " + re.escape(label) +
            r" \(" + ("First" if stage == "preliminary" else "Second") + r" Preliminary Estimates\)", body)
    require(r"Quarterly Real Growth Rate.*?Seasonally Adjusted Series, Quarter-to-Quarter", body)
    lines = [re.sub(r"\s+", " ", line).strip() for line in page.splitlines()
             if "国内総生産（ＧＤＰ）" in line and "Gross Domestic Product" in line]
    if len(lines) != 1:
        raise FetchError("ESRI GDP headline row needs review")
    if stage == "revised":
        printed = require(r"^国内総生産（ＧＤＰ） ([+-]?[\d.]+) ([+-]?[\d.]+) Gross Domestic Product$", lines[0])[2]
    else:
        # Five printed quarterly rates precede the contribution placeholder
        # and annualised rate. The fifth is the current q/q headline.
        row = require(r"^国内総生産（ＧＤＰ） Gross Domestic Product((?:[+-]?[\d.]+ ){5})\*\*\* [+-]?[\d.]+$", lines[0])
        printed = row[1].split()[4]
    return [observation(document, "jp_gdp", edition, "jp_gdp_qq", printed, "pdf_table")] if period >= since else []


def parse(document, family, since="1900-01"):
    if family in {"jp_cpi", "jp_tokyo_cpi"}:
        return parse_cpi(document, family, since)
    if family == "jp_gdp":
        return parse_gdp(document, since)
    raise FetchError("Japan issuer family needs review")


def fetch(client, family, since):
    if family in {"jp_cpi", "jp_tokyo_cpi"}:
        for edition, (period, _, _) in due(EDITIONS[family], family=family).items():
            if period >= since:
                if edition not in CPI_LISTINGS:
                    raise FetchError("e-Stat due CPI report archive needs review")
                cpi_archive_listing(client.get("statjp", CPI_LISTINGS[edition]), family, edition)
    return fetch_archives(client, family, since, PROVIDERS[family], ARCHIVES[family], parse)
