"""Narrow parsers for published US monthly figures, independent of FRED.

No rates are calculated from indexes here. PDF/HTML tables supply the issuer's
printed precision; a missing row, changed edition or title fails closed.
"""
from dataclasses import replace
from datetime import datetime
from html import unescape
from io import BytesIO
import re

from .adapters import Observation
from .bls import shift_month
from .catalogue import INDICATORS
from .http import FetchError
from .publication_times import new_york_release
from .us_editions import EDITIONS, observation_stage, reviewed

PUBLICATIONS = {
    "us_retail_sales": "https://www.census.gov/retail/marts/www/marts_current.pdf",
    "us_housing_starts": "https://www.census.gov/construction/nrc/pdf/newresconst.pdf",
    "us_durable_goods": "https://www.census.gov/manufacturing/m3/adv/pdf/durgd.pdf",
    "us_industrial_production": "https://www.federalreserve.gov/releases/g17/{date}/",
    "us_trade_balance": "https://www.bea.gov/news/2026/us-international-trade-goods-and-services-{month}-2026",
}
NUMBER = r"[+-]?(?:\d+\.\d+|\.\d+)"


def text(html):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


def match(pattern, body):
    found = re.search(pattern, body, re.I)
    if not found:
        raise FetchError("US issuer publication structure changed; needs review")
    return found


def publication(document, family, title):
    if document.body.startswith(b"%PDF"):
        from pypdf import PdfReader
        raw = "\n".join(page.extract_text() for page in PdfReader(BytesIO(document.body)).pages)
        body, method = re.sub(r"\s+", " ", raw), "pdf_table"
    else:
        raw = document.body.decode("utf-8")
        body, method = text(raw), "html"
    stamp = match(r"(?:FOR RELEASE AT|EMBARGOED UNTIL RELEASE AT|Release Date:).*?(\w+ \d{1,2}, \d{4})", body)[1]
    edition = datetime.strptime(stamp.title(), "%B %d, %Y").date().isoformat()
    head, _, _ = reviewed(family, edition)
    month = datetime.strptime(head, "%Y-%m").strftime("%B %Y")
    headings = {
        "us_retail_sales": r"ADVANCE MONTHLY SALES FOR RETAIL AND FOOD SERVICES,\s*(\w+ \d{4})",
        "us_durable_goods": r"MONTHLY ADVANCE REPORT ON DURABLE GOODS.*?INVENTORIES AND ORDERS\s*(\w+ \d{4})",
        "us_pce": r"Personal Income and Outlays,\s*(\w+ \d{4})",
    }
    month_confirmed = month.lower() in body.lower()
    if family in headings:
        month_confirmed = match(headings[family], body)[1].lower() == month.lower()
    if family == "us_industrial_production":
        month_confirmed = month.split()[0].lower() in body.lower() and head[:4] in body
    if title.lower() not in body.lower() or not month_confirmed:
        raise FetchError("US issuer title/reference month needs review")
    return raw, body, method, edition, head


def observation(document, family, edition, indicator, period, printed, method):
    head, event_stage, time = reviewed(family, edition)
    return Observation(indicator, family, period, float(printed.replace(",", "")), printed,
        indicator + ":published-table", INDICATORS[indicator].issuer_id, document, edition,
        **new_york_release(edition, time), stage=observation_stage(family, edition, period),
        event_stage=event_stage, provider_vintage=edition, method=method, precision="exact",
        revision="initial" if period == head and event_stage != "final" else "routine")


def pce_url(period):
    month = datetime.strptime(period, "%Y-%m").strftime("%B-%Y").lower()
    return "https://www.bea.gov/news/" + period[:4] + "/personal-income-and-outlays-" + month


def parse_pce_publication(document, since="1900-01"):
    raw, body, method, edition, head = publication(document, "us_pce", "Personal Income and Outlays")
    annual = match(r"From the same month one year ago,(.*?)(?:Personal Income and Related|Annual Update)", body)[1]
    out = []
    for indicator, pattern in (
        ("us_pce_price_index_yy", r"PCE price index.*?(increased|decreased) ([\d.]+) percent"),
        ("us_core_pce_yy", r"Excluding food and energy, the PCE price index (increased|decreased) ([\d.]+) percent"),
    ):
        value = match(pattern, annual)
        printed = ("-" if value[1].lower() == "decreased" else "") + value[2]
        out.append(observation(document, "us_pce", edition, indicator, head, printed, method))
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", raw, re.S):
        cells = [text(c) for c in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row, re.S)]
        indicator = {"PCE price index": "us_pce_price_index_mm",
                     "PCE price index excluding food and energy": "us_core_pce_mm"}.get(cells[0] if cells else "")
        if indicator:
            if len(cells) != 3 or not all(re.fullmatch(NUMBER, c) for c in cells[1:]):
                raise FetchError("BEA PCE publication table needs review")
            for period, value in zip((shift_month(head, -1), head), cells[1:]):
                out.append(observation(document, "us_pce", edition, indicator, period, value, method))
    if len(out) != 6:
        raise FetchError("BEA PCE requested publication rows absent")
    return [o for o in out if o.period >= since]


