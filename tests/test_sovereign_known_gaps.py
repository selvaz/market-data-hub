"""Known gaps and history staleness stay visible without degrading current data."""

from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

import validate_config
from market_data_hub import catalog, runner
from market_data_hub.config_loader import get_sovereign_yields
from market_data_hub.coverage import report
from market_data_hub.coverage.stalled_detector import is_stalled
from market_data_hub.db.connection import get_conn
from market_data_hub.db.upsert import upsert
from market_data_hub.sources import sovereign_yields as cb

FIX = Path(__file__).parent / "fixtures" / "sovereign_yields"
CFG = {
    "backfill_start": {"sovereign_yields": "2023-01-01"},
    "http": {"timeout": 10, "max_retries": 1},
}


def spec(symbol):
    return next(e for e in get_sovereign_yields() if e["symbol"] == symbol)


def test_known_gaps_are_disabled_for_collection():
    disabled = []
    for e in get_sovereign_yields():
        if e["provider"] in {"unavailable", "bundesbank", "ecos"}:
            assert e.get("enabled") is False, e["symbol"]
            assert e.get("disabled_reason", "").strip(), e["symbol"]
            disabled.append(e)
    assert len(disabled) == 19


def test_banca_d_italia_is_explicit_documented_history_with_real_freshness():
    (bdi,) = [e for e in get_sovereign_yields() if e["symbol"] == "CB_IT_10Y"]
    assert bdi["enabled"] is True and bdi["role"] == "history"
    assert bdi["max_lag_days"] == 3 and bdi["lag_calendar"] == "business"
    assert bdi["history_reason"].strip()
    assert spec("ITA_BTP10_MOT").get("role", "current") == "current"


def test_disabled_gaps_remain_discoverable():
    entries = catalog.list_macro_series(with_coverage=False).set_index("series_id")
    for e in get_sovereign_yields():
        row = entries.loc[e["symbol"]]
        assert row.role == e.get("role", "current")
        if not e.get("enabled", True):
            assert not row.enabled and row.disabled_reason == e["disabled_reason"]
    assert (
        entries.loc["CB_IT_10Y", "history_reason"]
        == spec("CB_IT_10Y")["history_reason"]
    )


def test_ecos_key_does_not_dynamically_enable_collection(monkeypatch, tmp_db):
    monkeypatch.setenv("ECOS_API_KEY", "private-test-key")
    entry = spec("CB_KR_10Y")
    assert (
        entry["enabled"] is False and entry["disabled_reason"] == "needs ECOS_API_KEY"
    )
    monkeypatch.setattr(runner, "get_sovereign_yields", lambda: [entry])
    fetch = Mock(side_effect=AssertionError("disabled source must not fetch"))
    monkeypatch.setattr(cb, "fetch_yield", fetch)
    with get_conn(tmp_db) as con:
        assert (
            runner.run_sovereign_yields(con, CFG, "disabled-key", end="2026-10-11") == 3
        )
    fetch.assert_not_called()


@pytest.mark.parametrize("explicit_current", [False, True])
def test_stale_current_still_degrades_including_default_role(
    monkeypatch, tmp_db, explicit_current
):
    entry = dict(spec("CB_GB_10Y"))
    entry.pop("role", None)
    if explicit_current:
        entry["role"] = "current"
    monkeypatch.setattr(runner, "get_sovereign_yields", lambda: [entry])
    frame = cb.parse_response(entry, (FIX / "boe.csv").read_bytes())
    monkeypatch.setattr(cb, "fetch_yield", lambda *a, **kw: frame)
    with get_conn(tmp_db) as con:
        assert (
            runner.run_sovereign_yields(con, CFG, "current-stale", end="2026-10-19")
            == 2
        )
        assert con.execute("SELECT status FROM download_log").fetchone() == ("stalled",)


@pytest.mark.parametrize("new_rows", [False, True])
def test_history_staleness_is_logged_and_flagged_without_degraded_exit(
    monkeypatch, tmp_db, new_rows
):
    entry = dict(
        spec("CB_IT_10Y"),
        role="history",
        max_lag_days=3,
        history_reason="Official daily observations released in monthly batches",
    )
    monkeypatch.setattr(runner, "get_sovereign_yields", lambda: [entry])
    monkeypatch.setattr(report, "get_sovereign_yields", lambda: [entry])
    frame = cb.parse_response(entry, (FIX / "bdi.csv").read_bytes())
    monkeypatch.setattr(
        cb, "fetch_yield", lambda *a, **kw: frame if new_rows else frame.iloc[:0]
    )
    monkeypatch.setattr(
        report,
        "is_stalled",
        lambda last, freq, **kw: is_stalled(last, freq, as_of=date(2026, 10, 11), **kw),
    )
    with get_conn(tmp_db) as con:
        if not new_rows:
            upsert(con, "macro_series", frame)
        assert runner.run_sovereign_yields(
            con, CFG, "history-stale", end="2026-10-11"
        ) == (0 if new_rows else 3)
        log = con.execute("SELECT status,error_msg FROM download_log").fetchone()
        assert log[0] == "stalled" and "freshness limit" in log[1]
        assert con.execute("SELECT count(*) FROM macro_series").fetchone()[0] == len(
            frame
        )
        assert report.rebuild_coverage(con, "history-coverage") == 1
        assert con.execute(
            "SELECT status,stalled,freq_detected FROM coverage_report"
        ).fetchone() == ("stalled", True, "D")


