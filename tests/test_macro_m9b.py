"""Offline golden certification from unmodified, hashed issuer publications."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest
import requests
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from market_data_hub.macro_releases import open_reader, open_writer, run_daily
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.eurostat_activity import parse as parse_eurostat
from market_data_hub.macro_releases.http import Document, FetchError

ROOT = Path(__file__).parent / "fixtures/macro_releases/m9b"
GOLDEN = json.loads((ROOT / "golden-set.json").read_text())["cases"]


def parse(doc, family, since="1900-01"):
    if family == "us_umich_sentiment":
        from market_data_hub.macro_releases.umich import parse as parse_umich
        return parse_umich(doc, family, since)
    if family == "ea_ec_surveys":
        from market_data_hub.macro_releases.ec_surveys import parse as parse_ec, parse_annex
        if "statistical_annex" in doc.url:
            return parse_annex(doc, document("ec-final.pdf"), since)
        return parse_ec(doc, family, since)
    if family == "uk_total_pay":
        from market_data_hub.macro_releases.ons_pay import parse as parse_pay
        return parse_pay(doc, family, since)
    if family == "us_new_home_sales":
        from market_data_hub.macro_releases.census_sales import parse as parse_census
        return parse_census(doc, family, since)
    if family == "uk_retail_sales":
        from market_data_hub.macro_releases.ons_retail import parse as parse_ons
        return parse_ons(doc, family, since)
    return parse_eurostat(doc, family, since)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    from datetime import datetime, timezone
    from market_data_hub.macro_releases.schedule import RUN_INSTANT
    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 24, 18, tzinfo=timezone.utc)
    monkeypatch.setattr(run_daily, "datetime", Clock)
    def forbidden(*args, **kwargs):
        raise AssertionError("Network forbidden in M9b tests")
    monkeypatch.setattr(requests.Session, "request", forbidden)
    token = RUN_INSTANT.set(datetime(2026, 10, 24, 18, tzinfo=timezone.utc))
    yield
    RUN_INSTANT.reset(token)


def document(name):
    body = (ROOT / name).read_bytes()
    meta = json.loads((ROOT / (name + ".meta.json")).read_text())
    assert hashlib.sha256(body).hexdigest() == meta["sha256"]
    return Document(body, meta["url"], meta["retrieved_at"], meta["sha256"])


@pytest.mark.parametrize("case", GOLDEN, ids=lambda c: c["family"] + ":" + c["edition"])
def test_golden(case, tmp_path):
    rows = parse(document(case["fixture"]), case["family"], "2026-01")
    row = next(o for o in rows if o.indicator_id == case["indicator"] and o.period == case["period"])
    assert (row.value, row.edition, row.stage) == (case["value"], case["edition"], case["stage"])
    assert row.document.url == case["source_url"]
    with open_writer(tmp_path / "golden.sqlite") as store:
        persist(store, [row])
        assert store.latest_official(row.indicator_id)[0]["value"] == case["value"]


def test_unknown_eurostat_edition_and_membership_fail_closed():
    doc = document("ea-unemployment.html")
    for before, after in ((b"2026-10-01", b"2026-10-02"), (b"Euro area (EA21)", b"Euro area (EA22)"),
                          (b"seasonally adjusted unemployment rate", b"unadjusted unemployment rate")):
        with pytest.raises(FetchError, match="needs review"):
            parse(replace(doc, body=doc.body.replace(before, after)), "ea_unemployment")


class Replay:
    def __init__(self, **kwargs):
        pass

    def get(self, provider, url, params=None):
        if url.endswith("/calendars/eventsJson"):
            return document("calendar.json")
        names = {c["fixture"] for c in GOLDEN} | {"ec-latest.html"}
        for name in sorted(names):
            doc = document(name)
            target = urlsplit(doc.url)
            if urlunsplit(target._replace(query="")) != url:
                continue
            if dict(parse_qsl(target.query)) != (params or {}):
                continue
            return doc
        raise AssertionError("Unexpected or future issuer request: " + url)


def test_all_families_daily_cards_editions_and_idempotence(tmp_path, monkeypatch):
    from market_data_hub.macro_releases.catalogue import REMAINING_FAMILIES
    monkeypatch.setattr(run_daily, "HttpClient", Replay)
    db = tmp_path / "m9b.sqlite"
    result = run_daily.execute(db, list(REMAINING_FAMILIES), 24, False, raw_dir=tmp_path / "none")
    assert result["status"] == "ok", result
    assert all(r["status"] == "ok" and r["vintages_added"] > 0 for r in result["families"].values())
    for family in result["families"].values():
        assert family["scheduled_editions"]
        assert all(e["status"] == "scheduled" and e["edition"] > "2026-10-24" for e in family["scheduled_editions"])
    with open_reader(db) as store:
        cards = store.release_cards("2026-10-19", "2026-10-23")["cards"]
        assert {c["indicator_id"]: c["resolution"]["value"] for c in cards} == {
            "uk_retail_sales_mm": -0.4, "uk_average_weekly_earnings_yy": 3.7,
            "ea_consumer_confidence": -17.9, "us_umich_consumer_sentiment": 48.0}
        assert all(c["resolution"]["status"] == "official" for c in cards)
        rows = store.conn.execute("SELECT estimate_stage,value FROM value_vintages WHERE indicator_id='us_umich_consumer_sentiment' AND reference_period='2026-09' ORDER BY issuer_published_at").fetchall()
        assert [tuple(r) for r in rows] == [("preliminary", 47.8), ("final", 48.1)]
        rows = store.conn.execute("SELECT estimate_stage,value FROM value_vintages WHERE indicator_id='ea21_core_hicp_yy' ORDER BY issuer_published_at").fetchall()
        assert [tuple(r) for r in rows] == [("flash", 2.5), ("final", 2.5)]
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages WHERE indicator_id='ea_economic_sentiment_indicator' AND issuer_published_at LIKE '2026-10-22%'").fetchone()[0] == 0
    again = run_daily.execute(db, list(REMAINING_FAMILIES), 24, False, raw_dir=tmp_path / "none")
    assert again["status"] == "no_change", again


def test_unknown_format_retains_document_and_requires_review(tmp_path, monkeypatch):
    class Changed(Replay):
        def get(self, *args, **kwargs):
            doc = super().get(*args, **kwargs)
            return replace(doc, body=doc.body.replace(b"Retail sales volumes are estimated", b"Retail sales values are estimated"))
    monkeypatch.setattr(run_daily, "HttpClient", Changed)
    db = tmp_path / "review.sqlite"
    result = run_daily.execute(db, ["uk_retail_sales"], 24, False, raw_dir=tmp_path / "none")
    assert result["families"]["uk_retail_sales"]["status"] == "needs_review", result
    with open_reader(db) as store:
        assert store.conn.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0] > 0
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 0


def test_ec_unreviewed_annex_typo_and_wrong_companion_fail_closed():
    from market_data_hub.macro_releases.ec_surveys import parse_annex
    with pytest.raises(FetchError, match="needs review"):
        parse_annex(replace(document("ec-annex.pdf"), document_hash="unreviewed"), document("ec-final.pdf"))
    with pytest.raises(FetchError, match="needs review"):
        parse_annex(document("ec-annex.pdf"), document("ec-flash.pdf"))


def test_michigan_public_headline_unknown_stage_or_next_release_fails_closed():
    doc = document("umich.html")
    for before, after in ((b"Final Results", b"Unknown Results"), (b"November 6, 2026", b"November 7, 2026"),
                          (b"October 2026", b"January 2027")):
        with pytest.raises(FetchError, match="needs review"):
            parse(replace(doc, body=doc.body.replace(before, after)), "us_umich_sentiment")


def test_eurostat_changed_calendar_period_fails_closed():
    from market_data_hub.macro_releases.eurostat_activity import calendar_editions
    doc = document("calendar.json")
    with pytest.raises(FetchError, match="needs review"):
        calendar_editions(replace(doc, body=doc.body.replace(b'"period":"August 2026"', b'"period":"July 2026"')), "ea_retail_sales")


def test_ec_latest_rollover_only_requires_uncollected_official_editions(tmp_path, monkeypatch):
    from datetime import datetime
    from market_data_hub.macro_releases import ec_surveys
    from market_data_hub.macro_releases.publication_times import local_release
    template = ec_surveys.parse(document("ec-flash.pdf"))[0]
    monkeypatch.setitem(ec_surveys.EDITIONS, "ea_ec_surveys", {
        "2026-10-22": ("2026-10", "flash", "16:00"),
        "2026-11-20": ("2026-11", "flash", "16:00"),
    })
    monkeypatch.setattr(ec_surveys, "ARCHIVES", {})
    state = {"date": "2026-10-24", "edition": "2026-10-22", "requests": []}
    class Clock:
        @staticmethod
        def now(tz):
            return datetime.fromisoformat(state["date"] + "T18:00:00+00:00")
    class Rolling:
        def __init__(self, **kwargs):
            pass
        def get(self, provider, url, params=None):
            state["requests"].append(url)
            period = state["edition"][:7]
            name = "Flash_consumer_" + period.replace("-", "_") + "_en.pdf"
            if url == ec_surveys.LATEST:
                body = ('<a href="/document/download/current_en?filename=' + name + '">Flash</a>').encode()
            else:
                assert (params or {}) == {"filename": name}
                body = state["edition"].encode()
            return Document(body, url, state["date"] + "T18:00:00Z", hashlib.sha256(body).hexdigest())
    def parse_flash(doc, family, since):
        edition = doc.body.decode()
        return [replace(template, document=doc, edition=edition, period=edition[:7],
                        **local_release(edition, "16:00", "Europe/Luxembourg"))]
    monkeypatch.setattr(run_daily, "datetime", Clock)
    monkeypatch.setattr(run_daily, "HttpClient", Rolling)
    monkeypatch.setattr(ec_surveys, "parse", parse_flash)
    db = tmp_path / "rollover.sqlite"
    first = run_daily.execute(db, ["ea_ec_surveys"], 24, False, raw_dir=tmp_path / "none")
    assert first["families"]["ea_ec_surveys"]["status"] == "ok", first
    # The next latest page has no October link. October must not be fetched again.
    state.update(date="2026-11-21", edition="2026-11-20", requests=[])
    second = run_daily.execute(db, ["ea_ec_surveys"], 24, False, raw_dir=tmp_path / "none")
    assert second["families"]["ea_ec_surveys"]["status"] == "ok", second
    assert second["families"]["ea_ec_surveys"]["vintages_added"] == 1
    with open_reader(db) as store:
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 2
    state["requests"] = []
    third = run_daily.execute(db, ["ea_ec_surveys"], 24, False, raw_dir=tmp_path / "none")
    assert third["families"]["ea_ec_surveys"]["status"] == "no_change", third
    assert state["requests"] == []


def test_ec_partial_final_edition_must_not_be_skipped(tmp_path):
    from market_data_hub.macro_releases.ec_surveys import parse_annex, stored_official_editions
    rows = parse_annex(document("ec-annex.pdf"), document("ec-final.pdf"))
    with open_writer(tmp_path / "partial.sqlite") as store:
        persist(store, rows[:1])
        assert "2026-09-29" not in stored_official_editions(store)
        persist(store, rows[1:])
        assert "2026-09-29" in stored_official_editions(store)


def test_each_new_family_has_two_issuer_golden_cases_and_schedule_url():
    from market_data_hub.macro_releases.catalogue import REMAINING_FAMILIES
    from market_data_hub.macro_releases.tier1_editions import EDITIONS, SCHEDULES
    for family in REMAINING_FAMILIES:
        assert len([c for c in GOLDEN if c["family"] == family]) >= 2
        assert SCHEDULES[family].startswith("https://")
        assert all(d <= "2026-12-31" for d in EDITIONS[family])


def test_eurostat_calendar_cache_is_one_unchanged_document_per_run(monkeypatch):
    from market_data_hub.macro_releases.http import HttpClient
    from market_data_hub.macro_releases.eurostat_activity import CALENDAR
    calls = []
    client = HttpClient(persistent_rates=False)
    doc = document("calendar.json")
    def request(provider, url, *, params=None):
        calls.append((provider, url, params))
        return doc
    monkeypatch.setattr(client, "request", request)
    params = {"start": "2026-09-01T00:00:00Z", "end": "2027-01-01T00:00:00Z"}
    assert client.get("eurostat", CALENDAR, params) is doc
    assert client.get("eurostat", CALENDAR, dict(reversed(list(params.items())))) is doc
    assert len(calls) == 1
    client.get("eurostat", CALENDAR, {**params, "theme": "2"})
    assert len(calls) == 2


def test_long_provider_interval_is_distinct_from_clock_skew(monkeypatch):
    from market_data_hub.macro_releases import http
    sleeps = []
    monkeypatch.setattr(http.time, "time", lambda: 1000.0)
    monkeypatch.setattr(http.time, "sleep", sleeps.append)
    limiter = http.RateLimiter("eurostat", interval=15.0, persistent=False)
    limiter.acquire()
    limiter.acquire()
    assert sleeps == [15.0]
    limiter.state["last"] = 1006.0
    with pytest.raises(FetchError, match="clock is ahead"):
        limiter.acquire()
