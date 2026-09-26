"""Regression coverage for explicit empty-duplicate resolution and Yahoo runs."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from market_data_hub.db.connection import get_conn
from market_data_hub.db.identity import (
    duplicate_active_listings,
    ensure_listing,
    resolve_empty_duplicate_listing,
)
from market_data_hub.db.upsert import upsert
from market_data_hub.services.prices import register_listing, resolve_instrument


def _make_bci_duplicate(db_path: str) -> tuple[str, str]:
    con = get_conn(db_path)
    try:
        keep_id = ensure_listing(
            con, "BCI", kind="ETF",
            name="abrdn Bloomberg All Commodity Strategy K-1 Free ETF",
            exchange="NYSE Arca", currency="USD")
    finally:
        con.close()

    duplicate = register_listing(
        "BCI", exchange="NYSE", currency="USD", kind="ETF",
        name="abrdn Bloomberg All Commodity Strategy K-1 Free ETF",
        db_path=db_path)
    return keep_id, duplicate["listing_id"]


def test_empty_duplicate_resolution_is_explicit_idempotent_and_preserves_alias(tmp_db):
    keep_id, duplicate_id = _make_bci_duplicate(tmp_db)
    con = get_conn(tmp_db)
    try:
        inventory = duplicate_active_listings(con, symbol="BCI")
        assert {row["listing_id"] for row in inventory} == {keep_id, duplicate_id}
        assert all(row["price_rows"] == 0 for row in inventory)

        first = resolve_empty_duplicate_listing(
            con, symbol="BCI", keep_listing_id=keep_id,
            deactivate_listing_id=duplicate_id)
        second = resolve_empty_duplicate_listing(
            con, symbol="BCI", keep_listing_id=keep_id,
            deactivate_listing_id=duplicate_id)

        rows = con.execute(
            "SELECT listing_id, active_to FROM listings "
            "WHERE symbol = 'BCI' ORDER BY listing_id").fetchall()
        active_aliases = con.execute(
            "SELECT target_id FROM identifier_aliases "
            "WHERE value = 'BCI' AND target_type = 'listing' "
            "AND valid_to IS NULL").fetchall()
        remaining_duplicates = duplicate_active_listings(con, symbol="BCI")
    finally:
        con.close()

    assert first["changed"] is True
    assert second["changed"] is False
    assert {row[0]: row[1] for row in rows}[keep_id] is None
    assert {row[0]: row[1] for row in rows}[duplicate_id] is not None
    assert active_aliases == [(keep_id,)]
    assert remaining_duplicates == []


def test_resolution_reactivates_expired_keeper_alias_before_coalescing(tmp_db):
    keep_id, duplicate_id = _make_bci_duplicate(tmp_db)
    alias_value = "BCI-FIGI-ALIAS"
    con = get_conn(tmp_db)
    try:
        con.execute("""
            INSERT INTO identifier_aliases
                (namespace, value, target_type, target_id, valid_from, valid_to)
            VALUES ('figi', ?, 'listing', ?, DATE '2018-01-01', DATE '2020-12-31'),
                   ('figi', ?, 'listing', ?, DATE '2021-01-01', NULL)
        """, [alias_value, keep_id, alias_value, duplicate_id])

        result = resolve_empty_duplicate_listing(
            con, symbol="BCI", keep_listing_id=keep_id,
            deactivate_listing_id=duplicate_id)
        aliases = con.execute(
            "SELECT target_id, valid_from, valid_to FROM identifier_aliases "
            "WHERE namespace = 'figi' AND value = ? AND target_type = 'listing'",
            [alias_value]).fetchall()
    finally:
        con.close()

    candidates = resolve_instrument(alias_value, db_path=tmp_db)
    assert result["changed"] is True
    assert aliases == [(keep_id, dt.date(2018, 1, 1), None)]
    assert [candidate["listing_id"] for candidate in candidates] == [keep_id]


def test_resolution_refuses_to_retire_a_duplicate_with_price_rows(tmp_db):
    keep_id, duplicate_id = _make_bci_duplicate(tmp_db)
    con = get_conn(tmp_db)
    try:
        upsert(con, "prices_daily", pd.DataFrame([{
            "date": dt.date(2025, 1, 2),
            "listing_id": duplicate_id,
            "symbol": "BCI",
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 1.0,
            "adj_close": 1.0,
            "volume": 1,
            "source": "test",
            "is_live": False,
        }]))
        with pytest.raises(ValueError, match="has 1 price rows"):
            resolve_empty_duplicate_listing(
                con, symbol="BCI", keep_listing_id=keep_id,
                deactivate_listing_id=duplicate_id)
        active_count = con.execute(
            "SELECT COUNT(*) FROM listings WHERE symbol = 'BCI' "
            "AND active_to IS NULL").fetchone()[0]
    finally:
        con.close()
    assert active_count == 2


def test_duplicate_listing_dry_run_is_read_only(tmp_db, capsys):
    from scripts.resolve_duplicate_listings import main

    keep_id, duplicate_id = _make_bci_duplicate(tmp_db)
    assert main(["--db", tmp_db, "--symbol", "BCI"]) == 0
    output = capsys.readouterr().out
    assert "DRY RUN (read_only=True)" in output
    assert keep_id in output
    assert duplicate_id in output

    con = get_conn(tmp_db, read_only=True)
    try:
        active_count = con.execute(
            "SELECT COUNT(*) FROM listings WHERE symbol = 'BCI' "
            "AND active_to IS NULL").fetchone()[0]
    finally:
        con.close()
    assert active_count == 2


def test_duplicate_listing_dry_run_rejects_missing_database(tmp_path, capsys):
    from scripts.resolve_duplicate_listings import main

    missing_db = tmp_path / "typo.duckdb"
    with pytest.raises(SystemExit) as exc_info:
        main(["--db", str(missing_db)])

    assert exc_info.value.code == 2
    assert f"database file does not exist: {missing_db}" in capsys.readouterr().err
    assert not missing_db.exists()


def test_yahoo_run_reports_ambiguous_symbol_and_continues(tmp_db, monkeypatch):
    from market_data_hub import runner

    keep_id, duplicate_id = _make_bci_duplicate(tmp_db)
    tickers = [
        {"symbol": "BCI", "asset_class": "COMMODITIES",
         "name": "abrdn Bloomberg All Commodity Strategy K-1 Free ETF"},
        {"symbol": "SPY", "asset_class": "EQUITY", "name": "SPDR S&P 500 ETF"},
    ]
    monkeypatch.setattr(runner, "get_yahoo_tickers", lambda: tickers)
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)

    def fake_yahoo_batch(symbols, start, end, **kwargs):
        assert symbols == ["BCI", "SPY"]
        return {
            symbol: pd.DataFrame([{
                "date": dt.date(2025, 1, 2), "symbol": symbol,
                "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0,
                "adj_close": 1.0, "volume": 100,
            }])
            for symbol in symbols
        }

    monkeypatch.setattr(runner.yh, "yahoo_batch", fake_yahoo_batch)
    cfg = {
        "incremental": {"tail_refresh_days": 5},
        "backfill_start": {"yahoo": "2017-01-01"},
        "parallelism": {"yahoo_workers": 1, "yahoo_batch_sleep": 0},
    }
    con = get_conn(tmp_db)
    try:
        runner.run_yahoo(con, cfg, "test-run", end="2025-01-03")
        outcomes = con.execute(
            "SELECT symbol, status, error_msg FROM download_log "
            "WHERE run_id = 'test-run' ORDER BY symbol").fetchall()
        stored_symbols = {row[0] for row in con.execute(
            "SELECT DISTINCT symbol FROM prices_daily").fetchall()}
    finally:
        con.close()

    assert keep_id != duplicate_id
    assert len(outcomes) == 2
    by_symbol = {row[0]: row[1:] for row in outcomes}
    assert by_symbol["BCI"][0] == "error"
    assert "ambiguous listing" in by_symbol["BCI"][1]
    assert by_symbol["SPY"] == ("ok", None)
    assert "BCI" not in stored_symbols
    assert "SPY" in stored_symbols
