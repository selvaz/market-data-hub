"""Recorded official ACM monthly CSV contracts, no network or live DB."""
from __future__ import annotations

import importlib
import json
from datetime import date
from pathlib import Path

import pytest
import requests

from market_data_hub import catalog, config_loader, runner
from market_data_hub.coverage import report, stalled_detector
from market_data_hub.db.connection import get_conn

FIXTURES = Path(__file__).parent / "fixtures"
SERIES_ID = "NYFED_ACM_TP10_MONTHLY"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session, "request",
                        lambda *a, **kw: pytest.fail("ACM tests must never access the network"))


def test_acm_config_is_monthly_with_verified_endpoint():
    entries = config_loader.get_nyfed_acm_series()
    assert len(entries) == 1
    meta = entries[0]
    recording = json.loads((FIXTURES / "nyfed_acm_metadata.json").read_text())
    assert meta["symbol"] == SERIES_ID
    assert meta["unit"] == recording["unit"] == "Percent"
    assert meta["frequency"] == "M" and meta["max_lag_days"] == 45
    assert meta["source"] == "nyfed_acm"
    assert recording["maturity_years"] == 10
    assert SERIES_ID not in {e["symbol"] for e in config_loader.get_fred_series()}
    assert "nyfed_acm" in runner._DEFAULT_SOURCES
    assert SERIES_ID in set(catalog.list_macro_series(with_coverage=False)["series_id"])
    assert SERIES_ID in set(catalog.search("ACM")["key"])


def test_recorded_acm_csv_ingested_with_honest_source_and_coverage(tmp_db, monkeypatch):
    acm = importlib.import_module("market_data_hub.sources.nyfed_acm")
    response = requests.Response()
    response.status_code = 200
    response._content = (FIXTURES / "nyfed_acm_2026-07_2026-09.csv").read_bytes()
    evidence = json.loads((FIXTURES / "nyfed_acm_metadata.json").read_text())
    calls = []

    def recorded_get(url, params, timeout, retries, base_sleep):
        assert url == evidence["url"] and params == {}
        assert (timeout, retries, base_sleep) == (1, 1, 0)
        calls.append(url)
        return response

    monkeypatch.setattr(acm, "_http_get", recorded_get)
    monkeypatch.setattr(stalled_detector, "lag_days", lambda *a, **kw: 11)
    cfg = {"backfill_start": {"nyfed_acm": "1961-01-01"},
           "http": {"timeout": 1, "max_retries": 1, "retry_base_sleep": 0}}
    with get_conn(tmp_db) as con:
        runner.run_nyfed_acm(con, cfg, "recorded-acm", start_override="2026-07-01", end="2026-09-30")
        rows = con.execute("SELECT date, value, unit, frequency, source FROM macro_series WHERE series_id=? ORDER BY date", [SERIES_ID]).fetchall()
        assert [r[0] for r in rows] == [date(2026, 7, 31), date(2026, 8, 31), date(2026, 9, 30)]
        assert [r[1] for r in rows] == pytest.approx([0.833222462972223, 0.75936106118762, 0.885008256163907])
        assert all(r[2:] == ("Percent", "M", "nyfed_acm") for r in rows)
        assert con.execute("SELECT source, status, rows_added FROM download_log WHERE run_id='recorded-acm'").fetchone() == ("nyfed_acm", "ok", 3)
        assert report.rebuild_coverage(con, "acm-coverage") == 1
        assert con.execute("SELECT source, freq_detected, stalled FROM coverage_report WHERE symbol=?", [SERIES_ID]).fetchone() == ("nyfed_acm", "M", False)
    assert len(calls) == 1


def test_acm_date_window_and_negative_premium_are_preserved(monkeypatch):
    acm = importlib.import_module("market_data_hub.sources.nyfed_acm")
    response = requests.Response()
    response.status_code = 200
    response._content = b"RunDates,TERMYld,ACMFITYld,GSWYld\n29-Jan-2021,-0.2,1.0,1.1\n26-Feb-2021,0.3,1.2,1.3\n"
    monkeypatch.setattr(acm, "_http_get", lambda *a: response)
    df = acm.fetch_acm("2021-01-01", "2021-01-31", meta=config_loader.get_nyfed_acm_series()[0])
    assert df["value"].tolist() == [-0.2]
    assert df["date"].dt.date.tolist() == [date(2021, 1, 29)]


def test_acm_schema_change_is_a_fault(monkeypatch):
    acm = importlib.import_module("market_data_hub.sources.nyfed_acm")
    response = requests.Response()
    response.status_code = 200
    response._content = b"RunDates,GSWYld\n30-Sep-2026,5.2\n"
    monkeypatch.setattr(acm, "_http_get", lambda *a: response)
    with pytest.raises(ValueError, match="TERMYld"):
        acm.fetch_acm("2026-01-01", "2026-12-31", meta=config_loader.get_nyfed_acm_series()[0])


def test_acm_month_end_publication_lag_boundary():
    meta = config_loader.get_nyfed_acm_series()[0]
    assert not stalled_detector.is_stalled(date(2026, 9, 30), "M", date(2026, 11, 14), max_lag_days=meta["max_lag_days"])
    assert stalled_detector.is_stalled(date(2026, 9, 30), "M", date(2026, 11, 15), max_lag_days=meta["max_lag_days"])
