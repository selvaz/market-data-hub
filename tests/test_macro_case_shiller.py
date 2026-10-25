"""Real public S&P headlines; optional FRED history stays a redistributor."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from market_data_hub.macro_releases import open_reader, open_writer, run_daily
from market_data_hub.macro_releases import case_shiller as cs
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.fred import FREDAdapter
from market_data_hub.macro_releases.housing_editions import EDITIONS
from market_data_hub.macro_releases.http import FetchError
from market_data_hub.macro_releases.schedule import PENDING_EDITIONS, RUN_INSTANT
from market_data_hub.macro_releases.tier1_publications import plain
from test_macro_m9f import document


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Case-Shiller tests must not use network")
    monkeypatch.setattr(requests.Session, "request", forbidden)
    # Stub every imported credential binding; never consult this host.
    for module in ("fred", "bls", "bea", "discovery"):
        monkeypatch.setattr("market_data_hub.macro_releases." + module + ".api_key", lambda _: "fixture")
    token = RUN_INSTANT.set(datetime(2026, 10, 24, 18, tzinfo=timezone.utc))
    yield
    RUN_INSTANT.reset(token)


class Replay:
    def __init__(self, **kwargs):
        self.requests = []

    def get(self, provider, url, params=None):
        assert provider == "spglobal"
        self.requests.append(url)
        parts = urlsplit(cs.INDEX)
        if url == cs.INDEX or url == cs.INDEX.split("?")[0]:
            if params:
                assert params == {k: v[0] for k, v in parse_qs(parts.query).items()}
            return document("case-shiller-press-index.html")
        assert not params
        for name in ("case-shiller-press-aug.html", "case-shiller-press-sept.html"):
            doc = document(name)
            if doc.url == url:
                return doc
        raise AssertionError("Unexpected S&P request: " + url)


def test_reviewed_calendar_is_last_tuesday_at_nine_et_from_saved_issuer_rule():
    for name in ("case-shiller-press-aug.html", "case-shiller-press-sept.html"):
        text = plain(document(name).body.decode())
        assert "published on the last Tuesday of each month at 9:00 am ET" in text
    for edition, (period, stage, time) in EDITIONS[cs.FAMILY].items():
        day = datetime.fromisoformat(edition)
        assert day.weekday() == 1 and (day + timedelta(days=7)).month != day.month
        assert stage == "regular" and time == "09:00"
        assert int(day.strftime("%Y%m")) - int(period.replace("-", "")) == 2


def test_daily_private_headline_cards_archive_editions_and_idempotence(tmp_path, monkeypatch):
    monkeypatch.setattr(run_daily, "HttpClient", Replay)
    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 24, 18, tzinfo=timezone.utc)
    monkeypatch.setattr(run_daily, "datetime", Clock)
    db = tmp_path / "case.sqlite"
    result = run_daily.execute(db, [cs.FAMILY], 24, False, raw_dir=tmp_path / "none")
    assert (result["families"][cs.FAMILY]["status"], result["families"][cs.FAMILY]["vintages_added"]) == ("ok", 4), result
    with open_reader(db) as store:
        cards = store.release_cards("2026-09-29", "2026-09-29")["cards"]
        assert {c["indicator_id"]: c["resolution"]["value"] for c in cards} == {
            "us_case_shiller_national_yy": 1.9, "us_case_shiller_20_yy": 2.5}
        assert all(c["issuer_id"] == "SPDJI" and c["source_class"] == "private" and
                   c["resolution"]["status"] == "private" and c["reference_period"] == "2026-07" and
                   c["released_at"] == "2026-09-29T13:00:00.000000+00:00" for c in cards)
        assert len(store.latest_official("us_case_shiller_national_yy")) == 2
    again = run_daily.execute(db, [cs.FAMILY], 24, False, raw_dir=tmp_path / "none")
    assert again["families"][cs.FAMILY]["status"] == "no_change", again


@pytest.mark.parametrize("before,after", [
    (b"Annual Gain in July 2026</div>", b"Annual Gain in June 2026</div>"),
    (b"Annual Gain in July 2026</div>", b"Annual Decline in July 2026</div>"),
    (b"Sept. 29, 2026", b"Sept. 28, 2026"),
    (b"today released the July 2026 results", b"today released the August 2026 results"),
    (b"posted a 1.9% annual gain for July 2026", b"posted a 2.9% annual gain for July 2026"),
    (b"reported a 1.9% annual gain for July", b"reported a 1.9% annual decline for July"),
    (b"reported a 1.9% annual gain for July", b"reported a 1.9% annual gain for June"),
    (b"20-City Composite posted a year-over-year", b"10-City Composite posted a year-over-year"),
    (b"20-City Composite posted a year-over-year", b"20-City Composite posted a month-over-month"),
    (b"National Home Price NSA Index", b"National Home Price SA Index"),
    (b"at 9:00 am ET", b"at 10:00 am ET"),
])
def test_changed_date_measure_adjustment_or_disagreement_fails_closed(before, after):
    doc = document("case-shiller-press-sept.html")
    assert before in doc.body
    with pytest.raises(FetchError, match="needs review"):
        cs.parse(replace(doc, body=doc.body.replace(before, after)))


def test_declining_headlines_keep_direction_and_ignore_uncollected_tables():
    doc = document("case-shiller-press-sept.html")
    body = doc.body.replace(b"1.9% annual gain", b"1.9% annual decline").replace(b"Annual Gain in July", b"Annual Decline in July")
    body = body.replace(b"year-over-year increase of 2.5%", b"year-over-year decrease of 2.5%")
    rows = cs.parse(replace(doc, body=body))
    assert [o.value for o in rows] == [-1.9, -2.5]
    # Unrounded supporting-table figures must not replace printed headlines.
    body = doc.body.replace(b"1.93", b"99.99").replace(b"2.47", b"99.99")
    assert [o.value for o in cs.parse(replace(doc, body=body))] == [1.9, 2.5]
    assert cs.parse(doc, since="2026-08") == []


def test_publication_urls_links_and_requested_editions_fail_closed():
    doc = document("case-shiller-press-sept.html")
    for url in (doc.url.replace("press.spglobal.com", "example.com"), doc.url + "?edition=2026-09-29",
                doc.url.replace("2026-09-29", "2026-09-28")):
        with pytest.raises(FetchError, match="needs review"):
            cs.parse(replace(doc, url=url))
    index = document("case-shiller-press-index.html")
    with pytest.raises(FetchError, match="needs review"):
        cs.release_links(replace(index, body=index.body.replace(b"https://press.spglobal.com/2026-09-29", b"https://example.com/2026-09-29")))
    duplicate = b'<a href="' + doc.url.encode() + b'-other">S&amp;P Cotality Case-Shiller Index Reports Other</a>'
    with pytest.raises(FetchError, match="ambiguous; needs review"):
        cs.release_links(replace(index, body=index.body + duplicate))


def test_release_instant_gate_missing_new_release_grace_and_stale_review():
    client = Replay()
    for instant, expected in (("2026-08-25T12:59:59Z", []), ("2026-08-25T13:00:00Z", ["2026-08-25"]),
                              ("2026-09-29T12:59:59Z", ["2026-08-25"]),
                              ("2026-09-29T13:00:00Z", ["2026-08-25", "2026-09-29"])):
        token = RUN_INSTANT.set(instant)
        try:
            assert sorted({o.edition for o in cs.fetch(client)}) == expected
            if not expected:
                assert client.requests == []
        finally:
            RUN_INSTANT.reset(token)
    token = RUN_INSTANT.set("2026-10-27T13:00:00Z")
    pending = PENDING_EDITIONS.set({})
    try:
        assert len(cs.fetch(client)) == 4
        assert (cs.FAMILY, "2026-10-27") in PENDING_EDITIONS.get()
    finally:
        RUN_INSTANT.reset(token)
        PENDING_EDITIONS.reset(pending)
    token = RUN_INSTANT.set("2026-10-27T19:00:00Z")
    try:
        with pytest.raises(FetchError, match="missing; needs review"):
            cs.fetch(client)
    finally:
        RUN_INSTANT.reset(token)


@pytest.mark.parametrize("status", [301, 404])
def test_retired_press_url_requires_review(status):
    class Retired(Replay):
        def get(self, provider, url, params=None):
            if url != cs.INDEX:
                raise FetchError("spglobal: HTTP " + str(status))
            return super().get(provider, url, params)
    with pytest.raises(FetchError, match="retired or redirected; needs review"):
        cs.fetch(Retired())


def test_changed_publication_retained_for_review_without_partial_vintages(tmp_path, monkeypatch):
    class Changed(Replay):
        def get(self, *args, **kwargs):
            doc = super().get(*args, **kwargs)
            body = doc.body.replace(b"20-City Composite posted a year-over-year", b"10-City Composite posted a year-over-year")
            return replace(doc, body=body, document_hash=hashlib.sha256(body).hexdigest())
    monkeypatch.setattr(run_daily, "HttpClient", Changed)
    db = tmp_path / "review.sqlite"
    result = run_daily.execute(db, [cs.FAMILY], 24, False, raw_dir=tmp_path / "none")
    assert result["families"][cs.FAMILY]["status"] == "needs_review", result
    with open_reader(db) as store:
        assert store.conn.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0] > 0
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 0


@pytest.mark.parametrize("area,value", [("national", 1.92885), ("20", 2.46896)])
def test_saved_fred_asof_history_never_certifies_or_overrides_issuer(area, value, tmp_path):
    indicator = "us_case_shiller_" + area + "_yy"
    doc = document("case-shiller-fred-" + area + ".json")
    assert "api_key" not in doc.url
    rows = FREDAdapter.parse(doc, indicator=indicator, edition="2026-09-29", since="2026-06")
    assert rows[-1].value == value and all(o.source == "FRED" and o.revision == "provider_history" for o in rows)
    assert all(o.precision == "date_only" for o in rows)
    with open_writer(tmp_path / "mirror.sqlite") as store:
        persist(store, rows)
        assert store.latest_official(indicator) == []
        assert all(r[0] == "unverified" for r in store.conn.execute("SELECT verification_status FROM value_vintages"))
        assert all(r[0] == "mirror" for r in store.conn.execute("SELECT evidence_role FROM source_series"))
        issuer = [o for o in cs.parse(document("case-shiller-press-sept.html")) if o.indicator_id == indicator]
        persist(store, issuer)
        assert store.latest_official(indicator)[0]["value"] == issuer[0].value
        # June was revised by July's as-of snapshot; the August press remains
        # the distinct first issuer edition (national 1.5%, Composite-20 2.1%).
        initial = [o for o in cs.parse(document("case-shiller-press-aug.html")) if o.indicator_id == indicator]
        persist(store, initial)
        june = next(r for r in store.latest_official(indicator) if r["reference_period"] == "2026-06")
        assert june["value"] == initial[0].value and june["value"] != rows[0].value


def test_fred_optional_fetch_uses_asof_and_exact_series_without_bls_history_window(monkeypatch):
    from market_data_hub.macro_releases import fred
    monkeypatch.setattr(fred, "api_key", lambda name: "offline-placeholder")
    class Mirror:
        def get(self, provider, url, params):
            assert provider == "fred" and url == fred.ENDPOINT
            assert params["units"] == "pc1" and params["observation_start"] == "2026-06-01"
            assert params["realtime_start"] == params["realtime_end"] == "2026-09-29"
            area = {"CSUSHPINSA": "national", "SPCS20RNSA": "20"}[params["series_id"]]
            return document("case-shiller-fred-" + area + ".json")
    assert len(FREDAdapter(Mirror()).fetch([cs.FAMILY], "2026-06")) == 4
    for field, value in (("units", "pch"), ("realtime_end", "2026-09-30")):
        doc = document("case-shiller-fred-national.json")
        body = doc.json()
        body[field] = value
        with pytest.raises(FetchError):
            FREDAdapter.parse(replace(doc, body=json.dumps(body).encode()), indicator="us_case_shiller_national_yy", edition="2026-09-29")


def test_exact_twenty_city_ff_alias_and_draft_only_concept(tmp_path):
    from market_data_hub.indicator_cards import indicator_card
    from market_data_hub.macro_releases.catalogue import INDICATORS
    from market_data_hub.macro_releases.consensus import Catalogue
    with open_writer(tmp_path / "aliases.sqlite") as store:
        for indicator in INDICATORS.values():
            store.put(indicator)
        catalogue = Catalogue(store)
        alias, reason = catalogue.match({"country": "USD"}, "S&P/CS Composite-20 HPI y/y")
        assert reason is None and alias["indicator_id"] == "us_case_shiller_20_yy"
        for title in ("S&P/CS Composite-10 HPI y/y", "S&P/CS Composite-20 HPI m/m", "Case-Shiller National HPI y/y"):
            assert catalogue.match({"country": "USD"}, title)[0] is None
        assert catalogue.match({"country": "CAD"}, "S&P/CS Composite-20 HPI y/y")[0] is None
    assert indicator_card("cat:us_case_shiller_20_yy") is None
