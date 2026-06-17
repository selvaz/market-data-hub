# -*- coding: utf-8 -*-
"""Discovery / catalog layer (catalog.py).

These exercise the STATIC universe + semantic filters (no DB needed) and the
coverage join on a throwaway DB.
"""
from __future__ import annotations

import datetime as dt
import json

import pandas as pd

from market_data_hub import catalog
from market_data_hub.db import connection as C
from market_data_hub.db.upsert import upsert


def test_list_datasets_covers_five_domains():
    domains = {d["domain"] for d in catalog.list_datasets()}
    assert domains == {"prices", "macro", "macro_panel", "crypto", "factors"}


def test_equity_emerging_markets():
    df = catalog.list_symbols(asset_class="EQUITY", area="Emerging Markets",
                              with_coverage=False)
    syms = set(df["symbol"])
    assert {"IEMG", "EMXC", "VWO"} <= syms
    assert all(df["asset_class"] == "EQUITY")


def test_us_sector_filter():
    energy = catalog.list_symbols(asset_class="EQUITY", area="USA",
                                  sector="Energy", with_coverage=False)
    assert list(energy["symbol"]) == ["XLE"]

    all_sectors = catalog.list_symbols(asset_class="EQUITY", area="USA",
                                       sector="*", with_coverage=False)
    assert {"XLE", "XLF", "XLV", "VGT"} <= set(all_sectors["symbol"])


def test_area_alias_us_equals_usa():
    # VIX term-structure symbols are tagged area "US" in config; normalized to USA.
    df = catalog.list_symbols(area="USA", with_coverage=False)
    assert "^VIX" in set(df["symbol"])


def test_list_sectors_groups_symbols():
    sec = catalog.list_sectors(area="USA")
    row = sec[sec["sector"] == "Financials"].iloc[0]
    assert "XLF" in row["symbols"]


def test_list_macro_indicators_by_pillar():
    growth = catalog.list_macro_indicators(pillar="growth")
    assert "real_gdp_growth" in set(growth["indicator_id"])
    assert all(growth["pillar"] == "growth")


def test_search_across_domains():
    res = catalog.search("emerging")
    assert not res.empty
    assert "prices" in set(res["domain"])


def test_coverage_attached_when_db_populated(tmp_db):
    con = C.get_conn()
    con.execute(
        "INSERT INTO coverage_report (symbol, source, asset_class, first_date, "
        "last_date, obs_count, freq_detected, lag_days, stalled, coverage_score, "
        "status) VALUES ('SPY','yahoo','EQUITY','2010-01-04','2024-01-02',3500,"
        "'D',1,FALSE,98.5,'ok')")
    con.close()
    df = catalog.list_symbols(asset_class="EQUITY", area="USA")
    spy = df[df["symbol"] == "SPY"].iloc[0]
    assert float(spy["coverage_score"]) == 98.5
    assert spy["freq_detected"] == "D"


def test_describe_series_resolves_domain():
    card = catalog.describe_series("DGS10")
    assert card["domain"] == "macro"
    assert card["series_id"] == "DGS10"
