"""State Street fund characteristics from its daily product-data workbook."""

from datetime import datetime, timezone
from io import BytesIO
import time

from openpyxl import load_workbook
import requests

URL = "https://www.ssga.com/library-content/products/fund-data/etfs/us/spdr-product-data-us-en.xlsx"
FUNDS = {"BIL": "USD", "BWX": None, "SPAB": "USD"}
FIELDS = {
    "Yield to Maturity": ("yield_to_maturity", 0.01),
    "Option Adjusted Duration": ("effective_duration", 1),
    "30 Day SEC Yield": ("sec_yield_30d", 0.01),
    "Fund Dividend Yield": ("distribution_yield", 0.01),
    "Average Coupon": ("average_coupon", 0.01),
    "Number of Holdings": ("holdings_count", 1),
}


def parse_workbook(data):
    """Parse labeled issuer values, preserving the workbook's as-of date."""
    book = load_workbook(BytesIO(data), read_only=True, data_only=True)
    try:
        rows = book.active.iter_rows(values_only=True)
        header = None
        result = {}
        for row in rows:
            if row[1] == "Ticker":
                header = {str(value).strip(): i for i, value in enumerate(row) if value}
                if not {"Ticker", "As of**", *FIELDS}.issubset(header):
                    raise ValueError(
                        "State Street workbook is missing expected columns"
                    )
                continue
            if header is None or row[1] not in FUNDS:
                continue
            date_value = datetime.strptime(row[header["As of**"]], "%b %d %Y").date()
            values = {}
            for label, (field, scale) in FIELDS.items():
                raw = row[header[label]]
                if raw in (None, "", "-"):
                    continue
                value = float(
                    str(raw)
                    .replace(",", "")
                    .replace("%", "")
                    .replace(" years", "")
                    .strip()
                )
                values[field] = (value * scale, label)
            result[row[1]] = (date_value, values)
        return result
    finally:
        book.close()


def collect(con, *, log=print):
    """Fetch the shared workbook at most once a UTC day, including failures."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if con.execute(
        "SELECT count(*) FROM download_log WHERE source='ssga_carry' AND CAST(started_at AS DATE)=?",
        [now.date()],
    ).fetchone()[0]:
        return 0
    run_id = f"ssga-carry-{now.isoformat()}"
    con.execute(
        "INSERT INTO download_log (run_id, started_at, source, symbol, status) VALUES (?, ?, 'ssga_carry', 'product-workbook', 'started')",
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
        data = parse_workbook(response.content)
        count = 0
        for symbol, currency in FUNDS.items():
            if symbol not in data:
                log(f"State Street carry {symbol}: workbook row missing")
                continue
            as_of, values = data[symbol]
            for field, (value, raw) in values.items():
                con.execute(
                    "INSERT INTO etf_carry_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                    [
                        now.date(),
                        symbol,
                        field,
                        value,
                        "ssga",
                        now,
                        raw,
                        as_of,
                        None,
                        currency,
                        None,
                    ],
                )
                count += 1
            missing = set(name for name, _ in FIELDS.values()) - set(values)
            if missing:
                log(
                    f"State Street carry {symbol}: missing {', '.join(sorted(missing))}"
                )
        con.execute(
            "UPDATE download_log SET ended_at=?, status='ok', rows_added=? WHERE run_id=?",
            [datetime.now(timezone.utc).replace(tzinfo=None), count, run_id],
        )
        return count
    except Exception as exc:
        con.execute(
            "UPDATE download_log SET ended_at=?, status='error', error_msg=? WHERE run_id=?",
            [datetime.now(timezone.utc).replace(tzinfo=None), str(exc), run_id],
        )
        log(f"State Street carry workbook failed ({type(exc).__name__}): {exc}")
        return 0
