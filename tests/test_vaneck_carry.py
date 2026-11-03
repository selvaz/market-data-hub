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


@pytest.mark.parametrize("symbol", ["EMLC", "HYEM", "IHY"])
def test_recorded_daily_blocks(symbol):
    import json
    from market_data_hub.vaneck_carry import parse_daily

    values = {}
    for kind in ("yield", "fundamentals", "holdings"):
        payload = json.loads(
            (FIXTURE.parent / f"vaneck_{symbol.lower()}_{kind}.json").read_text(
                encoding="utf8"
            )
        )
        values.update(parse_daily(kind, payload, symbol))
    assert set(values) == {
        "sec_yield_30d",
        "yield_to_worst",
        "effective_duration",
        "holdings_count",
    }
    assert all(item[2].isoformat() == "2026-11-02" for item in values.values())
    assert values["holdings_count"][0] == {"EMLC": 523, "HYEM": 545, "IHY": 499}[symbol]


def test_bad_value_fails_closed_and_logs_field():
    import json
    from market_data_hub.vaneck_carry import parse_daily

    data = json.loads(
        (FIXTURE.parent / "vaneck_hyem_yield.json").read_text(encoding="utf8")
    )
    data["data"]["Values"][1]["Value"] = "NaN%"
    logs = []
    assert parse_daily("yield", data, "HYEM", log=logs.append) == {}
    assert "HYEM" in logs[0] and "sec_yield_30d" in logs[0]


def test_fund_failure_does_not_block_other_funds(tmp_path, monkeypatch):
    import json
    from market_data_hub import vaneck_carry

    class Response:
        def __init__(self, kind, symbol):
            self.payload = json.loads(
                (FIXTURE.parent / f"vaneck_{symbol.lower()}_{kind}.json").read_text(
                    encoding="utf8"
                )
            )

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    def fetch(url, *, params, **kwargs):
        if params["ticker"] == "EMLC":
            raise requests.HTTPError("unavailable")
        kind = {371036: "yield", 230678: "fundamentals", 144475: "holdings"}[
            params["blockid"]
        ]
        return Response(kind, params["ticker"])

    monkeypatch.setattr(vaneck_carry.requests, "get", fetch)
    con = get_conn(str(tmp_path / "hub.duckdb"))
    assert collect(con, log=lambda _: None) == 8
    assert collect(con, log=lambda _: None) == 0
    assert (
        con.execute(
            "SELECT count(*) FROM download_log WHERE source='vaneck_carry' AND status='error'"
        ).fetchone()[0]
        == 1
    )
    con.close()
