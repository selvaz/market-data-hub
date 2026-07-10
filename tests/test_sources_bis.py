# -*- coding: utf-8 -*-
"""
Contract tests for sources/bis.py — verifies fetch_bis() and _period_end()
understand the real shape of the BIS native v2 CSV export (TIME_PERIOD,
OBS_VALUE, and a spec-selected country-dimension column) by monkeypatching
the module's single HTTP seam (_get_csv), same style as the ecb.py tests in
tests/test_audit_fixes_sources.py.
"""
from __future__ import annotations

import pandas as pd

from market_data_hub.sources import bis

_COUNTRIES = [
    {"iso2": "US", "iso3": "USA"},
    {"iso2": "IT", "iso3": "ITA", "euro": True},
    {"iso2": "FR", "iso3": "FRA", "euro": True},
]


def test_period_end_recognized_formats():
    assert bis._period_end("2025-Q4") == pd.Period("2025Q4", freq="Q").end_time.normalize()
    assert bis._period_end("2026-05") == pd.Period("2026-05", freq="M").end_time.normalize()
    assert bis._period_end("2025") == pd.Timestamp(2025, 12, 31)
    assert bis._period_end("garbage") is None


def test_fetch_bis_parses_dsr_csv(monkeypatch):
    csv_text = ("BORROWERS_CTY,TIME_PERIOD,OBS_VALUE\n"
                "US,2024-Q1,14.2\n"
                "GB,2024-Q1,12.0\n")   # GB not in our country list -> dropped

    def fake_get_csv(url, timeout, retries, base_sleep):
        return csv_text

    monkeypatch.setattr(bis, "_get_csv", fake_get_csv)
    spec = {"id": "private_dsr", "name": "Private DSR", "dataset": "WS_DSR",
            "code": "Q.{iso2}.P", "pillar": "credit", "orientation": 1,
            "bis_country_dim": "BORROWERS_CTY"}
    df = bis.fetch_bis(spec, _COUNTRIES, start_year=2020)

    assert list(df.columns) == bis._COLS
    assert len(df) == 1
    assert df.iloc[0]["country_iso3"] == "USA"
    assert df.iloc[0]["value"] == 14.2
    assert (df["source"] == "bis").all()


def test_fetch_bis_broadcasts_euro_aggregate(monkeypatch):
    csv_text = "REF_AREA,TIME_PERIOD,OBS_VALUE\nXM,2024-06,3.75\n"

    def fake_get_csv(url, timeout, retries, base_sleep):
        return csv_text

    monkeypatch.setattr(bis, "_get_csv", fake_get_csv)
    spec = {"id": "bis_policy_rate", "name": "Policy rate", "dataset": "WS_CBPOL",
            "code": "M.{iso2}", "pillar": "liquidity", "orientation": 0,
            "bis_country_dim": "REF_AREA", "euro_aggregate": "XM"}
    df = bis.fetch_bis(spec, _COUNTRIES, start_year=2020)

    # the aggregate is replicated onto every euro member, not onto USA
    assert set(df["country_iso3"]) == {"ITA", "FRA"}
    assert (df["value"] == 3.75).all()


def test_fetch_bis_missing_iso2_template_returns_empty():
    spec = {"id": "x", "name": "x", "dataset": "WS_DSR", "code": "Q.P",
            "pillar": "credit"}
    df = bis.fetch_bis(spec, _COUNTRIES)
    assert df.empty
    assert list(df.columns) == bis._COLS
