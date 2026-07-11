# -*- coding: utf-8 -*-
"""Point-in-time / vintage integrity: revisions must not leak backwards."""
from __future__ import annotations

import datetime as dt

import pandas as pd

from market_data_hub.db.connection import get_conn
from market_data_hub.db.upsert import upsert, record_vintage
from market_data_hub import reader as R


def _ms_row(value):
    return pd.DataFrame([{
        "date": dt.date(2024, 3, 31), "series_id": "GDP", "value": value,
        "series_name": "GDP", "unit": "usd", "frequency": "Q",
        "source": "fred", "country": "US",
    }])


def test_macro_series_vintage_records_revisions(tmp_db):
    con = get_conn()
    # First release on 2024-04-30 = 100.0
    upsert(con, "macro_series", _ms_row(100.0))
    n1 = record_vintage(con, "macro_series", _ms_row(100.0), "2024-04-30")
    # Same value re-observed later -> no new vintage row
    n_same = record_vintage(con, "macro_series", _ms_row(100.0), "2024-05-30")
    # Revision on 2024-06-30 = 105.0
    upsert(con, "macro_series", _ms_row(105.0))
    n2 = record_vintage(con, "macro_series", _ms_row(105.0), "2024-06-30")
    con.commit()
    con.close()

    assert (n1, n_same, n2) == (1, 0, 1)

    # latest read = revised value
    assert R.read_macro("GDP")["GDP"].iloc[0] == 105.0
    # point-in-time: as known on 2024-05-15 (before the revision) = 100.0
    assert R.read_macro("GDP", asof="2024-05-15")["GDP"].iloc[0] == 100.0
    # before the first release -> nothing known yet
    assert R.read_macro("GDP", asof="2024-04-01").empty
    # after the revision -> 105.0
    assert R.read_macro("GDP", asof="2024-07-01")["GDP"].iloc[0] == 105.0


def test_same_day_revision_merges_day_level_metadata(tmp_db):
    """vintage_date is the vintage unit: a same-day re-observation REPLACES
    that day's row but must inherit the predecessor's change_type and
    prior_value, so the surviving row describes the whole day relative to the
    previous day's knowledge (not the intraday step)."""
    con = get_conn()

    def _vint(value, day, run):
        upsert(con, "macro_series", _ms_row(value))
        return record_vintage(con, "macro_series", _ms_row(value), day, run_id=run)

    def _day_row(day):
        return con.execute(
            "SELECT value, change_type, prior_value, run_id FROM macro_series_vintage "
            "WHERE series_id='GDP' AND vintage_date = ?", [day]).fetchall()

    # Day 1: first-ever observation, then revised twice the same day.
    assert _vint(100.0, "2024-04-30", "run_a") == 1
    assert _vint(105.0, "2024-04-30", "run_b") == 1   # same-day replace still counts
    assert _vint(103.0, "2024-04-30", "run_c") == 1
    assert _day_row("2024-04-30") == [(103.0, "new", None, "run_c")]
    # date is still NEW today (not 'revised'), end-of-day value survives

    # Day 2: known date revised twice -> prior_value keeps day-1 knowledge.
    assert _vint(110.0, "2024-06-30", "run_d") == 1
    assert _vint(112.0, "2024-06-30", "run_e") == 1
    assert _day_row("2024-06-30") == [(112.0, "revised", 103.0, "run_e")]
    # not (112, 'revised', prior=110): 110 was an intraday step, not knowledge

    con.commit()
    con.close()

    # as-of reads see end-of-day values only
    assert R.read_macro("GDP", asof="2024-05-15")["GDP"].iloc[0] == 103.0
    assert R.read_macro("GDP", asof="2024-07-15")["GDP"].iloc[0] == 112.0


def _mp_row(value):
    return pd.DataFrame([{
        "date": dt.date(2023, 12, 31), "country_iso3": "USA",
        "indicator_id": "public_debt_gdp", "value": value,
        "indicator_name": "debt", "pillar": "sovereign", "orientation": -1,
        "source": "imf", "provider_dataset": "WEO", "provider_code": "X",
        "unit": "pct", "frequency": "A",
    }])


def test_macro_panel_vintage_pit_read(tmp_db):
    con = get_conn()
    upsert(con, "macro_panel", _mp_row(120.0))
    record_vintage(con, "macro_panel", _mp_row(120.0), "2024-04-01")
    upsert(con, "macro_panel", _mp_row(123.5))
    record_vintage(con, "macro_panel", _mp_row(123.5), "2024-10-01")
    con.commit()
    con.close()

    # wide PIT read for a single indicator, pivoted by country
    early = R.read_macro_panel("public_debt_gdp", wide=True, asof="2024-06-01")
    late = R.read_macro_panel("public_debt_gdp", wide=True, asof="2024-12-01")
    assert early["USA"].iloc[0] == 120.0
    assert late["USA"].iloc[0] == 123.5
