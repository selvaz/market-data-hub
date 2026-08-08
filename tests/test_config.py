# -*- coding: utf-8 -*-
"""Config-catalog consistency tests."""
from __future__ import annotations

import csv
from pathlib import Path

import validate_config as V
from market_data_hub.config_loader import (
    get_yahoo_tickers, get_fred_series, get_macro_panel_specs, get_countries,
)


def test_live_config_is_valid():
    assert V.validate() == []


def test_catalog_counts():
    # Yahoo list (FRED IDs filtered out by get_yahoo_tickers)
    assert len(get_yahoo_tickers()) == 137
    assert len(get_fred_series()) == 77   # 45 + 32 cross-country 10Y yields (IRLTLT01*)
    assert len(get_macro_panel_specs()) == 83   # ...+imf_policy_rate +iip_net/ext_debt_nonres/fx_debt (IMF SDMX)
    assert len(get_countries()) == 64


def test_no_fred_ids_in_yahoo_universe():
    fred = {e["symbol"] for e in get_fred_series()}
    yahoo = {e["symbol"] for e in get_yahoo_tickers()}
    assert yahoo & fred == set()


def test_every_yahoo_ticker_is_classified():
    assert all(e.get("asset_class") for e in get_yahoo_tickers())


def test_lazyportfolio_phase_a_universe_is_in_daily_and_master_catalogs():
    """Every Phase-A ETF must be daily-downloadable and taxonomy-registered."""
    phase_a = {
        "QAI", "WTMF", "MNA", "IGOV", "ISHG", "PICB", "VCIT", "VCLT", "IHY",
        "BNO", "USL", "EFV", "EFG", "SCZ", "ECH", "EPHE", "THD",
        "EIDO", "EPU", "TUR", "ARGT",
    }
    daily_symbols = {entry["symbol"] for entry in get_yahoo_tickers()}
    master_path = Path(__file__).parents[1] / "tickers_master.csv"
    with master_path.open(encoding="utf-8-sig", newline="") as handle:
        master_symbols = {row["Ticker"] for row in csv.DictReader(handle)}

    assert phase_a <= daily_symbols
    assert phase_a <= master_symbols
    assert "HYXU" not in daily_symbols


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
