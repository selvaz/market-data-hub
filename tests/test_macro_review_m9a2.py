"""Independent-review regressions; saved publications only, never live HTTP."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json

import pytest
import requests

from market_data_hub.macro_releases import open_reader, open_writer, run_daily, schedule
from market_data_hub.macro_releases.http import FetchError
from test_macro_us_monthly import Replay as MonthlyReplay, document as monthly_document
from test_macro_m9a2 import Replay, document


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Live network forbidden in review regressions")
    monkeypatch.setattr(requests.Session, "request", forbidden)
    # Discovery imports its own api_key binding; never use host credentials.
    for module in ("fred", "bls", "bea", "discovery"):
        monkeypatch.setattr("market_data_hub.macro_releases." + module + ".api_key", lambda _: "fixture")


@contextmanager
def at(instant):
    token = schedule.RUN_INSTANT.set(instant)
    pending = schedule.PENDING_EDITIONS.set({})
    try:
        yield
    finally:
        schedule.PENDING_EDITIONS.reset(pending)
        schedule.RUN_INSTANT.reset(token)


def collect(tmp_path, client, family, instant):
    db = tmp_path / "review.sqlite"
    result = run_daily.collect_family(lambda: open_writer(db), client, "review", family,
        {family: {"fetch_since": "2026-06", "fetch_until": instant[:10]}}, dry_run=True, run_at=instant)[family]
    return db, result


@pytest.mark.parametrize("family,edition,before,release", [
    ("us_jolts", "2026-09-29", "2026-09-29T13:30:00Z", "2026-09-29T14:00:00Z"),
    ("us_durable_goods", "2026-10-02", "2026-10-02T13:30:00Z", "2026-10-02T14:00:00Z"),
    ("ea_gdp", "2026-10-20", "2026-10-20T08:30:00Z", "2026-10-20T09:00:00Z"),
    ("ea_gdp", "2026-12-07", "2026-12-07T09:30:00Z", "2026-12-07T10:00:00Z"),
    ("uk_cpi", "2026-10-21", "2026-10-21T05:30:00Z", "2026-10-21T06:00:00Z"),
    ("us_jobless_claims", "2026-10-22", "2026-10-22T12:00:00Z", "2026-10-22T12:30:00Z"),
])
def test_due_uses_issuer_release_instant_and_dst(family, edition, before, release):
    calendar = schedule.family_editions(family)
    assert edition not in schedule.due(calendar, before, family=family)
    assert schedule.edition_status(edition, before, family=family) == "scheduled"
    assert edition in schedule.due(calendar, release, family=family)


def test_1330_run_keeps_previous_m3_edition_without_review(tmp_path):
    db, result = collect(tmp_path, MonthlyReplay(), "us_durable_goods", "2026-10-02T13:30:00Z")
    assert result["status"] == "ok", result
    assert any(d["edition"] == "2026-10-02" for d in result["scheduled_editions"])
    with open_reader(db) as store:
        assert not store.conn.execute("SELECT 1 FROM value_vintages v JOIN release_events e ON e.id=v.release_event_id WHERE e.edition='2026-10-02'").fetchone()
        rows = store.latest_official("us_durable_goods_orders_mm")
        assert any(r["reference_period"] == "2026-08" and r["value"] == 0 for r in rows)


def test_pre_release_claims_keeps_previous_publication(tmp_path):
    class Before(Replay):
        def get(self, provider, url, params=None):
            if url == "https://www.dol.gov/ui/data.pdf":
                return document("dol-2026-10-15.pdf")
            return super().get(provider, url, params)
    _, result = collect(tmp_path, Before(), "us_jobless_claims", "2026-10-22T12:00:00Z")
    assert result["status"] == "ok", result
    assert any(d["edition"] == "2026-10-22" for d in result["scheduled_editions"])


@pytest.mark.parametrize("family,edition,url,provider", [
    ("us_pce", "2026-10-29", "https://www.bea.gov/news/2026/personal-income-and-outlays-september-2026", "bea"),
    ("us_industrial_production", "2026-10-16", "https://www.federalreserve.gov/releases/g17/20261016/", "fred"),
    ("ea_gdp", "2026-10-30", "https://ec.europa.eu/eurostat/web/products-euro-indicators/w/2-30102026-ap", "eurostat"),
])
def test_newly_due_url_404_is_pending_but_old_404_is_error(family, edition, url, provider):
    class Missing:
        def get(self, *args):
            raise FetchError(provider + ": HTTP 404")
    release = schedule.release_instant(edition, family=family)
    from datetime import timedelta
    with at(release + timedelta(hours=1)):
        assert schedule.get_edition(Missing(), provider, url, family, edition) is None
        assert schedule.edition_status(edition, family=family) == "scheduled"
    with at(release + timedelta(hours=6)):
        with pytest.raises(FetchError, match="404"):
            schedule.get_edition(Missing(), provider, url, family, edition)


def test_recent_fred_head_delay_keeps_previous_edition(tmp_path):
    _, result = collect(tmp_path, MonthlyReplay(), "us_housing_starts", "2026-11-18T14:00:00Z")
    assert result["status"] == "ok", result
    assert any(d["edition"] == "2026-11-18" and d.get("reason") == "not_yet_published"
               for d in result["scheduled_editions"])


def test_execute_carries_full_utc_instant_to_fetchers(tmp_path, monkeypatch):
    observed = []
    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 2, 13, 30, tzinfo=timezone.utc)
    class Capture(MonthlyReplay):
        def get(self, *args, **kwargs):
            observed.append(schedule.run_instant())
            return super().get(*args, **kwargs)
    monkeypatch.setattr(run_daily, "datetime", Clock)
    monkeypatch.setattr(run_daily, "HttpClient", Capture)
    result = run_daily.execute(tmp_path / "clock.sqlite", ["us_durable_goods"], 24, True,
                               raw_dir=tmp_path / "none")
    assert result["status"] == "ok", result
    assert observed and all(t == Clock.now(None) for t in observed)


def test_new_pce_url_404_retains_old_official_values_without_review(tmp_path):
    class Late(MonthlyReplay):
        def get(self, provider, url, params=None):
            if url.endswith("personal-income-and-outlays-september-2026"):
                raise FetchError("bea: HTTP 404")
            return super().get(provider, url, params)
    db, result = collect(tmp_path, Late(), "us_pce", "2026-10-29T13:30:00Z")
    assert result["status"] == "ok", result
    assert any(d["edition"] == "2026-10-29" and d.get("reason") == "not_yet_published"
               for d in result["scheduled_editions"])
    with open_reader(db) as store:
        assert all(r["issuer_published_at"].startswith("2026-09-30") or r["issuer_published_at"].startswith("2026-08-26")
                   for r in store.latest_official("us_core_pce_mm"))


def test_gdp_update_is_not_requested_before_local_release_time(tmp_path):
    class Before(Replay):
        def get(self, provider, url, params=None):
            assert not url.endswith("/data/namq_10_gdp")
            return super().get(provider, url, params)
    _, result = collect(tmp_path, Before(), "ea_gdp", "2026-10-20T08:30:00Z")
    assert result["status"] == "ok", result
    assert any(d["edition"] == "2026-10-20" for d in result["scheduled_editions"])


def changed(doc, payload):
    body = json.dumps(payload).encode()
    return replace(doc, body=body, document_hash=hashlib.sha256(body).hexdigest())


@pytest.mark.parametrize("updated", ["2026-10-19T23:00:00+0200", "2026-10-20T10:59:59+02:00",
                                    "2026-10-20T11:00:00", None])
def test_gdp_update_cannot_relabel_an_older_or_unconfirmed_snapshot(updated):
    from market_data_hub.macro_releases.eurostat_gdp import parse_snapshot
    doc = document("ea-gdp.json")
    payload = doc.json()
    payload["updated"] = updated
    with pytest.raises(FetchError, match="needs review"):
        parse_snapshot(changed(doc, payload), "2026-10-20")
    payload["updated"] = "2026-10-20T11:00:00+02:00"
    assert parse_snapshot(changed(doc, payload), "2026-10-20")


def test_gdp_nightly_refresh_without_release_adds_no_verified_vintage(tmp_path):
    from market_data_hub.macro_releases.eurostat_gdp import parse_snapshot
    doc = document("ea-gdp.json")
    payload = doc.json()
    payload["updated"] = "2026-10-23T23:00:00+0200"
    refresh = changed(doc, payload)
    with open_writer(tmp_path / "refresh.sqlite") as store:
        first = run_daily.persist_counts(store, parse_snapshot(doc, "2026-10-20"))
        second = run_daily.persist_counts(store, parse_snapshot(refresh, "2026-10-20"))
        assert first["vintages_added"] == 4
        assert second["vintages_added"] == second["readings_added"] == 0
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages WHERE verification_status='verified'").fetchone()[0] == 4
        assert not store.conn.execute("SELECT 1 FROM value_vintages GROUP BY indicator_id,reference_period,release_event_id HAVING COUNT(*)>1").fetchone()


def test_census_estimate_stages_follow_printed_labels():
    from market_data_hub.macro_releases.us_publications import parse_retail, parse_housing, parse_durable, parse_durable_full
    from market_data_hub.macro_releases.us_editions import observation_stage
    retail = parse_retail(monthly_document("retail.pdf"))
    assert {o.stage for o in retail if o.period == "2026-09"} == {"advance"}
    assert {o.stage for o in retail if o.period == "2026-08"} == {"preliminary"}
    assert observation_stage("us_retail_sales", "2026-10-15", "2026-07") == "revised"
    housing = parse_housing(monthly_document("housing.pdf"))
    assert {o.stage for o in housing if o.period == "2026-08"} == {"revised"}
    assert all(o.event_stage == "regular" for o in housing)
    advance = parse_durable(monthly_document("durable.pdf"))
    full = parse_durable_full(document("census-m3.pdf"))
    assert {o.stage for o in advance if o.period == "2026-08"} == {"advance"}
    assert {o.stage for o in full if o.period == "2026-08"} == {"final"}
    assert {o.stage for o in [*advance, *full] if o.period == "2026-07"} == {"revised"}
    assert {o.indicator_id for o in advance} == {"us_durable_goods_orders_mm", "us_durable_goods_ex_transport_mm"}


def test_m3_current_pdf_must_match_latest_due_final():
    from market_data_hub.macro_releases.us_publications import fetch_publications
    with at("2026-10-24T13:30:00Z"):
        rows = fetch_publications(MonthlyReplay(), ["us_durable_goods"], "2026-06")
        assert any(o.edition == "2026-10-02" and o.stage == "final" for o in rows)
    # The identical October 2 issuer PDF must not stand in for November 3.
    with at("2026-11-03T22:00:00Z"):
        with pytest.raises(FetchError, match="latest due final.*needs review"):
            fetch_publications(MonthlyReplay(), ["us_durable_goods"], "2026-06")


def test_m3_previous_final_within_publication_grace_is_scheduled():
    from market_data_hub.macro_releases.us_publications import fetch_publications
    with at("2026-11-03T15:30:00Z"):
        rows = fetch_publications(MonthlyReplay(), ["us_durable_goods"], "2026-06")
        assert rows and all(o.edition != "2026-11-03" for o in rows)
        assert schedule.edition_status("2026-11-03", family="us_durable_goods") == "scheduled"


@pytest.mark.parametrize("prior,headline", [("August", "July"), ("June", "August"),
                                           ("July", "September"), ("", "August")])
def test_pce_publication_checks_month_headers_before_mapping_cells(prior, headline):
    from market_data_hub.macro_releases.us_publications import parse_pce_publication
    doc = monthly_document("pce.html")
    body = doc.body.replace(b">July</td>", b">PRIOR_MONTH</td>").replace(b">August</td>", b">HEADLINE_MONTH</td>")
    body = body.replace(b">PRIOR_MONTH</td>", (">" + prior + "</td>").encode())
    body = body.replace(b">HEADLINE_MONTH</td>", (">" + headline + "</td>").encode())
    assert body != doc.body
    with pytest.raises(FetchError, match="month headers.*review"):
        parse_pce_publication(replace(doc, body=body))
    good = parse_pce_publication(doc)
    assert {(o.period, o.value) for o in good if o.indicator_id == "us_pce_price_index_mm"} == {("2026-07", 0.1), ("2026-08", 0.3)}


@pytest.mark.parametrize("family", ["us_cpi", "us_payrolls"])
def test_malformed_bls_rss_deliberately_reviews_already_reviewed_edition(tmp_path, family):
    from test_macro_daily_review import Replay as PilotReplay
    client = PilotReplay(family)
    client.docs["listing"] = replace(client.docs["listing"], body=b"<rss><channel><broken>")
    db, result = collect(tmp_path, client, family, "2026-12-01T13:30:00Z")
    assert result["status"] == "needs_review", result
    assert "issuer_publication_unconfirmed" in result["review_reasons"]
    assert result["vintages_added"] == 0 and result["readings_added"] > 0
    with open_reader(db) as store:
        assert store.conn.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0] > 0


def test_pce_header_mismatch_is_reviewed_by_daily_collector(tmp_path):
    class WrongHeader(MonthlyReplay):
        def get(self, *args, **kwargs):
            doc = super().get(*args, **kwargs)
            if doc.url.endswith("personal-income-and-outlays-august-2026"):
                body = doc.body.replace(b">July</td>", b">June</td>")
                return replace(doc, body=body, document_hash=hashlib.sha256(body).hexdigest())
            return doc
    _, result = collect(tmp_path, WrongHeader(), "us_pce", "2026-10-24T13:30:00Z")
    assert result["status"] == "needs_review", result
    assert result["vintages_added"] == 0
