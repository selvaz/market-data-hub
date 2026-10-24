"""Reviewed FF measures and calendar linking; no network or production writes."""
from datetime import datetime

import pytest
import requests

from market_data_hub.macro_releases import consensus, open_writer
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.catalogue import INDICATORS
from market_data_hub.macro_releases.models import ScheduleVersion
from market_data_hub.macro_releases.schedule import edition_status
from market_data_hub.macro_releases.scheduled_events import persist_scheduled_events
from test_macro_consensus_ingest import archive, event, rows
from test_macro_read_api import observation


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("calendar linking must not use the network")
    monkeypatch.setattr(requests.Session, "request", forbidden)


@pytest.mark.parametrize("currency,title", [
    ("USD", "Core PPI m/m"), ("USD", "PPI q/q"),
    ("USD", "Retail Sales y/y"), ("USD", "Core CPI q/q"),
    ("USD", "Existing Home Sales"), ("GBP", "Retail Sales y/y"),
    ("GBP", "CPI m/m"), ("EUR", "Main Refinancing Rate"),
    ("CNY", "5-y Loan Prime Rate"), ("JPY", "Trade Balance"),
    ("EUR", "German Industrial Production m/m"),
    ("EUR", "Flash Manufacturing PMI"), ("USD", "Final Services PMI"),
])
def test_wrong_measure_transform_geography_and_private_pmi_do_not_map(tmp_path, currency, title):
    with open_writer(tmp_path / "macro.sqlite") as store:
        for indicator in INDICATORS.values():
            store.put(indicator)
        mapping, reason = consensus.Catalogue(store).match({"country": currency}, title)
        assert mapping is None and reason == "unmapped"


@pytest.mark.parametrize("title,indicator,family,edition,period,stage,value", [
    ("Advance GDP q/q", "us_gdp_qq_saar", "us_gdp", "2026-10-29", "2026-Q3", "advance", "2.5%"),
    ("Core PCE Price Index m/m", "us_core_pce_mm", "us_pce", "2026-10-29", "2026-09", "regular", "0.2%"),
    ("Durable Goods Orders m/m", "us_durable_goods_orders_mm", "us_durable_goods", "2026-10-27", "2026-09", "advance", "0.5%"),
    ("Federal Funds Rate", "fed_funds_rate_decision", "fomc", "2026-10-28", "2026-10-28", "final", "4.25%"),
    ("Prelim GDP q/q", "us_gdp_qq_saar", "us_gdp", "2026-11-25", "2026-Q3", "second", "2.5%"),
    ("Final GDP q/q", "us_gdp_qq_saar", "us_gdp", "2026-12-23", "2026-Q3", "third", "2.5%"),
])
def test_scheduled_forecast_and_later_official_share_event(
    tmp_path, title, indicator, family, edition, period, stage, value
):
    with open_writer(tmp_path / "macro.sqlite") as store:
        raw = tmp_path / "raw"
        archive(raw, [dict(event(), title=title, date=edition, forecast=value)],
                filename="2026-10-23.json", captured="2026-10-23T21:30:00Z")
        result = consensus.ingest(store, raw)
        assert result["status"] == "ok" and result["readings_added"] == 1, result
        reading, = rows(store)
        event_id = ":".join((INDICATORS[indicator].issuer_id, family, stage, edition))
        assert reading["release_event_id"] == event_id
        assert reading["reference_period"] == period
        assert reading["cutoff_basis"] == "scheduled" and reading["pre_release_eligible"] == 1
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 0
        pending, = store.release_cards(edition, edition, indicators=[indicator])["cards"]
        assert pending["status"] == "scheduled"
        assert pending["actual"] is pending["resolution"] is pending["surprise"] is None
        persist(store, [observation(indicator, family, period, 3.0, edition, stage,
                    INDICATORS[indicator].issuer_id, seen="2026-12-24T20:00:00Z")])
        actual = store.conn.execute("SELECT release_event_id FROM value_vintages").fetchone()[0]
        assert actual == event_id
        assert consensus.ingest(store, raw)["readings_added"] == 0
        official, = store.release_cards(edition, edition, indicators=[indicator],
                                       as_known_at="2026-12-25T00:00:00Z")["cards"]
        assert official["resolution"]["status"] == "official"
        assert official["consensus"][0]["release_event_id"] == event_id
        assert official["consensus"][0]["surprise"] is None
        assert official["consensus"][0]["surprise_eligible"] is False
        assert len(rows(store)) == 1


def test_missing_calendar_date_stays_not_found(tmp_path):
    with open_writer(tmp_path / "macro.sqlite") as store:
        archive(tmp_path / "raw", [dict(event(), title="Advance GDP q/q", date="2026-10-28")])
        result = consensus.ingest(store, tmp_path / "raw")
        assert result["skip_reasons"] == {"release_event_not_found": 1}
        assert rows(store) == []
        assert not store.conn.execute("SELECT 1 FROM release_events WHERE family='us_gdp' AND edition='2026-10-28'").fetchone()


