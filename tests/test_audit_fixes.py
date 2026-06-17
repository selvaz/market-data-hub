# -*- coding: utf-8 -*-
"""Regression tests for the audit fixes.

Covers: adjusted-live ratio, read_prices field whitelist, coverage excluding
live rows, yahoo_workers plumbing, and the UTC epoch conversion.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from market_data_hub.sources import yahoo as yh
from market_data_hub.sources import yahoo_direct as yd
from market_data_hub.db.connection import get_conn
from market_data_hub.db.upsert import upsert
from market_data_hub.coverage.report import rebuild_coverage
from market_data_hub import reader as R


# --- P1 #3: adjusted live price must be multiplicative, not additive ----------
def test_adjusted_live_price_uses_ratio():
    # close=100, adj_close=50 (factor 0.5), live=102 -> 51 (ratio), not 52 (add)
    assert yh.adjusted_live_price(102, 50, 100) == 51.0


def test_adjusted_live_price_guards():
    assert yh.adjusted_live_price(None, 50, 100) is None
    assert yh.adjusted_live_price(102, 50, 0) is None       # no divide-by-zero
    assert yh.adjusted_live_price(102, float("nan"), 100) is None


# --- P2 #7: read_prices field is whitelisted (no SQL injection) ---------------
def test_read_prices_rejects_invalid_field(tmp_db):
    with pytest.raises(ValueError):
        R.read_prices("SPY",
                      field="adj_close FROM prices_daily; DROP TABLE prices_daily; --")


def test_read_prices_accepts_known_fields(tmp_db):
    # a known field must not raise (empty DB -> empty frame is fine)
    R.read_prices("SPY", field="close")


# --- P1 #4: coverage must ignore live rows --------------------------------
def test_coverage_ignores_live_rows(tmp_db):
    con = get_conn()
    stale = dt.date(2024, 1, 10)
    today = dt.date.today()
    # five settled EOD days ending on a stale date
    eod = pd.DataFrame([{
        "date": stale - dt.timedelta(days=i), "symbol": "SPY", "open": 1,
        "high": 2, "low": 0.5, "close": 1.5, "adj_close": 1.4, "volume": 100,
        "source": "yahoo", "is_live": False} for i in range(5)])
    # a fresh same-day live row that must NOT count toward freshness
    live = pd.DataFrame([{
        "date": today, "symbol": "SPY", "open": None, "high": None, "low": None,
        "close": 1.6, "adj_close": 1.6, "volume": None, "source": "yahoo",
        "is_live": True}])
    upsert(con, "prices_daily", eod)
    upsert(con, "prices_daily", live)
    rebuild_coverage(con, "testrun")
    row = con.execute(
        "SELECT last_date FROM coverage_report WHERE symbol='SPY'").fetchone()
    con.close()
    assert row is not None
    assert pd.Timestamp(row[0]).date() == stale     # live row ignored


# --- P2 #8: yahoo_workers config is honored -----------------------------------
def test_yahoo_batch_forwards_workers(monkeypatch):
    captured = {}

    def _fake(tickers, start, end, **kwargs):
        captured.update(kwargs)
        return {}

    monkeypatch.setattr(yd, "yahoo_batch", _fake)
    yh.yahoo_batch(["SPY"], "2020-01-01", "2020-02-01", workers=5)
    assert captured.get("workers") == 5


# --- data correctness: epoch is UTC, not local-timezone dependent -------------
def test_epoch_is_utc():
    # 2020-01-01T00:00:00Z == 1577836800
    assert yd._epoch("2020-01-01") == 1577836800
    # end=True is the exclusive +1 day boundary
    assert yd._epoch("2020-01-01", end=True) == 1577836800 + 86400
