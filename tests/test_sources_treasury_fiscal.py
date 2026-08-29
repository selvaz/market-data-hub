# -*- coding: utf-8 -*-
"""Contract tests for the U.S. Treasury Fiscal Data source."""
from __future__ import annotations

import pandas as pd

from market_data_hub.sources import treasury_fiscal


class _FakeResponse:
    def __init__(self, json_data):
        self._json = json_data

    def json(self):
        return self._json


def _one_page(data):
    return _FakeResponse({
        "data": data,
        "meta": {"count": len(data), "total-count": len(data),
                 "total-pages": 1},
        "links": {"next": None},
    })


def test_operating_cash_balance_contract_and_null_coercion(monkeypatch):
    captured = {}

    def fake_http_get(url, params, timeout, retries, base_sleep):
        captured["url"] = url
        captured["params"] = params
        return _one_page([{
            "record_date": "2026-08-27",
            "account_type": "Treasury General Account (TGA) Opening Balance",
            "open_today_bal": "801234",
            "close_today_bal": "null",
            "open_month_bal": "756000",
            "open_fiscal_year_bal": "620000",
        }])

    monkeypatch.setattr(treasury_fiscal, "_http_get", fake_http_get)
    df = treasury_fiscal.fetch_operating_cash_balance(
        "2026-08-01", "2026-08-27")

    assert captured["url"].endswith("/v1/accounting/dts/operating_cash_balance")
    assert captured["params"]["page[size]"] == 10_000
    assert captured["params"]["filter"] == (
        "record_date:gte:2026-08-01,record_date:lte:2026-08-27")
    assert list(df.columns) == treasury_fiscal._CASH_COLUMNS
    assert pd.api.types.is_datetime64_any_dtype(df["record_date"])
    assert df.loc[0, "open_today_bal"] == 801234
    assert pd.isna(df.loc[0, "close_today_bal"])
    assert (df["source"] == "treasury_fiscal").all()


def test_debt_to_penny_contract_and_numeric_values(monkeypatch):
    def fake_http_get(url, params, timeout, retries, base_sleep):
        assert url.endswith("/v2/accounting/od/debt_to_penny")
        return _one_page([{
            "record_date": "2026-08-27",
            "debt_held_public_amt": "32313802811901.63",
            "intragov_hold_amt": "7763727020041.31",
            "tot_pub_debt_out_amt": "40077529831942.94",
        }])

    monkeypatch.setattr(treasury_fiscal, "_http_get", fake_http_get)
    df = treasury_fiscal.fetch_debt_to_penny("2026-08-01", "2026-08-27")

    assert list(df.columns) == treasury_fiscal._DEBT_COLUMNS
    assert pd.api.types.is_float_dtype(df["tot_pub_debt_out_amt"])
    assert df.loc[0, "tot_pub_debt_out_amt"] == 40077529831942.94


def test_auctions_contract_filter_and_bill_discount_rate(monkeypatch):
    captured = {}

    def fake_http_get(url, params, timeout, retries, base_sleep):
        captured["url"] = url
        captured["params"] = params
        return _one_page([{
            "record_date": "2026-08-25", "cusip": "912797XX1",
            "security_type": "Bill", "security_term": "6-Week",
            "auction_date": "2026-08-25", "issue_date": "2026-08-27",
            "maturity_date": "2026-10-08", "high_yield": "null",
            "avg_med_yield": "null", "high_discnt_rate": "4.125",
            "avg_med_discnt_rate": "4.100", "total_tendered": "150000000000",
            "total_accepted": "85000000000", "bid_to_cover_ratio": "2.54",
        }])

    monkeypatch.setattr(treasury_fiscal, "_http_get", fake_http_get)
    df = treasury_fiscal.fetch_auctions(
        "2026-08-01", "2026-08-27", security_type="Bill")

    assert captured["url"].endswith("/v1/accounting/od/auctions_query")
    assert captured["params"]["filter"] == (
        "record_date:gte:2026-08-01,record_date:lte:2026-08-27,"
        "security_type:eq:Bill")
    assert list(df.columns) == treasury_fiscal._AUCTION_COLUMNS
    assert pd.isna(df.loc[0, "high_yield"])
    assert df.loc[0, "high_discnt_rate"] == 4.125
    assert df.loc[0, "bid_to_cover_ratio"] == 2.54
    for column in ["record_date", "auction_date", "issue_date", "maturity_date"]:
        assert pd.api.types.is_datetime64_any_dtype(df[column])


def test_empty_response_has_fixed_contract(monkeypatch):
    monkeypatch.setattr(
        treasury_fiscal, "_http_get",
        lambda *args: _one_page([]))

    df = treasury_fiscal.fetch_debt_to_penny("2026-08-01", "2026-08-27")

    assert df.empty
    assert list(df.columns) == treasury_fiscal._DEBT_COLUMNS
