"""Live rows carry the session date of their price, never the collector's clock date."""

from contextlib import nullcontext
from datetime import date, datetime, time, timedelta, timezone

import duckdb
import pandas as pd
import pytest

from market_data_hub import runner
from market_data_hub.db.connection import apply_schema
from market_data_hub.db.identity import AmbiguousSymbolError, ensure_listing
from market_data_hub.db.live_cleanup import remove_wrong_dated_live_duplicates
from market_data_hub.db.upsert import upsert
from market_data_hub.sources.yahoo_direct import session_date_of

NY_WINTER = -5 * 3600


def _epoch(*args) -> int:
    return int(datetime(*args, tzinfo=timezone.utc).timestamp())


def test_session_date_is_exchange_local_not_utc():
    # UTC Wednesday is still Tuesday in New York: ignoring the offset fails.
    meta = {"regularMarketTime": _epoch(2026, 11, 11, 1, 0), "gmtoffset": NY_WINTER}
    assert session_date_of(meta) == "2026-11-10"


def test_session_date_missing_time_gives_none():
    assert session_date_of({"gmtoffset": NY_WINTER}) is None


EOD = {"SPY": (date(2026, 11, 9), 700.0, 700.0)}


def test_live_row_dated_by_session():
    rows = runner.live_rows(EOD, {"SPY": (749.92, "2026-11-10")})
    assert [(r["date"], r["close"], r["is_live"]) for r in rows] == [
        (date(2026, 11, 10), 749.92, True)
    ]


def test_weekend_run_does_not_restamp_friday_close():
    # Friday 11/06 settled; a Saturday run sees regularMarketTime on Friday.
    eod = {"SPY": (date(2026, 11, 6), 700.0, 700.0)}
    assert runner.live_rows(eod, {"SPY": (701.0, "2026-11-06")}) == []


def test_session_not_newer_than_settled_bar_is_skipped():
    assert runner.live_rows(EOD, {"SPY": (701.0, "2026-11-09")}) == []


@pytest.fixture
def live_db():
    con = duckdb.connect()
    apply_schema(con)
    yield con
    con.close()


def _seed(con, symbol, day, *, price=100.0, adj_close=None, live=False,
          listing_id=None, updated_at=None):
    listing_id = listing_id or ensure_listing(con, symbol)
    day = date.fromisoformat(day)
    # Explicit naive Pacific timestamps reproduce the production contract,
    # independently of the test connection's timezone.
    stamp = updated_at or datetime.combine(day + timedelta(days=1), time())
    upsert(con, "prices_daily", pd.DataFrame([{
        "date": day, "symbol": symbol, "listing_id": listing_id,
        "close": price, "adj_close": price if adj_close is None else adj_close,
        "is_live": live, "updated_at": stamp,
    }]))
    return listing_id


def _run_live(con, monkeypatch, entries=None, quotes=None, prices=None,
              today="2026-11-11", now=None):
    entries = entries if entries is not None else [{"symbol": "SPY", "asset_class": "EQUITY"}]
    calls = {}

    def fetch_quotes(symbols):
        calls["session"] = symbols
        return {s: q for s, q in (quotes or {}).items() if s in symbols}

    def fetch_prices(symbols):
        calls["legacy"] = symbols
        return {s: p for s, p in (prices or {}).items() if s in symbols}

    monkeypatch.setattr(runner, "get_yahoo_tickers", lambda: entries)
    monkeypatch.setattr(runner, "_today", lambda: today)
    monkeypatch.setattr(runner.yh, "get_live_quotes_batch", fetch_quotes)
    monkeypatch.setattr(runner.yh, "get_live_prices_batch", fetch_prices)
    cfg = {"live": {"enabled": True, "asset_classes": list({e["asset_class"] for e in entries})}}
    runner.run_live(con, cfg, "test-live", now=now)
    return calls


def test_superseded_live_rows_are_deleted(live_db, monkeypatch):
    con = live_db
    for day, live in [("2026-11-06", False), ("2026-11-07", True),
                      ("2026-11-08", True), ("2026-11-09", False), ("2026-11-10", True)]:
        _seed(con, "SPY", day, live=live)
    _run_live(con, monkeypatch)
    left = con.execute("SELECT date::VARCHAR, is_live FROM prices_daily ORDER BY date").fetchall()
    assert left == [("2026-11-06", False), ("2026-11-09", False), ("2026-11-10", True)]


