"""Offline EIA v2 response and ingestion contracts."""

import sys

import pytest

import run_eia_inventories
from market_data_hub.config_loader import get_eia_series
from market_data_hub.db.connection import get_conn
from market_data_hub.sources import eia


class Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def install_response(monkeypatch, payload):
    def get(url, params, timeout):
        assert url.startswith("https://api.eia.gov/v2/")
        return Response(payload)

    monkeypatch.setattr(eia.requests, "get", get)


@pytest.mark.parametrize("meta", get_eia_series(), ids=lambda e: e["symbol"])
def test_parse_units_and_cadence(monkeypatch, meta):
    monkeypatch.setenv("EIA_API_KEY", "test-secret")
    unit = (
        "thousand barrels"
        if meta["unit"] == "Thousand Barrels"
        else "billion cubic feet"
    )
    install_response(
        monkeypatch,
        {
            "response": {
                "total": 1,
                "data": [
                    {
                        "period": "2026-10-23",
                        "series": meta["symbol"],
                        "value": "1234",
                        "units": unit,
                    },
                ],
            }
        },
    )
    frame = eia.fetch("2026-01-01", "2026-12-31", meta=meta)
    assert frame.iloc[0]["value"] == 1234
    assert frame.iloc[0]["unit"] == meta["unit"]
    assert frame.iloc[0]["frequency"] == meta["publication_cadence"] == "W"
    assert meta["publication_day"] == (
        "Thursday" if meta["unit"] == "Bcf" else "Wednesday"
    )


def test_transport_failure_never_exposes_credential(monkeypatch):
    secret = "sentinel-test-secret"
    monkeypatch.setenv("EIA_API_KEY", secret)

    def broken(url, params, timeout):
        raise OSError("failed URL with " + secret)

    monkeypatch.setattr(eia.requests, "get", broken)
    with pytest.raises(eia.EIARequestError) as exc:
        eia.fetch("2026-01-01", "2026-12-31", meta=get_eia_series()[0])
    assert secret not in str(exc.value)
    assert "HTTP unavailable; series WCESTUS1" == str(exc.value)
    assert exc.value.__suppress_context__


def test_missing_key_exits_before_opening_db(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("EIA_API_KEY", raising=False)
    monkeypatch.setattr(
        sys, "argv", ["run_eia_inventories.py", "--db", str(tmp_path / "hub.db")]
    )
    assert run_eia_inventories.main() == 1
    assert "EIA_API_KEY is required" in capsys.readouterr().err
    assert not (tmp_path / "hub.db").exists()


def test_runner_writes_observations_and_vintages(monkeypatch, tmp_db):
    monkeypatch.setenv("EIA_API_KEY", "test-secret")
    meta = get_eia_series()[0]
    monkeypatch.setattr(run_eia_inventories, "get_eia_series", lambda: [meta])
    install_response(
        monkeypatch,
        {
            "response": {
                "total": 1,
                "data": [
                    {
                        "period": "2026-10-23",
                        "series": meta["symbol"],
                        "value": 1234,
                        "units": "thousand barrels",
                    },
                ],
            }
        },
    )
    monkeypatch.setattr(sys, "argv", ["run_eia_inventories.py", "--db", tmp_db])
    assert run_eia_inventories.main() == 0
    with get_conn(tmp_db, read_only=True) as con:
        assert (
            con.execute(
                "SELECT count(*) FROM macro_series WHERE source='eia'"
            ).fetchone()[0]
            == 1
        )
        assert (
            con.execute(
                "SELECT count(*) FROM macro_series_vintage WHERE source='eia'"
            ).fetchone()[0]
            == 1
        )
        assert (
            con.execute(
                "SELECT status FROM download_log WHERE source='eia'"
            ).fetchone()[0]
            == "ok"
        )
