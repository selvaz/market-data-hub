"""F05 public-source contracts: recorded payloads and scratch DBs only."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest
import requests

from market_data_hub import runner
from market_data_hub.config_loader import get_fred_series, get_yahoo_tickers
from market_data_hub.coverage import report
from market_data_hub.coverage.stalled_detector import is_stalled
from market_data_hub.db.connection import get_conn
from market_data_hub.db.identity import currency_for_symbol, ensure_listing
from market_data_hub.sources import fred, yahoo_direct

FIXTURES = Path(__file__).parent / "fixtures"
FRED_META = {
    "THREEFYTP10": ("Percent", "D", "Not Seasonally Adjusted", 10),
    "BAMLEMCBPIOAS": ("Percent", "D", "Not Seasonally Adjusted", None),
    "BAMLEMHBHYCRPIOAS": ("Percent", "D", "Not Seasonally Adjusted", None),
    "CES0500000003": ("Dollars per Hour", "M", "Seasonally Adjusted", 70),
    "GASDESW": ("Dollars per Gallon", "W", "Not Seasonally Adjusted", 10),
    "DDFUELNYH": ("Dollars per Gallon", "D", "Not Seasonally Adjusted", 10),
}
FUTURES_META = {
    "BZ=F": ("USD", "USD per barrel"),
    "CL=F": ("USD", "USD per barrel"),
    "GC=F": ("USD", "USD per troy ounce"),
    "TTF=F": ("EUR", "EUR per MWh"),
    "NG=F": ("USD", "USD per MMBtu"),
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("F05 tests must never access the network")

    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    if yahoo_direct._creq is not None:
        monkeypatch.setattr(yahoo_direct._creq.Session, "request", forbidden)


def test_fred_config_matches_recorded_published_metadata():
    recorded = json.loads((FIXTURES / "fred_f05_series_metadata.json").read_text())
    verified = {entry["series_id"]: entry for entry in recorded["series"]}
    assert set(verified) == set(FRED_META)
    entries = get_fred_series()
    configured = {entry["symbol"]: entry for entry in entries}
    assert set(FRED_META) <= set(configured)
    for sid, (unit, freq, adjustment, lag) in FRED_META.items():
        entry, evidence = configured[sid], verified[sid]
        assert sum(e["symbol"] == sid for e in entries) == 1
        assert evidence["http_status"] == 200 and f"({sid})" in evidence["title"]
        assert entry["unit"] == evidence["units"] == unit
        assert entry["frequency"] == freq
        assert evidence["frequency"].split(",")[0] == {"D": "Daily", "M": "Monthly", "W": "Weekly"}[freq]
        assert entry["seasonal_adjustment"] == evidence["seasonal_adjustment"] == adjustment
        assert entry.get("max_lag_days") == lag
        if sid.startswith("BAML"):
            assert "3 years" in evidence["history_limit"]


def test_yahoo_config_contains_continuous_futures_with_quote_units():
    recorded = json.loads((FIXTURES / "yahoo_f05_series_metadata.json").read_text())
    verified = {entry["symbol"]: entry for entry in recorded["series"]}
    entries = get_yahoo_tickers()
    configured = {entry["symbol"]: entry for entry in entries}
    assert set(verified) == set(FUTURES_META)
    assert set(FUTURES_META) <= set(configured)
    for symbol, (currency, unit) in FUTURES_META.items():
        entry = configured[symbol]
        assert sum(e["symbol"] == symbol for e in entries) == 1
        assert entry["asset_class"] == "COMMODITIES"
        assert entry["instrument_type"] == "futures_continuous"
        assert entry["currency"] == verified[symbol]["currency"] == currency
        assert entry["unit"] == unit and entry["frequency"] == "D"
        assert "roll" in entry["notes"].lower()


def test_recorded_fred_earnings_land_with_native_frequency(tmp_db, monkeypatch):
    meta = next(e for e in get_fred_series() if e["symbol"] == "CES0500000003")
    monkeypatch.setattr(runner, "get_fred_series", lambda: [meta])
    response = requests.Response()
    response.status_code = 200
    response._content = (FIXTURES / "fred_ces0500000003_2026-07-01_2026-09-01.csv").read_bytes()
    calls = []

    def recorded_get(url, params, *args):
        assert url == fred._CSV_URL
        assert params == {"id": "CES0500000003", "cosd": "2026-07-01", "coed": "2026-09-01"}
        calls.append(params)
        return response

    monkeypatch.setattr(fred, "_http_get", recorded_get)
    cfg = {"backfill_start": {"fred": "2026-07-01"},
           "http": {"timeout": 1, "max_retries": 1, "retry_base_sleep": 0},
           "parallelism": {"fred_sleep": 0}}
    with get_conn(tmp_db) as con:
        runner.run_fred(con, cfg, "recorded-f05-fred", end="2026-09-01")
        rows = con.execute("SELECT date, value, unit, frequency, country, source FROM macro_series ORDER BY date").fetchall()
        assert [(r[0], r[1]) for r in rows] == [(date(2026, 7, 1), 37.64), (date(2026, 8, 1), 37.76), (date(2026, 9, 1), 37.81)]
        assert all(r[2:] == ("Dollars per Hour", "M", "US", "fred") for r in rows)
        assert con.execute("SELECT status, rows_added FROM download_log WHERE run_id='recorded-f05-fred'").fetchone() == ("ok", 3)
    assert len(calls) == 1


def test_fred_adapter_preserves_declared_frequency(monkeypatch):
    response = requests.Response()
    response.status_code = 200
    response._content = (FIXTURES / "fred_ces0500000003_2026-07-01_2026-09-01.csv").read_bytes()
    monkeypatch.setattr(fred, "_http_get", lambda *args: response)
    df = fred.fetch_fred("CES0500000003", "2026-07-01", "2026-09-01", meta={"frequency": "M"})
    assert df["frequency"].tolist() == ["M", "M", "M"]


def test_recorded_yahoo_futures_land_as_instrument_prices(tmp_db, monkeypatch):
    meta = next(e for e in get_yahoo_tickers() if e["symbol"] == "CL=F")
    monkeypatch.setattr(runner, "get_yahoo_tickers", lambda: [meta])
    payload = json.loads((FIXTURES / "yahoo_cl_f_2026-10-05_2026-10-09.json").read_text())
    calls = []

    class RecordedSession:
        def get(self, url, *, params, timeout):
            assert url == yahoo_direct._BASE + "CL=F"
            assert params == {"period1": 1791158400, "period2": 1791590400, "interval": "1d", "events": "div,splits"}
            calls.append(url)
            return type("Response", (), {"status_code": 200, "json": lambda self: payload})()

    monkeypatch.setattr(yahoo_direct, "_session", lambda: RecordedSession())
    cfg = {"backfill_start": {"yahoo": "2026-10-05"},
           "incremental": {"tail_refresh_days": 5},
           "parallelism": {"yahoo_batch_sleep": 0, "yahoo_workers": 1}}
    with get_conn(tmp_db) as con:
        runner.run_yahoo(con, cfg, "recorded-f05-yahoo", end="2026-10-09")
        rows = con.execute("SELECT date, close, adj_close, source, is_live, listing_id FROM prices_daily ORDER BY date").fetchall()
        assert [r[0] for r in rows] == [date(2026, 10, d) for d in range(5, 10)]
        assert [r[1] for r in rows] == pytest.approx([89.43, 89.44, 88.28, 91.49, 91.85])
        assert all(r[1] == r[2] and r[3:5] == ("yahoo", False) for r in rows)
        assert len({r[5] for r in rows}) == 1
        assert con.execute("SELECT i.kind, l.currency FROM listings l JOIN instruments i USING(instrument_id) WHERE l.symbol='CL=F'").fetchone() == ("COMMODITIES", "USD")
        assert con.execute("SELECT status, rows_added FROM download_log WHERE run_id='recorded-f05-yahoo'").fetchone() == ("ok", 5)
    assert len(calls) == 1


def test_ttf_listing_currency_is_eur(tmp_db):
    assert currency_for_symbol("TTF=F") == "EUR"
    with get_conn(tmp_db) as con:
        ensure_listing(con, "TTF=F", kind="COMMODITIES")
        assert con.execute("SELECT currency FROM listings WHERE symbol='TTF=F'").fetchone() == ("EUR",)


@pytest.mark.parametrize("sid", ["THREEFYTP10", "CES0500000003", "GASDESW", "DDFUELNYH"])
def test_configured_publication_lag_reaches_coverage_and_is_stalled(sid, monkeypatch):
    entry = next(e for e in get_fred_series() if e["symbol"] == sid)
    limit = FRED_META[sid][3]
    assert entry["max_lag_days"] == limit
    last = date(2026, 9, 1)
    meta = report._meta_lookup()[sid]
    assert meta["max_lag_days"] == limit
    for lag, stalled in [(limit, False), (limit + 1, True)]:
        as_of = last + timedelta(days=lag)
        assert is_stalled(last, entry["frequency"], as_of, max_lag_days=limit) is stalled
        monkeypatch.setattr(report, "is_stalled", lambda last, freq, **kw: is_stalled(last, freq, as_of, **kw))
        frame = pd.DataFrame({"date": [last], "value": [1.0]})
        assert report._row_for(sid, "fred", frame, "date", meta, "lag-test")["stalled"] is stalled
    assert is_stalled(None, entry["frequency"], max_lag_days=limit)
