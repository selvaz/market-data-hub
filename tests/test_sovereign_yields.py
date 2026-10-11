"""Offline F08 regressions; small HTTP recordings are listed in manifest.json."""
from __future__ import annotations

import importlib
import io
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from zipfile import ZipFile

import pandas as pd
import pytest
import requests

from market_data_hub.db.connection import get_conn

FIX = Path(__file__).parent / "fixtures" / "sovereign_yields"


@pytest.fixture
def cb():
    return importlib.import_module("market_data_hub.sources.sovereign_yields")


def spec(symbol):
    from market_data_hub.config_loader import get_sovereign_yields
    return next(e for e in get_sovereign_yields() if e["symbol"] == symbol)


@pytest.mark.parametrize("symbol,fixture,latest,value", [
    ("CB_GB_10Y", "boe.csv", "2026-10-07", 5.4412),
    ("CB_JP_10Y", "jgb_current.csv", "2026-10-08", 3.089),
    ("CB_JP_2Y", "jgb_current.csv", "2026-10-08", 1.936),
    ("CB_IT_10Y", "bdi.csv", "2026-08-31", 4.10012),
    ("CB_ES_10Y", "bde.csv", "2026-10-07", 4.096),
    ("CB_BE_10Y", "nbb.xml", "2026-10-09", 4.31),
    ("CB_CH_10Y", "snb_current.csv", "2026-09-30", 0.586),
    ("CB_CH_2Y", "snb_current.csv", "2026-09-30", 0.287),
    ("CB_CH_10Y", "snb_live.json", "2026-10-09", 0.579),
    ("CB_SE_10Y", "riksbank_10.json", "2026-10-09", 3.197),
    ("CB_SE_2Y", "riksbank_2.json", "2026-10-09", 2.714),
    ("CB_KR_10Y", "ecos.json", "2026-10-08", 4.395),
    ("CB_CN_10Y", "chinabond.html", "2026-10-10", 1.6849),
    ("CB_ZA_R209", "sarb.json", "2026-10-08", 8.99),
])
def test_recorded_parsers(cb, symbol, fixture, latest, value):
    meta = spec(symbol)
    df = cb.parse_response(meta, (FIX / fixture).read_bytes())
    assert df.date.max() == pd.Timestamp(latest)
    assert df.sort_values("date").iloc[-1].value == pytest.approx(value)
    assert df.unit.unique().tolist() == ["%"]
    assert df.frequency.unique().tolist() == ["D"]
    assert df.country.unique().tolist() == [meta["country"]]
    assert df.source.unique().tolist() == [meta["source"]]
    assert df.series_id.unique().tolist() == [symbol]
    assert df.value.notna().all()
    assert not df.date.duplicated().any()


def test_bdi_zip_and_weekend_blanks(cb):
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("TIMESERIES.csv", (FIX / "bdi.csv").read_bytes())
    df = cb.parse_response(spec("CB_IT_10Y"), buffer.getvalue())
    assert set(df.date.dt.dayofweek) <= set(range(5))
    assert pd.Timestamp("2026-08-30") not in set(df.date)


def test_genuine_saturday_and_negative_yield_preserved(cb):
    df = cb.parse_response(spec("CB_CN_10Y"), (FIX / "chinabond.html").read_bytes())
    assert pd.Timestamp("2026-10-10") in set(df.date)
    df = cb.parse_response(spec("CB_GB_10Y"), b"DATE,IUDMNZC\n01 Oct 2026,-0.5\n")
    assert df.value.tolist() == [-0.5]


def test_japan_full_cp932_response_including_publisher_footnote(cb):
    df = cb.parse_response(spec("CB_JP_2Y"), (FIX / "jgb_full_current.csv").read_bytes())
    assert df.date.max() == pd.Timestamp("2026-10-08")
    assert df.value.iloc[-1] == pytest.approx(1.936)


