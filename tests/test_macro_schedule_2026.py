"""Forward dates must never be fetched or masquerade as publications."""
from market_data_hub.macro_releases import bea, bls, run_daily
from market_data_hub.macro_releases.schedule import due, edition_status
from market_data_hub.macro_releases.us_editions import EDITIONS
from test_macro_us_monthly import Replay


def test_reviewed_calendar_to_december():
    expected = {
        "us_ppi": ["2026-11-13", "2026-12-15"],
        "us_jolts": ["2026-11-03", "2026-12-01"],
        "us_pce": ["2026-10-29", "2026-11-25", "2026-12-23"],
        "us_retail_sales": ["2026-11-17", "2026-12-16"],
        "us_housing_starts": ["2026-11-18", "2026-12-17"],
        "us_durable_goods": ["2026-10-27", "2026-11-03", "2026-11-25", "2026-12-03", "2026-12-23"],
        "us_industrial_production": ["2026-11-17", "2026-11-24", "2026-12-16"],
        "us_trade_balance": ["2026-11-04", "2026-12-08"],
    }
    assert {f: sorted(d for d in e if d > "2026-10-24") for f, e in EDITIONS.items()} == expected
    assert bea.EDITIONS["2026-10-29"] == ("2026-Q3", "advance")
    assert bea.EDITIONS["2026-12-23"] == ("2026-Q3", "third")
    assert bls.EDITIONS["us_cpi", "2026-11"] == "2026-12-10"
    assert bls.EDITIONS["us_payrolls", "2026-11"] == "2026-12-04"


def test_future_edition_is_scheduled_and_becomes_due_on_issuer_date():
    assert edition_status("2026-10-29", "2026-10-24") == "scheduled"
    assert "2026-10-29" not in due(bea.EDITIONS, "2026-10-28")
    assert due(bea.EDITIONS, "2026-10-29T12:30:00Z")["2026-10-29"] == ("2026-Q3", "advance")


def test_forward_calendar_no_future_http_and_not_an_error(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 24, tzinfo=timezone.utc)
    monkeypatch.setattr(run_daily, "datetime", Clock)
    monkeypatch.setattr(run_daily, "HttpClient", Replay)
    monkeypatch.setattr("market_data_hub.macro_releases.fred.api_key", lambda _: "fixture")
    result = run_daily.execute(tmp_path / "calendar.sqlite", ["us_housing_starts"], 24, False,
                               raw_dir=tmp_path / "none")
    family = result["families"]["us_housing_starts"]
    assert family["status"] == "ok", result
    assert [d["edition"] for d in family["scheduled_editions"]] == ["2026-11-18", "2026-12-17"]
    assert all(d["status"] == "scheduled" for d in family["scheduled_editions"])
