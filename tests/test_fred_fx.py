"""Daily H.10 config and recorded ingestion contracts; no live data access."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
import requests

from market_data_hub import runner
from market_data_hub.config_loader import get_fred_series
from market_data_hub.db.connection import get_conn
from market_data_hub.sources import fred


# Independently specified from the published H.10 daily bilateral series.
H10_IDS = {
    "DEXMXUS", "DEXBZUS", "DEXINUS", "DEXCHUS", "DEXKOUS", "DEXJPUS",
    "DEXSZUS", "DEXCAUS", "DEXUSUK", "DEXUSEU", "DEXUSAL", "DEXSFUS",
    "DEXTAUS", "DEXHKUS", "DEXMAUS", "DEXTHUS", "DEXSIUS", "DEXNOUS",
    "DEXSDUS", "DEXDNUS", "DEXUSNZ", "DEXVZUS", "DEXSLUS",
}


def test_config_contains_daily_h10_exchange_rates():
    recording = json.loads((Path(__file__).parent / "fixtures" /
                            "fred_h10_series_metadata.json").read_text(encoding="utf-8"))
    verified = {entry["series_id"] for entry in recording["series"]}
    assert verified == H10_IDS
    assert all(entry["frequency"] == "Daily" for entry in recording["series"])
    entries = get_fred_series()
    ids = [entry["symbol"] for entry in entries]
    assert H10_IDS <= set(ids)
    assert all(ids.count(series_id) == 1 for series_id in H10_IDS)
    assert all(entry["asset_class"] == "FX" for entry in entries
               if entry["symbol"] in H10_IDS)


@pytest.mark.parametrize("series_id,currency,unit,convention,orientation", [
    ("DEXUSEU", "EUR", "USD per EUR", "usd_per_currency_unit", 1),
    ("DEXUSUK", "GBP", "USD per GBP", "usd_per_currency_unit", 1),
    ("DEXUSAL", "AUD", "USD per AUD", "usd_per_currency_unit", 1),
    ("DEXUSNZ", "NZD", "USD per NZD", "usd_per_currency_unit", 1),
    ("DEXJPUS", "JPY", "JPY per USD", "currency_units_per_usd", -1),
    ("DEXSZUS", "CHF", "CHF per USD", "currency_units_per_usd", -1),
    ("DEXSFUS", "ZAR", "ZAR per USD", "currency_units_per_usd", -1),
])
def test_known_quote_conventions(series_id, currency, unit, convention, orientation):
    entries = {entry["symbol"]: entry for entry in get_fred_series()}
    meta = entries[series_id]
    assert meta["currency"] == currency
    assert meta["unit"] == unit
    assert meta["quote_convention"] == convention
    assert meta["currency_strength_orientation"] == orientation


def test_all_h10_series_have_unambiguous_quote_metadata():
    entries = {entry["symbol"]: entry for entry in get_fred_series()}
    for series_id in H10_IDS:
        meta = entries[series_id]
        currency = meta["currency"]
        if series_id in {"DEXUSEU", "DEXUSUK", "DEXUSAL", "DEXUSNZ"}:
            assert meta["unit"] == f"USD per {currency}"
            assert meta["quote_convention"] == "usd_per_currency_unit"
            assert meta["currency_strength_orientation"] == 1
        else:
            assert meta["unit"] == f"{currency} per USD"
            assert meta["quote_convention"] == "currency_units_per_usd"
            assert meta["currency_strength_orientation"] == -1


def test_recorded_dex_response_ingested_into_macro_series(tmp_db, monkeypatch):
    # Fail closed at the transport as well as mocking the FRED HTTP seam.
    def forbid_network(*args, **kwargs):
        pytest.fail("recorded FRED ingestion must never access the network")

    monkeypatch.setattr(requests.sessions.Session, "request", forbid_network)
    entries = {entry["symbol"]: entry for entry in get_fred_series()}
    meta = entries["DEXJPUS"]
    monkeypatch.setattr(runner, "get_fred_series", lambda: [meta])
    csv_path = Path(__file__).parent / "fixtures" / "fred_dexjpus_2024-01-12_2024-01-16.csv"
    response = requests.Response()
    response.status_code = 200
    response._content = csv_path.read_bytes()
    calls = []

    def recorded_http_get(url, params, timeout, retries, base_sleep):
        assert url == fred._CSV_URL
        assert params == {"id": "DEXJPUS", "cosd": "2024-01-12", "coed": "2024-01-16"}
        calls.append(params)
        return response

    monkeypatch.setattr(fred, "_http_get", recorded_http_get)
    cfg = {
        "backfill_start": {"fred": "2024-01-12"},
        "http": {"timeout": 1, "max_retries": 1, "retry_base_sleep": 0},
        "parallelism": {"fred_sleep": 0},
    }
    # Explicit scratch connection; do not invoke run_daily or the whole pipeline.
    with get_conn(tmp_db) as con:
        runner.run_fred(con, cfg, "recorded-fx-test", end="2024-01-16")
        rows = con.execute(
            "SELECT date, value, unit, country, source, series_name "
            "FROM macro_series WHERE series_id = 'DEXJPUS' ORDER BY date"
        ).fetchall()
        assert [(row[0], row[1]) for row in rows] == [
            (date(2024, 1, 12), 144.86), (date(2024, 1, 16), 147.01),
        ]
        assert all(row[2:5] == ("JPY per USD", "JP", "fred") for row in rows)
        assert all(row[5] == meta["name"] for row in rows)
        # The holiday's empty observation must not turn into an artificial FX rate.
        assert con.execute("SELECT count(*) FROM macro_series").fetchone() == (2,)
        assert con.execute(
            "SELECT status, rows_added FROM download_log "
            "WHERE run_id = 'recorded-fx-test' AND symbol = 'DEXJPUS'"
        ).fetchone() == ("ok", 2)
    assert len(calls) == 1