@pytest.mark.parametrize("maturity,value", [("2033-01-01", 12.59), ("2037-01-01", 12.57)])
def test_tesouro_recorded_nominal_yields_keep_fixed_bond_identity(cb, maturity, value):
    year = maturity[:4]
    meta = {"symbol": f"CB_BR_NTNF_{year}", "provider": "tesouro",
            "provider_code": "Tesouro Prefixado com Juros Semestrais", "maturity": maturity,
            "country": "BRA", "source": "Tesouro Nacional",
            "name": f"NTN-F {year} nominal BRL morning purchase yield"}
    df = cb.parse_response(meta, (FIX / "tesouro.csv").read_bytes())
    assert df.date.max() == pd.Timestamp("2026-10-09")
    assert df.value.iloc[-1] == pytest.approx(value)
    assert df.unit.unique().tolist() == ["%"]
    assert df.series_id.unique().tolist() == [f"CB_BR_NTNF_{year}"]
    if year == "2033":
        assert df.date.min() == pd.Timestamp("2023-01-02")


@pytest.mark.parametrize("symbol,body", [
    ("CB_DE_10Y", (FIX / "blocked.html").read_bytes()),
    ("CB_FR_10Y", (FIX / "webstat_missing_key.json").read_bytes()),
    ("CB_ES_10Y", (FIX / "bde.csv").read_bytes().replace(b"DIARIA", b"MENSUAL")),
    ("CB_CH_10Y", (FIX / "snb_current.csv").read_bytes().replace(b"2026-09-30", b"2026-09")),
    ("CB_GB_10Y", b"DATE,IUDMNPY\n01 Oct 2026,5.3665\n"),
    ("CB_KR_10Y", (FIX / "ecos.json").read_bytes().replace(b"010210000", b"010100000")),
    ("CB_KR_10Y", (FIX / "ecos.json").read_bytes().replace(b"Percent Per Annum", b"Basis points")),
    ("CB_GB_10Y", b"DATE,IUDMNZC\n01 Oct 2026,inf\n"),
], ids=["challenge", "missing-key", "monthly-frequency", "monthly-date",
        "wrong-boe-series", "wrong-ecos-item", "wrong-unit", "infinity"])
def test_wrong_schema_frequency_series_units_and_challenges_fail(cb, symbol, body):
    with pytest.raises(cb.SourceUnavailable):
        cb.parse_response(spec(symbol), body)


def response(body):
    r = requests.Response()
    r.status_code = 200
    r._content = body
    return r


def test_japan_combines_history_and_current_inclusive_range(cb, monkeypatch):
    calls = []
    def request(url, **kwargs):
        calls.append(url)
        return response((FIX / ("jgb_history.csv" if "historical" in url
                                else "jgb_current.csv")).read_bytes())
    monkeypatch.setattr(cb, "_request", request)
    df = cb.fetch_yield(spec("CB_JP_10Y"), "2023-01-01", "2026-10-08")
    assert len(calls) == 2
    assert df.date.min().year == 2023
    assert df.date.max() == pd.Timestamp("2026-10-08")
    assert not df.date.duplicated().any()
    assert df.value.iloc[-1] == pytest.approx(3.089)


def test_japan_single_historical_month_still_uses_archive(cb, monkeypatch):
    def request(url, **kwargs):
        return response((FIX / ("jgb_history.csv" if "historical" in url
                                else "jgb_current.csv")).read_bytes())
    monkeypatch.setattr(cb, "_request", request)
    df = cb.fetch_yield(spec("CB_JP_10Y"), "2023-01-01", "2023-01-10")
    assert not df.empty
    assert df.date.max().year == 2023


def test_snb_uses_replacement_history_and_current_daily_endpoint(cb, monkeypatch):
    calls = []
    def request(url, **kwargs):
        calls.append(url)
        return response((FIX / ("snb_live.json" if "interestRates" in url
                                else "snb_current.csv")).read_bytes())
    monkeypatch.setattr(cb, "_request", request)
    df = cb.fetch_yield(spec("CB_CH_10Y"), "2023-01-01", "2026-10-11")
    assert len(calls) == 2
    assert df.date.min() == pd.Timestamp("2026-09-25")
    assert df.date.max() == pd.Timestamp("2026-10-09")
    assert df.value.iloc[-1] == pytest.approx(0.579)
    with pytest.raises(cb.SourceUnavailable):
        cb.parse_response(spec("CB_CH_2Y"), (FIX / "snb_live.json").read_bytes())


