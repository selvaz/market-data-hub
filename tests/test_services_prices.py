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


def test_dual_listings_same_ticker_are_isolated(tmp_db):
    """Audit CA-01 acceptance — the exact reproduction from the audit: two
    ACTIVE listings sharing the ticker ACME (XNAS/XMIL), different provider
    symbols and prices. Both ingested; neither overwrites the other; each
    summary reads its own series."""
    con = get_conn(tmp_db)
    try:
        xnas = svc._register_listing(con, {
            "symbol": "ACME", "kind": "EQUITY", "name": "ACME US",
            "exchange": "XNAS", "currency": "USD", "provider": "yahoo",
            "provider_symbol": "ACME"})
        xmil = svc._register_listing(con, {
            "symbol": "ACME", "kind": "EQUITY", "name": "ACME IT",
            "exchange": "XMIL", "currency": "EUR", "provider": "yahoo_it",
            "provider_symbol": "ACME.MI"})
    finally:
        con.close()

    def fetch_for(px0):
        def fetch(symbols, start, end):
            dates = pd.date_range(start, periods=2, freq="B")
            return {symbols[0]: pd.DataFrame({
                "date": dates.date, "close": [px0, px0 + 1.0],
                "adj_close": [px0, px0 + 1.0],
            })}
        return fetch

    svc.ensure_price_history(xnas["listing_id"], start="2024-01-01",
                             end="2024-01-05", db_path=tmp_db,
                             fetch=fetch_for(100.0))
    svc.ensure_price_history(xmil["listing_id"], start="2024-01-01",
                             end="2024-01-05", db_path=tmp_db,
                             fetch=fetch_for(90.0))

    us = svc.get_price_summary(xnas["listing_id"], db_path=tmp_db)
    it = svc.get_price_summary(xmil["listing_id"], db_path=tmp_db)
    assert us["last_adj_close"] == 101.0
    assert it["last_adj_close"] == 91.0

    con = get_conn(tmp_db, read_only=True)
    try:
        n = con.execute("SELECT COUNT(*) FROM prices_daily "
                        "WHERE symbol = 'ACME'").fetchone()[0]
        per_listing = con.execute(
            "SELECT listing_id, COUNT(*) FROM prices_daily "
            "WHERE symbol = 'ACME' GROUP BY listing_id").fetchall()
    finally:
        con.close()
    assert n == 4                       # 2 rows per listing, none overwritten
    assert {r[1] for r in per_listing} == {2}

    # the symbol-based auto-attach path must REFUSE the ambiguous ticker
    from market_data_hub.db.identity import AmbiguousSymbolError
    from market_data_hub.db.upsert import upsert as _upsert
    con = get_conn(tmp_db)
    try:
        with pytest.raises(AmbiguousSymbolError, match="ACME"):
            _upsert(con, "prices_daily", pd.DataFrame({
                "date": [pd.Timestamp("2024-02-01").date()],
                "symbol": ["ACME"], "adj_close": [1.0]}))
    finally:
        con.close()


