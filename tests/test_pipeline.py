# -*- coding: utf-8 -*-
"""End-to-end: seed a real schema'd DB, then exercise readers + analytics.

Validates that every column reference, view, INSERT arity and the dalio/classify
SQL stays consistent with db/schema.sql.
"""
from __future__ import annotations

import datetime as dt
import random

import pandas as pd

from market_data_hub.db.connection import get_conn
from market_data_hub.db.upsert import upsert
from market_data_hub import reader as R
from market_data_hub.coverage.report import (
    rebuild_coverage, rebuild_macro_panel_coverage)
from market_data_hub.dalio import run_dalio
from market_data_hub.classify import classify_countries

_IND = [
    ("gdp_growth_weo", "growth", 1, "A"), ("real_gdp_growth", "growth", 1, "A"),
    ("inflation_avg_weo", "liquidity", -1, "A"), ("inflation_cpi", "liquidity", -1, "A"),
    ("public_debt_gdp", "sovereign", -1, "A"), ("fiscal_balance_gdp", "sovereign", 1, "A"),
    ("labor_productivity_level", "growth", 1, "A"), ("bis_dsr_private", "debt_cycle", -1, "Q"),
    ("bis_credit_gap", "debt_cycle", -1, "Q"), ("bis_policy_rate", "liquidity", 0, "M"),
    ("fuel_exports_share", "geopolitical", 0, "A"), ("fuel_imports_share", "external", 0, "A"),
    ("exports_gdp", "external", 1, "A"), ("imports_gdp", "external", 0, "A"),
    ("natural_resource_rents_gdp", "geopolitical", 0, "A"),
    ("tourism_exports_share", "external", 0, "A"), ("remittances_gdp", "external", 0, "A"),
    ("metals_exports_share", "geopolitical", 0, "A"),
]


def _seed(con):
    random.seed(0)
    upsert(con, "prices_daily", pd.DataFrame([{
        "date": dt.date(2024, 1, 1) + dt.timedelta(days=i), "symbol": s,
        "open": 1, "high": 2, "low": 0.5, "close": 1.5, "adj_close": 1.4 + i * 0.01,
        "volume": 100, "source": "yahoo", "is_live": False}
        for s in ["SPY", "^VIX"] for i in range(40)]))
    upsert(con, "macro_series", pd.DataFrame([{
        "date": dt.date(2024, 1, 1) + dt.timedelta(days=i), "series_id": "DGS10",
        "value": 4.0 + i * 0.01, "series_name": "10Y", "unit": "pct",
        "frequency": "D", "source": "fred", "country": "US"} for i in range(30)]))
    upsert(con, "crypto_ohlcv", pd.DataFrame([{
        "ts": dt.datetime(2024, 1, 1) + dt.timedelta(hours=i), "symbol": "BTCUSDT",
        "timeframe": "1h", "open": 1, "high": 2, "low": 0.5, "close": 1.5,
        "volume": 10, "volume_quote": 10, "n_trades": 5, "taker_buy_base": 3,
        "is_closed": True} for i in range(48)]))
    upsert(con, "macro_panel", pd.DataFrame([{
        "date": dt.date(y, 12, 31), "country_iso3": c, "indicator_id": i,
        "value": random.uniform(1, 50), "indicator_name": i, "pillar": p,
        "orientation": o, "source": "imf" if "weo" in i else "wb",
        "provider_dataset": "X", "provider_code": "Y", "unit": "pct", "frequency": f}
        for c in ["USA", "ITA", "CHE"] for i, p, o, f in _IND
        for y in range(2015, 2031)]))


def test_full_pipeline(tmp_db):
    con = get_conn()
    _seed(con)
    cov_rows = rebuild_coverage(con, "testrun")
    panel_cov = rebuild_macro_panel_coverage(con, "testrun", n_countries_total=10)
    con.commit()
    con.close()

    # coverage_report now holds only per-symbol series: 2 yahoo + 1 fred + 1 crypto
    assert cov_rows == 4
    # macro_panel scored separately, cross-country: 18 indicators in the seed
    assert panel_cov == 18
    mpc = R.get_macro_panel_coverage()
    assert len(mpc) == 18
    assert (mpc["n_countries"] == 3).all()          # USA/ITA/CHE seeded
    assert (mpc["coverage_pct"] == 30.0).all()       # 3 / 10

    # readers (read-only handles, opened after the writer is closed)
    assert R.read_prices(["SPY", "^VIX"]).shape == (40, 2)
    assert R.read_macro("DGS10").shape[0] == 30
    assert R.read_crypto("BTCUSDT", "1h").shape[0] == 48
    assert R.read_macro_panel("public_debt_gdp", wide=True).shape == (16, 3)
    assert "coverage_score" in R.get_latest("SPY")
    assert R.get_coverage().shape == (4, 18)
    assert not R.get_stalled().empty   # view resolves

    # analytics
    ds = run_dalio()
    assert ds["countries"] == 3
    assert ds["signals"] == 3 * len(_IND)        # cross-country z per (country, indicator)
    assert ds["forecast_stale"] is False         # horizon 2030 > current year + 1

    cc = classify_countries()
    assert cc["countries"] == 64                 # from the real countries.yaml
