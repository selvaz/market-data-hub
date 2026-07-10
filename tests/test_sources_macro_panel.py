# -*- coding: utf-8 -*-
"""
Contract tests for sources/macro_panel.py — the primary->fallback dispatcher
sitting above the individual provider modules. These monkeypatch each
sub-module's fetch_* function (dispatch/merge logic under test, not any
provider's own parsing, which is covered by the per-provider contract tests).
"""
from __future__ import annotations

import pandas as pd

from market_data_hub.sources import bis as bs
from market_data_hub.sources import ecb as ec
from market_data_hub.sources import imf as im
from market_data_hub.sources import imf_sdmx as ims
from market_data_hub.sources import macro_panel as mp
from market_data_hub.sources import worldbank as wb

_HTTP = {"timeout": 30, "max_retries": 1, "retry_base_sleep": 0.0}
_COUNTRIES = [{"iso3": "USA"}]


def _df(iso3="USA", value=1.0):
    return pd.DataFrame([{"country_iso3": iso3, "value": value}])


def _empty():
    return pd.DataFrame(columns=["country_iso3", "value"])


def test_fetch_one_routes_by_source_string(monkeypatch):
    calls = []
    monkeypatch.setattr(im, "fetch_imf", lambda *a, **k: calls.append("IMF") or _df())
    monkeypatch.setattr(ims, "fetch_imf_sdmx", lambda *a, **k: calls.append("IMF_SDMX") or _df())
    monkeypatch.setattr(bs, "fetch_bis", lambda *a, **k: calls.append("BIS") or _df())
    monkeypatch.setattr(ec, "fetch_ecb", lambda *a, **k: calls.append("ECB") or _df())
    monkeypatch.setattr(wb, "fetch_worldbank", lambda *a, **k: calls.append("WB") or _df())

    for source, expected in [("IMF", "IMF"), ("IMF_SDMX", "IMF_SDMX"), ("BIS", "BIS"),
                             ("ECB", "ECB"), ("WDI", "WB"), (None, "WB")]:
        mp._fetch_one(source, {}, _COUNTRIES, start_year=2020, http=_HTTP)
    assert calls == ["IMF", "IMF_SDMX", "BIS", "ECB", "WB", "WB"]


def test_fetch_indicator_falls_back_when_primary_empty(monkeypatch):
    monkeypatch.setattr(wb, "fetch_worldbank",
                        lambda spec, *a, **k: _df(value=99.0) if spec.get("code") == "FB" else _empty())
    spec = {"source": "WDI", "code": "PRIMARY", "id": "x", "name": "X",
            "pillar": "growth", "orientation": 1, "unit": "percent", "freq": "A",
            "fallback": {"source": "WDI", "code": "FB"}}

    df, source_used, status = mp.fetch_indicator(spec, _COUNTRIES, start_year=2020, http=_HTTP)

    assert status == "fallback"
    assert source_used == "WDI(fallback)"
    assert df["value"].tolist() == [99.0]


def test_fetch_indicator_fallback_inherits_spec_fields(monkeypatch):
    seen_specs = []

    def fake_worldbank(spec, *a, **k):
        seen_specs.append(spec)
        return _empty() if spec.get("code") == "PRIMARY" else _df()

    monkeypatch.setattr(wb, "fetch_worldbank", fake_worldbank)
    spec = {"source": "WDI", "code": "PRIMARY", "id": "public_debt_gdp",
            "name": "Debt", "pillar": "sovereign", "orientation": -1,
            "unit": "percent", "freq": "A", "fallback": {"source": "WDI", "code": "FB"}}

    mp.fetch_indicator(spec, _COUNTRIES, start_year=2020, http=_HTTP)

    fb_spec = seen_specs[1]
    assert fb_spec["code"] == "FB"
    assert fb_spec["id"] == "public_debt_gdp"   # inherited from the primary spec
    assert fb_spec["pillar"] == "sovereign"


def test_fetch_indicator_select_best_keeps_wider_coverage(monkeypatch):
    wide = pd.DataFrame([{"country_iso3": "USA", "value": 1.0},
                         {"country_iso3": "ITA", "value": 2.0}])
    narrow = pd.DataFrame([{"country_iso3": "USA", "value": 1.0}])

    def fake_worldbank(spec, *a, **k):
        return narrow if spec.get("code") == "PRIMARY" else wide

    monkeypatch.setattr(wb, "fetch_worldbank", fake_worldbank)
    spec = {"source": "WDI", "code": "PRIMARY", "id": "x", "name": "X",
            "pillar": "growth", "fallback": {"source": "WDI", "code": "FB"}}

    df, source_used, status = mp.fetch_indicator(spec, _COUNTRIES, start_year=2020,
                                                 http=_HTTP, select_best=True)

    assert status == "fallback"
    assert len(df) == 2   # the wider (2-country) fallback wins over the 1-country primary


def test_fetch_indicator_no_fallback_and_empty_primary(monkeypatch):
    monkeypatch.setattr(wb, "fetch_worldbank", lambda *a, **k: _empty())
    spec = {"source": "WDI", "code": "X", "id": "x", "name": "X", "pillar": "growth"}

    df, source_used, status = mp.fetch_indicator(spec, _COUNTRIES, start_year=2020, http=_HTTP)

    assert status == "empty"
    assert source_used == "WDI"
    assert df.empty
