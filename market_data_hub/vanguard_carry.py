"""Vanguard BNDX quarterly fund fact sheet."""

from datetime import datetime, timezone
from io import BytesIO
import re
import time

from pypdf import PdfReader
import requests

URL = "https://workplace.vanguard.com/assets/corp/fund_communications/pdf_publish/us-products/fact-sheet/F3711.pdf"
FIELDS = ("average_duration", "holdings_count")


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
    try:
        for attempt in range(3):
            try:
                response = requests.get(
                    URL,
                    headers={"User-Agent": "market-data-hub/1.0 (fund data collector)"},
                    timeout=(10, 45),
                )
                break
            except requests.exceptions.ConnectTimeout:
                if attempt == 2:
                    raise
                time.sleep(2**attempt)
        response.raise_for_status()
        as_of, values = parse_pdf(response.content)
        for field, (value, raw) in values.items():
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
            "UPDATE download_log SET ended_at=?, status='ok', rows_added=? WHERE run_id=?",
            [datetime.now(timezone.utc).replace(tzinfo=None), len(values), run_id],
        )
        return len(values)
    except Exception as exc:
        con.execute(
            "UPDATE download_log SET ended_at=?, status='error', error_msg=? WHERE run_id=?",
            [datetime.now(timezone.utc).replace(tzinfo=None), str(exc), run_id],
        )
        log(f"Vanguard carry BNDX failed ({type(exc).__name__}): {exc}")
        return 0