@pytest.mark.parametrize("day,prior", [("2026-11-10", "2026-11-09"),
                                       ("2026-07-14", "2026-07-13")])
@pytest.mark.parametrize("db_zone", ["UTC", "Asia/Tokyo"])
def test_partial_daily_bar_does_not_block_post_close_live(live_db, monkeypatch, day, prior, db_zone):
    con = live_db
    con.execute(f"SET TimeZone = '{db_zone}'")
    _seed(con, "SPY", prior, price=100, adj_close=50)
    # 12:59:59 Pacific = 15:59:59 New York in both summer and winter.
    _seed(con, "SPY", day, price=90, updated_at=datetime.fromisoformat(day + "T12:59:59"))
    _run_live(con, monkeypatch, quotes={"SPY": (110.0, day)})
    row = con.execute("SELECT close, adj_close, is_live FROM prices_daily WHERE date = ?", [day]).fetchone()
    assert row == (110.0, 55.0, True)
    # A later collector must continue refreshing the provisional session row.
    _run_live(con, monkeypatch, quotes={"SPY": (112.0, day)})
    assert con.execute("SELECT close, is_live FROM prices_daily WHERE date = ?", [day]).fetchone() == (112.0, True)


@pytest.mark.parametrize("stamp", ["2026-11-10T13:00:00", "2026-11-10T13:30:36",
                                  "2026-11-11T00:00:00"])
def test_daily_bar_ingested_at_or_after_close_is_settled(live_db, monkeypatch, stamp):
    _seed(live_db, "SPY", "2026-11-09")
    _seed(live_db, "SPY", "2026-11-10", price=105, updated_at=datetime.fromisoformat(stamp))
    _run_live(live_db, monkeypatch, quotes={"SPY": (110, "2026-11-10")})
    assert live_db.execute("SELECT close, is_live FROM prices_daily WHERE date = '2026-11-10'").fetchone() == (105, False)


def test_partial_daily_bar_cannot_trigger_cleanup(live_db, monkeypatch):
    _seed(live_db, "SPY", "2026-11-06")
    _seed(live_db, "SPY", "2026-11-09", live=True)
    _seed(live_db, "SPY", "2026-11-10", updated_at=datetime(2026, 11, 10, 7))
    _run_live(live_db, monkeypatch)
    assert live_db.execute("SELECT count(*) FROM prices_daily WHERE is_live").fetchone() == (1,)


@pytest.mark.parametrize("symbol,kind,day,settled", [
    ("EURUSD=X", "FX", "2026-10-19", "2026-10-18"),  # London BST daily bar is UTC Sunday
    ("CL=F", "COMMODITIES", "2026-11-08", "2026-11-06"),  # Sunday evening is Monday's session
    ("BTC-USD", "ALTERNATIVES", "2026-11-10", "2026-11-10"),
    ("EXSA.DE", "EQUITY", "2026-11-10", "2026-11-10"),
    ("^N225", "EQUITY", "2026-11-10", "2026-11-10"),
    ("VFINX", "EQUITY", "2026-11-10", "2026-11-10"),
])
def test_every_instrument_uses_its_quote_session_date(
        live_db, monkeypatch, symbol, kind, day, settled):
    _seed(live_db, symbol, "2026-10-01", live=True)
    _seed(live_db, symbol, settled)
    entry = {"symbol": symbol, "asset_class": kind}
    if symbol == "VFINX":
        entry["instrument_type"] = "history_proxy"
    calls = _run_live(live_db, monkeypatch, entries=[entry],
                      quotes={symbol: (111, day)}, prices={symbol: 112})
    assert live_db.execute("SELECT close, is_live FROM prices_daily WHERE date = '2026-10-01'").fetchone() == (100, True)
    expected = [(100, False)] if day == settled else [(111, True)]
    assert live_db.execute(
        "SELECT close, is_live FROM prices_daily WHERE date = ?", [day]
    ).fetchall() == expected
    assert calls["session"] == [symbol]
    assert "legacy" not in calls


