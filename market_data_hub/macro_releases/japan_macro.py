"""Narrow official Japan release tables, with independently printed editions."""
from datetime import datetime
from io import BytesIO
import re
import unicodedata

from .asia import observation, fetch_archives
from .asia_editions import reviewed
from .http import FetchError
from .tier1_publications import require, publication_text

ARCHIVES = {
    "jp_gdp": {
        "2026-08-17": "https://www.esri.cao.go.jp/en/sna/data/sokuhou/files/2026/qe262/pdf/gaiyou2621_e.pdf",
        "2026-09-08": "https://www.esri.cao.go.jp/en/sna/data/sokuhou/files/2026/qe262_2/pdf/gaiyou2622_e.pdf",
    },
}
PROVIDERS = {"jp_gdp": "esri"}


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
    if family == "jp_gdp":
        return parse_gdp(document, since)
    raise FetchError("Japan issuer family needs review")


def fetch(client, family, since):
    return fetch_archives(client, family, since, PROVIDERS[family], ARCHIVES[family], parse)
