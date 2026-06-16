# -*- coding: utf-8 -*-
"""Cross-country macro_panel coverage scoring + coverage-based source selection."""
from __future__ import annotations

import datetime as dt

import pandas as pd

from market_data_hub.db.connection import get_conn
from market_data_hub.db.upsert import upsert
from market_data_hub.coverage.report import rebuild_macro_panel_coverage
from market_data_hub import reader as R
from market_data_hub.sources import macro_panel as mp


def _panel_row(iid, ctry, year, value=1.0, freq="A", source="wb", pillar="growth"):
    return {"date": dt.date(year, 12, 31), "country_iso3": ctry, "indicator_id": iid,
            "value": value, "indicator_name": iid, "pillar": pillar, "orientation": 1,
            "source": source, "provider_dataset": "X", "provider_code": "Y",
            "unit": "pct", "frequency": freq}


# ---- cross-country coverage scoring ----

def test_panel_coverage_scores_countries_and_stalled(tmp_db):
    con = get_conn()
    rows = []
    # fresh indicator: 3 countries, latest 2025
    for c in ["USA", "ITA", "CHE"]:
        for y in (2023, 2024, 2025):
            rows.append(_panel_row("fresh_ind", c, y))
    # stale indicator: 2 countries, latest 2018 -> stalled
    for c in ["USA", "DEU"]:
        for y in (2016, 2017, 2018):
            rows.append(_panel_row("stale_ind", c, y))
    upsert(con, "macro_panel", pd.DataFrame(rows))
    n = rebuild_macro_panel_coverage(con, "t", n_countries_total=10)
    con.commit(); con.close()

    assert n == 2
    cov = R.get_macro_panel_coverage().set_index("indicator_id")
    assert cov.loc["fresh_ind", "n_countries"] == 3
    assert cov.loc["fresh_ind", "coverage_pct"] == 30.0      # 3/10
    assert cov.loc["fresh_ind", "status"] == "ok"
    assert cov.loc["stale_ind", "n_countries"] == 2
    assert bool(cov.loc["stale_ind", "stalled"]) is True
    assert cov.loc["stale_ind", "status"] == "stalled"


# ---- coverage-based source selection ----

def test_country_coverage_counts_distinct_nonnull():
    df = pd.DataFrame([_panel_row("x", "USA", 2024), _panel_row("x", "ITA", 2024)])
    assert mp._country_coverage(df) == 2
    assert mp._country_coverage(pd.DataFrame()) == 0


def test_select_best_picks_wider_source(monkeypatch):
    # primary (IMF) covers 1 country; fallback (WB) covers 3
    primary = pd.DataFrame([_panel_row("ind", "USA", 2024, source="imf")])
    fallback = pd.DataFrame([_panel_row("ind", c, 2024, source="wb")
                             for c in ["USA", "ITA", "CHE"]])

    def fake_fetch(source, spec, countries, *, start_year, http):
        return primary if source == "IMF" else fallback

    monkeypatch.setattr(mp, "_fetch_one", fake_fetch)
    spec = {"id": "ind", "source": "IMF", "fallback": {"source": "WB", "code": "Z"}}

    # default: primary non-empty -> used as-is
    _, src, status = mp.fetch_indicator(spec, [], start_year=2000, http={})
    assert status == "ok" and src == "IMF"

    # select_best: fallback's wider coverage wins
    df, src, status = mp.fetch_indicator(spec, [], start_year=2000, http={}, select_best=True)
    assert status == "fallback" and "WB" in src
    assert df["country_iso3"].nunique() == 3
