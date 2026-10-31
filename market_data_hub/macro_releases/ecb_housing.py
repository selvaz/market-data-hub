"""ECB mortgage releases: dated issuer prints, checked against exact SDMX keys.

The rolling Data Portal response corroborates only the latest due headline.
Its revised history and update timestamp never create earlier release vintages.
"""
import csv
from dataclasses import replace
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO, StringIO
import re
import unicodedata
from urllib.parse import urljoin

from .http import FetchError
from .housing_editions import EDITIONS, reviewed
from .schedule import due, get_edition, mark_pending, publication_grace, run_date
from .tier1_publications import plain, require

HOST = "https://www.ecb.europa.eu"
LOANS = "ea_house_purchase_loans"
COST = "ea_house_purchase_cost"
CALENDAR = HOST + "/press/calendars/statscal/html/index.en.html"
SERIES = {LOANS: "BSI.M.U2.Y.U.A22T.A.I.U2.2250.Z01.A",
          COST: "MIR.M.U2.B.A2C.AM.R.A.2250.EUR.N"}
INDICATORS = {LOANS: "ea_loans_house_purchase_yy", COST: "ea_cost_borrow_house"}
TITLES = {LOANS: "Monetary developments in the euro area",
          COST: "Euro area bank interest rate statistics"}
LISTINGS = {LOANS: HOST + "/press/stats/md/html/index.en.html",
            COST: HOST + "/press/stats/mfi/html/index.en.html"}
ARCHIVES = {
    LOANS: {
        "2026-08-27": HOST + "/press/stats/md/html/ecb.md2607~e7127e7d02.en.html",
        "2026-09-25": HOST + "/press/stats/md/html/ecb.md2608~406f8d243d.en.html",
    },
    COST: {
        "2026-09-02": HOST + "/press/stats/mfi/html/ecb.mir260902~d54675e442.en.html",
        "2026-10-01": HOST + "/press/stats/mfi/html/ecb.mir2610~8e4898ad10.en.html",
    },
}


def series_url(family):
    flow, key = SERIES[family].split(".", 1)
    return f"https://data-api.ecb.europa.eu/service/data/{flow}/{key}"


def calendar_editions(document, family):
    """Review actual title/dataset/period/clock; do not mistake a Z for UTC."""
    raw = document.body.decode("utf-8")
    require(r"All times are local to Frankfurt am Main\.", plain(raw))
    found = {}
    window = []
    flow = SERIES[family].split(".")[0]
    for stamp, content in re.findall(r"<dt\b[^>]*>(.*?)</dt>\s*<dd\b[^>]*>(.*?)</dd>", raw, re.S):
        window.append(datetime.strptime(require(r"^(\d{2}/\d{2}/\d{4} \d{2}:\d{2})", plain(stamp))[1],
                                        "%d/%m/%Y %H:%M").date().isoformat())
        body = plain(content)
        if not body.startswith(TITLES[family]):
            continue
        when = datetime.strptime(plain(stamp), "%d/%m/%Y %H:%M")
        edition = when.date().isoformat()
        if edition < min(EDITIONS[family]) or edition > "2026-12-31":
            continue
        period = require(re.escape(TITLES[family]) + r" \(Dataset: " + flow +
                         r"\) Reference period: (\w{3}-\d{4}) Includes press release\.$", body)[1]
        metadata = (datetime.strptime(period, "%b-%Y").strftime("%Y-%m"), "regular", when.strftime("%H:%M"))
        if edition in found or "Tentative" in plain(stamp) or EDITIONS[family].get(edition) != metadata:
            raise FetchError("ECB housing calendar edition needs review")
        found[edition] = metadata
    eligible = due(EDITIONS[family], family=family)
    if not window:
        raise FetchError("ECB housing calendar window missing; needs review")
    # The issuer removes completed releases from this forward calendar. Require
    # every future edition and all reviewed dates within its visible window.
    # Older due editions still need their independently dated publication below.
    required = {d for d in EDITIONS[family] if d not in eligible or d >= min(window)}
    if not required.issubset(found):
        raise FetchError("ECB housing calendar edition missing; needs review")
    return found


def publication_metadata(document, family):
    raw = document.body.decode("utf-8")
    stamps = re.findall(r'<meta\s+property="article:published_time"\s+content="([^"]+)"', raw)
    if len(stamps) != 1:
        raise FetchError("ECB housing publication date needs review")
    edition = stamps[0]
    period, _, _ = reviewed(family, edition)
    label = datetime.strptime(period, "%Y-%m").strftime("%B %Y")
    body = plain(require(r"(?s)<main\b[^>]*>(.*?)</main>", raw)[1])
    date = require(re.escape(TITLES[family] + ": " + label) + r" (\d{1,2} \w+ \d{4})", body)[1]
    if datetime.strptime(date, "%d %B %Y").date().isoformat() != edition:
        raise FetchError("ECB housing title and publication date disagree; needs review")
    prefix = "/press/stats/md/html/ecb.md" if family == LOANS else "/press/stats/mfi/html/ecb.mir"
    code = datetime.strptime(period if family == LOANS else edition[:7], "%Y-%m").strftime("%y%m")
    if not re.fullmatch(re.escape(HOST + prefix + code) + r"(?:\d{2})?~[a-f0-9]{10}\.en\.html", document.url):
        raise FetchError("ECB housing issuer URL needs review")
    return edition, period, body


