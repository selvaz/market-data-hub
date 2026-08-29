# -*- coding: utf-8 -*-
"""CFTC Commitments of Traders weekly positioning from the Socrata API.

Canonical outputs are purpose-fit for ``cftc_tff_positioning`` and
``cftc_legacy_positioning`` rather than the single-value macro tables.
"""
from __future__ import annotations

import time
from typing import Optional

import pandas as pd
import requests

_TFF_URL = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"
_LEGACY_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
_PAGE_SIZE = 50000

_TFF_RENAME = {
    "report_date_as_yyyy_mm_dd": "report_date",
    "dealer_positions_long_all": "dealer_long",
    "dealer_positions_short_all": "dealer_short",
    "dealer_positions_spread_all": "dealer_spread",
    "asset_mgr_positions_long": "asset_mgr_long",
    "asset_mgr_positions_short": "asset_mgr_short",
    "asset_mgr_positions_spread": "asset_mgr_spread",
    "lev_money_positions_long": "lev_money_long",
    "lev_money_positions_short": "lev_money_short",
    "lev_money_positions_spread": "lev_money_spread",
    "other_rept_positions_long": "other_rept_long",
    "other_rept_positions_short": "other_rept_short",
    "other_rept_positions_spread": "other_rept_spread",
    "tot_rept_positions_long_all": "total_reportable_long",
    "tot_rept_positions_short": "total_reportable_short",
    "nonrept_positions_long_all": "nonreportable_long",
    "nonrept_positions_short_all": "nonreportable_short",
    "pct_of_oi_dealer_long_all": "pct_oi_dealer_long",
    "pct_of_oi_dealer_short_all": "pct_oi_dealer_short",
    "pct_of_oi_asset_mgr_long": "pct_oi_asset_mgr_long",
    "pct_of_oi_asset_mgr_short": "pct_oi_asset_mgr_short",
    "pct_of_oi_lev_money_long": "pct_oi_lev_money_long",
    "pct_of_oi_lev_money_short": "pct_oi_lev_money_short",
    "traders_tot_all": "traders_total",
}
_TFF_COLS = [
    "report_date", "contract_market_name", "cftc_contract_market_code",
    "commodity_name", "commodity_subgroup_name", "open_interest_all",
    "dealer_long", "dealer_short", "dealer_spread", "asset_mgr_long",
    "asset_mgr_short", "asset_mgr_spread", "lev_money_long",
    "lev_money_short", "lev_money_spread", "other_rept_long",
    "other_rept_short", "other_rept_spread", "total_reportable_long",
    "total_reportable_short", "nonreportable_long", "nonreportable_short",
    "pct_oi_dealer_long", "pct_oi_dealer_short", "pct_oi_asset_mgr_long",
    "pct_oi_asset_mgr_short", "pct_oi_lev_money_long",
    "pct_oi_lev_money_short", "traders_total", "source",
]

_LEGACY_RENAME = {
    "report_date_as_yyyy_mm_dd": "report_date",
    "noncomm_positions_long_all": "noncomm_long",
    "noncomm_positions_short_all": "noncomm_short",
    # The upstream Socrata field itself is misspelled.
    "noncomm_postions_spread_all": "noncomm_spread",
    "comm_positions_long_all": "comm_long",
    "comm_positions_short_all": "comm_short",
    "tot_rept_positions_long_all": "total_reportable_long",
    "tot_rept_positions_short": "total_reportable_short",
    "nonrept_positions_long_all": "nonreportable_long",
    "nonrept_positions_short_all": "nonreportable_short",
}
_LEGACY_COLS = [
    "report_date", "contract_market_name", "cftc_contract_market_code",
    "commodity_name", "open_interest_all", "noncomm_long", "noncomm_short",
    "noncomm_spread", "comm_long", "comm_short", "total_reportable_long",
    "total_reportable_short", "nonreportable_long", "nonreportable_short",
    "source",
]


def _http_get(url: str, params: dict, timeout: int, retries: int,
              base_sleep: float) -> requests.Response:
    """GET one Socrata page with bounded retry and exponential backoff."""
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
        f"cftc_cot: no attempts made (retries={retries})")


def _fetch_all_pages(base_url: str, where_clauses: list[str], timeout: int,
                     retries: int, base_sleep: float) -> list[dict]:
    """Fetch every matching Socrata page in stable report/contract order."""
    rows: list[dict] = []
    offset = 0
    while True:
        params = {
            "$limit": _PAGE_SIZE,
            "$offset": offset,
            "$order": "report_date_as_yyyy_mm_dd, contract_market_name",
            "$where": " AND ".join(where_clauses),
        }
        page = _http_get(
            base_url, params, timeout, retries, base_sleep).json()
        rows.extend(page)
        if len(page) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE
    return rows


def _where(start: str, end: str, contract_name: Optional[str]) -> list[str]:
    clauses = [
        "report_date_as_yyyy_mm_dd between "
        f"'{start}T00:00:00' and '{end}T00:00:00'"
    ]
    if contract_name:
        escaped = contract_name.replace("'", "''")
        clauses.append(f"contract_market_name like '%{escaped}%'")
    return clauses


def _normalize(rows: list[dict], rename: dict[str, str], columns: list[str],
               source: str) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=columns)
    df = pd.DataFrame(rows).rename(columns=rename)
    for column in columns:
        if column not in df.columns and column != "source":
            df[column] = None
    df["report_date"] = pd.to_datetime(df["report_date"], errors="coerce")
    text_columns = {
        "report_date", "contract_market_name", "cftc_contract_market_code",
        "commodity_name", "commodity_subgroup_name", "source",
    }
    for column in set(columns) - text_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df["source"] = source
    return df[columns]


def fetch_tff_futures(start: str, end: str, *,
                      contract_name: Optional[str] = None, timeout=30,
                      retries=3, base_sleep=1.0) -> pd.DataFrame:
    """Download TFF Futures Only positioning for an inclusive date range.

    Returns the fixed column contract in ``_TFF_COLS``. Position, percentage,
    open-interest, and trader-count fields are numeric; ``report_date`` is a
    pandas datetime and ``source`` is ``"cftc_tff"``.
    """
    rows = _fetch_all_pages(
        _TFF_URL, _where(start, end, contract_name), timeout, retries, base_sleep)
    return _normalize(rows, _TFF_RENAME, _TFF_COLS, "cftc_tff")


def fetch_legacy_futures(start: str, end: str, *,
                         contract_name: Optional[str] = None, timeout=30,
                         retries=3, base_sleep=1.0) -> pd.DataFrame:
    """Download Legacy Futures Only positioning for an inclusive date range.

    Returns the fixed column contract in ``_LEGACY_COLS``. Position and
    open-interest fields are numeric; ``report_date`` is a pandas datetime and
    ``source`` is ``"cftc_legacy"``.
    """
    rows = _fetch_all_pages(
        _LEGACY_URL, _where(start, end, contract_name), timeout, retries,
        base_sleep)
    return _normalize(rows, _LEGACY_RENAME, _LEGACY_COLS, "cftc_legacy")
