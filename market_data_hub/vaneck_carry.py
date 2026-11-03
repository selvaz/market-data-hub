"""VanEck's dated, text-extractable US fund fact sheets."""

from datetime import datetime, timezone
from io import BytesIO
import re
import time

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


def collect(con, *, log=print):
    """Collect once per fund per UTC day; isolate failures by fund."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    count = 0
    for symbol, (slug, currency) in FUNDS.items():
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
        try:
            url = f"https://www.vaneck.com/us/en/investments/{slug}-fact-sheet.pdf"
            for attempt in range(3):
                try:
                    response = requests.get(
                        url,
                        headers={
                            "User-Agent": "market-data-hub/1.0 (fund data collector)"
                        },
                        timeout=(10, 45),
                    )
                    break
                except requests.exceptions.ConnectTimeout:
                    if attempt == 2:
                        raise
                    time.sleep(2**attempt)
            response.raise_for_status()
            as_of, values = parse_pdf(response.content, symbol)
            for field, (value, raw) in values.items():
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
            missing = set(FIELDS) - set(values)
            if missing:
                log(f"VanEck carry {symbol}: missing {', '.join(sorted(missing))}")
            con.execute(
                "UPDATE download_log SET ended_at=?, status='ok', rows_added=? WHERE run_id=?",
                [datetime.now(timezone.utc).replace(tzinfo=None), len(values), run_id],
            )
        except Exception as exc:
            con.execute(
                "UPDATE download_log SET ended_at=?, status='error', error_msg=? WHERE run_id=?",
                [datetime.now(timezone.utc).replace(tzinfo=None), str(exc), run_id],
            )
            log(f"VanEck carry {symbol} failed ({type(exc).__name__}): {exc}")
    return count