def test_seed_is_idempotent_and_never_overrides_schedule_revision(tmp_path):
    with open_writer(tmp_path / "macro.sqlite") as store:
        seeded = persist_scheduled_events(store)
        assert seeded["events_added"] > 0 and seeded["memberships_added"] > 0
        assert persist_scheduled_events(store) == dict(events_added=0, schedules_added=0, memberships_added=0)
        event_id = "BEA:us_gdp:advance:2026-10-29"
        store.put(ScheduleVersion(release_event_id=event_id, expected_at="2026-10-30T12:30:00Z",
                    issuer_timezone="America/New_York", precision="exact", source_id="BEA",
                    recorded_at="2099-01-01T00:00:00Z"))
        archive(tmp_path / "raw", [dict(event(), title="Advance GDP q/q", date="2026-10-29")])
        assert consensus.ingest(store, tmp_path / "raw")["skip_reasons"] == {"release_event_not_found": 1}
        assert rows(store) == []


def test_boj_date_only_bound_and_no_invented_publication(tmp_path):
    with open_writer(tmp_path / "macro.sqlite") as store:
        raw = tmp_path / "raw"
        archive(raw, [dict(event(), title="BOJ Policy Rate", country="JPY", date="2026-10-30", forecast="0.75%")],
                captured="2026-10-29T14:59:59Z")
        assert consensus.ingest(store, raw)["readings_added"] == 1
        row, = rows(store)
        assert row["release_cutoff_at"] == "2026-10-29T15:00:00.000000+00:00"
        assert row["cutoff_precision"] == "date_only" and row["pre_release_eligible"] == 1
        assert store.conn.execute("SELECT COUNT(*) FROM readings WHERE published_at IS NOT NULL").fetchone()[0] == 0
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 0


@pytest.mark.parametrize("family,stage,edition,published,omitted", [
    ("ea_ec_surveys", "flash", "2026-10-22", "ea_consumer_confidence", "ea_economic_sentiment_indicator"),
    ("us_durable_goods", "final", "2026-10-02", "us_durable_goods_orders_mm", "us_durable_goods_ex_transport_mm"),
])
def test_seed_membership_and_empty_queries_follow_the_published_stage(
    tmp_path, family, stage, edition, published, omitted
):
    with open_writer(tmp_path / "stages.sqlite") as store:
        persist_scheduled_events(store, recorded_at="2026-01-01T00:00:00Z")
        members = {r[0] for r in store.conn.execute(
            "SELECT r.indicator_id FROM readings r JOIN release_events e ON e.id=r.release_event_id "
            "WHERE e.family=? AND e.edition=? AND e.estimate_stage=?", (family, edition, stage))}
        assert members == {published}
        for cutoff in (edition + "T00:00:00Z", edition + "T23:59:59Z"):
            explanation = store.explain_empty(omitted, (edition, edition), as_known_at=cutoff)
            assert explanation["reason"] == "no_release_scheduled", explanation
        other_stage = "final" if stage == "flash" else "advance"
        assert store.conn.execute(
            "SELECT 1 FROM readings r JOIN release_events e ON e.id=r.release_event_id "
            "WHERE e.family=? AND e.estimate_stage=? AND r.indicator_id=?",
            (family, other_stage, omitted)).fetchone()


@pytest.mark.parametrize("family,edition,before,at", [
    ("fomc", "2026-10-28", "2026-10-28T17:59:59+00:00", "2026-10-28T18:00:00+00:00"),
    ("us_gdp", "2026-10-29", "2026-10-29T12:29:59+00:00", "2026-10-29T12:30:00+00:00"),
    ("us_cpi", "2026-11-10", "2026-11-10T13:29:59+00:00", "2026-11-10T13:30:00+00:00"),
])
def test_release_instant_gate_and_dst(family, edition, before, at):
    assert edition_status(edition, datetime.fromisoformat(before), family=family) == "scheduled"
    assert edition_status(edition, datetime.fromisoformat(at), family=family) == "due"


@pytest.mark.parametrize("currency,title,edition", [
    ("EUR", "Deposit Facility Rate", "2026-10-29"),
    ("GBP", "Official Bank Rate", "2026-11-05"),
    ("CNY", "1-y Loan Prime Rate", "2026-11-20"),
    ("CAD", "Overnight Rate", "2026-10-28"),
])
def test_other_policy_scalars_link_with_percent_units(tmp_path, currency, title, edition):
    with open_writer(tmp_path / "macro.sqlite") as store:
        archive(tmp_path / "raw", [dict(event(), country=currency, title=title, date=edition, forecast="3.0%")])
        assert consensus.ingest(store, tmp_path / "raw")["readings_added"] == 1
        row, = rows(store)
        assert row["reference_period"] == edition and row["value"] == 3.0


def test_runner_does_not_publish_an_early_same_day_figure(tmp_path, monkeypatch):
    from market_data_hub.macro_releases import run_daily

    class Clock:
        @staticmethod
        def now(tz):
            return datetime.fromisoformat("2026-10-29T12:29:59+00:00")

    class EarlyPrint:
        def __init__(self, client):
            pass

        def fetch(self, *args):
            return [observation("us_gdp_qq_saar", "us_gdp", "2026-Q3", 3.0,
                                "2026-10-29", "advance", "BEA")]

    monkeypatch.setattr(run_daily, "datetime", Clock)
    monkeypatch.setitem(run_daily.ADAPTERS, "us_gdp", EarlyPrint)
    raw = tmp_path / "raw"
    archive(raw, [dict(event(), title="Advance GDP q/q", date="2026-10-29")])
    db = tmp_path / "macro.sqlite"
    result = run_daily.execute(db, ["us_gdp"], 24, False, raw_dir=raw)
    assert result["families"]["us_gdp"]["status"] == "no_change"
    assert result["consensus"]["readings_added"] == 1
    with open_writer(db) as store:
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 0
