"""Live rows carry the session date of their price, never the collector's clock date."""

from datetime import date, datetime, timezone

import duckdb

from market_data_hub import runner
from market_data_hub.sources.yahoo_direct import session_date_of

NY_WINTER = -5 * 3600


def _epoch(*args) -> int:
    return int(datetime(*args, tzinfo=timezone.utc).timestamp())


def test_session_date_is_exchange_local_not_utc():
    # 16:00 New York on Tue 10/11 is 21:00 UTC; a run at 23:46 PT (07:46 UTC
    # Wed) used to stamp the row 11/11.
    meta = {"regularMarketTime": _epoch(2026, 11, 10, 21, 0), "gmtoffset": NY_WINTER}
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


def test_superseded_live_rows_are_deleted():
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE prices_daily (date DATE, listing_id VARCHAR, symbol VARCHAR, "
        "close DOUBLE, is_live BOOLEAN)"
    )
    con.executemany(
        "INSERT INTO prices_daily VALUES (?, 'L1', 'SPY', ?, ?)",
        [
            ("2026-11-06", 1.0, False),
            ("2026-11-07", 1.0, True),  # clock-dated Saturday row
            ("2026-11-08", 1.0, True),  # clock-dated Sunday row
            ("2026-11-09", 2.0, False),
            ("2026-11-10", 3.0, True),  # real live row, newer than settled
        ],
    )
    con.execute(runner.SUPERSEDED_LIVE_ROWS_SQL)
    left = con.execute("SELECT date::VARCHAR, is_live FROM prices_daily ORDER BY date").fetchall()
    assert left == [("2026-11-06", False), ("2026-11-09", False), ("2026-11-10", True)]