def test_chinabond_backfill_chunks_one_year_no_truncation(cb, monkeypatch):
    calls = []
    def request(url, **kwargs):
        calls.append(kwargs["params"])
        return response((FIX / "chinabond.html").read_bytes())
    monkeypatch.setattr(cb, "_request", request)
    cb.fetch_yield(spec("CB_CN_10Y"), "2023-01-01", "2026-10-11")
    assert len(calls) == 4
    assert calls[0]["startDate"] == "2023-01-01"
    assert calls[-1]["endDate"] == "2026-10-11"
    for previous, following in zip(calls, calls[1:]):
        assert pd.Timestamp(following["startDate"]) == pd.Timestamp(previous["endDate"]) + pd.Timedelta(days=1)
    assert all((pd.Timestamp(x["endDate"]) - pd.Timestamp(x["startDate"])).days < 365 for x in calls)


@pytest.mark.parametrize("symbol,env", [("CB_FR_10Y", "WEBSTAT_API_KEY"),
                                        ("CB_KR_10Y", "ECOS_API_KEY"),
                                        ("CB_MX_10Y", "BANXICO_API_TOKEN"),
                                        ("CB_TR_10Y", "EVDS_API_KEY")])
def test_missing_key_is_unavailable_without_http(cb, monkeypatch, symbol, env):
    monkeypatch.delenv(env, raising=False)
    request = Mock(side_effect=AssertionError("must not make HTTP calls without a key"))
    monkeypatch.setattr(cb, "_request", request)
    with pytest.raises(cb.SourceUnavailable, match=env):
        cb.fetch_yield(spec(symbol), "2023-01-01", "2026-10-11")
    request.assert_not_called()


def test_ecos_pagination_and_key_redaction(cb, monkeypatch):
    monkeypatch.setenv("ECOS_API_KEY", "private-test-key")
    payload = (FIX / "ecos.json").read_bytes()
    monkeypatch.setattr(cb, "_request", lambda *a, **k: response(payload))
    df = cb.fetch_yield(spec("CB_KR_10Y"), "2026-10-01", "2026-10-11")
    assert len(df) == 5
    # HTTP errors must never leak the key embedded in ECOS's mandatory URL.
    def fail(*a, **k):
        raise requests.ConnectionError("URL contains private-test-key")
    monkeypatch.setattr(cb, "_request", fail)
    with pytest.raises(cb.SourceUnavailable) as exc:
        cb.fetch_yield(spec("CB_KR_10Y"), "2026-10-01", "2026-10-11")
    assert "private-test-key" not in str(exc.value)


@pytest.mark.parametrize("changes", [{"time_period": "2026-10"},
                                      {"time_period_end": "2026-10-31"},
                                      {"obs_status": "F"}])
def test_webstat_rejects_monthly_periods_and_forecasts(cb, changes):
    row = {"series_key": spec("CB_FR_10Y")["provider_code"],
           "time_period": "2026-10-07", "time_period_start": "2026-10-07",
           "time_period_end": "2026-10-07", "obs_status": "A", "obs_value": 3.5}
    row.update(changes)
    with pytest.raises(cb.SourceUnavailable):
        cb.parse_response(spec("CB_FR_10Y"), json.dumps([row]).encode())


def test_webstat_documented_schema_and_authorization(cb, monkeypatch):
    # Positive keyed response cannot be recorded without an owner's key. This
    # example follows the publisher's migration guide; it is not a live fixture.
    row = {"series_key": spec("CB_FR_10Y")["provider_code"],
           "time_period": "2026-10-07", "time_period_start": "2026-10-07",
           "time_period_end": "2026-10-07", "obs_status": "A", "obs_value": 3.5}
    request = Mock(return_value=response(json.dumps([row]).encode()))
    monkeypatch.setenv("WEBSTAT_API_KEY", "private-test-key")
    monkeypatch.setattr(cb, "_request", request)
    df = cb.fetch_yield(spec("CB_FR_10Y"), "2023-01-01", "2026-10-11")
    assert df.value.tolist() == [3.5]
    assert request.call_args.kwargs["headers"] == {"Authorization": "Apikey private-test-key"}
    assert request.call_args.kwargs["params"]["refine"].endswith(row["series_key"])


