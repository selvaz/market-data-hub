from pathlib import Path

from market_data_hub.db.connection import get_conn
from market_data_hub.ssga_carry import collect, parse_workbook

FIXTURE = Path(__file__).parent / "fixtures/ssga_product_data.xlsx"


def test_recorded_workbook_units_and_dates():
    rows = parse_workbook(FIXTURE.read_bytes())
    assert set(rows) == {"BIL", "BWX", "SPAB"}
    as_of, bil = rows["BIL"]
    assert as_of.isoformat() == "2026-11-02"
    assert bil["yield_to_maturity"][0] == 0.0403
    assert bil["effective_duration"][0] == 0.15
    assert bil["sec_yield_30d"][0] == 0.0383
    assert bil["holdings_count"][0] == 33
    assert rows["SPAB"][1]["average_coupon"][0] == 0.0401


def test_one_workbook_per_day_and_stored_metadata(tmp_path, monkeypatch):
    from market_data_hub import ssga_carry

    class Response:
        content = FIXTURE.read_bytes()

        def raise_for_status(self):
            pass

    calls = []
    monkeypatch.setattr(
        ssga_carry.requests, "get", lambda *a, **k: calls.append(a) or Response()
    )
    con = get_conn(str(tmp_path / "hub.duckdb"))
    assert collect(con, log=lambda _: None) == 24
    assert collect(con, log=lambda _: None) == 0
    assert len(calls) == 1
    assert con.execute(
        "SELECT DISTINCT proxy_symbol FROM etf_carry_snapshots WHERE symbol='AGG'"
    ).fetchall() == [("SPAB",)]
    assert (
        con.execute(
            "SELECT source_as_of, index_currency FROM etf_carry_snapshots WHERE symbol='BIL' LIMIT 1"
        ).fetchone()[1]
        == "USD"
    )
    con.close()
