# -*- coding: utf-8 -*-
"""Tests for runner.refresh() — official non-invasive update of symbols already
in the warehouse (network mocked)."""
from __future__ import annotations

import pandas as pd

import market_data_hub.runner as runner
from market_data_hub.db.connection import get_conn
from market_data_hub.db.upsert import upsert


def _bars(symbol: str, dates, base: float = 100.0) -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.to_datetime(dates), "symbol": symbol,
        "open": base, "high": base + 1, "low": base - 1,
        "close": base, "adj_close": base, "volume": 1000,
    })


def test_refresh_updates_only_existing(tmp_path, monkeypatch) -> None:
    db = str(tmp_path / "m.duckdb")
    con = get_conn(db)
    init = _bars("TEST", ["2024-01-01", "2024-01-02"])
    init["source"] = "yahoo"; init["is_live"] = False
    upsert(con, "prices_daily", init)
    con.close()

    # fake network: new bars (yahoo frame has no source/is_live — run_yahoo adds them)
    monkeypatch.setattr(runner.yh, "yahoo_batch",
                        lambda tickers, start, end, **kw:
                        {"TEST": _bars("TEST", ["2024-01-03", "2024-01-04"], 101.0)})

    out = runner.refresh(symbols=["TEST"], db_path=db)
    assert out.get("TEST", 0) > 0

    con = get_conn(db)
    n = con.execute("SELECT count(*) FROM prices_daily WHERE symbol='TEST'").fetchone()[0]
    con.close()
    assert n == 4                                    # 2 original + 2 refreshed


def test_refresh_skips_symbols_not_in_warehouse(tmp_path, monkeypatch) -> None:
    db = str(tmp_path / "empty.duckdb")
    get_conn(db).close()                             # schema only, no rows

    called = {"n": 0}
    def _fake(*a, **k):
        called["n"] += 1; return {}
    monkeypatch.setattr(runner.yh, "yahoo_batch", _fake)

    out = runner.refresh(symbols=["NOPE"], db_path=db)
    assert out == {}
    assert called["n"] == 0                           # nothing existing → no download attempted
