"""Regression checks for applying main's release clock to the M9b/M9c families."""
from datetime import datetime, timedelta

import pytest

from market_data_hub.macro_releases import open_reader, run_daily, schedule
from market_data_hub.macro_releases.eurostat_activity import calendar_editions
from test_macro_m9b import Replay, document


@pytest.mark.parametrize("family,edition,instant", [
    ("ea_unemployment", "2026-10-01", "2026-10-01T09:00:00+00:00"),
    ("ea_core_hicp", "2026-10-16", "2026-10-16T09:00:00+00:00"),
    ("ea_trade_balance", "2026-10-16", "2026-10-16T09:00:00+00:00"),
    ("ea_industrial_production", "2026-10-15", "2026-10-15T09:00:00+00:00"),
    ("ea_retail_sales", "2026-10-06", "2026-10-06T09:00:00+00:00"),
])
def test_eurostat_calendar_does_not_authorize_early_archive_fetch(family, edition, instant):
    release = datetime.fromisoformat(instant)
    token = schedule.RUN_INSTANT.set(release - timedelta(seconds=1))
    try:
        assert edition not in calendar_editions(document("calendar.json"), family)
        schedule.RUN_INSTANT.set(release)
        assert edition in calendar_editions(document("calendar.json"), family)
    finally:
        schedule.RUN_INSTANT.reset(token)


def test_ons_retail_collector_uses_london_clock(tmp_path, monkeypatch):
    clock = {"at": datetime.fromisoformat("2026-10-23T05:59:59+00:00")}
    class Clock:
        @staticmethod
        def now(tz):
            return clock["at"]
    monkeypatch.setattr(run_daily, "datetime", Clock)
    monkeypatch.setattr(run_daily, "HttpClient", Replay)
    db = tmp_path / "retail.sqlite"
    for due in (False, True):
        if due:
            clock["at"] += timedelta(seconds=1)
        result = run_daily.execute(db, ["uk_retail_sales"], 24, False, raw_dir=tmp_path / "none")
        assert result["families"]["uk_retail_sales"]["status"] == "ok", result
        with open_reader(db) as store:
            editions = {r[0] for r in store.conn.execute("SELECT edition FROM release_events")}
        assert ("2026-10-23" in editions) is due


def test_boj_date_only_permission_does_not_invent_release_instant():
    assert schedule.release_instant("2026-10-30", family="boj_policy") is None
    assert schedule.edition_status("2026-10-30", "2026-10-29T14:59:59Z", family="boj_policy") == "scheduled"
    assert schedule.edition_status("2026-10-30", "2026-10-29T15:00:00Z", family="boj_policy") == "due"
    assert not schedule.publication_grace("boj_policy", "2026-10-30")
