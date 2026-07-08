# -*- coding: utf-8 -*-
"""DB-path resolution and upsert behavior."""
from __future__ import annotations

import datetime as dt

import pandas as pd

from market_data_hub.db import connection as C
from market_data_hub.db.upsert import upsert


def test_resolve_prefers_explicit_then_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MARKET_DATA_DB", str(tmp_path / "env.duckdb"))
    assert C._resolve_db_path("/explicit/x.duckdb") == "/explicit/x.duckdb"
    assert C._resolve_db_path() == str(tmp_path / "env.duckdb")


def test_default_db_is_repo_local():
    assert C._default_db() == "market_data.duckdb"


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


