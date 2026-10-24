"""Offline public NAR headline certification; private issuer labels are explicit."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib

import pytest
import requests

from market_data_hub.macro_releases import open_reader, open_writer, run_daily
from market_data_hub.macro_releases import nar
from market_data_hub.macro_releases.housing_editions import EDITIONS
from market_data_hub.macro_releases.http import FetchError
from market_data_hub.macro_releases.schedule import PENDING_EDITIONS, RUN_INSTANT
from market_data_hub.macro_releases.tier1_publications import plain
from test_macro_m9f import document


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("NAR tests must not use network")
    monkeypatch.setattr(requests.Session, "request", forbidden)
    token = RUN_INSTANT.set(datetime(2026, 10, 24, 18, tzinfo=timezone.utc))
    yield
    RUN_INSTANT.reset(token)


def test_nar_calendar_matches_saved_issuer_schedule():
    body = plain(document("nar-calendar.html").body.decode())
    assert "All releases are distributed at 10 a.m. Eastern Time" in body
    labels = {"us_existing_home_sales": "Existing-Home Sales", "us_pending_home_sales": "Pending Home Sales Index"}
    for family in nar.LANDINGS:
        for edition, (period, stage, time) in EDITIONS[family].items():
            day = datetime.strptime(edition, "%Y-%m-%d")
            month = datetime.strptime(period, "%Y-%m")
            stamp = ("Sep." if day.month == 9 else day.strftime("%b.")) + " " + str(day.day)
            assert stamp + " " + month.strftime("%B") + " " + labels[family] in body
            assert stage == "regular" and time == "10:00"


class Replay:
    def __init__(self, **kwargs):
        self.requests = []

    def get(self, provider, url, params=None):
        assert provider == "nar" and params is None
        self.requests.append(url)
        for name in ("nar-existing-index.html", "nar-existing-sept.html", "nar-pending-index.html", "nar-pending-sept.html"):
            doc = document(name)
            if doc.url == url:
                return doc
        raise AssertionError("Unexpected NAR request: " + url)


@pytest.mark.parametrize("family", list(nar.LANDINGS))
def test_daily_private_headline_cards_and_idempotence(family, tmp_path, monkeypatch):
    monkeypatch.setattr(run_daily, "HttpClient", Replay)
    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 24, 18, tzinfo=timezone.utc)
    monkeypatch.setattr(run_daily, "datetime", Clock)
    db = tmp_path / "nar.sqlite"
    expected = ({"us_existing_home_sales": 3.99, "us_existing_home_sales_mm": 1.5}
                if family == "us_existing_home_sales" else {"us_pending_home_sales_mm": -7.3})
    result = run_daily.execute(db, [family], 24, False, raw_dir=tmp_path / "none")
    assert (result["families"][family]["status"], result["families"][family]["vintages_added"]) == ("ok", len(expected)), result
    with open_reader(db) as store:
        cards = store.release_cards("2026-10-01", "2026-10-24", indicators=list(expected))["cards"]
        assert {c["indicator_id"]: c["resolution"]["value"] for c in cards} == expected
        assert all(c["source_class"] == "private" and c["issuer_id"] == "NAR" and
                   c["resolution"]["status"] == "official" and c["reference_period"] == "2026-09" for c in cards)
        if family == "us_existing_home_sales":
            level = next(c for c in cards if c["indicator_id"] == family)
            assert level["resolution"]["scale"] == 1000000
            assert level["released_at"] == "2026-10-13T14:01:00.000000+00:00"
    again = run_daily.execute(db, [family], 24, False, raw_dir=tmp_path / "none")
    assert again["families"][family]["status"] == "no_change", again


@pytest.mark.parametrize("before,after", [
    (b"Existing-Home Sales Increased", b"New-Home Sales Increased"),
    (b"1.5% increase in existing-home sales", b"2.5% increase in existing-home sales"),
    (b"seasonally adjusted annual rate of 3.99", b"unadjusted annual rate of 3.99"),
    (b"3.99 million in September", b"3.99 billion in September"),
    (b"3.99 million in September", b"3.99 million in August"),
    (b"2026-10-13T09:01:00-05:00", b"2026-10-13T09:01:00"),
    (b"2026-10-13T09:01:00-05:00", b"2026-10-14T09:01:00-05:00"),
])
def test_existing_headline_measure_date_or_disagreement_fails_closed(before, after):
    doc = document("nar-existing-sept.html")
    assert before in doc.body
    with pytest.raises(FetchError, match="needs review"):
        nar.parse_existing(replace(doc, body=doc.body.replace(before, after)))


def test_nar_link_discovery_only_accepts_one_publisher_press_release():
    doc = document("nar-existing-index.html")
    link = nar.release_link(doc, "us_existing_home_sales")
    assert link == document("nar-existing-sept.html").url
    for body in (b'<a href="https://example.com/newsroom/existing-home-sales">Read the full news release</a>',
                 b'<a href="https://www.nar.realtor/overview/existing-home-sales">Read the full news release</a>',
                 b'<a href="/newsroom/a-existing-home-sales">Read the full news release</a><a href="/newsroom/b-existing-home-sales">Read the full news release</a>'):
        with pytest.raises(FetchError, match="needs review"):
            nar.release_link(replace(doc, body=body), "us_existing_home_sales")
    with pytest.raises(FetchError, match="needs review"):
        nar.parse_existing(document("nar-existing-index.html"))


@pytest.mark.parametrize("before,after", [
    (b"Pending Home Sales Decreased 7.3%", b"Existing Home Sales Decreased 7.3%"),
    (b"Pending Home Sales Decreased 7.3%", b"Pending Home Sales Increased 7.3%"),
    (b"7.3% decrease in pending home sales", b"11.3% decrease in pending home sales"),
    (b"7.3% decrease in pending home sales", b"7.3% increase in pending home sales"),
    (b"7.3% decrease in pending home sales", b"7.3% decrease in regional pending home sales"),
    (b"Pending home sales in September decreased", b"Pending home sales in August decreased"),
    (b"7.3% month-over-month", b"7.3% year-over-year"),
    (b"2026-10-20T09:00:00-05:00", b"2026-10-21T09:00:00-05:00"),
])
def test_pending_measure_direction_reference_or_clock_disagreement_fails_closed(before, after):
    doc = document("nar-pending-sept.html")
    assert before in doc.body
    with pytest.raises(FetchError, match="needs review"):
        nar.parse_pending(replace(doc, body=doc.body.replace(before, after)))


def test_pending_normalizes_issuer_link_and_gates_calendar_without_using_overview_values():
    doc = document("nar-pending-index.html")
    assert nar.release_link(doc, "us_pending_home_sales") == document("nar-pending-sept.html").url
    with pytest.raises(FetchError, match="needs review"):
        nar.parse_pending(doc)
    for instant, count in (("2026-10-20T13:59:59Z", 0), ("2026-10-20T14:00:00Z", 1)):
        token = RUN_INSTANT.set(instant)
        try:
            assert len(nar.fetch(Replay(), "us_pending_home_sales", "2026-01")) == count
        finally:
            RUN_INSTANT.reset(token)
    assert nar.parse_pending(document("nar-pending-sept.html"), since="2026-10") == []
    token = RUN_INSTANT.set("2026-11-18T21:00:00Z")
    try:
        with pytest.raises(FetchError, match="needs review"):
            nar.fetch(Replay(), "us_pending_home_sales", "2026-01")
    finally:
        RUN_INSTANT.reset(token)


def test_calendar_and_actual_page_clock_both_gate_existing_release():
    client = Replay()
    token = RUN_INSTANT.set("2026-08-11T13:59:59Z")
    try:
        assert nar.fetch(client, "us_existing_home_sales", "2026-01") == []
        assert client.requests == []
    finally:
        RUN_INSTANT.reset(token)
    for instant, count in (("2026-10-13T13:59:59Z", 0), ("2026-10-13T14:00:00Z", 0),
                           ("2026-10-13T14:01:00Z", 2)):
        token = RUN_INSTANT.set(instant)
        pending = PENDING_EDITIONS.set({})
        try:
            assert len(nar.fetch(client, "us_existing_home_sales", "2026-01")) == count
            if instant == "2026-10-13T14:00:00Z":
                assert ("us_existing_home_sales", "2026-10-13") in PENDING_EDITIONS.get()
        finally:
            RUN_INSTANT.reset(token)
            PENDING_EDITIONS.reset(pending)
    token = RUN_INSTANT.set("2026-11-12T21:00:00Z")
    try:
        with pytest.raises(FetchError, match="needs review"):
            nar.fetch(client, "us_existing_home_sales", "2026-01")
    finally:
        RUN_INSTANT.reset(token)


def test_changed_nar_document_is_retained_without_any_vintage(tmp_path, monkeypatch):
    class Changed(Replay):
        def get(self, *args, **kwargs):
            doc = super().get(*args, **kwargs)
            body = doc.body.replace(b"1.5% increase in existing-home sales", b"2.5% increase in existing-home sales")
            return replace(doc, body=body, document_hash=hashlib.sha256(body).hexdigest())
    monkeypatch.setattr(run_daily, "HttpClient", Changed)
    result = run_daily.execute(tmp_path / "review.sqlite", ["us_existing_home_sales"], 24, False, raw_dir=tmp_path / "none")
    assert result["families"]["us_existing_home_sales"]["status"] == "needs_review", result
    with open_reader(tmp_path / "review.sqlite") as store:
        assert store.conn.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0] >= 1
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 0


def test_retired_press_release_redirect_requires_review():
    class Redirect(Replay):
        def get(self, provider, url, params=None):
            if "/newsroom/" in url:
                raise FetchError("nar: HTTP 301")
            return super().get(provider, url, params)
    with pytest.raises(FetchError, match="redirect needs review"):
        nar.fetch(Redirect(), "us_existing_home_sales", "2026-01")


def test_existing_exact_ff_alias_and_draft_only_context(tmp_path):
    from market_data_hub.indicator_cards import indicator_card
    from market_data_hub.macro_releases.catalogue import INDICATORS
    from market_data_hub.macro_releases.consensus import Catalogue
    with open_writer(tmp_path / "aliases.sqlite") as store:
        for indicator in INDICATORS.values():
            store.put(indicator)
        catalogue = Catalogue(store)
        alias, reason = catalogue.match({"country": "USD"}, "Existing Home Sales")
        assert reason is None and alias["indicator_id"] == "us_existing_home_sales"
        assert catalogue.match({"country": "USD"}, "Existing Home Sales m/m")[0] is None
        assert catalogue.match({"country": "CAD"}, "Existing Home Sales")[0] is None
    assert indicator_card("cat:us_existing_home_sales") is None


def test_pending_exact_ff_alias_and_draft_only_context(tmp_path):
    from market_data_hub.indicator_cards import indicator_card
    from market_data_hub.macro_releases.catalogue import INDICATORS
    from market_data_hub.macro_releases.consensus import Catalogue
    with open_writer(tmp_path / "aliases.sqlite") as store:
        for indicator in INDICATORS.values():
            store.put(indicator)
        catalogue = Catalogue(store)
        alias, reason = catalogue.match({"country": "USD"}, "Pending Home Sales m/m")
        assert reason is None and alias["indicator_id"] == "us_pending_home_sales_mm"
        assert catalogue.match({"country": "USD"}, "Pending Home Sales")[0] is None
        assert catalogue.match({"country": "USD"}, "Pending Home Sales y/y")[0] is None
        assert catalogue.match({"country": "CAD"}, "Pending Home Sales m/m")[0] is None
    assert indicator_card("cat:us_pending_home_sales_mm") is None
