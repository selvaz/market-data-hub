from pathlib import Path

import pytest
import requests

from market_data_hub.db.connection import get_conn
from market_data_hub.vaneck_carry import collect, parse_pdf

FIXTURE = Path(__file__).parent / "fixtures/vaneck_emlc_fact_sheet.pdf"


def test_fact_sheet_recorded_values():
    date, values = parse_pdf(FIXTURE.read_bytes())
    assert date.isoformat() == "2026-09-30"
    assert values["effective_duration"][0] == 5.09
    assert values["sec_yield_30d"][0] == pytest.approx(0.0646)
    assert values["holdings_count"][0] == 529
    assert "yield_to_maturity" not in values


def test_fund_failure_does_not_block_other_funds(tmp_path, monkeypatch):
    from market_data_hub import vaneck_carry

    class Response:
        def __init__(self, symbol):
            self.content = (
                FIXTURE.parent / f"vaneck_{symbol}_fact_sheet.pdf"
            ).read_bytes()

        def raise_for_status(self):
            pass

    def fetch(url, **kwargs):
        if "jp-morgan" in url:
            raise requests.HTTPError("unavailable")
        return Response("hyem" if "high-yield-bond-etf-hyem" in url else "ihy")

    monkeypatch.setattr(vaneck_carry.requests, "get", fetch)
    con = get_conn(str(tmp_path / "hub.duckdb"))
    assert collect(con, log=lambda _: None) == 6
    assert collect(con, log=lambda _: None) == 0
    assert (
        con.execute(
            "SELECT count(*) FROM download_log WHERE source='vaneck_carry' AND status='error'"
        ).fetchone()[0]
        == 1
    )
    con.close()