def test_fresh_current_plus_stale_history_and_disabled_gaps_is_ok(monkeypatch, tmp_db):
    entries = [
        spec("CB_GB_10Y"),
        dict(spec("CB_IT_10Y"), role="history", max_lag_days=3),
        spec("CB_DE_10Y"),
        spec("CB_PT_10Y"),
        spec("CB_KR_10Y"),
    ]
    monkeypatch.setattr(runner, "get_sovereign_yields", lambda: entries)

    def fetch(entry, *args, **kwargs):
        assert entry["enabled"]
        return cb.parse_response(
            entry,
            (
                FIX / ("boe.csv" if entry["symbol"] == "CB_GB_10Y" else "bdi.csv")
            ).read_bytes(),
        )

    fetch_mock = Mock(side_effect=fetch)
    monkeypatch.setattr(cb, "fetch_yield", fetch_mock)
    with get_conn(tmp_db) as con:
        assert runner.run_sovereign_yields(con, CFG, "mixed", end="2026-10-11") == 0
        assert dict(
            con.execute("SELECT symbol,status FROM download_log").fetchall()
        ) == {"CB_GB_10Y": "ok", "CB_IT_10Y": "stalled"}
    assert fetch_mock.call_count == 2


@pytest.mark.parametrize(
    "outcome,expected", [("transport", 2), ("fault", 1), ("missing", 2)]
)
def test_history_role_does_not_hide_failed_ingestion(
    monkeypatch, tmp_db, outcome, expected
):
    entry = dict(spec("CB_IT_10Y"), role="history", max_lag_days=3)
    monkeypatch.setattr(runner, "get_sovereign_yields", lambda: [entry])

    def fetch(*args, **kwargs):
        if outcome == "transport":
            raise cb.SourceUnavailable("unexpected transport outage")
        if outcome == "fault":
            raise ValueError("parser failure")
        return pd.DataFrame()

    monkeypatch.setattr(cb, "fetch_yield", fetch)
    with get_conn(tmp_db) as con:
        assert (
            runner.run_sovereign_yields(con, CFG, "history-failure", end="2026-10-11")
            == expected
        )
        assert con.execute("SELECT status FROM download_log").fetchone()[0] == (
            "error" if outcome == "fault" else "unavailable"
        )


@pytest.mark.parametrize(
    "change,valid,match",
    [
        ({"role": "other"}, False, "role"),
        ({"role": "history"}, False, "history_reason"),
        ({"role": "history", "history_reason": "   "}, False, "history_reason"),
        (
            {
                "role": "history",
                "history_reason": "Official monthly batch",
                "max_lag_days": 40,
            },
            True,
            "",
        ),
        ({"role": "current", "max_lag_days": 40}, False, "max_lag_days"),
        (
            {
                "role": "history",
                "history_reason": "Official monthly batch",
                "max_lag_days": -1,
            },
            False,
            "max_lag_days",
        ),
        (
            {
                "role": "history",
                "history_reason": "Official monthly batch",
                "max_lag_days": True,
            },
            False,
            "max_lag_days",
        ),
        ({"enabled": False}, False, "disabled_reason"),
        ({"enabled": False, "disabled_reason": "   "}, False, "disabled_reason"),
        ({"enabled": False, "disabled_reason": "Known gap"}, True, ""),
        ({}, True, ""),
    ],
)
def test_validator_roles_documentation_and_disabled_reason(
    monkeypatch, change, valid, match
):
    entry = dict(spec("CB_GB_10Y"), **change)
    if "role" not in change:
        entry.pop("role", None)
    load = validate_config._y
    monkeypatch.setattr(
        validate_config,
        "_y",
        lambda name: (
            {"series": [entry]} if name == "sovereign_yields.yaml" else load(name)
        ),
    )
    errors = validate_config.validate()
    if valid:
        assert errors == []
    else:
        assert any(match in e for e in errors), errors


def test_swiss_2y_is_a_disabled_known_gap():
    # First live run 2026-10-13: the SNB 2y export stopped at 2026-09-30, so the
    # series degraded every daily run. It stays discoverable, out of collection.
    (ch2,) = [e for e in get_sovereign_yields() if e["symbol"] == "CB_CH_2Y"]
    assert ch2["enabled"] is False
    assert "2026-09-30" in ch2["disabled_reason"]
