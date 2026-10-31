"""Publication lag overrides must reach coverage flags and v_stalled offline."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

import validate_config as validator
from market_data_hub.config_loader import get_fred_series
from market_data_hub.coverage import report, stalled_detector as detector
from market_data_hub.db.connection import get_conn
from market_data_hub.db.upsert import upsert


FRIDAY = date(2026, 10, 2)
H10_IDS = {entry["series_id"] for entry in json.loads(
    (Path(__file__).parent / "fixtures" / "fred_h10_series_metadata.json")
    .read_text(encoding="utf-8"))["series"]}


def test_all_h10_series_configure_ten_day_publication_lag():
    entries = get_fred_series()
    overridden = {entry["symbol"] for entry in entries if "max_lag_days" in entry}
    # EIA oil and the H.10 broad dollar publish weekly too (31/10).
    assert overridden == H10_IDS | {"THREEFYTP10", "CES0500000003", "GASDESW", "DDFUELNYH",
                                    "DCOILBRENTEU", "DCOILWTICO", "DTWEXBGS"}
    assert all(entry["max_lag_days"] == 10 for entry in entries
               if entry["symbol"] in H10_IDS)


@pytest.mark.parametrize("days,expected", [(6, False), (10, False), (11, True)])
def test_daily_publication_lag_override(days, expected):
    # Friday -> following Thursday is six calendar days, and still healthy.
    as_of = FRIDAY + timedelta(days=days)
    assert detector.is_stalled(FRIDAY, "D", as_of, max_lag_days=10) is expected
    assert detector.is_stalled(FRIDAY, "D", as_of) is True


@pytest.mark.parametrize("freq,threshold", list(detector.STALLED_THRESHOLD_DAYS.items()))
def test_default_threshold_boundaries_are_unchanged(freq, threshold):
    assert detector.is_stalled(FRIDAY, freq, FRIDAY + timedelta(days=threshold)) is False
    assert detector.is_stalled(FRIDAY, freq, FRIDAY + timedelta(days=threshold + 1)) is True


@pytest.mark.parametrize("last,as_of,freq", [
    # FRED dates a period at its start: these are the latest values on the
    # day before the next release, so they must not count as stalled.
    (date(2026, 9, 1), date(2026, 11, 5), "M"),    # UNRATE Sep, until Oct payrolls
    (date(2026, 8, 1), date(2026, 10, 29), "M"),   # PCE Aug, until Sep PCE
    (date(2026, 4, 1), date(2026, 10, 29), "Q"),   # GDP Q2, until Q3 advance
])
def test_on_time_period_start_releases_are_not_stalled(last, as_of, freq):
    assert detector.is_stalled(last, freq, as_of) is False


def test_discontinued_monthly_series_is_still_stalled():
    assert detector.is_stalled(date(2023, 1, 1), "M", date(2026, 10, 28)) is True


def test_publication_override_does_not_hide_missing_data():
    assert detector.is_stalled(None, "D", FRIDAY, max_lag_days=10) is True


def test_zero_day_override_is_not_replaced_by_default():
    assert detector.is_stalled(FRIDAY, "D", FRIDAY, max_lag_days=0) is False
    assert detector.is_stalled(FRIDAY, "D", FRIDAY + timedelta(days=1),
                               max_lag_days=0) is True


@pytest.mark.parametrize("bad", [-1, True, 1.5, "10"])
def test_invalid_override_rejected_by_detector(bad):
    with pytest.raises(ValueError, match="max_lag_days"):
        detector.is_stalled(FRIDAY, "D", FRIDAY, max_lag_days=bad)


@pytest.mark.parametrize("bad", [-1, True, 1.5, "10", None])
def test_config_validation_rejects_invalid_publication_lag(monkeypatch, bad):
    original = validator._y

    def config(name):
        content = original(name)
        if name == "macro_series.yaml":
            content["fred"][0]["max_lag_days"] = bad
        return content

    monkeypatch.setattr(validator, "_y", config)
    assert any("max_lag_days" in error for error in validator.validate())


@pytest.mark.parametrize("days,fx_stalled", [(6, False), (10, False), (11, True)])
def test_publication_lag_reaches_coverage_and_stalled_view(tmp_db, monkeypatch,
                                                        days, fx_stalled):
    check_date = FRIDAY + timedelta(days=days)
    real_lag_days = detector.lag_days

    def fixed_lag_days(last_date, as_of=None):
        return real_lag_days(last_date, as_of or check_date)

    # Freeze both displayed lag and the detector's clock without mocking its verdict.
    monkeypatch.setattr(detector, "lag_days", fixed_lag_days)
    monkeypatch.setattr(report, "lag_days", fixed_lag_days)
    dates = pd.bdate_range(end=FRIDAY, periods=5)
    with get_conn(tmp_db) as con:
        upsert(con, "macro_series", pd.DataFrame([
            {"date": day, "series_id": symbol, "value": 1.0, "source": "fred"}
            for symbol in sorted(H10_IDS | {"DGS10"}) for day in dates
        ]))
        upsert(con, "prices_daily", pd.DataFrame([
            {"date": day, "symbol": "SPY", "close": 1.0, "adj_close": 1.0,
             "source": "yahoo", "is_live": False}
            for day in dates
        ]))
        assert report.rebuild_coverage(con, "publication-lag-test") == 25
        rows = con.execute(
            "SELECT symbol, freq_detected, lag_days, stalled, status FROM coverage_report"
        ).fetchall()
        expected_stalled = {"SPY", "DGS10"} | (H10_IDS if fx_stalled else set())
        assert all(row[1:3] == ("D", days) for row in rows)
        assert {row[0] for row in rows if row[3]} == expected_stalled
        assert all(row[4] == ("stalled" if row[3] else "ok") for row in rows)
        # This is the same persisted flag read by reader.get_stalled().
        assert {row[0] for row in con.execute("SELECT symbol FROM v_stalled").fetchall()} == expected_stalled
