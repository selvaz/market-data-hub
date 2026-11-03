import json
from datetime import datetime
from pathlib import Path

import pytest

from market_data_hub import equity_snapshots as snap
from market_data_hub.db.connection import get_conn
from market_data_hub.coverage.report import rebuild_coverage
from market_data_hub.reader import read_etf_valuations, read_market_breadth

FIXTURES = Path(__file__).parent / "fixtures" / "equity_snapshots"


def test_yahoo_direct_multiples_not_reciprocals():
    data = json.loads((FIXTURES / "yahoo_spy.json").read_text())
    trailing, forward, book, dividend, raw = snap.parse_valuation(data["quote"], data["summary"])
    assert trailing == 23.87835 and book == 1.7522482
    assert forward is None and dividend == 0.0074921935
    assert json.loads(raw)["topHoldings.equityHoldings"]["priceToEarnings"]["raw"] == 0.04132
    assert snap.parse_valuation({}, data["summary"])[0] is None


def test_breadth_observable_denominators():
    rows = json.loads((FIXTURES / "tradingview.json").read_text())
    rows.append({"close": 10, "SMA50": None, "change": 0})
    assert snap.parse_breadth(rows) == (0, 2, 50, 2, 1, 1, 3, 0, 2, 0, 2)
    assert snap.parse_breadth([{}]) == (None, 0, None, 0, None, None, 0, None, 0, None, 0)


def test_rejects_incomplete_breadth(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"totalCount": 2, "data": []}

    monkeypatch.setattr(snap.requests, "post", lambda *args, **kwargs: Response())
    with pytest.raises(ValueError, match="empty or malformed"):
        snap.fetch_breadth()


def test_first_capture_and_point_in_time(tmp_path):
    path = str(tmp_path / "hub.duckdb")
    con = get_conn(path)
    earlier = datetime(2026, 11, 2, 22)
    later = datetime(2026, 11, 2, 23)
    rows = [("SPY", 20., None, None, None, "{}")]
    snap.store_valuations(con, rows, earlier)
    snap.store_valuations(con, [("SPY", 40., None, None, None, "{}")], later)
    values = (50., 2, 50., 2, 1, 1, 2, 0, 2, 0, 2)
    snap.store_breadth(con, 2, values, earlier)
    snap.store_breadth(con, 2, values, later)
    snap.store_valuations(con, [("SPY", 30., None, None, None, "{}")], datetime(2026, 11, 3, 22))
    rebuild_coverage(con, "test")
    assert con.execute("SELECT source FROM coverage_report WHERE source IN "
                       "('yahoo_valuation', 'tradingview_breadth') ORDER BY source").fetchall() == [
                           ("tradingview_breadth",), ("yahoo_valuation",)]
    con.close()
    assert read_etf_valuations("SPY", as_of="2026-11-02T21:00:00", db_path=path).empty
    assert read_market_breadth("US", as_of="2026-11-02T21:00:00", db_path=path).empty
    assert read_etf_valuations("SPY", as_of="2026-11-02T23:30:00", db_path=path).iloc[0].trailing_pe == 20
    assert read_etf_valuations("SPY", as_of="2026-11-03T23:30:00", db_path=path).iloc[0].trailing_pe == 30
    assert read_etf_valuations("SPY", as_of="2026-11-02T14:00:00-08:00", db_path=path).iloc[0].trailing_pe == 20
    assert len(read_market_breadth("US", as_of="2026-11-02T23:30:00", db_path=path)) == 1
