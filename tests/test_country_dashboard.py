"""Neutral country-dashboard data and rendering tests."""
from __future__ import annotations

import datetime as dt

import pandas as pd

from market_data_hub import country_dashboard as dashboard
from market_data_hub.db.connection import get_conn
from market_data_hub.db.upsert import upsert


def _row(country, indicator, year, value, *, dataset="WDI", unit="percent"):
    return {
        "date": dt.date(year, 12, 31), "country_iso3": country,
        "indicator_id": indicator, "value": value, "indicator_name": indicator,
        "pillar": "neutral", "orientation": 0, "source": "test",
        "provider_dataset": dataset, "provider_code": indicator,
        "unit": unit, "frequency": "A",
    }


def test_dashboard_uses_eur_and_fresh_fuel_trade(tmp_db, monkeypatch):
    countries = [
        {"iso3": "ITA", "name": "Italy", "region_group": "Euro Area", "region_geo": "Europe",
         "income": "High income", "development": "DM", "euro": True, "eu": True,
         "fx_regime": "float"},
        {"iso3": "ARG", "name": "Argentina", "region_group": "LAC", "region_geo": "Americas",
         "income": "Upper middle income", "development": "EM", "fx_regime": "managed_float",
         "imf_program": True, "imf_program_type": "EFF"},
    ]
    monkeypatch.setattr(dashboard, "get_countries", lambda: countries)
    con = get_conn()
    rows = []
    for country, exports, imports in [("ITA", 30.0, 40.0), ("ARG", 25.0, 15.0)]:
        rows.extend([
            _row(country, "fuel_exports_share", 2025, exports),
            _row(country, "fuel_imports_share", 2025, imports),
            _row(country, "exports_gdp", 2025, 30.0),
            _row(country, "imports_gdp", 2025, 25.0),
            _row(country, "public_debt_gdp", 2025, 100.0 if country == "ITA" else 70.0, dataset="WEO"),
            _row(country, "inflation_avg_weo", 2025, 2.0 if country == "ITA" else 30.0, dataset="WEO"),
            _row(country, "fiscal_balance_gdp", 2025, -3.0, dataset="WEO"),
            _row(country, "current_account_gdp", 2025, 1.0, dataset="WEO"),
            _row(country, "gdp_growth_weo", 2025, 1.0, dataset="WEO"),
        ])
    upsert(con, "macro_panel", pd.DataFrame(rows))
    con.commit()
    data = dashboard.collect_dashboard(con, now=dt.datetime(2026, 7, 10, tzinfo=dt.timezone.utc))
    con.close()

    italy = data["countries"]["ITA"]
    argentina = data["countries"]["ARG"]
    assert "Currency: EUR" in italy["tags"]
    assert all("FX regime: float" not in tag for tag in italy["tags"])
    assert italy["fuel"]["status"] == "fresh"
    assert "Net fuel trade:" in italy["fuel"]["label"]
    assert "IMF arrangement: EFF" in argentina["tags"]
    html = dashboard.render_html(data)
    assert "Dalio" not in html
    assert "Country data dashboard" in html
    assert "<script" not in html
    assert '<details id="country-ITA"' in html


def test_stale_fuel_data_is_not_presented_as_exposure(tmp_db, monkeypatch):
    monkeypatch.setattr(dashboard, "get_countries", lambda: [
        {"iso3": "CYP", "name": "Cyprus", "region_group": "Euro Area", "region_geo": "Europe",
         "income": "High income", "development": "DM", "euro": True, "fx_regime": "float"},
    ])
    con = get_conn()
    rows = [_row("CYP", iid, 2020, 20.0) for iid in (
        "fuel_exports_share", "fuel_imports_share", "exports_gdp", "imports_gdp")]
    upsert(con, "macro_panel", pd.DataFrame(rows))
    con.commit()
    data = dashboard.collect_dashboard(con, now=dt.datetime(2026, 7, 10, tzinfo=dt.timezone.utc))
    con.close()

    cyprus = data["countries"]["CYP"]
    assert cyprus["fuel"]["status"] == "stale"
    assert not any(tag.startswith("Net fuel trade:") for tag in cyprus["tags"])
