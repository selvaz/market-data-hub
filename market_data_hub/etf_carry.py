"""Yahoo ETF carry: today's observations only, never historical backfill.

Rates are decimal fractions; duration is years; hedge flag is 0 or 1.
Yahoo's quote netExpenseRatio is percent, unlike summary raw ratios.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from market_data_hub.sources.yahoo_direct import _session

FIELDS = {
    "trailing_distribution_yield": 1,
    "sec_30_day_yield": 1,
    "expense_ratio": 1,
    "currency_hedged": 1,
    "yield_to_maturity": 1,
}
ASSET_CLASSES = {"EQUITY", "FIXED_INCOME", "COMMODITIES", "REAL_ESTATE", "ALTERNATIVES"}


def universe(entries):
    return [
        e
        for e in entries
        if e.get("asset_class") in ASSET_CLASSES
        and not e.get("instrument_type")
        and not e["symbol"].startswith("^")
    ]


def parse(quote, summary=None):
    """Return field -> (value, raw path); only documented, unambiguous mappings."""
    result = {}

    def put(field, value, raw, scale=1):
        if isinstance(value, dict):
            value = value.get("raw")
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        ):
            result[field] = (float(value) * scale, raw)

    put(
        "trailing_distribution_yield",
        quote.get("trailingAnnualDividendYield"),
        "quote.trailingAnnualDividendYield",
    )
    put("expense_ratio", quote.get("netExpenseRatio"), "quote.netExpenseRatio", 0.01)
    return result


def _client():
    session = _session()
    if session is None:
        raise RuntimeError("curl_cffi is required")
    response = session.get(
        "https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=20
    )
    response.raise_for_status()
    return session, response.text


def _quote_batch(symbols):
    session, crumb = _client()
    response = session.get(
        "https://query1.finance.yahoo.com/v7/finance/quote",
        params={"symbols": ",".join(symbols), "crumb": crumb},
        timeout=30,
    )
    response.raise_for_status()
    body = response.json()["quoteResponse"]
    if body.get("error"):
        raise RuntimeError(str(body["error"]))
    return {q["symbol"]: q for q in body["result"]}


def collect(con, entries, *, log=print):
    """Fetch once per run and insert without replacing an earlier same-day capture."""
    candidates = universe(entries)
    quotes = {}
    for i in range(0, len(candidates), 40):
        symbols = [e["symbol"] for e in candidates[i : i + 40]]
        try:
            quotes.update(_quote_batch(symbols))
        except Exception as exc:
            log(f"ETF carry quote batch failed ({type(exc).__name__}): {exc}")
            for symbol in symbols:
                try:
                    quotes.update(_quote_batch([symbol]))
                except Exception as individual_exc:
                    log(
                        f"ETF carry {symbol} quote failed ({type(individual_exc).__name__}): {individual_exc}"
                    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    counts = dict.fromkeys(FIELDS, 0)
    matched = 0
    for entry in candidates:
        symbol = entry["symbol"]
        quote = quotes.get(symbol, {})
        if quote.get("quoteType") != "ETF":
            continue
        matched += 1
        for field, (value, raw) in parse(quote).items():
            con.execute(
                "INSERT INTO etf_carry_snapshots VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT DO NOTHING",
                [now.date(), symbol, field, value, "yahoo", now, raw],
            )
            counts[field] += 1
    log(
        f"ETF carry coverage: {matched}/{len(candidates)} ETF quotes; "
        + ", ".join(f"{field}={count}" for field, count in counts.items())
    )
    return counts
