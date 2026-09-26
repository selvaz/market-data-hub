# -*- coding: utf-8 -*-
"""Offline contract tests for CFTC historical ZIP parsing and backfill."""
from __future__ import annotations

import csv
import io
import sys
import zipfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional

import pandas as pd
import pytest

from market_data_hub.db import connection as cx
from market_data_hub.db.upsert import upsert
from market_data_hub.sources import cftc_cot, cftc_history


def _headers(report: str) -> List[str]:
    if report == "tff":
        special = {
            "report_date_as_yyyy_mm_dd": "Report_Date_as_YYYY-MM-DD",
            "asset_mgr_positions_long": "Asset_Mgr_Positions_Long_All",
            "asset_mgr_positions_short": "Asset_Mgr_Positions_Short_All",
            "asset_mgr_positions_spread": "Asset_Mgr_Positions_Spread_All",
            "lev_money_positions_long": "Lev_Money_Positions_Long_All",
            "lev_money_positions_short": "Lev_Money_Positions_Short_All",
            "lev_money_positions_spread": "Lev_Money_Positions_Spread_All",
            "other_rept_positions_long": "Other_Rept_Positions_Long_All",
            "other_rept_positions_short": "Other_Rept_Positions_Short_All",
            "other_rept_positions_spread": "Other_Rept_Positions_Spread_All",
        }
        headers = [special.get(field, field.upper())
                   for field in cftc_cot._TFF_RENAME]
        return ["Market_and_Exchange_Names", "CFTC_Contract_Market_Code",
                "Open_Interest_All"] + headers + ["FutOnly_or_Combined"]
    special_legacy = {
        "report_date_as_yyyy_mm_dd": "As of Date in Form YYYY-MM-DD",
        "noncomm_positions_long_all": "Noncommercial Positions-Long (All)",
        "noncomm_positions_short_all": "Noncommercial Positions-Short (All)",
        "noncomm_postions_spread_all": "Noncommercial Positions-Spreading (All)",
        "comm_positions_long_all": "Commercial Positions-Long (All)",
        "comm_positions_short_all": "Commercial Positions-Short (All)",
        "tot_rept_positions_long_all": "Total Reportable Positions-Long (All)",
        "tot_rept_positions_short": "Total Reportable Positions-Short (All)",
        "nonrept_positions_long_all": "Nonreportable Positions-Long (All)",
        "nonrept_positions_short_all": "Nonreportable Positions-Short (All)",
    }
    headers = [special_legacy.get(field, field.upper())
               for field in cftc_cot._LEGACY_RENAME]
    return ["Market and Exchange Names", "CFTC Contract Market Code",
            "Open Interest (All)"] + headers


