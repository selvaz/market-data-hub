"""VanEck daily fund-page carry and historical fact-sheet parsing."""

from datetime import datetime, timezone
from io import BytesIO
import re

from pypdf import PdfReader
import requests

FUNDS = {
    "EMLC": ("jp-morgan-em-local-currency-bond-etf-emlc", None),
    "HYEM": ("emerging-markets-high-yield-bond-etf-hyem", "USD"),
    "IHY": ("international-high-yield-bond-etf-ihy", None),
}
FIELDS = {
    "effective_duration": (r"(?m)^Effective Duration \(yrs\)\s+([\d.]+)", 1),
    "sec_yield_30d": (r"(?m)^30-Day SEC Yield\s+([\d.]+)%", 0.01),
    "holdings_count": (r"(?m)^Number of Holdings\s+([\d,]+)", 1),
}


def parse_pdf(data, symbol=None):
    """Read only explicitly labeled facts, with their common publication date."""
    pdf = PdfReader(BytesIO(data))
    text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    ticker = re.search(r"(?m)^Fund Ticker\s+(\S+)", text)
    if symbol and (not ticker or ticker[1] != symbol):
        raise ValueError(f"VanEck fact sheet does not identify {symbol}")
    dates = set(re.findall(r"As of ([A-Za-z]+ \d{1,2}, \d{4})", text))
    if len(dates) != 1:
        raise ValueError("Missing or inconsistent VanEck fact sheet dates")
    as_of = datetime.strptime(dates.pop(), "%B %d, %Y").date()
    values = {}
    for field, (pattern, scale) in FIELDS.items():
        match = re.search(pattern, text)
        if match:
            values[field] = (float(match[1].replace(",", "")) * scale, match[0].strip())
    if not values:
        raise ValueError("No recognized VanEck fund metrics")
    return as_of, values


# These are the page IDs used by the issuer's three page blocks.
PAGE_IDS = {"EMLC": 233118, "HYEM": 233122, "IHY": 233123}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
}


def parse_daily(kind, payload, symbol, *, log=print):
    """Parse a single issuer block, retaining only explicitly dated values."""
    fields = {
        "yield": ("sec_yield_30d",),
        "fundamentals": ("yield_to_worst", "effective_duration"),
        "holdings": ("holdings_count",),
    }[kind]
    result = {}
    try:
        if kind == "fundamentals":
            if payload["blockId"] != 230678:
                raise ValueError("wrong block")
            markup = payload["markup"]
            section = re.search(
                r"<h3[^>]*>\s*Fundamentals\s*<span[^>]*>as of (\d{2}/\d{2}/\d{4})</span>\s*</h3>(.*?)</ul>",
                markup,
                re.S,
            )
            if not section:
                raise ValueError("missing dated Fundamentals")
            as_of = datetime.strptime(section[1], "%m/%d/%Y").date()
            items = re.findall(
                r"<li[^>]*>\s*<div>\s*([^<]+).*?</div>\s*<div>\s*([^<]+)</div>\s*</li>",
                section[2],
                re.S,
            )
            values = {label.strip(): value.strip() for label, value in items}
            specs = (
                ("yield_to_worst", "Yield to Worst", 0.01),
                ("effective_duration", "Effective Duration (yrs)", 1),
            )
        else:
            data = payload["data"]
            if data["Ticker"] != symbol:
                raise ValueError("wrong ticker")
            if kind == "yield":
                if data["LongVersionTitle"] != "Yields":
                    raise ValueError("wrong block")
                as_of = datetime.strptime(
                    data["LongVersionAsOfDate"], "%m/%d/%Y"
                ).date()
                values = {item["Title"]: item["Value"] for item in data["Values"]}
                yield_items = [
                    item
                    for item in data["Values"]
                    if item["Title"] == "30-Day SEC Yield"
                ]
                if len(yield_items) != 1:
                    raise ValueError("missing or ambiguous yield")
                item_date = yield_items[0]["AsOfDate"]
                if item_date is not None:
                    as_of = datetime.strptime(item_date, "%m/%d/%Y").date()
                specs = (("sec_yield_30d", "30-Day SEC Yield", 0.01),)
            else:
                if data["SubTitle"] != "Total Holdings":
                    raise ValueError("wrong holdings block")
                as_of = datetime.strptime(data["AsOfDate"], "%m/%d/%Y").date()
                values = {"Total Holdings": data["TotalAmount"]}
                specs = (("holdings_count", "Total Holdings", 1),)
        for field, label, scale in specs:
            try:
                raw = values[label]
                pattern = (
                    r"[+-]?\d+(?:\.\d+)?%"
                    if field == "sec_yield_30d"
                    else (
                        r"[\d,]+"
                        if field == "holdings_count"
                        else r"[+-]?\d+(?:\.\d+)?"
                    )
                )
                if not isinstance(raw, str) or not re.fullmatch(pattern, raw):
                    raise ValueError("invalid number")
                result[field] = (
                    float(raw.replace(",", "").rstrip("%")) * scale,
                    label + ": " + raw,
                    as_of,
                )
            except (KeyError, ValueError, TypeError):
                log(f"VanEck carry {symbol}: missing or invalid {field}")
    except (KeyError, ValueError, TypeError):
        for field in fields:
            log(f"VanEck carry {symbol}: missing or invalid {field}")
    return result


