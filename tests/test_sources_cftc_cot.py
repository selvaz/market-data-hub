# -*- coding: utf-8 -*-
"""Contract tests for the CFTC COT Socrata source."""
from __future__ import annotations

import pandas as pd

from market_data_hub.sources import cftc_cot


class _FakeResponse:
    def __init__(self, json_data):
        self._json = json_data

    def json(self):
        return self._json


def _tff_row(date="2026-08-25T00:00:00.000", name="10-YEAR U.S. TREASURY"):
    return {
        "report_date_as_yyyy_mm_dd": date,
        "contract_market_name": name,
        "cftc_contract_market_code": "043602",
        "commodity_name": "10-YEAR U.S. TREASURY NOTES",
        "commodity_subgroup_name": "U.S. TREASURY SECURITIES",
        "commodity_group_name": "FINANCIAL INSTRUMENTS",
        "open_interest_all": "2813176",
        "dealer_positions_long_all": "46404",
        "dealer_positions_short_all": "592142",
        "dealer_positions_spread_all": "111820",
        "asset_mgr_positions_long": "1234567",
        "asset_mgr_positions_short": "305421",
        "asset_mgr_positions_spread": "224100",
        "lev_money_positions_long": "380200",
        "lev_money_positions_short": "622300",
        "lev_money_positions_spread": "118900",
        "other_rept_positions_long": "75200",
        "other_rept_positions_short": "49100",
        "other_rept_positions_spread": "38100",
        "tot_rept_positions_long_all": "2225191",
        "tot_rept_positions_short": "2067783",
        "nonrept_positions_long_all": "587985",
        "nonrept_positions_short_all": "745393",
        "pct_of_oi_dealer_long_all": "1.6",
        "pct_of_oi_dealer_short_all": "21.0",
        "pct_of_oi_asset_mgr_long": "43.9",
        "pct_of_oi_asset_mgr_short": "10.9",
        "pct_of_oi_lev_money_long": "13.5",
        "pct_of_oi_lev_money_short": "22.1",
        "traders_tot_all": "464",
    }


def _legacy_row():
    return {
        "report_date_as_yyyy_mm_dd": "2026-08-25T00:00:00.000",
        "contract_market_name": "CRUDE OIL, LIGHT SWEET - NYMEX",
        "cftc_contract_market_code": "067651",
        "commodity_name": "CRUDE OIL, LIGHT SWEET",
        "open_interest_all": "1800123",
        "noncomm_positions_long_all": "402100",
        "noncomm_positions_short_all": "215900",
        "noncomm_postions_spread_all": "164300",
        "comm_positions_long_all": "810400",
        "comm_positions_short_all": "1043500",
        "tot_rept_positions_long_all": "1376800",
        "tot_rept_positions_short": "1423700",
        "nonrept_positions_long_all": "423323",
        "nonrept_positions_short_all": "376423",
    }


def test_tff_real_shape_is_renamed_and_coerced(monkeypatch):
    captured = {}

    def fake_http_get(url, params, timeout, retries, base_sleep):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse([_tff_row()])

    monkeypatch.setattr(cftc_cot, "_http_get", fake_http_get)
    df = cftc_cot.fetch_tff_futures(
        "2026-08-01", "2026-08-28", contract_name="TREASURY")

    assert captured["url"] == cftc_cot._TFF_URL
    assert captured["params"]["$limit"] == 50000
    assert "report_date_as_yyyy_mm_dd between" in captured["params"]["$where"]
    assert "contract_market_name like '%TREASURY%'" in captured["params"]["$where"]
    assert list(df.columns) == cftc_cot._TFF_COLS
    assert pd.api.types.is_datetime64_any_dtype(df["report_date"])
    assert pd.api.types.is_numeric_dtype(df["dealer_long"])
    assert df.loc[0, "dealer_long"] == 46404
    assert df.loc[0, "pct_oi_dealer_short"] == 21.0
    assert df.loc[0, "traders_total"] == 464
    assert df.loc[0, "source"] == "cftc_tff"


def test_legacy_real_shape_including_vendor_typo(monkeypatch):
    def fake_http_get(url, params, timeout, retries, base_sleep):
        assert url == cftc_cot._LEGACY_URL
        return _FakeResponse([_legacy_row()])

    monkeypatch.setattr(cftc_cot, "_http_get", fake_http_get)
    df = cftc_cot.fetch_legacy_futures("2026-08-01", "2026-08-28")

    assert list(df.columns) == cftc_cot._LEGACY_COLS
    assert pd.api.types.is_datetime64_any_dtype(df["report_date"])
    assert pd.api.types.is_numeric_dtype(df["noncomm_spread"])
    assert df.loc[0, "noncomm_spread"] == 164300
    assert df.loc[0, "comm_short"] == 1043500
    assert df.loc[0, "source"] == "cftc_legacy"


def test_pagination_concatenates_pages(monkeypatch):
    calls = []
    pages = [
        [_tff_row(name="CONTRACT A"), _tff_row(name="CONTRACT B")],
        [_tff_row(name="CONTRACT C")],
    ]

    def fake_http_get(url, params, timeout, retries, base_sleep):
        calls.append(params.copy())
        return _FakeResponse(pages[len(calls) - 1])

    monkeypatch.setattr(cftc_cot, "_PAGE_SIZE", 2)
    monkeypatch.setattr(cftc_cot, "_http_get", fake_http_get)
    df = cftc_cot.fetch_tff_futures("2026-08-01", "2026-08-28")

    assert len(df) == 3
    assert [call["$offset"] for call in calls] == [0, 2]
    assert [call["$limit"] for call in calls] == [2, 2]
