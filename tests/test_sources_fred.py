# -*- coding: utf-8 -*-
"""
Contract tests for sources/fred.py — verifies fetch_fred() understands the
real shape of both FRED response formats (JSON API with api_key, public CSV
without one) by monkeypatching the module's single HTTP seam (_http_get),
same pattern as tests/test_audit_fixes_sources.py for imf_sdmx.py/ecb.py.
"""
from __future__ import annotations

from market_data_hub.sources import fred


class _FakeResponse:
    def __init__(self, *, json_data=None, text=None):
        self._json = json_data
        self.text = text

    def json(self):
        return self._json


def test_json_path_used_when_api_key_given(monkeypatch):
    captured = {}

    def fake_http_get(url, params, timeout, retries, base_sleep):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse(json_data={"observations": [
            {"date": "2024-01-01", "value": "5.33"},
            {"date": "2024-02-01", "value": "5.50"},
        ]})

    monkeypatch.setattr(fred, "_http_get", fake_http_get)
    df = fred.fetch_fred("DFF", "2024-01-01", "2024-12-31", api_key="dummy",
                         meta={"name": "Fed Funds Rate", "unit": "percent"})

    assert captured["url"] == fred._API_URL
    assert list(df.columns) == ["date", "series_id", "value", "series_name",
                                "unit", "frequency", "source", "country"]
    assert len(df) == 2
    assert df["value"].tolist() == [5.33, 5.50]
    assert (df["series_id"] == "DFF").all()
    assert (df["series_name"] == "Fed Funds Rate").all()
    assert (df["unit"] == "percent").all()
    assert (df["source"] == "fred").all()
    assert (df["country"] == "US").all()


def test_csv_path_used_without_api_key(monkeypatch):
    csv_text = "DATE,DGS10\n2024-01-01,4.02\n2024-01-02,4.05\n"

    def fake_http_get(url, params, timeout, retries, base_sleep):
        assert url == fred._CSV_URL
        return _FakeResponse(text=csv_text)

    monkeypatch.setattr(fred, "_http_get", fake_http_get)
    df = fred.fetch_fred("DGS10", "2024-01-01", "2024-12-31")

    assert len(df) == 2
    assert df["value"].tolist() == [4.02, 4.05]
    assert (df["series_id"] == "DGS10").all()
    # no meta given: series_name falls back to the series_id
    assert (df["series_name"] == "DGS10").all()


def test_csv_missing_value_marker_is_dropped(monkeypatch):
    # fredgraph.csv marks a missing observation as "." (not NaN/empty)
    csv_text = "DATE,DGS10\n2024-01-01,4.02\n2024-01-02,.\n"

    def fake_http_get(url, params, timeout, retries, base_sleep):
        return _FakeResponse(text=csv_text)

    monkeypatch.setattr(fred, "_http_get", fake_http_get)
    df = fred.fetch_fred("DGS10", "2024-01-01", "2024-12-31")

    assert len(df) == 1
    assert df["value"].tolist() == [4.02]


def test_json_path_empty_observations_returns_empty_frame(monkeypatch):
    def fake_http_get(url, params, timeout, retries, base_sleep):
        return _FakeResponse(json_data={"observations": []})

    monkeypatch.setattr(fred, "_http_get", fake_http_get)
    df = fred.fetch_fred("DFF", "2024-01-01", "2024-12-31", api_key="dummy")

    assert df.empty
    assert list(df.columns) == ["date", "series_id", "value", "series_name",
                                "unit", "frequency", "source", "country"]
