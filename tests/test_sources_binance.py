# -*- coding: utf-8 -*-
"""
Contract test for sources/binance.py — verifies fetch_klines() understands
the real shape of a Binance kline page (list-of-lists, not objects) and its
pagination-by-close-time logic. The HTTP seam here is not extracted into a
dedicated helper like the other providers (it's an inline
`session.get(...)` inside the pagination loop), so this monkeypatches
requests.Session.get directly rather than a module-level function.
"""
from __future__ import annotations

import pandas as pd
import pytest
import requests

from market_data_hub.sources import binance


def _kline_row(open_ms, close_ms, o, h, l, c, v):  # noqa: E741 (l = low, matches Binance field order)
    return [open_ms, str(o), str(h), str(l), str(c), str(v), close_ms,
            "1000.0", 42, "500.0", "600.0", "0"]


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_fetch_klines_parses_single_page(monkeypatch):
    start = pd.Timestamp("2024-01-01", tz="UTC")
    end = pd.Timestamp("2024-01-02", tz="UTC")
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    page = [_kline_row(start_ms, end_ms - 1, 42000, 42500, 41800, 42300, 123.4)]

    calls = []

    def fake_get(self, url, params=None, timeout=None):
        calls.append(params)
        return _FakeResponse(page)

    monkeypatch.setattr(requests.Session, "get", fake_get)
    monkeypatch.setattr(binance.time, "sleep", lambda s: None)

    df = binance.fetch_klines("btcusdt", "1h", start, end, retries=1)

    assert list(df.columns) == binance._OUT_COLS
    assert len(df) == 1
    row = df.iloc[0]
    assert row["symbol"] == "BTCUSDT"
    assert row["open"] == 42000.0
    assert row["close"] == 42300.0
    assert row["volume"] == 123.4
    assert len(calls) == 1   # last_close+1 >= end_ts after one page -> loop stops


def test_fetch_klines_paginates_across_two_pages(monkeypatch):
    start = pd.Timestamp("2024-01-01", tz="UTC")
    end = pd.Timestamp("2024-01-03", tz="UTC")
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    mid_ms = start_ms + 3_600_000

    page1 = [_kline_row(start_ms, mid_ms - 1, 1, 2, 0.5, 1.5, 10)]
    page2 = [_kline_row(mid_ms, end_ms - 1, 1.5, 2.5, 1.0, 2.0, 20)]
    pages = [page1, page2]

    def fake_get(self, url, params=None, timeout=None):
        return _FakeResponse(pages.pop(0) if pages else [])

    monkeypatch.setattr(requests.Session, "get", fake_get)
    monkeypatch.setattr(binance.time, "sleep", lambda s: None)

    df = binance.fetch_klines("ethusdt", "1h", start, end, retries=1)

    assert len(df) == 2
    assert df["ts"].is_monotonic_increasing


def test_fetch_klines_unsupported_timeframe_raises():
    with pytest.raises(ValueError):
        binance.fetch_klines("BTCUSDT", "3m", "2024-01-01", "2024-01-02")