def parse(document, family=COST, since="1900-01"):
    from .housing import observation
    if family == LOANS:
        return parse_loans(document, family, since)
    if family != COST:
        raise FetchError("ECB housing family needs review")
    edition, period, body = publication_metadata(document, family)
    label = datetime.strptime(period, "%Y-%m").strftime("%B %Y")
    require(r"which combines interest rates on all loans to households for house purchase, .*? in " + label, body)
    raw = document.body.decode("utf-8")
    require(re.escape("https://data.ecb.europa.eu/data/datasets/MIR/" + SERIES[COST]), raw)
    # Only the first headline bullet: never corporations, consumption, APRC or
    # a mortgage fixation bucket. Later editions may render Table 2 as an image.
    bullet = plain(require(r"(?s)<li\b[^>]*>(Composite cost-of-borrowing indicator.*?)</li>", raw)[1])
    combined = re.fullmatch(r"Composite cost-of-borrowing indicator for new loans to corporations and for new loans to households for house purchase broadly unchanged at [+-]?\d+\.\d{2}% and ([+-]?\d+\.\d{2})%, respectively", bullet)
    printed = combined[1] if combined else require(r"indicator for new loans to households for house purchase .*? (?:at|to) ([+-]?\d+\.\d{2})%", bullet)[1]
    table = re.search(r"Households Composite cost-of-borrowing indicator for house purchase ([+-]?\d+\.\d{2})%", body)
    if table and table[1] != printed:
        raise FetchError("ECB housing borrowing-cost headline and table disagree; needs review")
    return [replace(observation(document, family, edition, INDICATORS[family], printed),
                    series_key=SERIES[family])] if period >= since else []


