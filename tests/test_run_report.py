# -*- coding: utf-8 -*-
"""send_telegram_run_report.py — the report must degrade, not crash, on a
pre-v3 database whose vintage tables lack run_id/change_type/prior_value
(collect_report opens read-only and never runs migrations)."""
from __future__ import annotations

import pytest

pytest.importorskip("lazytools", reason="report script imports the Telegram connector at module level")

import send_telegram_run_report as srr  # noqa: E402

from market_data_hub.db.connection import get_conn  # noqa: E402


def test_country_updates_degrades_gracefully_on_pre_v3_db(tmp_db):
    con = get_conn()  # fresh DB is created at the current (v3) shape
    # Simulate a v2 database: recreate the vintage tables without the
    # change-tracking columns (DuckDB disallows DROP COLUMN under indexes).
    con.execute("DROP TABLE macro_series_vintage")
    con.execute("DROP TABLE macro_panel_vintage")
    con.execute("""
        CREATE TABLE macro_series_vintage (
            date DATE NOT NULL, series_id VARCHAR NOT NULL, value DOUBLE,
            vintage_date DATE NOT NULL, source VARCHAR,
            PRIMARY KEY (date, series_id, vintage_date))
    """)
    con.execute("""
        CREATE TABLE macro_panel_vintage (
            date DATE NOT NULL, country_iso3 VARCHAR NOT NULL,
            indicator_id VARCHAR NOT NULL, value DOUBLE,
            vintage_date DATE NOT NULL, source VARCHAR,
            PRIMARY KEY (date, country_iso3, indicator_id, vintage_date))
    """)
    con.commit()

    text = srr._country_updates(con, "some_run_id")
    con.close()

    assert "change tracking unavailable" in text
    assert "Country updates" in text        # the section header still renders


def test_country_updates_without_run_id():
    assert "no run_id available" in srr._country_updates(None, None)
