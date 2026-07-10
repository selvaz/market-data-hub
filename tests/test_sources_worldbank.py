# -*- coding: utf-8 -*-
"""
Contract tests for sources/worldbank.py — verifies fetch_worldbank() and
_annual_date() understand the real shape of the World Bank API's classic
2-element response ([header_with_pages, [obs...]]) by monkeypatching the
module's single HTTP seam (_get_json).
"""
from __future__ import annotations

import datetime as dt

from market_data_hub.sources import worldbank as wb

_SPEC = {"id": "public_debt_gdp", "name": "General government debt (% GDP)",
         "code": "GC.DOD.TOTL.GD.ZS", "dataset": "WDI", "pillar": "sovereign",
         "orientation": -1, "unit": "percent"}

_COUNTRIES = [{"iso3": "USA", "wb": "US"}, {"iso3": "ITA", "wb": "IT"}]


def test_annual_date_parses_wdi_year_string():
    assert wb._annual_date("2023") == dt.date(2023, 12, 31)
    assert wb._annual_date("not-a-year") is None


def test_fetch_worldbank_parses_classic_two_element_response(monkeypatch):
    def fake_get_json(url, params, timeout, retries, base_sleep):
        return [
            {"page": 1, "pages": 1, "per_page": 20000, "total": 2},
            [
                {"countryiso3code": "USA", "date": "2023", "value": 121.7},
                {"countryiso3code": "ITA", "date": "2023", "value": 140.2},
                {"countryiso3code": "ITA", "date": "2022", "value": None},  # dropped
            ],
        ]

    monkeypatch.setattr(wb, "_get_json", fake_get_json)
    df = wb.fetch_worldbank(_SPEC, _COUNTRIES, start_year=2020, end_year=2023)

    assert list(df.columns) == wb._COLS
    assert len(df) == 2
    row = df[df["country_iso3"] == "USA"].iloc[0]
    assert row["value"] == 121.7
    assert row["date"] == dt.date(2023, 12, 31)
    assert (df["indicator_id"] == "public_debt_gdp").all()
    assert (df["source"] == "worldbank").all()


def test_fetch_worldbank_follows_pagination(monkeypatch):
    calls = []

    def fake_get_json(url, params, timeout, retries, base_sleep):
        calls.append(params["page"])
        if params["page"] == 1:
            return [{"page": 1, "pages": 2},
                    [{"countryiso3code": "USA", "date": "2022", "value": 118.0}]]
        return [{"page": 2, "pages": 2},
                [{"countryiso3code": "USA", "date": "2023", "value": 121.7}]]

    monkeypatch.setattr(wb, "_get_json", fake_get_json)
    df = wb.fetch_worldbank(_SPEC, [_COUNTRIES[0]], start_year=2020, end_year=2023)

    assert calls == [1, 2]
    assert sorted(df["value"].tolist()) == [118.0, 121.7]


def test_fetch_worldbank_drops_countries_outside_request(monkeypatch):
    # a batch response may include a country we didn't ask for (aggregate
    # region codes etc.) -- must be filtered out, not silently kept
    def fake_get_json(url, params, timeout, retries, base_sleep):
        return [{"page": 1, "pages": 1},
                [{"countryiso3code": "USA", "date": "2023", "value": 121.7},
                 {"countryiso3code": "WLD", "date": "2023", "value": 90.0}]]

    monkeypatch.setattr(wb, "_get_json", fake_get_json)
    df = wb.fetch_worldbank(_SPEC, [_COUNTRIES[0]], start_year=2020, end_year=2023)

    assert set(df["country_iso3"]) == {"USA"}
