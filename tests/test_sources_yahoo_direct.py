# -*- coding: utf-8 -*-
"""
Contract test for sources/yahoo_direct.py — verifies _parse() understands
the real shape of Yahoo's chart v8 JSON. _parse() is already a pure
function (json dict -> DataFrame), so no HTTP mocking is needed at all.
"""
from __future__ import annotations

from market_data_hub.sources import yahoo_direct as yd


def _chart_json(timestamps, opens, highs, lows, closes, adjcloses, volumes):
    return {"chart": {"result": [{
        "timestamp": timestamps,
        "indicators": {
            "quote": [{"open": opens, "high": highs, "low": lows,
                       "close": closes, "volume": volumes}],
            "adjclose": [{"adjclose": adjcloses}],
        },
    }]}}


def test_parse_extracts_ohlcv_frame():
    j = _chart_json(
        timestamps=[1704067200, 1704153600],   # 2024-01-01, 2024-01-02 UTC
        opens=[100.0, 101.0], highs=[102.0, 103.0], lows=[99.0, 100.0],
        closes=[101.5, 102.5], adjcloses=[101.0, 102.0], volumes=[1000, 1500],
    )
    df = yd._parse("AAPL", j)

    assert list(df.columns) == yd._OUT_COLS
    assert len(df) == 2
    assert (df["symbol"] == "AAPL").all()
    assert df["close"].tolist() == [101.5, 102.5]
    assert df["adj_close"].tolist() == [101.0, 102.0]


def test_parse_falls_back_to_close_when_adjclose_missing():
    j = {"chart": {"result": [{
        "timestamp": [1704067200],
        "indicators": {
            "quote": [{"open": [100.0], "high": [102.0], "low": [99.0],
                      "close": [101.5], "volume": [1000]}],
            "adjclose": [],
        },
    }]}}
    df = yd._parse("AAPL", j)
    assert df.iloc[0]["adj_close"] == 101.5


def test_parse_drops_rows_with_no_price_data():
    j = _chart_json(
        timestamps=[1704067200, 1704153600],
        opens=[100.0, None], highs=[102.0, None], lows=[99.0, None],
        closes=[101.5, None], adjcloses=[101.0, None], volumes=[1000, None],
    )
    df = yd._parse("AAPL", j)
    assert len(df) == 1   # the all-null second row is dropped


def test_parse_missing_result_returns_empty_frame():
    df = yd._parse("DELISTED", {"chart": {"result": None}})
    assert df.empty
    assert list(df.columns) == yd._OUT_COLS


def test_parse_malformed_json_returns_empty_frame_not_raise():
    df = yd._parse("X", {"unexpected": "shape"})
    assert df.empty
    assert list(df.columns) == yd._OUT_COLS
