# -*- coding: utf-8 -*-
"""DB-path resolution and upsert behavior."""
from __future__ import annotations

import datetime as dt

import pandas as pd

from market_data_hub.db import connection as C
from market_data_hub.db.upsert import upsert


def test_resolve_prefers_explicit_then_env(monkeypatch, tmp_path):
    explicit = str(tmp_path / "explicit.duckdb")
    monkeypatch.setenv("MARKET_DATA_DB", str(tmp_path / "env.duckdb"))
    assert C._resolve_db_path(explicit) == explicit
    assert C._resolve_db_path() == str(tmp_path / "env.duckdb")


def test_default_db_is_repo_local():
    assert C._default_db().endswith("market_data.duckdb")
    assert C._resolve_db_path("market_data.duckdb").endswith("market_data.duckdb")


def test_upsert_is_idempotent(tmp_db):
    con = C.get_conn()
    rows = pd.DataFrame([{
        "date": dt.date(2024, 1, 1), "symbol": "SPY", "open": 1, "high": 2,
        "low": 0.5, "close": 1.5, "adj_close": 1.4, "volume": 100,
        "source": "yahoo", "is_live": False,
    }])
    added, updated = upsert(con, "prices_daily", rows)
    assert (added, updated) == (1, 0)
    added2, updated2 = upsert(con, "prices_daily", rows)   # same PK
    assert (added2, updated2) == (0, 1)
    assert con.execute("SELECT count(*) FROM prices_daily").fetchone()[0] == 1
    con.close()


def test_upsert_defaults_is_live_false_when_column_absent(tmp_db):
    # A provider fetch (Yahoo daily bars) yields no is_live column. It must be
    # written as FALSE, not NULL — read_prices / extract_series filter
    # ``is_live = FALSE``, and in SQL ``NULL = FALSE`` is NULL, so NULL bars are
    # silently invisible and a freshly ingested ticker would never appear.
    con = C.get_conn()
    rows = pd.DataFrame([{
        "date": dt.date(2024, 1, 1), "symbol": "NEWTKR", "open": 1, "high": 2,
        "low": 0.5, "close": 1.5, "adj_close": 1.4, "volume": 100, "source": "yahoo",
    }])  # deliberately NO is_live column
    upsert(con, "prices_daily", rows)
    live = con.execute("SELECT is_live FROM prices_daily WHERE symbol = 'NEWTKR'").fetchall()
    assert live and all(v[0] is False for v in live), live
    con.close()
    # end-to-end: the reader (which filters is_live = FALSE) now sees the bar
    from market_data_hub.reader import read_prices
    px = read_prices("NEWTKR", field="adj_close")
    assert not px.empty and "NEWTKR" in px.columns


def test_upsert_counts_are_truthful_under_intra_batch_pk_duplicates(tmp_db):
    # A source batch can repeat a primary key (e.g. the BIS euro-aggregate
    # broadcast used to duplicate (date, country) pairs): INSERT OR REPLACE
    # collapses those to one stored row, so the reported (added, updated)
    # must count distinct keys, not raw batch rows — this used to report a
    # constant phantom rows_added on every run.
    con = C.get_conn()
    base = {"open": 1, "high": 2, "low": 0.5, "close": 1.5, "adj_close": 1.4,
            "volume": 100, "source": "yahoo", "is_live": False}
    batch = pd.DataFrame([
        {"date": dt.date(2024, 1, 1), "symbol": "SPY", **base},
        {"date": dt.date(2024, 1, 1), "symbol": "SPY", **base, "close": 9.9},  # dup PK
        {"date": dt.date(2024, 1, 2), "symbol": "SPY", **base},
    ])
    added, updated = upsert(con, "prices_daily", batch)
    assert (added, updated) == (2, 0)   # 2 distinct keys, not 3 raw rows
    assert con.execute("SELECT count(*) FROM prices_daily").fetchone()[0] == 2
    # keep='last' matches INSERT OR REPLACE semantics: the later row wins
    stored = con.execute("SELECT close FROM prices_daily WHERE date = DATE '2024-01-01'").fetchone()[0]
    assert stored == 9.9
    added2, updated2 = upsert(con, "prices_daily", batch)   # replay: all existing
    assert (added2, updated2) == (0, 2)
    con.close()


