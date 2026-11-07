# -*- coding: utf-8 -*-
"""U.S. Treasury Fiscal Data downloads with fixed DataFrame contracts."""
from __future__ import annotations

import time
from typing import Optional

import pandas as pd
import requests

_BASE_URL = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
_PAGE_SIZE = 10_000

_CASH_COLUMNS = [
    "record_date", "account_type", "open_today_bal", "close_today_bal",
    "open_month_bal", "open_fiscal_year_bal", "source",
]
_DEBT_COLUMNS = [
    "record_date", "debt_held_public_amt", "intragov_hold_amt",
    "tot_pub_debt_out_amt", "source",
]
_AUCTION_COLUMNS = [
    "record_date", "cusip", "security_type", "security_term", "auction_date",
    "issue_date", "maturity_date", "high_yield", "avg_med_yield",
    "high_discnt_rate", "avg_med_discnt_rate", "total_tendered",
    "total_accepted", "bid_to_cover_ratio", "source",
]


def _http_get(url: str, params: dict, timeout: int, retries: int,
              base_sleep: float) -> requests.Response:
    """GET one Fiscal Data page with exponential retry/backoff."""
    headers = {"User-Agent": "market-data-hub/0.1", "Connection": "close"}
    last = None
    for attempt in range(retries):
        try:
            with requests.Session() as session:
                response = session.get(
                    url, params=params, headers=headers, timeout=timeout)
                response.raise_for_status()
                return response
        except Exception as exc:
            last = exc
            if attempt < retries - 1:
                time.sleep(base_sleep * (4 ** attempt))
    raise last if last else RuntimeError(
        f"treasury_fiscal: no attempts made (retries={retries})")


def _fetch_pages(path: str, params: dict, timeout: int, retries: int,
                 base_sleep: float) -> list[dict]:
    """Fetch every page, using the API's ``meta.total-pages`` value."""
    rows: list[dict] = []
    page = 1
    while True:
        page_params = dict(params)
        page_params["page[size]"] = _PAGE_SIZE
        page_params["page[number]"] = page
        response = _http_get(
            f"{_BASE_URL}{path}", page_params, timeout, retries, base_sleep)
        payload = response.json()
        batch = payload.get("data", [])
        rows.extend(batch)

        total_pages = payload.get("meta", {}).get("total-pages")
        if total_pages is not None:
            try:
                if page >= int(total_pages):
                    break
            except (TypeError, ValueError):
                if len(batch) < _PAGE_SIZE:
                    break
        elif len(batch) < _PAGE_SIZE:
            break
        page += 1
    return rows


def _frame(rows: list[dict], columns: list[str], numeric: list[str],
           dates: list[str], sort_by: list[str]) -> pd.DataFrame:
    """Apply a fixed contract and normalize Fiscal Data string values."""
    if not rows:
        return pd.DataFrame(columns=columns)
    data_columns = [column for column in columns if column != "source"]
    df = pd.DataFrame(rows).reindex(columns=data_columns)
    for column in numeric:
        df[column] = pd.to_numeric(
            df[column].replace("null", None), errors="coerce")
    for column in dates:
        df[column] = pd.to_datetime(df[column], errors="coerce")
    df = df.dropna(subset=["record_date"])
    df["source"] = "treasury_fiscal"
    return df.sort_values(sort_by).reset_index(drop=True)[columns]


def fetch_operating_cash_balance(
        start: str, end: str, *, timeout: int = 30, retries: int = 3,
        base_sleep: float = 1.0) -> pd.DataFrame:
    """Download Daily Treasury Statement cash-balance rows, inclusive."""
    rows = _fetch_pages(
        "/v1/accounting/dts/operating_cash_balance",
        {"sort": "-record_date",
         "filter": f"record_date:gte:{start},record_date:lte:{end}"},
        timeout, retries, base_sleep)
    numeric = ["open_today_bal", "close_today_bal", "open_month_bal",
               "open_fiscal_year_bal"]
    return _frame(rows, _CASH_COLUMNS, numeric, ["record_date"],
                  ["record_date", "account_type"])


def fetch_debt_to_penny(
        start: str, end: str, *, timeout: int = 30, retries: int = 3,
        base_sleep: float = 1.0) -> pd.DataFrame:
    """Download daily Debt to the Penny totals, inclusive.

    The three amount columns are float64 over penny-precise vendor figures
    that already run 14-15 digits before the decimal (e.g.
    "40077529831942.94") -- right at float64's ~15-17 significant-digit
    precision edge, so the stored value can drift by a fraction of a dollar
    on the largest totals. Immaterial at the trillion-dollar granularity
    this data is actually read at; a caller needing exact-penny precision
    should hit the vendor API directly instead.
    """
    rows = _fetch_pages(
        "/v2/accounting/od/debt_to_penny",
        {"sort": "-record_date",
         "filter": f"record_date:gte:{start},record_date:lte:{end}"},
        timeout, retries, base_sleep)
    numeric = ["debt_held_public_amt", "intragov_hold_amt",
               "tot_pub_debt_out_amt"]
    return _frame(rows, _DEBT_COLUMNS, numeric, ["record_date"],
                  ["record_date"])


def fetch_auctions(
        start: str, end: str, *, security_type: Optional[str] = None,
        timeout: int = 30, retries: int = 3,
        base_sleep: float = 1.0) -> pd.DataFrame:
    """Download Treasury auction results, optionally by security type."""
    filters = [f"record_date:gte:{start}", f"record_date:lte:{end}"]
    if security_type is not None:
        filters.append(f"security_type:eq:{security_type}")
    rows = _fetch_pages(
        "/v1/accounting/od/auctions_query",
        {"sort": "-auction_date", "filter": ",".join(filters)},
        timeout, retries, base_sleep)
    numeric = [
        "high_yield", "avg_med_yield", "high_discnt_rate",
        "avg_med_discnt_rate", "total_tendered", "total_accepted",
        "bid_to_cover_ratio",
    ]
    dates = ["record_date", "auction_date", "issue_date", "maturity_date"]
    return _frame(rows, _AUCTION_COLUMNS, numeric, dates,
                  ["auction_date", "cusip"])
