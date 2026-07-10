# -*- coding: utf-8 -*-
"""Regression tests for the sources audit fixes (P3.2/P3.3/P3.5/P3.6/P1.5
batch) — no lazyhmm dependency, always runnable (incl. CI).

Covers: investor-base lock+vintage, IMF SDMX UNIT_MULT scaling,
fetch-failure logging and strict ECB period parsing. The dalio.py-specific
cases from this batch (orientation-0 exclusion, policy-rate vs implied-rate
split, deleveraging quality) moved to the LazyRay repo along with dalio.py
itself -- see LazyRay's tests/test_dalio_audit_fixes.py.
"""
from __future__ import annotations

import datetime as dt
import logging
import sys
from datetime import datetime, timezone

import duckdb
import pandas as pd

from market_data_hub.sources import ecb
from market_data_hub.sources import imf_sdmx

# ---------------------------------------------------------------------------
# import_investor_base.py — writer lock + macro_panel_vintage
# ---------------------------------------------------------------------------
def test_investor_base_records_vintage(tmp_db, monkeypatch, capsys):
    import import_investor_base as iib

    df = pd.DataFrame([{
        "date": dt.date(2024, 12, 31), "country_iso3": "USA",
        "indicator_id": iib.INDICATOR_ID, "value": 33.5,
        "indicator_name": iib.INDICATOR_NAME, "pillar": "markets",
        "orientation": -1, "source": "arslanalp_tsuda",
        "provider_dataset": "AT_investor_base", "provider_code": "manual_xlsx",
        "unit": "percent", "frequency": "Q",
        "updated_at": datetime.now(timezone.utc)}])
    monkeypatch.setattr(iib, "load", lambda p: df)
    monkeypatch.setattr(sys, "argv", ["import_investor_base.py", "dummy.xlsx"])
    assert iib.main() == 0

    con = duckdb.connect(tmp_db, read_only=True)
    try:
        assert con.execute(
            "SELECT count(*) FROM macro_panel "
            "WHERE indicator_id = 'nonresident_debt_share'").fetchone()[0] == 1
        vintage = con.execute(
            "SELECT value FROM macro_panel_vintage "
            "WHERE indicator_id = 'nonresident_debt_share'").fetchall()
        assert vintage == [(33.5,)]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# sources/imf_sdmx.py — UNIT_MULT scaling + failure logging
# ---------------------------------------------------------------------------
_SPEC = {"dataset": "IIP", "code": "{iso3}.NETAL_P.NIIP.USD.A", "id": "niip_usd",
         "name": "Net IIP (USD)", "pillar": "markets"}


def test_unit_mult_scales_obs_value(monkeypatch):
    text = ("COUNTRY,TIME_PERIOD,OBS_VALUE,UNIT_MULT\n"
            "USA,2024,5,6\n"          # millions -> x 10^6
            "USA,2023,7,\n"           # blank -> unscaled
            "USA,2022,3,N/A\n")       # non-numeric -> unscaled
    monkeypatch.setattr(imf_sdmx, "_get_csv", lambda *a, **k: text)
    df = imf_sdmx.fetch_imf_sdmx(_SPEC, [{"iso3": "USA"}], base_sleep=0)
    by_year = {d.year: v for d, v in zip(df["date"], df["value"])}
    assert by_year == {2024: 5e6, 2023: 7.0, 2022: 3.0}


def test_missing_unit_mult_column_unchanged(monkeypatch):
    text = "COUNTRY,TIME_PERIOD,OBS_VALUE\nUSA,2024,5\n"
    monkeypatch.setattr(imf_sdmx, "_get_csv", lambda *a, **k: text)
    df = imf_sdmx.fetch_imf_sdmx(_SPEC, [{"iso3": "USA"}], base_sleep=0)
    assert df["value"].tolist() == [5.0]


def test_imf_fetch_failure_is_logged(monkeypatch, caplog):
    def _boom(*a, **k):
        raise ConnectionError("dns exploded")

    monkeypatch.setattr(imf_sdmx, "_get_csv", _boom)
    with caplog.at_level(logging.WARNING, logger="market_data_hub.sources.imf_sdmx"):
        df = imf_sdmx.fetch_imf_sdmx(_SPEC, [{"iso3": "USA"}], base_sleep=0)
    assert df.empty
    msgs = [r.getMessage() for r in caplog.records]
    assert any("niip_usd" in m and "USA" in m and "dns exploded" in m for m in msgs)
    assert any("Net IIP (USD)" in m for m in msgs)   # human-readable name too


# ---------------------------------------------------------------------------
# sources/ecb.py — strict period parsing + failure logging
# ---------------------------------------------------------------------------
def test_ecb_period_end_recognized_formats():
    assert ecb._period_end("2026-05") == pd.Timestamp("2026-05-31")
    assert ecb._period_end("2026-Q1") == pd.Timestamp("2026-03-31")
    assert ecb._period_end("2026") == pd.Timestamp("2026-12-31")


def test_ecb_sub_monthly_period_not_binned_to_month_end(caplog):
    # a daily period keeps its own day instead of silently becoming month-end
    assert ecb._period_end("2026-05-04") == pd.Timestamp("2026-05-04")
    with caplog.at_level(logging.WARNING, logger="market_data_hub.sources.ecb"):
        assert ecb._period_end("2026-W18") is None
    assert any("2026-W18" in r.getMessage() for r in caplog.records)


def test_ecb_fetch_failure_is_logged(monkeypatch, caplog):
    def _boom(*a, **k):
        raise ConnectionError("proxy down")

    monkeypatch.setattr(ecb, "_get_csv", _boom)
    spec = {"dataset": "MIR", "code": "M.{iso2}.B.A2I.AM.R.A.2240.EUR.N",
            "id": "cost_borrowing_corp", "name": "Cost of borrowing, corporations",
            "pillar": "markets"}
    with caplog.at_level(logging.WARNING, logger="market_data_hub.sources.ecb"):
        df = ecb.fetch_ecb(spec, [{"iso2": "DE", "iso3": "DEU"}])
    assert df.empty
    msgs = [r.getMessage() for r in caplog.records]
    assert any("cost_borrowing_corp" in m and "proxy down" in m for m in msgs)
    assert any("Cost of borrowing, corporations" in m for m in msgs)


def test_ecb_fetch_happy_path_parses_csv(monkeypatch):
    # even the file that already covers ecb.py's failure path and its pure
    # _period_end() helper never exercised a successful CSV->DataFrame fetch
    text = ("REF_AREA,TIME_PERIOD,OBS_VALUE\n"
            "DE,2026-05,4.12\n"
            "FR,2026-05,4.30\n"
            "GB,2026-05,9.99\n")   # GB not in our country list -> dropped
    monkeypatch.setattr(ecb, "_get_csv", lambda *a, **k: text)
    spec = {"dataset": "MIR", "code": "M.{iso2}.B.A2I.AM.R.A.2240.EUR.N",
            "id": "cost_borrowing_corp", "name": "Cost of borrowing, corporations",
            "pillar": "markets", "orientation": 1, "unit": "percent"}
    countries = [{"iso2": "DE", "iso3": "DEU"}, {"iso2": "FR", "iso3": "FRA"}]

    df = ecb.fetch_ecb(spec, countries, start_year=2020)

    assert list(df.columns) == ecb._COLS
    assert set(df["country_iso3"]) == {"DEU", "FRA"}
    assert df[df["country_iso3"] == "DEU"].iloc[0]["value"] == 4.12
    assert (df["date"] == pd.Timestamp("2026-05-31").date()).all()
    assert (df["source"] == "ecb").all()