def collect(con, *, log=print):
    """Collect daily blocks independently so one failure does not mask others."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    count = 0
    blocks = {
        "yield": ("FundDetailsBlock", 371036),
        "fundamentals": ("ContentAreaUsBlock", 230678),
        "holdings": ("HoldingsBlock", 144475),
    }
    for symbol, (_, currency) in FUNDS.items():
        if con.execute(
            "SELECT count(*) FROM download_log WHERE source='vaneck_carry' AND symbol=? AND CAST(started_at AS DATE)=?",
            [symbol, now.date()],
        ).fetchone()[0]:
            continue
        run_id = f"vaneck-{symbol}-{now.isoformat()}"
        con.execute(
            "INSERT INTO download_log (run_id, started_at, source, symbol, status) VALUES (?, ?, 'vaneck_carry', ?, 'started')",
            [run_id, now, symbol],
        )
        values = {}
        errors = []
        for kind, (controller, block) in blocks.items():
            try:
                url = f"https://www.vaneck.com/Main/{controller}/GetContent/"
                response = requests.get(
                    url,
                    params={
                        "blockid": block,
                        "pageid": PAGE_IDS[symbol],
                        "ticker": symbol,
                        "reactlang": "en",
                        "reactctr": "us",
                    },
                    headers=HEADERS,
                    timeout=(10, 45),
                )
                response.raise_for_status()
                values.update(parse_daily(kind, response.json(), symbol, log=log))
            except (requests.RequestException, ValueError) as exc:
                errors.append(f"{kind}: {exc}")
                for field in {
                    "yield": ("sec_yield_30d",),
                    "fundamentals": ("yield_to_worst", "effective_duration"),
                    "holdings": ("holdings_count",),
                }[kind]:
                    log(f"VanEck carry {symbol}: {field} unavailable ({exc})")
        for field, (value, raw, as_of) in values.items():
            con.execute(
                "INSERT INTO etf_carry_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                [
                    now.date(),
                    symbol,
                    field,
                    value,
                    "vaneck",
                    now,
                    raw,
                    as_of,
                    None,
                    currency,
                    None,
                ],
            )
        count += len(values)
        con.execute(
            "UPDATE download_log SET ended_at=?, status=?, rows_added=?, error_msg=? WHERE run_id=?",
            [
                datetime.now(timezone.utc).replace(tzinfo=None),
                "error" if errors else "ok",
                len(values),
                "; ".join(errors) or None,
                run_id,
            ],
        )
    return count
