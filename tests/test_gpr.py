"""Offline GPR workbook and ingestion contracts."""

import sys
from pathlib import Path

import pytest

import run_gpr
from market_data_hub.config_loader import get_gpr_series
from market_data_hub.db.connection import get_conn
from market_data_hub.sources import gpr

FIXTURES = Path(__file__).parent / "fixtures" / "gpr"


def test_parse_dates_and_gaps():
    daily = gpr.parse((FIXTURES / "daily.xls").read_bytes(), "D", get_gpr_series())
    monthly = gpr.parse((FIXTURES / "monthly.xls").read_bytes(), "M", get_gpr_series())
    assert list(
        daily.loc[daily.series_id == "GPRD", "date"].dt.strftime("%Y-%m-%d")
    ) == ["2024-01-01", "2024-01-03"]
    assert len(daily.loc[daily.series_id == "GPRD_THREAT"]) == 1
    assert set(monthly.series_id) == {"GPR", "GPRA", "GPRT"}
    assert monthly.date.max().strftime("%Y-%m-%d") == "2024-02-01"


def test_malformed_file():
    with pytest.raises(ValueError, match="Invalid GPR D workbook"):
        gpr.parse(b"not an Excel file", "D", get_gpr_series())


def test_runner_idempotent_and_no_future_rows(monkeypatch, tmp_db):
    def fetch(freq, metadata):
        return gpr.parse(
            (FIXTURES / ("daily.xls" if freq == "D" else "monthly.xls")).read_bytes(),
            freq,
            metadata,
        )

    monkeypatch.setattr(gpr, "fetch", fetch)
    monkeypatch.setattr(sys, "argv", ["run_gpr.py", "--db", tmp_db])
    assert run_gpr.main() == 0
    assert run_gpr.main() == 0
    with get_conn(tmp_db, read_only=True) as con:
        assert (
            con.execute(
                "SELECT count(*) FROM macro_series WHERE source='gpr'"
            ).fetchone()[0]
            == 11
        )
        assert (
            con.execute("SELECT max(date) FROM macro_series WHERE series_id='GPRD'")
            .fetchone()[0]
            .isoformat()
            == "2024-01-03"
        )
        assert (
            con.execute("SELECT max(date) FROM macro_series WHERE series_id='GPR'")
            .fetchone()[0]
            .isoformat()
            == "2024-02-01"
        )
        assert (
            con.execute(
                "SELECT count(*) FROM macro_series_vintage WHERE source='gpr'"
            ).fetchone()[0]
            == 11
        )
        assert (
            con.execute(
                "SELECT count(*) FROM macro_series WHERE source='gpr' AND updated_at IS NULL"
            ).fetchone()[0]
            == 0
        )


def test_download_uses_certifi_and_timeout(monkeypatch):
    class Response:
        content = (FIXTURES / "daily.xls").read_bytes()

        def raise_for_status(self):
            pass

    def get(url, **kwargs):
        assert url == gpr.URLS["D"]
        assert kwargs["verify"] == gpr.certifi.where()
        assert kwargs["timeout"] == 60
        assert "market-data-hub" in kwargs["headers"]["User-Agent"]
        return Response()

    monkeypatch.setattr(gpr.requests, "get", get)
    assert not gpr.fetch("D", get_gpr_series()).empty


def test_runner_lock_held(monkeypatch, tmp_db):
    from contextlib import contextmanager

    @contextmanager
    def locked(path):
        raise run_gpr.DBLockTimeout("locked")
        yield

    monkeypatch.setattr(run_gpr, "db_write_lock", locked)
    monkeypatch.setattr(sys, "argv", ["run_gpr.py", "--db", tmp_db])
    assert run_gpr.main() == 9