def parse_loans(document, family=LOANS, since="1900-01"):
    from .housing import observation
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError
    if family != LOANS:
        raise FetchError("ECB house-purchase loans family needs review")
    try:
        pages = [unicodedata.normalize("NFKC", page.extract_text()) for page in PdfReader(BytesIO(document.body)).pages]
    except PdfReadError:
        raise FetchError("ECB housing loans PDF structure needs review") from None
    tables = [re.sub(r"\s+", " ", page) for page in pages if "4 Adjusted loans to the private sector:" in page]
    if len(tables) != 1:
        raise FetchError("ECB housing loans annex table needs review")
    table = tables[0]
    label = require(r"4 Adjusted loans to the private sector: (\w+ \d{4}) a\)", table)[1]
    period = datetime.strptime(label, "%B %Y").strftime("%Y-%m")
    editions = [d for d, meta in EDITIONS[family].items() if meta[0] == period]
    if len(editions) != 1:
        raise FetchError("ECB housing loans annex period needs review")
    edition = editions[0]
    code = datetime.strptime(period, "%Y-%m").strftime("%y%m")
    if not re.fullmatch(re.escape(HOST + "/press/pdf/md/ecb.md" + code) + r"_annex~[a-f0-9]{10}\.en\.pdf", document.url):
        raise FetchError("ECB housing loans annex issuer URL needs review")
    require(r"annual percentage changes; data adjusted for seasonal effects b\)", table)
    require(r"Loans adjusted for the effects of transfers to and from MFI balance sheets, as well as for notional cash pooling", table)
    require(r"Includes loans to non-profit institutions serving households\.", table)
    months = [(int(period[:4]) * 12 + int(period[5:]) - 1 - n) for n in (2, 1, 0)]
    dates = [datetime(y // 12, y % 12 + 1, 1) for y in months]
    short = [d.strftime("%b") for d in dates]
    labels = [short[-1], *short, *short]
    years = [period[:4], *[str(d.year) for d in dates], *[str(d.year) for d in dates]]
    require(r"Annual growth rate level " + " ".join(labels + years), table)
    row = require(r"1\.2\. Lending for house purchase ((?:[+-]?\d+(?:\.\d+)? ){6}[+-]?\d+(?:\.\d+)?) 1\.3\. Other lending", table)[1].split()
    if len(row) != 7 or any(not re.fullmatch(r"[+-]?\d+\.\d", rate) for rate in row[4:]):
        raise FetchError("ECB housing loans annual column needs review")
    return [replace(observation(document, family, edition, INDICATORS[family], row[-1], "pdf_table"),
                    series_key=SERIES[family])] if period >= since else []


def check_series(document, family, observation):
    """Compare the latest published rate, rounding only for corroboration."""
    if document.url.split("?")[0] != series_url(family):
        raise FetchError("ECB housing SDMX issuer URL needs review")
    rows = list(csv.DictReader(StringIO(document.body.decode("utf-8-sig"))))
    if not rows or len({row["TIME_PERIOD"] for row in rows}) != len(rows):
        raise FetchError("ECB housing SDMX period metadata needs review")
    decimals, unit = ("1", "PCCH") if family == LOANS else ("4", "PCPA")
    for row in rows:
        if (row["KEY"] != SERIES[family] or row["UNIT"] != unit or row["UNIT_MULT"] != "0" or
                row["DECIMALS"] != decimals or row["OBS_STATUS"] not in ("A", "P") or row["OBS_CONF"] != "F" or
                not re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", row["TIME_PERIOD"])):
            raise FetchError("ECB housing SDMX identity or status needs review")
        # KEY and individual dimensions must agree, not merely a display title.
        dims = ("FREQ", "REF_AREA", "ADJUSTMENT", "BS_REP_SECTOR", "BS_ITEM", "MATURITY_ORIG", "DATA_TYPE", "COUNT_AREA", "BS_COUNT_SECTOR", "CURRENCY_TRANS", "BS_SUFFIX") if family == LOANS else (
            "FREQ", "REF_AREA", "BS_REP_SECTOR", "BS_ITEM", "MATURITY_NOT_IRATE", "DATA_TYPE_MIR", "AMOUNT_CAT", "BS_COUNT_SECTOR", "CURRENCY_TRANS", "IR_BUS_COV")
        if ".".join(row[d] for d in dims) != SERIES[family].split(".", 1)[1]:
            raise FetchError("ECB housing SDMX dimension metadata needs review")
    head = max(rows, key=lambda row: row["TIME_PERIOD"])
    try:
        value = Decimal(head["OBS_VALUE"])
        agrees = value.is_finite() and value.quantize(Decimal(observation.printed), rounding=ROUND_HALF_UP) == Decimal(observation.printed)
    except InvalidOperation:
        agrees = False
    if head["TIME_PERIOD"] != observation.period or not agrees:
        raise FetchError("ECB housing latest SDMX and publication disagree; needs review")


def fetch(client, family, since="1900-01"):
    if run_date() > "2026-12-31":
        raise FetchError("ECB housing reviewed calendar horizon exhausted; needs review")
    eligible = {d: meta for d, meta in due(EDITIONS[family], family=family).items() if meta[0] >= since}
    if not eligible:
        return []
    calendar_editions(client.get("ecb", CALENDAR), family)
    urls = dict(ARCHIVES[family])
    if any(d not in urls for d in eligible):
        listing = client.get("ecb", LISTINGS[family])
        for stamp, path, title in re.findall(r'<dt isoDate="(\d{4}-\d{2}-\d{2})">(?:(?!<dt\b).)*?<a href="([^\"]+\.en\.html)"[^>]*>([^<]+)</a>', listing.body.decode(), re.S):
            if title.startswith(TITLES[family] + ":") and stamp in eligible:
                url = urljoin(HOST, path)
                if stamp in urls and urls[stamp] != url:
                    raise FetchError("ECB housing conflicting archive links; needs review")
                urls[stamp] = url
    out = []
    for edition in eligible:
        if edition not in urls:
            if publication_grace(family, edition):
                mark_pending(family, edition)
                continue
            raise FetchError("ECB housing due publication link missing; needs review")
        try:
            doc = get_edition(client, "ecb", urls[edition], family, edition)
            if doc is None:
                continue
            stamp, period, _ = publication_metadata(doc, family)
            if stamp != edition:
                raise FetchError("ECB housing requested edition needs review")
            if family == LOANS:
                paths = re.findall(r'href="(/press/pdf/md/ecb\.md\d{4}_annex~[a-f0-9]{10}\.en\.pdf)"', doc.body.decode())
                if len(set(paths)) != 1:
                    raise FetchError("ECB housing loans dated annex link needs review")
                doc = get_edition(client, "ecb", urljoin(HOST, paths[0]), family, edition)
                if doc is None:
                    continue
            rows = parse(doc, family, since)
        except FetchError as exc:
            if str(exc) == "ecb: HTTP 404":
                raise FetchError("ECB housing due publication missing; needs review") from None
            raise
        if len(rows) != 1 or rows[0].edition != edition or rows[0].period != period:
            raise FetchError("ECB housing requested archive period needs review")
        out += rows
    if out:
        latest = max(out, key=lambda row: row.edition)
        # Never certify the current series response against an earlier archive
        # while the latest reviewed publication is still missing in its grace.
        if latest.edition == max(eligible):
            series = client.get("ecb", series_url(family), {"startPeriod": latest.period, "format": "csvdata"})
            check_series(series, family, latest)
    return out
