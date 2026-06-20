# -*- coding: utf-8 -*-
"""read_instrument routing: a namespaced InstrumentId dispatches to the right
read_* function with the right keys. No DuckDB needed — the read_* calls are
captured via monkeypatch."""
from __future__ import annotations

import pytest

from market_data_hub import reader
from market_data_hub.lazydatacore import InstrumentId, NotResolvableError

_READERS = ("read_prices", "read_crypto", "read_macro", "read_macro_panel",
            "read_factors")


@pytest.fixture
def captured(monkeypatch):
    """Replace every read_* with a recorder; return the last call's record."""
    rec: dict = {}

    def make(name):
        def fake(*args, **kwargs):
            rec.clear()
            rec.update(name=name, args=args, kwargs=kwargs)
            return f"{name}-result"
        return fake

    for name in _READERS:
        monkeypatch.setattr(reader, name, make(name))
    return rec


def test_routes_price(captured):
    out = reader.read_instrument("price:AAPL", start="2024-01-01")
    assert out == "read_prices-result"
    assert captured["name"] == "read_prices"
    assert captured["args"][0] == "AAPL"
    assert captured["kwargs"]["start"] == "2024-01-01"
    assert captured["kwargs"]["wide"] is False


def test_ticker_alias_routes_to_prices(captured):
    reader.read_instrument("ticker:AAPL")
    assert captured["name"] == "read_prices"
    assert captured["args"][0] == "AAPL"


def test_routes_crypto_with_timeframe(captured):
    reader.read_instrument("crypto:BTCUSDT@4h")
    assert captured["name"] == "read_crypto"
    assert captured["args"][0] == "BTCUSDT"
    assert captured["kwargs"]["timeframe"] == "4h"


def test_crypto_default_timeframe(captured):
    reader.read_instrument("crypto:BTCUSDT")
    assert captured["kwargs"]["timeframe"] == "1h"


def test_routes_macro_with_asof(captured):
    reader.read_instrument("macro:FEDFUNDS", asof="2020-01-01")
    assert captured["name"] == "read_macro"
    assert captured["args"][0] == "FEDFUNDS"
    assert captured["kwargs"]["asof"] == "2020-01-01"


def test_routes_macro_panel_pair(captured):
    reader.read_instrument("macro_panel:USA/real_gdp_growth")
    assert captured["name"] == "read_macro_panel"
    assert captured["args"][0] == "real_gdp_growth"
    assert captured["kwargs"]["countries"] == ["USA"]


def test_routes_factor_pair(captured):
    reader.read_instrument("factor:FF5_daily/MKT")
    assert captured["name"] == "read_factors"
    assert captured["args"][0] == "MKT"
    assert captured["kwargs"]["factor_set"] == "FF5_daily"


def test_accepts_instrument_id_object(captured):
    reader.read_instrument(InstrumentId.parse("price:SPY"))
    assert captured["name"] == "read_prices"
    assert captured["args"][0] == "SPY"


def test_reference_identity_not_resolvable(captured):
    with pytest.raises(NotResolvableError):
        reader.read_instrument("cik:0000320193")