def test_ecos_two_pages_do_not_silently_truncate_backfill(cb, monkeypatch):
    monkeypatch.setenv("ECOS_API_KEY", "private-test-key")
    recorded = json.loads((FIX / "ecos.json").read_text())
    rows = recorded["StatisticSearch"]["row"]
    pages = [response(json.dumps({"StatisticSearch": {"list_total_count": 5,
                              "row": page}}).encode()) for page in [rows[:3], rows[3:]]]
    request = Mock(side_effect=pages)
    monkeypatch.setattr(cb, "_request", request)
    df = cb.fetch_yield(spec("CB_KR_10Y"), "2026-10-01", "2026-10-11")
    assert len(df) == 5
    assert "/1/1000/" in request.call_args_list[0].args[0]
    assert "/4/1003/" in request.call_args_list[1].args[0]


@pytest.mark.parametrize("status,attempts", [(401, 1), (429, 3), (503, 3)])
def test_http_retries_transient_failures_only_and_redacts_url(cb, monkeypatch, status, attempts):
    reply = response(b"failure")
    reply.status_code = status
    reply.url = "https://example.invalid/private-test-key"
    session = Mock()
    session.get.return_value = reply
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(cb.requests, "Session", Mock(return_value=session))
    monkeypatch.setattr(cb.time, "sleep", Mock())
    with pytest.raises(cb.SourceUnavailable) as exc:
        cb._request(reply.url, retries=3, base_sleep=0)
    assert session.get.call_count == attempts
    assert "private-test-key" not in str(exc.value)


def test_business_lag_weekend_holiday_and_threshold():
    from market_data_hub.coverage.stalled_detector import is_stalled
    assert not is_stalled("2026-10-02", "D", as_of=date(2026, 10, 7),
                          max_lag_days=3, lag_calendar="business")
    assert is_stalled("2026-10-02", "D", as_of=date(2026, 10, 8),
                      max_lag_days=3, lag_calendar="business")
    assert not is_stalled("2026-10-02", "D", as_of=date(2026, 10, 8),
                          max_lag_days=3, lag_calendar="business", holidays=["2026-10-05"])
    assert is_stalled(None, "D", max_lag_days=3, lag_calendar="business")
    # Legacy FRED limits stay in calendar days.
    assert is_stalled("2026-10-02", "D", as_of=date(2026, 10, 7), max_lag_days=3)


def test_runner_idempotence_revisions_and_unavailable_series(cb, monkeypatch, tmp_db):
    from market_data_hub import runner
    cfg = {"backfill_start": {"sovereign_yields": "2023-01-01"},
           "incremental": {"sovereign_tail_days": 7}, "http": {"timeout": 10, "max_retries": 1}}
    entries = [spec("CB_GB_10Y"), spec("CB_FR_10Y")]
    monkeypatch.setattr(runner, "get_sovereign_yields", lambda: entries)
    calls = []
    def fetch(meta, start, end, **kwargs):
        calls.append((meta["symbol"], start))
        if meta["symbol"] == "CB_FR_10Y":
            raise cb.SourceUnavailable("missing WEBSTAT_API_KEY")
        return cb.parse_response(meta, (FIX / "boe.csv").read_bytes())
    monkeypatch.setattr(cb, "fetch_yield", fetch)
    with get_conn(tmp_db) as con:
        assert runner.run_sovereign_yields(con, cfg, "first", end="2026-10-11") == 2
        first = con.execute("SELECT date, series_id, value, source, country FROM macro_series ORDER BY date").fetchall()
        assert runner.run_sovereign_yields(con, cfg, "repeat", end="2026-10-11") == 2
        assert con.execute("SELECT date, series_id, value, source, country FROM macro_series ORDER BY date").fetchall() == first
        assert calls[0][1] == "2023-01-01"
        assert calls[2][1] == "2026-09-30"
        assert con.execute("SELECT status FROM download_log WHERE source='sovereign_yields' AND symbol='CB_FR_10Y'").fetchall() == [("unavailable",), ("unavailable",)]
        assert con.execute("SELECT count(*) FROM macro_series WHERE series_id='CB_FR_10Y'").fetchone()[0] == 0
        revised = cb.parse_response(entries[0], (FIX / "boe.csv").read_bytes())
        revised.loc[revised.date == revised.date.max(), "value"] = 5.5
        monkeypatch.setattr(cb, "fetch_yield", lambda *a, **k: revised)
        monkeypatch.setattr(runner, "get_sovereign_yields", lambda: entries[:1])
        assert runner.run_sovereign_yields(con, cfg, "revision", end="2026-10-11") == 0
        assert con.execute("SELECT value FROM macro_series ORDER BY date DESC LIMIT 1").fetchone()[0] == 5.5
        assert con.execute("SELECT count(*) FROM macro_series").fetchone()[0] == len(first)


