# -*- coding: utf-8 -*-
"""
Contract test for sources/imf.py — verifies fetch_imf() understands the real
shape of the IMF DataMapper response ({"values": {code: {imf_country: {year:
value}}}}) by monkeypatching the module's single HTTP seam (_get_json).
"""
from __future__ import annotations

from market_data_hub.sources import imf

_SPEC = {"id": "gdp_growth_weo", "name": "GDP growth (WEO)", "code": "NGDP_RPCH",
         "dataset": "WEO", "pillar": "growth", "orientation": 1, "unit": "percent"}

_COUNTRIES = [
    {"iso3": "USA", "imf": "111"},
    {"iso3": "ITA", "imf": "136"},
    {"iso3": "ARG", "imf": "213"},
]


def test_fetch_imf_parses_datamapper_shape(monkeypatch):
    def fake_get_json(url, timeout, retries, base_sleep):
        assert url == f"{imf._BASE}/NGDP_RPCH"
        return {"values": {"NGDP_RPCH": {
            "111": {"2023": 2.5, "2024": 2.1},
            "136": {"2023": 0.7, "2024": None},   # None values must be skipped
            # ARG (213) deliberately absent from the response
        }}}

    monkeypatch.setattr(imf, "_get_json", fake_get_json)
    df = imf.fetch_imf(_SPEC, _COUNTRIES, start_year=2020, end_year=2025)

    assert list(df.columns) == imf._COLS
    assert len(df) == 3   # USA x2 + ITA x1 (None dropped, ARG absent -> no rows)
    usa = df[df["country_iso3"] == "USA"].sort_values("date")
    assert usa["value"].tolist() == [2.5, 2.1]
    assert (df["indicator_id"] == "gdp_growth_weo").all()
    assert (df["provider_dataset"] == "WEO").all()
    assert (df["frequency"] == "A").all()
    assert (df["status"] == "ok").all()
    assert "ARG" not in set(df["country_iso3"])


def test_fetch_imf_filters_by_year_window(monkeypatch):
    def fake_get_json(url, timeout, retries, base_sleep):
        return {"values": {"NGDP_RPCH": {
            "111": {"1999": 4.0, "2023": 2.5, "2031": 1.8},
        }}}

    monkeypatch.setattr(imf, "_get_json", fake_get_json)
    df = imf.fetch_imf(_SPEC, _COUNTRIES, start_year=2020, end_year=2025)

    assert df["value"].tolist() == [2.5]


def test_fetch_imf_returns_empty_frame_on_fetch_failure(monkeypatch):
    def fake_get_json(url, timeout, retries, base_sleep):
        raise RuntimeError("WAF 403")

    monkeypatch.setattr(imf, "_get_json", fake_get_json)
    df = imf.fetch_imf(_SPEC, _COUNTRIES)

    assert df.empty
    assert list(df.columns) == imf._COLS