def parse_housing(document, since="1900-01"):
    _, body, method, edition, head = publication(document, "us_housing_starts", "NEW RESIDENTIAL CONSTRUCTION")
    out = []
    for indicator, pattern in (
        ("us_housing_starts", r"Privately-owned housing starts in (\w+) were at a seasonally adjusted annual rate of ([\d,]+)\..*?revised (\w+) estimate of ([\d,]+)"),
        ("us_building_permits", r"Privately-owned housing units authorized by building permits in (\w+) were at a seasonally adjusted annual rate of ([\d,]+)\..*?revised (\w+) rate of ([\d,]+)"),
    ):
        values = match(pattern, body)
        for period, month, printed in ((head, values[1], values[2]), (shift_month(head, -1), values[3], values[4])):
            if datetime.strptime(period, "%Y-%m").strftime("%B").lower() != month.lower():
                raise FetchError("Census housing reference month needs review")
            obs = observation(document, "us_housing_starts", edition, indicator, period, printed, method)
            # Census prints units; the adapter contract stores thousands of units.
            out.append(replace(obs, value=obs.value / 1000))
    return [o for o in out if o.period >= since]


def parse_retail(document, since="1900-01"):
    raw, _, method, edition, head = publication(document, "us_retail_sales", "ADVANCE MONTHLY SALES FOR RETAIL AND FOOD SERVICES")
    table = match(r"(Table 2\.\s+Estimated Change in Monthly Sales.*?)(?:Table 3\.)", raw.replace("\n", " "))[1]
    out = []
    for indicator, label in (("us_retail_sales_mm", r"total"),
                             ("us_retail_sales_ex_auto_mm", r"Total \(excl\. motor vehicle & parts\)")):
        row = match(label + r"[^\d+-]*((?:" + NUMBER + r"\s+){5}" + NUMBER + r")", table)[1]
        values = re.findall(NUMBER, row)
        for period, value in ((head, values[0]), (shift_month(head, -1), values[2])):
            out.append(observation(document, "us_retail_sales", edition, indicator, period, value, method))
    return [o for o in out if o.period >= since]


def parse_durable(document, since="1900-01"):
    raw, _, method, edition, head = publication(document, "us_durable_goods", "MONTHLY ADVANCE REPORT ON DURABLE GOODS")
    table = match(r"(Table 1\.\s+Durable Goods Manufacturers.*?)(?:Excluding defense:)", raw.replace("\n", " "))[1]
    parts = re.split(r"Excluding transportation:", table)
    if len(parts) != 2:
        raise FetchError("Census durable goods table needs review")
    out = []
    for indicator, section in zip(("us_durable_goods_orders_mm", "us_durable_goods_ex_transport_mm"), parts):
        # Skip three printed SA dollar levels; take the two published m/m rates.
        row = match(r"New Orders\d?[^\d]*([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+(" + NUMBER + r")\s+(" + NUMBER + r")", section)
        for period, value in ((head, row[4]), (shift_month(head, -1), row[5])):
            out.append(observation(document, "us_durable_goods", edition, indicator, period, value, method))
    return [o for o in out if o.period >= since]


def parse_ip(document, since="1900-01"):
    raw, _, method, edition, head = publication(document, "us_industrial_production", "Industrial Production and Capacity Utilization")
    out = []
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", raw, re.S):
        cells = [text(c) for c in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row, re.S)]
        if cells and cells[0] in ("Total index", "Total industry"):
            if len(cells) != 14 or not all(re.fullmatch(NUMBER, c) for c in cells[1:]):
                raise FetchError("Fed G.17 summary columns need review")
            indicator = "us_industrial_production_mm" if cells[0] == "Total index" else "us_capacity_utilization"
            for period, value in ((head, cells[-2]), (shift_month(head, -1), cells[-3])):
                out.append(observation(document, "us_industrial_production", edition, indicator, period, value, method))
    if len(out) != 4:
        raise FetchError("Fed G.17 requested summary rows absent")
    return [o for o in out if o.period >= since]


def parse_trade(document, since="1900-01"):
    _, body, method, edition, head = publication(document, "us_trade_balance", "U.S. International Trade in Goods and Services")
    row = match(r"(deficit|surplus) was \$([\d.]+) billion in (\w+),.*?from \$([\d.]+) billion in (\w+), revised", body)
    out = []
    for period, month, value in ((head, row[3], row[2]), (shift_month(head, -1), row[5], row[4])):
        if datetime.strptime(period, "%Y-%m").strftime("%B").lower() != month.lower():
            raise FetchError("BEA trade reference month needs review")
        printed = ("-" if row[1].lower() == "deficit" else "") + value
        out.append(observation(document, "us_trade_balance", edition, "us_trade_balance", period, printed, method))
    return [o for o in out if o.period >= since]


PARSERS = {"us_housing_starts": parse_housing, "us_retail_sales": parse_retail,
           "us_durable_goods": parse_durable, "us_industrial_production": parse_ip,
           "us_trade_balance": parse_trade}


def fetch_publications(client, families, since):
    out = []
    for family in families:
        if family not in PUBLICATIONS:
            continue
        url = PUBLICATIONS[family]
        if family in ("us_industrial_production", "us_trade_balance"):
            for edition, (period, _, _) in EDITIONS[family].items():
                if period >= since:
                    month = datetime.strptime(period, "%Y-%m").strftime("%B").lower()
                    out += PARSERS[family](client.get("fred", url.format(date=edition.replace("-", ""), month=month)), since)
        else:
            out += PARSERS[family](client.get("fred", url), since)
    return out
