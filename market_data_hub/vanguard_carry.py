"""Vanguard BNDX quarterly fund fact sheet."""

from datetime import datetime, timezone
from io import BytesIO
import html
import json
import re
import time

from pypdf import PdfReader
import requests

URL = "https://workplace.vanguard.com/assets/corp/fund_communications/pdf_publish/us-products/fact-sheet/F3711.pdf"
FIELDS = ("average_duration", "holdings_count")
PROFILE_URL = "https://investor.vanguard.com/investment-products/etfs/profile/bndx"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
}


def parse_profile(data, *, log=print):
    """Parse BNDX's 30-day SEC yield and its own publication date."""
    try:
        if data["dashboard"]["ticker"] != "BNDX":
            raise ValueError("wrong ticker")
        price = data["price"]
        raw = price["secYield"]
        if not isinstance(raw, str) or not re.fullmatch(r"[+-]?\d+(?:\.\d+)?%", raw):
            raise ValueError("invalid yield")
        as_of = datetime.strptime(price["secYieldAsOfDate"], "%m/%d/%Y").date()
        return {
            "sec_yield_30d": (float(raw[:-1]) / 100, "30-day SEC yield: " + raw, as_of)
        }
    except (KeyError, TypeError, ValueError):
        log("Vanguard carry BNDX: missing or invalid sec_yield_30d")
        return {}


def profile_from_html(text):
    match = re.search(r"data-vgn-funds-profile='([^']+)'", text)
    if not match:
        raise ValueError("missing profile data")
    return json.loads(html.unescape(match[1]))


def parse_pdf(data):
    text = " ".join(
        (page.extract_text() or "") for page in PdfReader(BytesIO(data)).pages
    )
    date = re.search(r"BNDX\s+As of ([A-Za-z]+ \d{1,2}, \d{4})", text)
    duration = re.search(
        r"Average duration\s+\d+\s+([\d.]+)\s+years\s+[\d.]+\s+years", text
    )
    count = re.search(r"Number of bonds\s+([\d,]+)\s+[\d,]+", text)
    if not date or not duration or not count:
        raise ValueError("BNDX fact sheet missing dated fund characteristics")
    as_of = datetime.strptime(date[1], "%B %d, %Y").date()
    return as_of, {
        "average_duration": (float(duration[1]), "Average duration"),
        "holdings_count": (float(count[1].replace(",", "")), "Number of bonds"),
    }


def collect(con, *, log=print):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if con.execute(
        "SELECT count(*) FROM download_log WHERE source='vanguard_carry' AND symbol='BNDX' AND CAST(started_at AS DATE)=?",
        [now.date()],
    ).fetchone()[0]:
        return 0
    run_id = f"vanguard-BNDX-{now.isoformat()}"
    con.execute(
        "INSERT INTO download_log (run_id, started_at, source, symbol, status) VALUES (?, ?, 'vanguard_carry', 'BNDX', 'started')",
        [run_id, now],
    )
    values = {}
    errors = []
    try:
        response = requests.get(PROFILE_URL, headers=HEADERS, timeout=(10, 45))
        response.raise_for_status()
        values.update(parse_profile(profile_from_html(response.text), log=log))
    except (requests.RequestException, ValueError) as exc:
        errors.append(f"profile: {exc}")
        log(f"Vanguard carry BNDX: sec_yield_30d unavailable ({exc})")
    try:
        for attempt in range(3):
            try:
                response = requests.get(URL, headers=HEADERS, timeout=(10, 45))
                break
            except requests.exceptions.ConnectTimeout:
                if attempt == 2:
                    raise
                time.sleep(2**attempt)
        response.raise_for_status()
        as_of, facts = parse_pdf(response.content)
        values.update(
            {field: (value, raw, as_of) for field, (value, raw) in facts.items()}
        )
    except (requests.RequestException, ValueError) as exc:
        errors.append(f"fact sheet: {exc}")
        for field in FIELDS:
            log(f"Vanguard carry BNDX: {field} unavailable ({exc})")
    for field, (value, raw, as_of) in values.items():
        con.execute(
            "INSERT INTO etf_carry_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            [
                now.date(),
                "BNDX",
                field,
                value,
                "vanguard",
                now,
                raw,
                as_of,
                None,
                None,
                "USD",
            ],
        )
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
    return len(values)
