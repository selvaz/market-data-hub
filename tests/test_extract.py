# -*- coding: utf-8 -*-
"""Analysis-ready extraction (extract.py) and the JSON tool layer (agent_tools)."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from market_data_hub import agent_tools, extract
from market_data_hub.db import connection as C
from market_data_hub.db.upsert import upsert


def _seed_prices(symbols=("SPY", "TLT"), n=40, start="2020-01-01"):
    """Insert n business-day rows of synthetic daily prices for each symbol."""
    dates = pd.bdate_range(start=start, periods=n)
    con = C.get_conn()
    rng = np.random.default_rng(0)
    for sym in symbols:
        px = 100.0 * np.cumprod(1 + rng.normal(0, 0.01, size=n))
        df = pd.DataFrame({
            "date": [d.date() for d in dates],
            "symbol": sym,
            "open": px, "high": px * 1.01, "low": px * 0.99,
            "close": px, "adj_close": px, "volume": 1000,
            "source": "yahoo", "is_live": False,
        })
        upsert(con, "prices_daily", df)
    con.close()


def test_extract_series_levels(tmp_db):
    _seed_prices()
    df, meta = extract.extract_series(["SPY", "TLT"], domain="prices")
    assert list(df.columns) == ["SPY", "TLT"]
    assert isinstance(df.index, pd.DatetimeIndex)
    assert meta["n_cols"] == 2
    assert meta["transform"] == "level"


def test_log_returns_use_view(tmp_db):
    _seed_prices()
    df, meta = extract.extract_series(["SPY"], domain="prices",
                                      transform="log_return")
    assert meta["used_returns_view"] is True
    # log returns are small numbers centered near zero
    assert df["SPY"].abs().mean() < 0.1


def test_extract_returns_weekly(tmp_db):
    _seed_prices(n=40)
    df, meta = extract.extract_returns(["SPY", "TLT"], frequency="W")
    assert meta["transform"] == "log_return"
    assert meta["frequency"] == "W"
    # weekly buckets over ~8 calendar weeks << 40 daily rows
    assert 4 <= meta["n_rows"] <= 12
    assert list(df.columns) == ["SPY", "TLT"]


def test_extract_returns_feeds_dataframe_shape(tmp_db):
    """The returned frame must be the (DatetimeIndex, numeric columns) shape
    that MSRegimeEngine.fit expects."""
    _seed_prices()
    df, _ = extract.extract_returns(["SPY", "TLT"], frequency="W")
    assert df.select_dtypes("number").shape[1] == 2
    assert df.index.is_monotonic_increasing


def test_tool_get_returns_is_valid_json(tmp_db):
    _seed_prices()
    out = agent_tools.tool_get_returns("SPY,TLT", frequency="W")
    payload = json.loads(out)
    assert "meta" in payload and "data" in payload
    assert payload["meta"]["domain"] == "prices"


def test_tool_list_symbols_json(tmp_db):
    out = agent_tools.tool_list_symbols(asset_class="EQUITY", area="Emerging Markets")
    payload = json.loads(out)
    assert payload["n"] >= 1
    assert all(s["asset_class"] == "EQUITY" for s in payload["symbols"])
