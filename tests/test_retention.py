# -*- coding: utf-8 -*-
"""Retention/pruning: dry_run counts without deleting; real prune deletes the
expected rows and keeps the rest; keep-N-per-key works for vintages. No network."""
from __future__ import annotations

import datetime as dt

import pandas as pd

from market_data_hub.db.connection import get_conn
from market_data_hub.db.retention import prune


def _seed_download_log(con):
    # 2 old rows (200 days ago) + 2 recent (1 day ago).
    old = dt.datetime.now() - dt.timedelta(days=200)
    recent = dt.datetime.now() - dt.timedelta(days=1)
    for i, (started, sym) in enumerate(
        [(old, "A"), (old, "B"), (recent, "C"), (recent, "D")]
    ):
        con.execute(
            "INSERT INTO download_log "
            "(run_id, started_at, ended_at, source, symbol, rows_added, "
            " rows_updated, status, error_msg, duration_sec) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [f"run{i}", started, started, "yahoo", sym, 0, 0, "ok", None, 0.0],
        )


def _seed_crypto(con):
    old = dt.datetime.now() - dt.timedelta(days=400)
    recent = dt.datetime.now() - dt.timedelta(days=2)
    rows = pd.DataFrame([
        {"ts": old, "symbol": "BTCUSDT", "timeframe": "1h", "open": 1, "high": 1,
         "low": 1, "close": 1, "volume": 1, "volume_quote": 1, "n_trades": 1,
         "taker_buy_base": 1, "is_closed": True, "updated_at": old},
        {"ts": recent, "symbol": "BTCUSDT", "timeframe": "1h", "open": 1, "high": 1,
         "low": 1, "close": 1, "volume": 1, "volume_quote": 1, "n_trades": 1,
         "taker_buy_base": 1, "is_closed": True, "updated_at": recent},
    ])
    con.register("_seed", rows)
    con.execute("INSERT INTO crypto_ohlcv SELECT * FROM _seed")
    con.unregister("_seed")


def _seed_vintage(con):
    # One logical key (date, series_id) with 3 distinct vintage_dates.
    for vd in ("2024-01-01", "2024-02-01", "2024-03-01"):
        con.execute(
            "INSERT INTO macro_series_vintage "
            "(date, series_id, value, vintage_date, source) VALUES "
            "(?, 'GDP', 100.0, ?, 'fred')",
            [dt.date(2023, 12, 31), vd],
        )


def test_prune_dry_run_counts_and_deletes_nothing(tmp_db):
    con = get_conn()
    _seed_download_log(con)
    _seed_crypto(con)
    _seed_vintage(con)

    report = prune(
        con,
        download_log_days=90,
        crypto_days=180,
        vintage_keep_per_key=1,
        dry_run=True,
    )
    assert report == {
        "download_log": 2,
        "crypto_ohlcv": 1,
        "macro_series_vintage": 2,
        "macro_panel_vintage": 0,
    }
    # nothing deleted
    assert con.execute("SELECT count(*) FROM download_log").fetchone()[0] == 4
    assert con.execute("SELECT count(*) FROM crypto_ohlcv").fetchone()[0] == 2
    assert con.execute(
        "SELECT count(*) FROM macro_series_vintage"
    ).fetchone()[0] == 3
    con.close()


def test_prune_deletes_expected_and_keeps_rest(tmp_db):
    con = get_conn()
    _seed_download_log(con)
    _seed_crypto(con)
    _seed_vintage(con)

    report = prune(
        con,
        download_log_days=90,
        crypto_days=180,
        vintage_keep_per_key=1,
    )
    assert report["download_log"] == 2
    assert report["crypto_ohlcv"] == 1
    assert report["macro_series_vintage"] == 2

    # download_log: only the 2 recent rows survive
    assert con.execute("SELECT count(*) FROM download_log").fetchone()[0] == 2
    survivors = {r[0] for r in con.execute(
        "SELECT symbol FROM download_log"
    ).fetchall()}
    assert survivors == {"C", "D"}

    # crypto: only the recent candle survives
    assert con.execute("SELECT count(*) FROM crypto_ohlcv").fetchone()[0] == 1

    # vintage: keep newest 1 vintage_date per key
    rows = con.execute(
        "SELECT vintage_date FROM macro_series_vintage"
    ).fetchall()
    assert len(rows) == 1
    assert str(rows[0][0]) == "2024-03-01"
    con.close()


def test_prune_skips_none_targets(tmp_db):
    con = get_conn()
    _seed_download_log(con)
    # only download_log targeted; others skipped (None)
    report = prune(con, download_log_days=90)
    assert set(report.keys()) == {"download_log"}
    assert report["download_log"] == 2
    con.close()


def test_prune_keep_n_per_key_for_vintages(tmp_db):
    con = get_conn()
    _seed_vintage(con)  # 3 vintage_dates for one key
    report = prune(con, download_log_days=None, vintage_keep_per_key=2)
    assert report["macro_series_vintage"] == 1  # 3 - keep 2
    kept = {str(r[0]) for r in con.execute(
        "SELECT vintage_date FROM macro_series_vintage"
    ).fetchall()}
    assert kept == {"2024-02-01", "2024-03-01"}
    con.close()