def test_v7_migration_rebuilds_prices_keyed_by_listing(tmp_path, monkeypatch):
    """A v6-shaped DB (prices keyed by symbol, no listing_id) is rebuilt on
    open: rows preserved, listing auto-registered, new key in place."""
    import duckdb as _duck

    from market_data_hub.db import connection as dbc

    db = str(tmp_path / "old.duckdb")
    monkeypatch.setenv("MARKET_DATA_DB", db)
    raw = _duck.connect(db)
    raw.execute("""
        CREATE TABLE prices_daily (
            date DATE NOT NULL, symbol VARCHAR NOT NULL,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
            adj_close DOUBLE, volume BIGINT, source VARCHAR,
            is_live BOOLEAN DEFAULT FALSE, updated_at TIMESTAMP,
            PRIMARY KEY (date, symbol))""")
    raw.execute("CREATE TABLE schema_meta (key VARCHAR PRIMARY KEY, value VARCHAR)")
    raw.execute("INSERT INTO schema_meta VALUES ('schema_version', '6')")
    raw.execute("INSERT INTO prices_daily (date, symbol, adj_close) VALUES "
                "('2024-01-02', 'OLDCO', 10.0), ('2024-01-03', 'OLDCO', 11.0)")
    raw.close()

    con = dbc.get_conn(db)   # triggers migrate()
    try:
        assert dbc.get_schema_version(con) == dbc.SCHEMA_VERSION
        rows = con.execute("SELECT date, listing_id, symbol, adj_close "
                           "FROM prices_daily ORDER BY date").fetchall()
        assert len(rows) == 2
        assert all(r[1].startswith("lst_") for r in rows)
        assert {r[3] for r in rows} == {10.0, 11.0}
        lst = con.execute("SELECT COUNT(*) FROM listings "
                          "WHERE symbol = 'OLDCO'").fetchone()[0]
        assert lst == 1
    finally:
        con.close()


def test_register_listing_enables_arbitrary_single_name(tmp_db):
    """Audit CA-05: an unknown symbol is rejected until registered with
    explicit identity; after register_listing the normal ensure job works."""
    with pytest.raises(svc.UnknownInstrumentError, match="register_listing"):
        svc.ensure_price_history("NEWCO", db_path=tmp_db, fetch=_fake_fetch)

    # identity is never guessed: exchange/currency are mandatory
    with pytest.raises(ValueError, match="exchange and currency"):
        svc.register_listing("NEWCO", exchange="", currency="",
                             db_path=tmp_db)

    reg = svc.register_listing("NEWCO", exchange="XNAS", currency="USD",
                               name="New Co", db_path=tmp_db, verify=False)
    assert reg["created"] is True and reg["listing_id"].startswith("lst_")

    # idempotent: same (symbol, provider, exchange) -> same listing
    again = svc.register_listing("NEWCO", exchange="XNAS", currency="USD",
                                 db_path=tmp_db, verify=False)
    assert again["created"] is False
    assert again["listing_id"] == reg["listing_id"]

    # second venue -> its OWN listing, not an overwrite
    milan = svc.register_listing("NEWCO", exchange="XMIL", currency="EUR",
                                 db_path=tmp_db, verify=False)
    assert milan["created"] is True
    assert milan["listing_id"] != reg["listing_id"]

    # bare symbol is now ambiguous by design; the listing_id path works
    with pytest.raises(svc.AmbiguousInstrumentError):
        svc.ensure_price_history("NEWCO", db_path=tmp_db, fetch=_fake_fetch)
    res = svc.ensure_price_history(reg["listing_id"], start="2024-01-01",
                                   end="2024-01-31", db_path=tmp_db,
                                   fetch=_fake_fetch)
    assert res["status"] == "completed" and res["rows_added"] == 5
    assert svc.get_price_summary(reg["listing_id"],
                                 db_path=tmp_db)["n_obs"] == 5


def test_register_unknown_symbol_writes_nothing(tmp_db):
    def empty_fetch(symbols, start, end):
        return {symbols[0]: pd.DataFrame()}

    def unknown_sec(symbol):
        raise ValueError(symbol)

    result = svc.register_listing(
        "ZZQXW", exchange="NASDAQ", currency="USD", db_path=tmp_db,
        verification_fetch=empty_fetch, sec_resolve=unknown_sec)

    assert result["registered"] is False
    assert result["reason"] == "unknown_symbol"
    assert [item["source"] for item in result["checked"]] == [
        "price_provider", "sec_ticker_map"]
    con = get_conn(tmp_db, read_only=True)
    try:
        counts = [con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in ("listings", "instruments", "identifier_aliases",
                                "ingestion_jobs", "ingestion_runs")]
    finally:
        con.close()
    assert counts == [0, 0, 0, 0, 0]


