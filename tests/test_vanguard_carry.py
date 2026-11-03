from pathlib import Path
from market_data_hub.vanguard_carry import parse_pdf


def test_bndx_recorded_fund_not_index_characteristics():
    data = (
        Path(__file__).parent / "fixtures/vanguard_bndx_fact_sheet.pdf"
    ).read_bytes()
    date, values = parse_pdf(data)
    assert date.isoformat() == "2026-09-30"
    assert values["average_duration"][0] == 6.4
    assert values["holdings_count"][0] == 6837


def test_profile_sec_yield_and_date():
    import json
    from market_data_hub.vanguard_carry import parse_profile

    data = json.loads(
        (Path(__file__).parent / "fixtures/vanguard_bndx_profile.json").read_text(
            encoding="utf8"
        )
    )
    values = parse_profile(data)
    assert values["sec_yield_30d"][0] == 0.039
    assert values["sec_yield_30d"][2].isoformat() == "2026-10-30"


def test_profile_missing_or_bad_yield_fails_closed():
    import json
    from market_data_hub.vanguard_carry import parse_profile

    data = json.loads(
        (Path(__file__).parent / "fixtures/vanguard_bndx_profile.json").read_text(
            encoding="utf8"
        )
    )
    for value in ("NaN%", None):
        data["price"]["secYield"] = value
        logs = []
        assert parse_profile(data, log=logs.append) == {}
        assert "BNDX" in logs[0] and "sec_yield_30d" in logs[0]
    del data["price"]["secYieldAsOfDate"]
    data["price"]["secYield"] = "3.90%"
    assert parse_profile(data, log=lambda _: None) == {}


def test_collect_profile_and_factsheet(tmp_path, monkeypatch):
    import html
    import json
    from market_data_hub import vanguard_carry
    from market_data_hub.db.connection import get_conn

    fixtures = Path(__file__).parent / "fixtures"
    profile = json.loads(
        (fixtures / "vanguard_bndx_profile.json").read_text(encoding="utf8")
    )

    class Response:
        text = (
            "data-vgn-funds-profile='"
            + html.escape(json.dumps(profile), quote=True)
            + "'"
        )
        content = (fixtures / "vanguard_bndx_fact_sheet.pdf").read_bytes()

        def raise_for_status(self):
            pass

    monkeypatch.setattr(
        vanguard_carry.requests, "get", lambda *args, **kwargs: Response()
    )
    con = get_conn(str(tmp_path / "hub.duckdb"))
    assert vanguard_carry.collect(con) == 3
    assert vanguard_carry.collect(con) == 0
    rows = con.execute(
        "SELECT field, source_as_of FROM etf_carry_snapshots ORDER BY field"
    ).fetchall()
    assert [(field, str(date)) for field, date in rows] == [
        ("average_duration", "2026-09-30"),
        ("holdings_count", "2026-09-30"),
        ("sec_yield_30d", "2026-10-30"),
    ]
    con.close()
