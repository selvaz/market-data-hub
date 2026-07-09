# -*- coding: utf-8 -*-
"""Config-catalog consistency tests."""
from __future__ import annotations

import validate_config as V
from market_data_hub.config_loader import (
    get_yahoo_tickers, get_fred_series, get_macro_panel_specs, get_countries,
)


def test_live_config_is_valid():
    assert V.validate() == []


def test_catalog_counts():
    # Yahoo list (FRED IDs filtered out by get_yahoo_tickers)
    assert len(get_yahoo_tickers()) == 111
    assert len(get_fred_series()) == 77   # 45 + 32 cross-country 10Y yields (IRLTLT01*)
    assert len(get_macro_panel_specs()) == 80   # +reer/ie/rltir/NFC_LS +2 ECB +PVD_LS/HH_LS/net-debt/gini +imf_policy_rate
    assert len(get_countries()) == 64


def test_no_fred_ids_in_yahoo_universe():
    fred = {e["symbol"] for e in get_fred_series()}
    yahoo = {e["symbol"] for e in get_yahoo_tickers()}
    assert yahoo & fred == set()


def test_every_yahoo_ticker_is_classified():
    assert all(e.get("asset_class") for e in get_yahoo_tickers())


def test_validator_detects_a_fred_leak(monkeypatch):
    # Simulate a polluted Yahoo list and confirm the validator flags it.
    fred = V._y("macro_series.yaml")["fred"]
    leaked_id = fred[0]["symbol"]
    polluted = list(V._y("tickers.yaml")["yahoo"]) + [{"symbol": leaked_id}]
    orig = V._y

    def fake(name):
        if name == "tickers.yaml":
            return {"yahoo": polluted}
        return orig(name)

    monkeypatch.setattr(V, "_y", fake)
    errs = V.validate()
    assert any("FRED series IDs in the Yahoo list" in e for e in errs)
