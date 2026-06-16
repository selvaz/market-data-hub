# -*- coding: utf-8 -*-
"""Fama-French factor layer: CSV parser + factor_returns storage/reads."""
from __future__ import annotations

import datetime as dt

import pandas as pd

from market_data_hub.sources.factors import _parse_french_csv, CATALOG
from market_data_hub.db.connection import get_conn
from market_data_hub.db.upsert import upsert
from market_data_hub import reader as R

# Mimics a Ken French daily CSV: preamble, header, daily block, blank line,
# then an annual block that must be ignored. Values are in percent.
_SAMPLE = """This file was created by the Ken French Data Library.

,Mkt-RF,SMB,HML,RMW,CMA,RF
19630701,  0.10, -0.20,  0.30,  0.05, -0.01, 0.012
19630702, -0.50,  0.40, -0.10,  0.02,  0.03, 0.012
20231229,  1.00,  0.00, -99.99, 0.10,  0.20, 0.020

  Annual Factors: January-December
,Mkt-RF,SMB,HML,RMW,CMA,RF
1964, 12.0, 3.0, -2.0, 1.0, 0.5, 3.5
"""


def test_parse_french_csv_first_block_only():
    df = _parse_french_csv(_SAMPLE, "FF5_daily", "D")
    # 3 daily dates x 6 factors, annual block excluded
    assert df["date"].nunique() == 3
    assert set(df["factor"]) == {"Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"}
    assert df["date"].max() == dt.date(2023, 12, 29)
    assert df["date"].min() == dt.date(1963, 7, 1)
    # percent -> decimal conversion
    v = df[(df.date == dt.date(1963, 7, 1)) & (df.factor == "Mkt-RF")]["value"].iloc[0]
    assert abs(v - 0.001) < 1e-12
    # -99999 sentinel dropped (HML on the last day)
    assert df[(df.date == dt.date(2023, 12, 29)) & (df.factor == "HML")].empty


def test_catalog_shapes():
    assert "FF5_daily" in CATALOG and CATALOG["FF5_daily"]["frequency"] == "D"


def test_factor_returns_roundtrip(tmp_db):
    con = get_conn()
    df = _parse_french_csv(_SAMPLE, "FF5_daily", "D")
    added, _ = upsert(con, "factor_returns", df)
    assert added == len(df)
    # idempotent re-upsert
    a2, u2 = upsert(con, "factor_returns", df)
    assert (a2, u2) == (0, len(df))
    con.commit()
    con.close()

    wide = R.read_factors(factor_set="FF5_daily")
    assert "Mkt-RF" in wide.columns and "RF" in wide.columns
    assert wide.shape[0] == 3                       # 3 dates
    long = R.read_factors(factors="SMB", wide=False)
    assert set(long["factor"]) == {"SMB"}