def test_register_known_symbol_uses_provider_metadata_and_records_hints(tmp_db):
    def known_fetch(symbols, start, end):
        frame = pd.DataFrame({"close": [10.0]})
        frame.attrs.update(name="Authoritative SA", exchange="XPAR",
                           currency="EUR")
        return {symbols[0]: frame}

    result = svc.register_listing(
        "KNOWN", exchange="CALLER", currency="EUR", name="LLM Name",
        db_path=tmp_db, verification_fetch=known_fetch)

    assert result["registered"] is True
    assert result["exchange"] == "XPAR"
    con = get_conn(tmp_db, read_only=True)
    try:
        stored = con.execute("""
            SELECT i.name, l.exchange, l.currency
            FROM listings l JOIN instruments i USING (instrument_id)
        """).fetchone()
        hints = con.execute("""
            SELECT name_hint, exchange_hint, currency_hint
            FROM listing_registration_hints
        """).fetchone()
    finally:
        con.close()
    assert stored == ("Authoritative SA", "XPAR", "EUR")
    assert hints == ("LLM Name", "CALLER", "EUR")


def test_prune_dry_run_lists_empty_offline_listing(tmp_db):
    listing = svc.register_listing(
        "EMPTY", exchange="XNAS", currency="USD", db_path=tmp_db,
        verify=False)

    result = svc.prune_empty_listings(
        dry_run=True, older_than_minutes=-1, db_path=tmp_db)

    assert result["count"] == 1
    assert result["listings"][0]["listing_id"] == listing["listing_id"]
    assert svc.resolve_instrument(listing["listing_id"], db_path=tmp_db)


