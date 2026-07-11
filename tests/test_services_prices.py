# -*- coding: utf-8 -*-
"""Tests for services.prices — the plan v3.1 Fase 2 vertical slice.

Acceptance (plan §7 Step 2): a listing not yet present is resolved, ingested
under lock, read back from the DB, and a repeated request neither duplicates
price rows nor spawns a second ingestion run for the same request hash.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from market_data_hub.db.connection import get_conn
from market_data_hub.services import prices as svc


def _fake_fetch(symbols, start, end):
    """Deterministic offline provider stub."""
    out = {}
    for sym in symbols:
        dates = pd.date_range(start, periods=5, freq="B")
        out[sym] = pd.DataFrame({
            "date": dates.date,
            "symbol": sym,
            "open": 100.0, "high": 101.0, "low": 99.0,
            "close": [100.0 + i for i in range(5)],
            "adj_close": [100.0 + i for i in range(5)],
            "volume": 1_000,
            "source": "test",
        })
    return out


def _failing_fetch(symbols, start, end):
    raise RuntimeError("provider down")


def test_ensure_resolves_registers_ingests_and_is_idempotent(tmp_db):
    # SPY is in the config universe but has no identity rows nor prices yet.
    res = svc.ensure_price_history("SPY", start="2024-01-01", end="2024-01-31",
                                   db_path=tmp_db, fetch=_fake_fetch)
    assert res["status"] == "completed"
    assert res["reused"] is False
    assert res["rows_added"] == 5
    assert res["listing_id"].startswith("lst_")

    con = get_conn(tmp_db, read_only=True)
    try:
        n_prices = con.execute(
            "SELECT COUNT(*) FROM prices_daily WHERE symbol = 'SPY'").fetchone()[0]
        n_runs = con.execute("SELECT COUNT(*) FROM ingestion_runs").fetchone()[0]
        n_jobs = con.execute("SELECT COUNT(*) FROM ingestion_jobs").fetchone()[0]
        n_listings = con.execute(
            "SELECT COUNT(*) FROM listings WHERE symbol = 'SPY'").fetchone()[0]
    finally:
        con.close()
    assert (n_prices, n_runs, n_jobs, n_listings) == (5, 1, 1, 1)

    # Same request again: job is reused, nothing re-ingested, no new run.
    res2 = svc.ensure_price_history("SPY", start="2024-01-01", end="2024-01-31",
                                    db_path=tmp_db, fetch=_failing_fetch)
    assert res2["reused"] is True
    assert res2["job_id"] == res["job_id"]
    con = get_conn(tmp_db, read_only=True)
    try:
        assert con.execute("SELECT COUNT(*) FROM ingestion_runs").fetchone()[0] == 1
        assert con.execute(
            "SELECT COUNT(*) FROM prices_daily WHERE symbol = 'SPY'"
        ).fetchone()[0] == 5
    finally:
        con.close()


def test_resolve_is_read_only_and_returns_candidates(tmp_db):
    cands = svc.resolve_instrument("SPY", db_path=tmp_db)
    assert len(cands) == 1
    assert cands[0]["registered"] is False       # config-only, nothing written
    con = get_conn(tmp_db, read_only=True)
    try:
        assert con.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 0
    finally:
        con.close()


def test_ambiguous_query_raises_with_candidates(tmp_db):
    # Register the same symbol on two venues -> ambiguity must NOT be guessed.
    con = get_conn(tmp_db)
    try:
        for venue in ("XNAS", "XMIL"):
            svc._register_listing(con, {
                "symbol": "ACME", "kind": "EQUITY", "name": f"ACME {venue}",
                "exchange": venue, "currency": None, "provider": venue.lower(),
            })
    finally:
        con.close()
    with pytest.raises(svc.AmbiguousInstrumentError) as ei:
        svc.ensure_price_history("ACME", db_path=tmp_db, fetch=_fake_fetch)
    assert len(ei.value.candidates) == 2
    # narrowing by exchange resolves it
    assert len(svc.resolve_instrument("ACME", exchange="XMIL",
                                      db_path=tmp_db)) == 1


def test_unknown_symbol_raises(tmp_db):
    with pytest.raises(svc.UnknownInstrumentError):
        svc.ensure_price_history("NOPE_XYZ", db_path=tmp_db, fetch=_fake_fetch)


def test_failed_run_is_recorded_and_job_retryable(tmp_db):
    with pytest.raises(RuntimeError):
        svc.ensure_price_history("SPY", start="2024-01-01", end="2024-01-31",
                                 db_path=tmp_db, fetch=_failing_fetch)
    job_rows = None
    con = get_conn(tmp_db, read_only=True)
    try:
        job_rows = con.execute(
            "SELECT status, error_msg FROM ingestion_jobs").fetchall()
        run_status = con.execute(
            "SELECT status FROM ingestion_runs").fetchone()[0]
    finally:
        con.close()
    assert job_rows[0][0] == "error" and "provider down" in job_rows[0][1]
    assert run_status == "error"

    # Retry with a working provider reuses the SAME job (same request hash).
    res = svc.ensure_price_history("SPY", start="2024-01-01", end="2024-01-31",
                                   db_path=tmp_db, fetch=_fake_fetch)
    assert res["status"] == "completed"
    con = get_conn(tmp_db, read_only=True)
    try:
        assert con.execute("SELECT COUNT(*) FROM ingestion_jobs").fetchone()[0] == 1
        assert con.execute(
            "SELECT status FROM ingestion_jobs").fetchone()[0] == "completed"
    finally:
        con.close()


def test_price_summary_has_no_raw_bars(tmp_db):
    svc.ensure_price_history("SPY", start="2024-01-01", end="2024-01-31",
                             db_path=tmp_db, fetch=_fake_fetch)
    summary = svc.get_price_summary("SPY", db_path=tmp_db)
    assert summary["n_obs"] == 5
    assert summary["last_adj_close"] == 104.0
    assert summary["total_return_pct"] == pytest.approx(4.0)
    # go/no-go: no per-bar data leaves the summary
    flat = json.dumps(summary, default=str)
    assert "open" not in flat and "volume" not in flat
    assert not any(isinstance(v, (list, dict)) for v in summary.values())


def test_job_status_reader(tmp_db):
    res = svc.ensure_price_history("SPY", start="2024-01-01", end="2024-01-31",
                                   db_path=tmp_db, fetch=_fake_fetch,
                                   requester="pytest")
    job = svc.get_job_status(res["job_id"], db_path=tmp_db)
    assert job["status"] == "completed"
    assert job["requester"] == "pytest"
    assert job["rows_written"] == 5
    assert svc.get_job_status("job_missing", db_path=tmp_db) is None


def test_resolve_degrades_gracefully_on_pre_v5_db(tmp_db):
    """Codex P1: a read-only connection never runs migrate(), so a DB file
    created before schema v5 (no identity tables) must not make resolve_
    instrument raise -- it should degrade to config-only candidates."""
    from market_data_hub.db.connection import get_conn as _get_conn

    # Simulate a pre-v5 DB: open a writer connection, then drop the identity
    # tables schema v5 added, mimicking a file created before that migration.
    con = _get_conn(tmp_db)
    try:
        for t in ("listings", "instruments", "identifier_aliases"):
            con.execute(f"DROP TABLE IF EXISTS {t}")
    finally:
        con.close()

    cands = svc.resolve_instrument("SPY", db_path=tmp_db)
    assert len(cands) == 1
    assert cands[0]["registered"] is False


def test_ensure_fetches_provider_symbol_not_warehouse_symbol(tmp_db):
    """Codex P2: when provider_symbol differs from the warehouse symbol, the
    fetch must use provider_symbol, and the upserted rows must carry the
    warehouse symbol back (listings.symbol), not the provider's."""
    captured = {}

    def fetch(symbols, start, end):
        captured["symbols"] = symbols
        return {symbols[0]: pd.DataFrame({
            "date": pd.date_range(start, periods=3, freq="B").date,
            "close": [1.0, 2.0, 3.0], "adj_close": [1.0, 2.0, 3.0],
        })}

    con = svc.get_conn(tmp_db)
    try:
        cand = svc._register_listing(con, {
            "symbol": "BRK.B", "kind": "EQUITY", "name": "Berkshire B",
            "exchange": None, "currency": None, "provider": "yahoo",
            "provider_symbol": "BRK-B",
        })
    finally:
        con.close()

    res = svc.ensure_price_history(cand["listing_id"], start="2024-01-01",
                                   end="2024-01-10", db_path=tmp_db, fetch=fetch)
    assert res["status"] == "completed"
    assert captured["symbols"] == ["BRK-B"]

    con = svc.get_conn(tmp_db, read_only=True)
    try:
        rows = con.execute(
            "SELECT DISTINCT symbol FROM prices_daily").fetchall()
    finally:
        con.close()
    assert rows == [("BRK.B",)]


def test_tool_layer_gating_and_shapes(tmp_db):
    from market_data_hub import agent_tools as at

    # write gate: allow_write defaults to False
    out = json.loads(at.tool_ensure_price_history("SPY", start="2024-01-01"))
    assert "allow_write" in out["error"]

    # resolve tool never writes
    out = json.loads(at.tool_resolve_instrument("SPY"))
    assert out["n"] == 1 and out["ambiguous"] is False

    # summary on empty hub points at the ensure capability
    out = json.loads(at.tool_get_price_summary("SPY"))
    assert out["n_obs"] == 0 and "ensure_price_history" in out["note"]