@pytest.mark.parametrize("outcome,expected", [("stale", 2), ("unavailable", 2),
                                             ("fault", 1), ("disabled", 3)])
def test_runner_distinguishes_stale_unavailable_fault_and_noop(cb, monkeypatch, tmp_db,
                                                              outcome, expected):
    from market_data_hub import runner
    entries = [spec("CB_IT_10Y")] if outcome != "disabled" else []
    monkeypatch.setattr(runner, "get_sovereign_yields", lambda: entries)
    def fetch(*args, **kwargs):
        if outcome == "unavailable":
            raise cb.SourceUnavailable("recorded source unavailable")
        if outcome == "fault":
            raise ValueError("programming failure")
        return cb.parse_response(entries[0], (FIX / "bdi.csv").read_bytes())
    monkeypatch.setattr(cb, "fetch_yield", fetch)
    cfg = {"backfill_start": {"sovereign_yields": "2023-01-01"},
           "http": {"timeout": 10, "max_retries": 1}}
    with get_conn(tmp_db) as con:
        assert runner.run_sovereign_yields(con, cfg, "test", end="2026-10-11") == expected
        if outcome == "stale":
            assert con.execute("SELECT status FROM download_log").fetchone()[0] == "stalled"
        else:
            assert con.execute("SELECT count(*) FROM macro_series").fetchone()[0] == 0


def test_failed_chinabond_backfill_chunk_does_not_commit_partial_series(cb, monkeypatch, tmp_db):
    from market_data_hub import runner
    entry = spec("CB_CN_10Y")
    monkeypatch.setattr(runner, "get_sovereign_yields", lambda: [entry])
    request = Mock(side_effect=[response((FIX / "chinabond.html").read_bytes()),
                               cb.SourceUnavailable("second year failed")])
    monkeypatch.setattr(cb, "_request", request)
    cfg = {"backfill_start": {"sovereign_yields": "2023-01-01"},
           "http": {"timeout": 10, "max_retries": 1}}
    with get_conn(tmp_db) as con:
        assert runner.run_sovereign_yields(con, cfg, "test", end="2026-10-11") == 2
        assert con.execute("SELECT count(*) FROM macro_series").fetchone()[0] == 0


def test_coverage_uses_central_bank_and_missing_catalog_entries(cb, monkeypatch, tmp_db):
    from market_data_hub.coverage import report
    from market_data_hub.db.upsert import upsert
    entries = [spec("CB_GB_10Y"), spec("CB_FR_10Y")]
    monkeypatch.setattr(report, "get_sovereign_yields", lambda: entries)
    with get_conn(tmp_db) as con:
        upsert(con, "macro_series", cb.parse_response(entries[0], (FIX / "boe.csv").read_bytes()))
        report.rebuild_coverage(con, "test")
        rows = con.execute("SELECT symbol, source, freq_detected, stalled FROM coverage_report ORDER BY symbol").fetchall()
        assert len(rows) == 2
        assert rows[0] == ("CB_FR_10Y", "Banque de France", "D", True)
        assert rows[1][0:3] == ("CB_GB_10Y", "Bank of England", "D")