def _make_zip(path: Path, report: str, rows: List[Dict[str, str]],
              *, headers: Optional[List[str]] = None) -> Path:
    headers = headers or _headers(report)
    csv_text = io.StringIO(newline="")
    writer = csv.DictWriter(csv_text, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("fixture.txt", csv_text.getvalue())
    return path


def _tff_row(report_date: str = "2026-09-22",
             code: str = "043602", dealer_long: str = "46404") -> Dict[str, str]:
    row = {header: "1" for header in _headers("tff")}
    row.update({
        "Market_and_Exchange_Names": "10-YEAR U.S. TREASURY",
        "CFTC_Contract_Market_Code": code,
        "Open_Interest_All": "2813176",
        "Report_Date_as_YYYY-MM-DD": report_date,
        "DEALER_POSITIONS_LONG_ALL": dealer_long,
        "FutOnly_or_Combined": "FutOnly",
    })
    return row


def _legacy_row(report_date: str = "2025-12-30",
                code: str = "067651") -> Dict[str, str]:
    row = {header: "2" for header in _headers("legacy")}
    row.update({
        "Market and Exchange Names": "CRUDE OIL, LIGHT SWEET - NYMEX",
        "CFTC Contract Market Code": code,
        "Open Interest (All)": "1800123",
        "As of Date in Form YYYY-MM-DD": report_date,
        "Noncommercial Positions-Long (All)": "402100",
        "Noncommercial Positions-Short (All)": "215900",
        "Noncommercial Positions-Spreading (All)": "164300",
        "Commercial Positions-Long (All)": "810400",
        "Commercial Positions-Short (All)": "1043500",
    })
    return row


def test_tff_historical_zip_maps_to_existing_connector_columns(tmp_path):
    archive = _make_zip(tmp_path / "tff.zip", "tff", [
        _tff_row(),
        _tff_row("12/27/2016 12:00:00 AM", code="090741"),
    ])

    assert cftc_history.count_archive_rows(
        archive, "tff", date(2006, 1, 1), date(2026, 12, 31)) == {
            2016: 1, 2026: 1}
    frames = list(cftc_history.iter_archive_batches(
        archive, "tff", date(2006, 1, 1), date(2026, 12, 31)))

    assert len(frames) == 1
    frame = frames[0]
    assert list(frame.columns) == cftc_cot._TFF_COLS
    assert frame.loc[0, "report_date"].date() == date(2026, 9, 22)
    assert frame.loc[0, "cftc_contract_market_code"] == "043602"
    assert frame.loc[0, "dealer_long"] == 46404
    assert frame.loc[0, "asset_mgr_long"] == 1
    assert frame.loc[0, "source"] == "cftc_tff"
    assert frame.loc[1, "report_date"].date() == date(2016, 12, 27)


def test_legacy_historical_zip_maps_old_space_delimited_headers(tmp_path):
    archive = _make_zip(tmp_path / "legacy.zip", "legacy", [_legacy_row()])

    frame = next(cftc_history.iter_archive_batches(
        archive, "legacy", date(2006, 1, 1), date(2026, 12, 31)))

    assert list(frame.columns) == cftc_cot._LEGACY_COLS
    assert frame.loc[0, "report_date"].date() == date(2025, 12, 30)
    assert frame.loc[0, "contract_market_name"].startswith("CRUDE OIL")
    assert frame.loc[0, "cftc_contract_market_code"] == "067651"
    assert frame.loc[0, "noncomm_spread"] == 164300
    assert frame.loc[0, "comm_short"] == 1043500
    assert frame.loc[0, "commodity_name"] is None or pd.isna(
        frame.loc[0, "commodity_name"])
    assert frame.loc[0, "source"] == "cftc_legacy"


def test_upsert_is_idempotent_and_refreshes_updated_at_for_overlap(tmp_path):
    archive = _make_zip(tmp_path / "tff.zip", "tff", [_tff_row()])
    frame = next(cftc_history.iter_archive_batches(
        archive, "tff", date(2026, 1, 1), date(2026, 12, 31)))
    db_path = tmp_path / "cftc-test.duckdb"
    con = cx.get_conn(str(db_path))
    try:
        existing = frame.copy()
        existing["dealer_long"] = 40000
        existing["updated_at"] = pd.Timestamp("2026-09-23 00:00:00")
        assert upsert(con, "cftc_tff_positioning", existing) == (1, 0)

        first_updated = con.execute(
            "SELECT updated_at FROM cftc_tff_positioning "
            "WHERE report_date = DATE '2026-09-22' AND "
            "cftc_contract_market_code = '043602'").fetchone()[0]
        added, updated = upsert(con, "cftc_tff_positioning", frame)
        assert (added, updated) == (0, 1)
        second_updated, dealer_long, variant = con.execute(
            "SELECT updated_at, dealer_long, report_variant "
            "FROM cftc_tff_positioning "
            "WHERE report_date = DATE '2026-09-22' AND "
            "cftc_contract_market_code = '043602'").fetchone()
        assert dealer_long == 46404
        assert variant == "futures_only"
        assert second_updated >= first_updated

        third_added, third_updated = upsert(con, "cftc_tff_positioning", frame)
        assert (third_added, third_updated) == (0, 1)
        count = con.execute(
            "SELECT COUNT(*) FROM cftc_tff_positioning "
            "WHERE report_date = DATE '2026-09-22' AND "
            "cftc_contract_market_code = '043602'").fetchone()[0]
        assert count == 1
    finally:
        con.close()


def test_backfill_preserves_socrata_classification_and_updates_positions(
        tmp_path, monkeypatch):
    import run_cftc_cot

    monkeypatch.setattr(
        run_cftc_cot, "_today", lambda: date(2026, 9, 26))

    fixture_date = date(2025, 1, 6)
    archive = _make_zip(
        tmp_path / "overlap.zip", "tff",
        [_tff_row(fixture_date.isoformat(), dealer_long="46404")])
    archived_frame = next(cftc_history.iter_archive_batches(
        archive, "tff", fixture_date, fixture_date))

    existing = archived_frame.copy()
    existing["commodity_name"] = "Financial"
    existing["commodity_subgroup_name"] = "Treasury"
    existing["dealer_long"] = 40000
    db_path = tmp_path / "cftc-overlap.duckdb"
    con = cx.get_conn(str(db_path))
    try:
        assert upsert(con, "cftc_tff_positioning", existing) == (1, 0)
    finally:
        con.close()

    spec = cftc_history.ArchiveSpec(
        "tff", fixture_date.year, fixture_date.year,
        f"fut_fin_txt_{fixture_date.year}.zip")
    monkeypatch.setattr(
        cftc_history, "historical_archive_plan",
        lambda _report, _start, _end: [spec])
    monkeypatch.setattr(
        cftc_history, "download_archive",
        lambda _url, destination: _copy_fixture(archive, destination))

    args = SimpleNamespace(
        db=db_path, backfill_years=1, only="tff", dry_run=False)
    assert run_cftc_cot._run_historical_backfill(args) == 0

    con = cx.get_conn(str(db_path), read_only=True)
    try:
        commodity_name, subgroup_name, dealer_long = con.execute(
            "SELECT commodity_name, commodity_subgroup_name, dealer_long "
            "FROM cftc_tff_positioning WHERE report_date = ? "
            "AND cftc_contract_market_code = '043602'",
            [fixture_date],
        ).fetchone()
        assert (commodity_name, subgroup_name) == ("Financial", "Treasury")
        assert dealer_long == 46404
    finally:
        con.close()


@pytest.mark.parametrize("old_version", [21, 23])
def test_schema_migration_adds_nullable_variant_to_existing_cftc_rows(
        tmp_path, old_version):
    archive = _make_zip(tmp_path / "legacy.zip", "legacy", [_legacy_row()])
    frame = next(cftc_history.iter_archive_batches(
        archive, "legacy", date(2006, 1, 1), date(2026, 12, 31)))
    db_path = tmp_path / f"legacy-v{old_version}.duckdb"
    con = cx.get_conn(str(db_path))
    try:
        assert upsert(con, "cftc_legacy_positioning", frame) == (1, 0)
        con.execute("DROP INDEX idx_cftc_tff_contract")
        con.execute("DROP INDEX idx_cftc_legacy_contract")
        con.execute("ALTER TABLE cftc_tff_positioning DROP COLUMN report_variant")
        con.execute("ALTER TABLE cftc_legacy_positioning DROP COLUMN report_variant")
        con.execute(
            "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
            [str(old_version)])
    finally:
        con.close()

    con = cx.get_conn(str(db_path))
    try:
        assert cx.get_schema_version(con) == 24
        row = con.execute(
            "SELECT cftc_contract_market_code, report_variant "
            "FROM cftc_legacy_positioning").fetchone()
        assert row == ("067651", "futures_only")
        columns = {column[1]: column for column in con.execute(
            "PRAGMA table_info('cftc_legacy_positioning')").fetchall()}
        assert columns["report_variant"][3] == 0  # nullable
        assert columns["report_variant"][4] == "'futures_only'"
    finally:
        con.close()


def test_malformed_archive_is_rejected_before_a_write(tmp_path):
    archive = _make_zip(
        tmp_path / "bad.zip", "tff", [],
        headers=["not", "a", "CFTC", "COT", "file"])

    with pytest.raises(cftc_history.CFTCArchiveError, match="missing required"):
        cftc_history.count_archive_rows(
            archive, "tff", date(2026, 1, 1), date(2026, 12, 31))


def test_archive_batches_have_a_fixed_maximum_size(tmp_path):
    current_year = date.today().year
    rows = [
        _tff_row(f"{current_year}-01-{day:02d}", code=f"0436{day:02d}")
        for day in range(1, 8)
    ]
    archive = _make_zip(tmp_path / "many.zip", "tff", rows)

    batches = list(cftc_history.iter_archive_batches(
        archive, "tff", date(current_year, 1, 1),
        date(current_year, 12, 31), batch_size=2))

    assert [len(batch) for batch in batches] == [2, 2, 2, 1]
    assert max(map(len, batches)) <= 2


def test_cli_dry_run_reports_year_counts_without_opening_database(
        tmp_path, monkeypatch, capsys):
    import run_cftc_cot

    today = date(2026, 1, 5)
    monkeypatch.setattr(run_cftc_cot, "_today", lambda: today)
    fixture_date = date(2026, 1, 4).isoformat()
    archive = _make_zip(tmp_path / "fixture.zip", "tff",
                        [_tff_row(fixture_date)])
    monkeypatch.setattr(sys, "argv", [
        "run_cftc_cot.py", "--db", str(tmp_path / "never-open.duckdb"),
        "--backfill-years", "1", "--only", "tff", "--dry-run",
    ])
    monkeypatch.setattr(cftc_history, "download_archive",
                        lambda _url, destination: _copy_fixture(archive, destination))
    monkeypatch.setattr(
        cx, "get_conn",
        lambda *_args, **_kwargs: pytest.fail("dry-run must not open the DB"))

    assert run_cftc_cot.main() == 0
    output = capsys.readouterr().out
    assert f"{today.year}: 1 would upsert rows" in output
    assert "no database connection opened" in output
    assert not (tmp_path / "never-open.duckdb").exists()


def _copy_fixture(source: Path, destination: Path) -> Path:
    destination.write_bytes(source.read_bytes())
    return destination
