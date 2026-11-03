"""Free, first-capture ETF valuation and TradingView breadth snapshots."""
from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml

from market_data_hub.etf_carry import _quote_batch, _summary

CONFIG = Path(__file__).parent / "config" / "equity_snapshots.yaml"
FIELDS = ("close", "SMA50", "SMA200", "change", "price_52_week_high", "price_52_week_low")
UNIVERSE = "us_listed_cap10b"


def symbols():
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["symbols"]


def number(value):
    if isinstance(value, dict):
        value = value.get("raw")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def parse_valuation(quote, summary):
    """Only direct quote multiples; holdings ratios are retained unconverted."""
    raw = {"quote": {k: quote.get(k) for k in (
        "trailingPE", "forwardPE", "priceToBook", "trailingAnnualDividendYield")}}
    raw["topHoldings.equityHoldings"] = summary.get("topHoldings", {}).get("equityHoldings", {})
    return (number(quote.get("trailingPE")), number(quote.get("forwardPE")),
            number(quote.get("priceToBook")), number(quote.get("trailingAnnualDividendYield")),
            json.dumps(raw, allow_nan=False))


def fetch_valuations(universe):
    quotes = {}
    for i in range(0, len(universe), 40):
        quotes.update(_quote_batch(universe[i:i + 40]))
    def one(symbol):
        try:
            return _summary(symbol)
        except Exception as exc:
            print(f"Yahoo {symbol} holdings unavailable: {type(exc).__name__}: {exc}")
            return {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        summaries = dict(zip(universe, pool.map(one, universe)))
    return [(s, *parse_valuation(quotes[s], summaries[s])) for s in universe
            if s in quotes and quotes[s].get("quoteType") == "ETF"]


def parse_breadth(rows):
    """Compute ratios over observable rows and counts from a complete scan."""
    def compare(field, predicate):
        values = [(number(r.get("close")), number(r.get(field))) for r in rows]
        values = [(a, b) for a, b in values if a is not None and b is not None]
        return sum(predicate(a, b) for a, b in values), len(values)
    a50, n50 = compare("SMA50", lambda a, b: a > b)
    a200, n200 = compare("SMA200", lambda a, b: a > b)
    highs, nh = compare("price_52_week_high", lambda a, b: a >= b)
    lows, nl = compare("price_52_week_low", lambda a, b: a <= b)
    changes = [v for r in rows if (v := number(r.get("change"))) is not None]
    return (100 * a50 / n50 if n50 else None, n50,
            100 * a200 / n200 if n200 else None, n200,
            sum(v > 0 for v in changes) if changes else None,
            sum(v < 0 for v in changes) if changes else None, len(changes),
            highs if nh else None, nh, lows if nl else None, nl)


def fetch_breadth():
    """Scan the complete US-listed stock universe with market cap >= $10bn."""
    filters = [{"left": "type", "operation": "equal", "right": "stock"},
               {"left": "market_cap_basic", "operation": "egreater", "right": 10_000_000_000}]
    rows, seen = [], set()
    total = None
    while total is None or len(rows) < total:
        body = {"filter": filters, "columns": list(FIELDS),
                "sort": {"sortBy": "name", "sortOrder": "asc"},
                "range": [len(rows), len(rows) + 500]}
        response = requests.post("https://scanner.tradingview.com/america/scan", json=body, timeout=30)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data.get("totalCount"), int) or not data.get("data"):
            raise ValueError("TradingView returned an empty or malformed scan")
        if total is not None and total != data["totalCount"]:
            raise ValueError("TradingView universe changed during pagination")
        total = data["totalCount"]
        for item in data["data"]:
            if item["s"] in seen or len(item["d"]) != len(FIELDS):
                raise ValueError("TradingView returned duplicate or malformed rows")
            seen.add(item["s"])
            rows.append(dict(zip(FIELDS, item["d"])))
    if len(rows) != total:
        raise ValueError("TradingView returned an incomplete universe")
    return total, parse_breadth(rows)


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def collection_date(instant):
    return instant.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("America/Los_Angeles")).date()


def store_valuations(con, rows, fetched):
    day = collection_date(fetched)
    for symbol, trailing, forward, book, dividend, raw in rows:
        con.execute("INSERT INTO etf_valuation_snapshots VALUES (?, ?, ?, ?, ?, ?, NULL, 'yahoo', ?, ?) ON CONFLICT DO NOTHING",
                    [day, symbol, trailing, forward, book, dividend, fetched, raw])


def store_breadth(con, size, values, fetched):
    con.execute("INSERT INTO market_breadth_snapshots VALUES (?, 'US', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'tradingview', ?) ON CONFLICT DO NOTHING",
                [collection_date(fetched), UNIVERSE, size, *values,
                 "TradingView US-listed stocks, market cap >= USD 10bn; close vs daily SMA and 52-week extremes", fetched])