def test_daily_job_wiring_and_catalog():
    from market_data_hub import runner
    from market_data_hub.config_loader import get_sovereign_yields
    from validate_config import validate
    entries = get_sovereign_yields()
    assert "sovereign_yields" in runner._DEFAULT_SOURCES
    required = {"DEU", "FRA", "ITA", "ESP", "PRT", "GRC", "NLD", "BEL", "AUT", "IRL", "GBR", "JPN", "CHE", "SWE", "MEX", "BRA", "ARG", "CHN", "IND", "IDN", "ZAF", "TUR", "POL", "KOR", "COL", "CHL", "PER"}
    assert required <= {x["country"] for x in entries}
    assert all(x["unit"] == "%" and x["frequency"] == "D" and
               x["max_lag_days"] == 3 and x["lag_calendar"] == "business" for x in entries)
    assert validate() == []


def test_unknown_endpoint_does_not_claim_no_daily_series(cb):
    with pytest.raises(cb.SourceUnavailable, match="unverified|unresolved|not verified"):
        cb.fetch_yield(spec("CB_NL_10Y"), "2023-01-01", "2026-10-11")


def test_cli_propagates_degraded_and_fault_even_with_report(monkeypatch):
    import run_daily
    import run_backfill
    monkeypatch.setattr("sys.argv", ["run_daily.py", "--sources", "sovereign_yields"])
    call = Mock(return_value=2)
    monkeypatch.setattr(run_daily, "run", call)
    assert run_daily.main() == 2
    call.assert_called_once()
    monkeypatch.setattr("sys.argv", ["run_backfill.py", "--sources", "sovereign_yields", "--start", "2023-01-01"])
    monkeypatch.setattr(run_backfill, "run", call)
    assert run_backfill.main() == 2
    assert call.call_args.kwargs["start_override"] == "2023-01-01"


@pytest.mark.parametrize("result", [1, 2])
def test_daily_successful_report_preserves_ingestion_fault_or_degraded(monkeypatch, tmp_path, result):
    import run_daily
    monkeypatch.setattr("sys.argv", ["run_daily.py", "--sources", "sovereign_yields", "--report"])
    monkeypatch.setattr(run_daily, "run", Mock(return_value=result))
    data = {"total_rows": 1, "tables": [{"series": 1}], "score_avg": 100, "stalled": []}
    report = SimpleNamespace(REPORT_DIR=tmp_path, collect=Mock(return_value=data),
                             render_html=Mock(return_value="<p>test</p>"),
                             render_md=Mock(return_value="test"))
    monkeypatch.setitem(__import__("sys").modules, "make_report", report)
    connection = Mock()
    connect = Mock(return_value=connection)
    monkeypatch.setattr("market_data_hub.db.connection.get_conn", connect)
    monkeypatch.setattr("market_data_hub.artifact_registry.register_report_artifact", Mock())
    monkeypatch.setattr("market_data_hub.country_dashboard.write_dashboard", Mock(return_value=tmp_path / "dashboard.html"))
    assert run_daily.main() == result
    connect.assert_called_once_with(None, read_only=True)
    connection.close.assert_called_once()


def test_live_lock_owner_returns_nine_without_opening_db(monkeypatch):
    from market_data_hub import runner
    from market_data_hub.lock import DBLockTimeout
    def held(*a, **k):
        raise DBLockTimeout("live test owner")
    monkeypatch.setattr(runner, "db_write_lock", held)
    connection = Mock(side_effect=AssertionError("must not open database"))
    monkeypatch.setattr(runner, "get_conn", connection)
    assert runner.run(sources=["sovereign_yields"]) == 9
    connection.assert_not_called()


@pytest.mark.parametrize("field,bad", [("max_lag_days", 30), ("frequency", "M"),
                                       ("country", "DE"), ("lag_calendar", "calendar"),
                                       ("unit", "bps"), ("provider", "mystery")])
def test_config_validates_daily_yield_contract(monkeypatch, field, bad):
    import validate_config
    original = validate_config._y
    entry = dict(spec("CB_DE_10Y"), **{field: bad})
    monkeypatch.setattr(validate_config, "_y", lambda name: {"series": [entry]}
                        if name == "sovereign_yields.yaml" else original(name))
    assert any(field in error for error in validate_config.validate())