def test_2347_pacific_uses_listing_sessions_not_collectors_tomorrow(
        live_db, monkeypatch):
    """07:47 UTC is still 23:47 on 11/12 in America/Los_Angeles."""
    entries = [
        {"symbol": "SPY", "asset_class": "EQUITY"},
        {"symbol": "EXSA.DE", "asset_class": "EQUITY"},
        {"symbol": "EURUSD=X", "asset_class": "FX"},
    ]
    for entry in entries:
        _seed(live_db, entry["symbol"], "2026-11-11")
    clock = datetime(2026, 11, 13, 7, 47, tzinfo=timezone.utc)
    _run_live(
        live_db, monkeypatch, entries=entries, now=clock,
        quotes={
            "SPY": (110, "2026-11-12"),
            "EXSA.DE": (120, "2026-11-12"),
            # H0 uses the quote's session date for 24-hour instruments too.
            "EURUSD=X": (1.16, "2026-11-13"),
        },
    )
    dates = dict(live_db.execute(
        "SELECT symbol, date::VARCHAR FROM prices_daily WHERE is_live"
    ).fetchall())
    assert dates == {
        "SPY": "2026-11-12",
        "EXSA.DE": "2026-11-12",
        "EURUSD=X": "2026-11-13",
    }


def test_wrong_dated_duplicate_cleanup_is_dry_run_and_idempotent(live_db):
    _seed(live_db, "SPY", "2026-11-12", price=110, live=True)
    _seed(live_db, "SPY", "2026-11-13", price=110, live=True)
    assert remove_wrong_dated_live_duplicates(
        live_db, "2026-11-13"
    ) == 1
    assert live_db.execute("SELECT count(*) FROM prices_daily").fetchone() == (2,)
    assert remove_wrong_dated_live_duplicates(
        live_db, "2026-11-13", dry_run=False
    ) == 1
    assert remove_wrong_dated_live_duplicates(
        live_db, "2026-11-13", dry_run=False
    ) == 0


def test_fred_source_restriction_does_not_run_live(monkeypatch):
    con = duckdb.connect()
    apply_schema(con)
    calls = []
    monkeypatch.setattr(runner, "get_settings", lambda: {
        "live": {"enabled": True}, "backfill_start": {}
    })
    monkeypatch.setattr(runner, "db_write_lock", lambda _path: nullcontext())
    monkeypatch.setattr(runner, "get_conn", lambda _path: con)
    monkeypatch.setattr(
        runner, "run_fred", lambda *_args, **_kwargs: calls.append("fred")
    )
    monkeypatch.setattr(
        runner, "run_live", lambda *_args, **_kwargs: calls.append("live")
    )
    monkeypatch.setattr(runner, "rebuild_coverage", lambda *_args: 0)
    monkeypatch.setattr(runner, "rebuild_macro_panel_coverage", lambda *_args: 0)
    monkeypatch.setattr(runner, "get_countries", lambda: [])

    assert runner.run(sources=["fred"], db_path="unused.duckdb") == 0
    assert calls == ["fred"]


def test_retired_listing_cannot_block_active_listing(live_db, monkeypatch):
    active = _seed(live_db, "SPY", "2026-11-09", price=100, adj_close=50)
    retired = ensure_listing(live_db, "SPY", provider="retired")
    live_db.execute("UPDATE listings SET active_to = '2026-11-01' WHERE listing_id = ?", [retired])
    _seed(live_db, "SPY", "2026-11-10", listing_id=retired, price=200)
    _seed(live_db, "SPY", "2026-11-08", listing_id=retired, live=True)
    _run_live(live_db, monkeypatch, quotes={"SPY": (110, "2026-11-10")})
    assert live_db.execute("SELECT listing_id, close, adj_close, is_live FROM prices_daily WHERE listing_id = ? AND date = '2026-11-10'", [active]).fetchone() == (active, 110, 55, True)
    assert live_db.execute("SELECT count(*) FROM prices_daily WHERE listing_id = ? AND is_live", [retired]).fetchone() == (1,)


def test_ambiguous_active_listing_is_rejected_before_cleanup(live_db, monkeypatch):
    _seed(live_db, "SPY", "2026-11-09")
    _seed(live_db, "SPY", "2026-11-08", live=True)
    ensure_listing(live_db, "SPY", provider="other")
    with pytest.raises(AmbiguousSymbolError):
        _run_live(live_db, monkeypatch, quotes={"SPY": (110, "2026-11-09")})
    assert live_db.execute("SELECT count(*) FROM prices_daily WHERE is_live").fetchone() == (1,)


def test_listing_without_settled_history_keeps_live_rows(live_db, monkeypatch):
    _seed(live_db, "SPY", "2026-11-10", live=True)
    _run_live(live_db, monkeypatch, quotes={"SPY": (110, "2026-11-11")})
    assert live_db.execute("SELECT date::VARCHAR, close FROM prices_daily").fetchall() == [("2026-11-10", 100)]
