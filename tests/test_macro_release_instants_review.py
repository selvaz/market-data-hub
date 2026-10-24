"""M9b release-day regressions; all issuer responses are replayed offline."""
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
import requests

from market_data_hub.macro_releases import open_reader, run_daily
from market_data_hub.macro_releases.http import FetchError
from market_data_hub.macro_releases import schedule
from market_data_hub.macro_releases.tier1_editions import EDITIONS
from test_macro_m9b import Replay


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network forbidden in release-instant tests")
    monkeypatch.setattr(requests.Session, "request", forbidden)


@pytest.mark.parametrize("family,release,prior,instant", [
    ("us_new_home_sales", "2026-10-27", "2026-09-24", "2026-10-27T14:00:00+00:00"),
    ("us_umich_sentiment", "2026-11-06", "2026-10-23", "2026-11-06T15:00:00+00:00"),
    ("ea_ec_surveys", "2026-11-20", "2026-10-22", "2026-11-20T15:00:00+00:00"),
])
def test_release_day_preserves_previous_edition_before_release_and_during_grace(
        tmp_path, monkeypatch, family, release, prior, instant):
    if family == "ea_ec_surveys":
        # Isolate flash releases from final editions; the replay is October's
        # genuine flash publication, with no November link on the latest page.
        monkeypatch.setitem(EDITIONS, family, {d: m for d, m in EDITIONS[family].items() if m[1] == "flash"})
    stamp = datetime.fromisoformat(instant)
    clock = {"at": stamp - timedelta(minutes=30)}
    class Clock:
        @staticmethod
        def now(tz):
            return clock["at"]
    monkeypatch.setattr(run_daily, "datetime", Clock)
    monkeypatch.setattr(run_daily, "HttpClient", Replay)
    db = tmp_path / "release.sqlite"
    for label, at in (("before", stamp - timedelta(minutes=30)), ("grace", stamp + timedelta(minutes=5))):
        clock["at"] = at
        result = run_daily.execute(db, [family], 24, False, raw_dir=tmp_path / "none")
        outcome = result["families"][family]
        assert outcome["status"] in ("ok", "no_change"), result
        pending = next(e for e in outcome["scheduled_editions"] if e["edition"] == release)
        if label == "grace":
            assert pending["reason"] == "not_yet_published"
        with open_reader(db) as store:
            editions = {r[0] for r in store.conn.execute("SELECT edition FROM release_events")}
            assert max(editions) == prior
            assert release not in editions
    # A stale rolling publication may not hide a missing release indefinitely.
    clock["at"] = stamp + timedelta(hours=6)
    late = run_daily.execute(db, [family], 24, False, raw_dir=tmp_path / "none")
    assert late["families"][family]["status"] == "needs_review", late


@pytest.mark.parametrize("family,edition,instant", [
    ("us_new_home_sales", "2026-10-27", "2026-10-27T14:00:00+00:00"),
    ("us_umich_sentiment", "2026-11-06", "2026-11-06T15:00:00+00:00"),
    ("ea_ec_surveys", "2026-11-20", "2026-11-20T15:00:00+00:00"),
])
def test_tier1_due_at_issuer_instant_with_dst(family, edition, instant):
    stamp = datetime.fromisoformat(instant)
    assert edition not in schedule.due(EDITIONS[family], stamp - timedelta(seconds=1), family=family)
    assert edition in schedule.due(EDITIONS[family], stamp, family=family)
    assert edition not in schedule.due(EDITIONS[family], edition, family=family)


def test_only_edition_specific_404_gets_bounded_publication_delay_grace():
    class Missing:
        def __init__(self, code):
            self.code = code
        def get(self, provider, url):
            raise FetchError(f"{provider}: HTTP {self.code}")
    pending = schedule.PENDING_EDITIONS.set({})
    clock = schedule.RUN_AT.set(datetime.fromisoformat("2026-10-27T14:05:00+00:00"))
    try:
        assert schedule.get_edition(Missing(404), "census", "https://www.census.gov/edition.pdf",
                                    "us_new_home_sales", "2026-10-27") is None
        assert ("us_new_home_sales", "2026-10-27") in schedule.PENDING_EDITIONS.get()
        for code in (403, 500):
            with pytest.raises(FetchError, match=f"HTTP {code}"):
                schedule.get_edition(Missing(code), "census", "https://www.census.gov/edition.pdf",
                                     "us_new_home_sales", "2026-10-27")
        schedule.RUN_AT.set(datetime.fromisoformat("2026-10-27T20:00:00+00:00"))
        with pytest.raises(FetchError, match="HTTP 404"):
            schedule.get_edition(Missing(404), "census", "https://www.census.gov/edition.pdf",
                                 "us_new_home_sales", "2026-10-27")
    finally:
        schedule.PENDING_EDITIONS.reset(pending)
        schedule.RUN_AT.reset(clock)


def test_publication_grace_does_not_excuse_a_changed_format(tmp_path, monkeypatch):
    class Changed(Replay):
        def get(self, *args, **kwargs):
            doc = super().get(*args, **kwargs)
            return replace(doc, body=b"unrecognized issuer layout")
    class Clock:
        @staticmethod
        def now(tz):
            return datetime.fromisoformat("2026-10-27T14:05:00+00:00")
    monkeypatch.setattr(run_daily, "datetime", Clock)
    monkeypatch.setattr(run_daily, "HttpClient", Changed)
    result = run_daily.execute(tmp_path / "changed.sqlite", ["us_new_home_sales"], 24, False,
                               raw_dir=tmp_path / "none")
    assert result["families"]["us_new_home_sales"]["status"] == "needs_review", result
