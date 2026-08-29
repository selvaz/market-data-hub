# -*- coding: utf-8 -*-
"""
Contract tests for sources/alfred.py using realistic FRED JSON responses and
the module's single HTTP seam (_http_get).
"""
from __future__ import annotations

import pandas as pd
import pytest

from market_data_hub.sources import alfred


class _FakeResponse:
    def __init__(self, json_data):
        self._json = json_data

    def json(self):
        return self._json


def test_fetch_vintage_dates_returns_flat_list(monkeypatch):
    captured = {}
    payload = {
        "realtime_start": "1776-07-04",
        "realtime_end": "9999-12-31",
        "order_by": "vintage_date",
        "sort_order": "desc",
        "count": 668,
        "offset": 0,
        "limit": 5,
        "vintage_dates": ["2026-08-12", "2026-07-14", "2026-06-10"],
    }

    def fake_http_get(url, params, timeout, retries, base_sleep):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse(payload)

    monkeypatch.setattr(alfred, "_http_get", fake_http_get)
    result = alfred.fetch_vintage_dates(
        "CPIAUCSL", api_key="dummy", limit=5)

    assert captured["url"] == alfred._VINTAGE_DATES_URL
    assert captured["params"]["realtime_start"] == "1776-07-04"
    assert captured["params"]["realtime_end"] == "9999-12-31"
    assert captured["params"]["limit"] == 5
    assert result == ["2026-08-12", "2026-07-14", "2026-06-10"]


def test_fetch_as_of_preserves_real_revision_across_vintages(monkeypatch):
    captured = []
    values = {"2024-02-15": "309.685", "2026-08-27": "309.698"}

    def fake_http_get(url, params, timeout, retries, base_sleep):
        captured.append((url, params))
        value = values[params["realtime_start"]]
        return _FakeResponse({
            "realtime_start": params["realtime_start"],
            "realtime_end": params["realtime_end"],
            "observation_start": "2024-01-01",
            "observation_end": "2024-01-01",
            "count": 1,
            "observations": [{
                "realtime_start": params["realtime_start"],
                "realtime_end": params["realtime_end"],
                "date": "2024-01-01",
                "value": value,
            }],
        })

    monkeypatch.setattr(alfred, "_http_get", fake_http_get)
    original = alfred.fetch_as_of(
        "CPIAUCSL", "2024-01-01", "2024-01-01", "2024-02-15",
        api_key="dummy")
    revised = alfred.fetch_as_of(
        "CPIAUCSL", "2024-01-01", "2024-01-01", "2026-08-27",
        api_key="dummy")

    assert all(url == alfred._OBSERVATIONS_URL for url, _ in captured)
    assert all(params["realtime_start"] == params["realtime_end"]
               for _, params in captured)
    assert list(original.columns) == [
        "date", "series_id", "value", "as_of", "source"]
    assert original.loc[0, "date"] == pd.Timestamp("2024-01-01")
    assert original.loc[0, "value"] == 309.685
    assert revised.loc[0, "value"] == 309.698
    assert original.loc[0, "as_of"] == pd.Timestamp("2024-02-15")
    assert revised.loc[0, "as_of"] == pd.Timestamp("2026-08-27")
    assert (original["series_id"] == "CPIAUCSL").all()
    assert (original["source"] == "alfred").all()


@pytest.mark.parametrize("function,args", [
    (alfred.fetch_vintage_dates, ("CPIAUCSL",)),
    (alfred.fetch_as_of,
     ("CPIAUCSL", "2024-01-01", "2024-01-01", "2024-02-15")),
])
def test_api_key_is_required(monkeypatch, function, args):
    monkeypatch.delenv("FRED_API_KEY", raising=False)

    with pytest.raises(ValueError, match="FRED_API_KEY"):
        function(*args)


def test_existing_fred_environment_key_is_reused(monkeypatch):
    captured = {}

    def fake_http_get(url, params, timeout, retries, base_sleep):
        captured["api_key"] = params["api_key"]
        return _FakeResponse({"vintage_dates": []})

    monkeypatch.setenv("FRED_API_KEY", "environment-key")
    monkeypatch.setattr(alfred, "_http_get", fake_http_get)

    assert alfred.fetch_vintage_dates("CPIAUCSL") == []
    assert captured["api_key"] == "environment-key"
