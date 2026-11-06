"""Offline tests for the FRED metadata capture and public read path."""

import json
from pathlib import Path

import duckdb
import pytest

from market_data_hub import catalog, fred_metadata, reader
from market_data_hub.db import connection
from market_data_hub.db.connection import get_conn


@pytest.fixture
def response_data():
    return json.loads(
        (Path(__file__).parent / "fixtures" / "fred_series_gdpc1.json").read_text()
    )


def test_capture_cache_versions_and_catalog(tmp_path, monkeypatch, response_data):
    db = tmp_path / "hub.duckdb"
    monkeypatch.setattr(fred_metadata, "get_fred_series", lambda: [{"symbol": "GDPC1"}])

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return response_data

    class Session:
        calls = 0

        def get(self, url, *, params, timeout):
            assert params["series_id"] == "GDPC1"
            self.calls += 1
            return Response()

    client = Session()
    con = get_conn(str(db))
    assert fred_metadata.refresh(con, "test-key", session=client) == []
    assert fred_metadata.refresh(con, "test-key", session=client) == []
    assert client.calls == 1
    response_data["seriess"][0]["title"] = "Revised title"
    assert fred_metadata.refresh(con, "test-key", max_age_days=0, session=client) == []
    assert con.execute("SELECT count(*) FROM fred_series_metadata").fetchone()[0] == 2
    con.close()
    result = reader.read_fred_series_metadata(str(db)).iloc[0]
    assert result["title"] == "Revised title"
    assert result["units"] == response_data["seriess"][0]["units"]
    row = catalog.list_macro_series(with_coverage=False, db_path=str(db))
    row = row.set_index("series_id").loc["GDPC1"]
    assert row["unit"] == result["units"]
    assert row["seasonal_adjustment"] == result["seasonal_adjustment"]


def test_missing_table_and_bad_response(tmp_path, monkeypatch):
    db = tmp_path / "old.duckdb"
    duckdb.connect(str(db)).close()
    assert reader.read_fred_series_metadata(str(db)).empty
    with pytest.raises(FileNotFoundError):
        reader.read_fred_series_metadata(str(tmp_path / "missing.duckdb"))
    assert not (tmp_path / "missing.duckdb").exists()
    missing = catalog.list_macro_series(
        with_coverage=False, db_path=str(tmp_path / "missing.duckdb")
    )
    assert missing["fred_metadata_status"].eq("unavailable: FileNotFoundError").all()
    assert not (tmp_path / "missing.duckdb").exists()
    monkeypatch.setattr(fred_metadata, "get_fred_series", lambda: [{"symbol": "GDPC1"}])

    class BadSession:
        def get(self, *args, **kwargs):
            raise fred_metadata.requests.ConnectionError("secret")

    con = get_conn(str(db))
    assert fred_metadata.refresh(con, "test-key", session=BadSession()) == ["GDPC1"]
    assert con.execute("SELECT count(*) FROM fred_series_metadata").fetchone()[0] == 0
    con.close()


def test_reader_waits_for_lock_and_catalog_degrades(tmp_path, monkeypatch):
    db = tmp_path / "hub.duckdb"
    get_conn(str(db)).close()
    original = duckdb.connect
    attempts = []

    def locked_once(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise duckdb.IOException("Conflicting lock")
        return original(*args, **kwargs)

    monkeypatch.setattr(connection.duckdb, "connect", locked_once)
    monkeypatch.setattr(connection, "_READER_LOCK_WAIT_S", 1)
    monkeypatch.setattr(connection, "_READER_LOCK_POLL_S", 0.01)
    assert reader.read_fred_series_metadata(str(db)).empty
    assert len(attempts) == 2

    def locked(*args, **kwargs):
        raise duckdb.IOException("Conflicting lock")

    monkeypatch.setattr(connection.duckdb, "connect", locked)
    monkeypatch.setattr(connection, "_READER_LOCK_WAIT_S", 0)
    result = catalog.list_macro_series(with_coverage=False, db_path=str(db))
    assert not result.empty
    assert result["units"].isna().all()
    assert result["fred_metadata_status"].eq("unavailable: IOException").all()


def test_legacy_timezone_metadata_can_be_read(tmp_path, response_data):
    db = tmp_path / "legacy.duckdb"
    con = get_conn(str(db))
    con.execute(
        "CREATE TABLE legacy AS SELECT * REPLACE "
        "(CAST(retrieved_at AS TIMESTAMPTZ) AS retrieved_at) FROM fred_series_metadata"
    )
    con.execute("DROP TABLE fred_series_metadata")
    con.execute("ALTER TABLE legacy RENAME TO fred_series_metadata")
    item = response_data["seriess"][0]
    fields = fred_metadata.FIELDS
    placeholders = ", ".join("?" for _ in range(len(fields) + 2))
    con.execute(
        f"INSERT INTO fred_series_metadata VALUES ({placeholders})",
        ["GDPC1", "2026-11-05 12:00:00+00", *(item[field] for field in fields)],
    )
    con.close()
    result = reader.read_fred_series_metadata(str(db))
    assert isinstance(result.iloc[0]["retrieved_at"], str)
    assert result.iloc[0]["units"] == item["units"]