def test_default_fetch_carries_yahoo_metadata(tmp_db, monkeypatch):
    from market_data_hub.sources import yahoo_direct

    now = pd.Timestamp('2024-01-10', tz='UTC')
    monkeypatch.setattr(svc, '_now', lambda: now.to_pydatetime())

    class Response:
        status_code = 200

        def json(self):
            return {"chart": {"result": [{
                "timestamp": [int(now.timestamp())],
                "meta": {"longName": "Provider SA", "exchangeName": "PAR",
                         "currency": "EUR"},
                "indicators": {"quote": [{"open": [1], "high": [1],
                                            "low": [1], "close": [1], "volume": [1]}]},
            }]}}

    class Session:
        def get(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(yahoo_direct, "_session", lambda: Session())
    monkeypatch.setattr(yahoo_direct, "_creq", object())
    result = svc.register_listing("TEST.PA", exchange="caller", currency="gbp",
                                  name="Caller", db_path=tmp_db)
    assert result["exchange"] == "XPAR" and result["currency"] == "EUR"
    con = get_conn(tmp_db, read_only=True)
    try:
        assert con.execute("""
            SELECT name, exchange, currency FROM listings JOIN instruments USING (instrument_id)
        """).fetchone() == ("Provider SA", "XPAR", "EUR")
        assert con.execute("""
            SELECT name_hint, exchange_hint, currency_hint FROM listing_registration_hints
        """).fetchone() == ("Caller", "CALLER", "GBP")
    finally:
        con.close()


@pytest.mark.parametrize(("stored_exchange", "provider_exchange"), [
    ("XNAS", "NMS"),
    ("NMS", "NMS"),
    ("XNAS", "UNMAPPED"),
])
def test_verified_registration_reuses_existing_yahoo_venue(
        tmp_db, stored_exchange, provider_exchange):
    original = svc.register_listing("AAPL", exchange=stored_exchange, currency="USD",
                                    db_path=tmp_db, verify=False)

    def provider_fetch(symbols, start, end):
        frame = pd.DataFrame({"close": [10.0]})
        frame.attrs.update(exchange=provider_exchange, currency="USD")
        return {symbols[0]: frame}

    repeated = svc.register_listing(
        "AAPL", exchange="XNAS", currency="USD", db_path=tmp_db,
        verification_fetch=provider_fetch, sec_resolve=lambda symbol: {})

    assert repeated["created"] is False
    assert repeated["listing_id"] == original["listing_id"]
    assert repeated["exchange"] == stored_exchange
    assert [row["listing_id"] for row in svc.resolve_instrument("AAPL", db_path=tmp_db)] == [
        original["listing_id"]
    ]


def test_numeric_cik_does_not_confirm_ticker(tmp_db):
    result = svc.register_listing(
        "9999999999", exchange="XNAS", currency="USD", db_path=tmp_db,
        verification_fetch=lambda symbols, start, end: {})
    assert result["registered"] is False
    assert not any(item["confirmed"] for item in result["checked"])
    con = get_conn(tmp_db)
    try:
        for table in ("listings", "instruments", "identifier_aliases",
                      "ingestion_jobs", "ingestion_runs", "listing_registration_hints"):
            assert con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    finally:
        con.close()


def test_sec_ticker_match_can_confirm_without_prices(tmp_db):
    result = svc.register_listing(
        "KNOWN", exchange="XNAS", currency="USD", db_path=tmp_db,
        verification_fetch=lambda symbols, start, end: {},
        sec_resolve=lambda symbol: {"cik": "0000000001", "ticker": symbol,
                                    "name": "Known Corporation"})
    assert result["registered"] is True
    assert result["checked"][-1] == {"source": "sec_ticker_map", "confirmed": True}


def test_repeat_registration_returns_stored_currency_and_records_hints(tmp_db):
    svc.register_listing("REPEAT", exchange="xnas", currency="USD",
                         db_path=tmp_db, verify=False)
    result = svc.register_listing("REPEAT", exchange="XNAS", currency="EUR",
                                  name="New hint", db_path=tmp_db, verify=False)
    svc.register_listing("REPEAT", exchange="XNAS", currency="EUR",
                         name="New hint", db_path=tmp_db, verify=False)
    assert result["created"] is False and result["currency"] == "USD"
    con = get_conn(tmp_db, read_only=True)
    try:
        assert con.execute("SELECT count(*) FROM listing_registration_hints").fetchone()[0] == 2
    finally:
        con.close()


def test_prune_removes_only_empty_listing_and_related_ledger(tmp_db):
    empty = svc.register_listing("EMPTY", exchange="XNAS", currency="USD",
                                 db_path=tmp_db, verify=False)
    active = svc.register_listing("ACTIVE", exchange="XNAS", currency="USD",
                                  db_path=tmp_db, verify=False)
    svc.ensure_price_history(empty["listing_id"], db_path=tmp_db,
                             fetch=lambda symbols, start, end: {})
    con = get_conn(tmp_db)
    try:
        con.execute("INSERT INTO instruments (instrument_id, kind) VALUES ('unrelated', 'OTHER')")
        req = json.dumps({"listing_id": active["listing_id"]})
        con.execute("INSERT INTO ingestion_runs (run_id, kind, input_json, status) "
                    "VALUES ('active_run', 'price_history', ?, 'running')", [req])
    finally:
        con.close()
    result = svc.prune_empty_listings(dry_run=False, older_than_minutes=-1, db_path=tmp_db)
    assert [item["listing_id"] for item in result["listings"]] == [empty["listing_id"]]
    con = get_conn(tmp_db, read_only=True)
    try:
        assert con.execute("SELECT instrument_id FROM instruments ORDER BY instrument_id").fetchall() == sorted(
            [(active["instrument_id"],), ("unrelated",)])
        assert con.execute("SELECT count(*) FROM ingestion_jobs").fetchone()[0] == 0
        assert con.execute("SELECT run_id FROM ingestion_runs").fetchall() == [("active_run",)]
        assert con.execute("SELECT count(*) FROM listing_registration_hints").fetchone()[0] == 1
    finally:
        con.close()


@pytest.mark.parametrize("payload", ["prices", "facts"])
def test_prune_selects_candidates_after_acquiring_lock(tmp_db, monkeypatch, payload):
    from contextlib import contextmanager

    listing = svc.register_listing("RACE", exchange="XNAS", currency="USD",
                                   db_path=tmp_db, verify=False)
    real_lock = svc.db_write_lock

    @contextmanager
    def payload_arrives_before_lock(db_path):
        con = get_conn(db_path)
        try:
            if payload == "prices":
                con.execute("INSERT INTO prices_daily (date, listing_id, symbol, close) "
                            "VALUES ('2024-01-01', ?, 'RACE', 10)", [listing["listing_id"]])
            else:
                con.execute("INSERT INTO issuers (issuer_id, cik) VALUES ('issuer', '0000000001')")
                con.execute("UPDATE instruments SET issuer_id = 'issuer' WHERE instrument_id = ?",
                            [listing["instrument_id"]])
                con.execute("INSERT INTO sec_company_facts "
                            "(fact_id, cik, taxonomy, concept, unit, end_date) VALUES "
                            "('fact', '0000000001', 'us-gaap', 'Assets', 'USD', '2024-01-01')")
        finally:
            con.close()
        with real_lock(db_path):
            yield

    monkeypatch.setattr(svc, "db_write_lock", payload_arrives_before_lock)
    result = svc.prune_empty_listings(dry_run=False, older_than_minutes=-1, db_path=tmp_db)
    assert result["count"] == 0
    assert svc.resolve_instrument(listing["listing_id"], db_path=tmp_db)


def test_fetch_runs_without_holding_the_writer_lock(tmp_db, monkeypatch):
    """Audit CA-06: a slow provider must not block other writers — the fetch
    phase runs with NO writer lock held."""
    from market_data_hub.services import prices as _svc

    lock_state = {"held": False, "held_during_fetch": None}
    real_lock = _svc.db_write_lock

    from contextlib import contextmanager

    @contextmanager
    def tracking_lock(*a, **k):
        lock_state["held"] = True
        try:
            with real_lock(*a, **k):
                yield
        finally:
            lock_state["held"] = False

    monkeypatch.setattr(_svc, "db_write_lock", tracking_lock)

    def fetch(symbols, start, end):
        lock_state["held_during_fetch"] = lock_state["held"]
        return _fake_fetch(symbols, start, end)

    res = _svc.ensure_price_history("SPY", start="2024-01-01",
                                    end="2024-01-31", db_path=tmp_db,
                                    fetch=fetch)
    assert res["status"] == "completed"
    assert lock_state["held_during_fetch"] is False


def test_write_failure_rolls_back_payload_and_marks_error(tmp_db, monkeypatch):
    """Audit CA-06 fault injection: an error AFTER the price upsert (inside
    the commit transaction) must leave NO materialized payload — payload and
    ledger commit or roll back together — and the job ends 'error'."""
    from market_data_hub.db.upsert import upsert as real_upsert
    from market_data_hub.services import prices as _svc

    def sabotaged(con, table, df, **kw):
        real_upsert(con, table, df, **kw)
        raise RuntimeError("boom after upsert")

    monkeypatch.setattr(_svc, "upsert", sabotaged)
    with pytest.raises(RuntimeError, match="boom after upsert"):
        _svc.ensure_price_history("SPY", start="2024-01-01", end="2024-01-31",
                                  db_path=tmp_db, fetch=_fake_fetch)

    con = get_conn(tmp_db, read_only=True)
    try:
        n_prices = con.execute(
            "SELECT COUNT(*) FROM prices_daily WHERE symbol = 'SPY'"
        ).fetchone()[0]
        job = con.execute(
            "SELECT status, error_msg FROM ingestion_jobs").fetchone()
        run = con.execute(
            "SELECT status FROM ingestion_runs").fetchone()[0]
    finally:
        con.close()
    assert n_prices == 0                      # rolled back with the ledger
    assert job[0] == "error" and "boom after upsert" in job[1]
    assert run == "error"


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
