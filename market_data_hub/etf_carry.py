"""Yahoo ETF carry: today's observations only, never historical backfill.

Rates are decimal fractions; duration is years; hedge flag is 0 or 1.
Yahoo's quote netExpenseRatio is percent, unlike summary raw ratios.
"""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from market_data_hub.sources.yahoo_direct import _session

FIELDS = {
    "trailing_distribution_yield": 1,
    "sec_30_day_yield": 1,
    "expense_ratio": 1,
    "currency_hedged": 1,
    "yield_to_maturity": 1,
    "duration": 1,
}
ASSET_CLASSES = {"EQUITY", "FIXED_INCOME", "COMMODITIES", "REAL_ESTATE", "ALTERNATIVES"}


def universe(entries):
    return [e for e in entries if e.get("asset_class") in ASSET_CLASSES
            and not e.get("instrument_type") and not e["symbol"].startswith("^")]


def parse(quote, summary=None):
    """Return field -> (value, raw path); only documented, unambiguous mappings."""
    result = {}

    def put(field, value, raw, scale=1):
        if isinstance(value, dict):
            value = value.get("raw")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            result[field] = (float(value) * scale, raw)

    put("trailing_distribution_yield", quote.get("trailingAnnualDividendYield"),
        "quote.trailingAnnualDividendYield")
    put("expense_ratio", quote.get("netExpenseRatio"), "quote.netExpenseRatio", 0.01)
    holdings = (summary or {}).get("topHoldings", {}).get("bondHoldings", {})
    put("duration", holdings.get("duration"), "topHoldings.bondHoldings.duration")
    return result


def _client():
    session = _session()
    if session is None:
        raise RuntimeError("curl_cffi is required")
    response = session.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=20)
    response.raise_for_status()
    return session, response.text


def _quote_batch(symbols):
    session, crumb = _client()
    response = session.get("https://query1.finance.yahoo.com/v7/finance/quote",
                           params={"symbols": ",".join(symbols), "crumb": crumb}, timeout=30)
    response.raise_for_status()
    body = response.json()["quoteResponse"]
    if body.get("error"):
        raise RuntimeError(str(body["error"]))
    return {q["symbol"]: q for q in body["result"]}


def _summary(symbol):
    session, crumb = _client()
    response = session.get(f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}",
                           params={"modules": "topHoldings", "crumb": crumb}, timeout=30)
    response.raise_for_status()
    body = response.json()["quoteSummary"]
    if body.get("error"):
        raise RuntimeError(str(body["error"]))
    return (body.get("result") or [{}])[0]


def collect(con, entries, *, log=print):
    """Fetch once per run and insert without replacing an earlier same-day capture."""
    candidates = universe(entries)
    quotes = {}
    for i in range(0, len(candidates), 40):
        symbols = [e["symbol"] for e in candidates[i:i + 40]]
        try:
            quotes.update(_quote_batch(symbols))
        except Exception as exc:
            log(f"ETF carry quote batch failed ({type(exc).__name__}): {exc}")
            for symbol in symbols:
                try:
                    quotes.update(_quote_batch([symbol]))
                except Exception as individual_exc:
                    log(f"ETF carry {symbol} quote failed ({type(individual_exc).__name__}): {individual_exc}")
    bond_symbols = [e["symbol"] for e in candidates if e["asset_class"] == "FIXED_INCOME"
                    and quotes.get(e["symbol"], {}).get("quoteType") == "ETF"]
    summaries = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_summary, s): s for s in bond_symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                summaries[symbol] = future.result()
            except Exception as exc:
                log(f"ETF carry {symbol} summary failed ({type(exc).__name__}): {exc}")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    counts = dict.fromkeys(FIELDS, 0)
    matched = 0
    for entry in candidates:
        symbol = entry["symbol"]
        quote = quotes.get(symbol, {})
        if quote.get("quoteType") != "ETF":
            continue
        matched += 1
        for field, (value, raw) in parse(quote, summaries.get(symbol)).items():
            con.execute("INSERT INTO etf_carry_snapshots VALUES (?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT DO NOTHING",
                        [now.date(), symbol, field, value, "yahoo", now, raw])
            counts[field] += 1
    log(f"ETF carry coverage: {matched}/{len(candidates)} ETF quotes; " +
        ", ".join(f"{field}={count}" for field, count in counts.items()))
    return counts
