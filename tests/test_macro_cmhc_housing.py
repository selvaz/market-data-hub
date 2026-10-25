"""Offline CMHC monthly SAAR releases, original prints and scheduled gating."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import re

import pytest

from market_data_hub.macro_releases import cmhc_housing as ca, open_reader, run_daily
from market_data_hub.macro_releases.http import FetchError
from market_data_hub.macro_releases.schedule import RUN_INSTANT, PENDING_EDITIONS, release_instant
from test_macro_m9f import document, offline as offline


def transport_variant(doc):
    body = re.sub(rb'(<meta name="VIcurrentDateTime" content=")[^"]+', rb'\g<1>999999999999999999', doc.body)
    body = re.sub(rb'(<input name="__RequestVerificationToken" type="hidden" value=")[^"]+', rb'\g<1>another-public-render-token', body)
    body = re.sub(rb"(SearchResourcesProperties_)[0-9a-fA-F]{32}", rb"\g<1>" + b"f" * 32, body)
    assert b"another-public-render-token" in body and b"999999999999999999" in body
    assert b"SearchResourcesProperties_" + b"f" * 32 in body
    assert body != doc.body
    return replace(doc, body=body, document_hash=hashlib.sha256(body).hexdigest())


def test_cmhc_render_tokens_do_not_change_identity_but_headlines_and_dates_do():
    doc = document("cmhc-september2026.html")
    changed = transport_variant(doc)
    assert changed.document_hash != doc.document_hash
    assert changed.identity_hash == doc.identity_hash
    for before, after in ((b"248,896", b"249,896"), (b"October 16, 2026", b"October 17, 2026")):
        body = changed.body.replace(before, after)
        assert body != changed.body
        assert replace(changed, body=body, document_hash=hashlib.sha256(body).hexdigest()).identity_hash != doc.identity_hash


@pytest.mark.parametrize("provider", ["bls", "bea", "fred", "eurostat", "ons", "dol", "census",
    "ecfin", "umich", "fed", "ecb", "boe", "boj", "pboc", "boc", "fhfa", "nar", "spglobal",
    "freddiemac", "destatis", "cmhc", "mlit", "nbs"])
def test_cmhc_http_extension_preserves_other_providers_request_settings(provider):
    from types import SimpleNamespace
    from market_data_hub.macro_releases.http import HttpClient
    class Session:
        def request(self, method, url, **kwargs):
            assert method == "GET" and url == ca.CALENDAR
            if provider in {"boe", "cmhc"}:
                assert kwargs["headers"] == {"User-Agent": "Mozilla/5.0"}
            else:
                assert "headers" not in kwargs
            assert kwargs["allow_redirects"] is False and self.trust_env is False
            assert kwargs["timeout"] == (10, 45) and kwargs["verify"]
            return SimpleNamespace(status_code=200, content=document("cmhc-calendar.html").body, headers={})
    client = HttpClient(session=Session(), persistent_rates=False, max_attempts=1)
    assert client.get(provider, ca.CALENDAR).body == document("cmhc-calendar.html").body


@pytest.mark.parametrize("url", ["https://www.bankofengland.co.uk/release", "https://www.destatis.de/release",
    "https://www.census.gov/release", "https://www.ecb.europa.eu/release", "https://ec.europa.eu/release",
    "https://www.ons.gov.uk/release", "https://www.fhfa.gov/release", "https://www.nar.realtor/release",
    "https://www.freddiemac.com/release", "https://www.mlit.go.jp/release", "https://www.stats.gov.cn/release",
    "https://www.cmhc-schl.gc.ca.other.test/release", "https://other.test/?source=cmhc-schl.gc.ca"])
def test_cmhc_transport_cleanup_never_changes_other_issuer_identities(url):
    from market_data_hub.macro_releases.http import normalize_body
    doc = replace(document("cmhc-september2026.html"), url=url)
    changed = transport_variant(doc)
    # Even identical Sitecore fields and a CMHC link in another issuer's body
    # must retain their original identities; the source host controls cleanup.
    assert doc.identity_hash == doc.document_hash
    assert changed.identity_hash == changed.document_hash
    assert changed.identity_hash != doc.identity_hash
    assert normalize_body(changed.body) == changed.body


class Replay:
    def __init__(self, **kwargs):
        self.requests = []

    def get(self, provider, url, params=None):
        assert provider == "cmhc" and not params
        self.requests.append(url)
        if url == ca.CALENDAR:
            return document("cmhc-calendar.html")
        for edition, month in (("2026-09-16", "august"), ("2026-10-16", "september")):
            if url == ca.publication_url(edition):
                return document("cmhc-" + month + "2026.html")
        raise AssertionError("Unexpected CMHC request: " + url)


def test_issuer_calendar_clock_and_regular_stage():
    from market_data_hub.macro_releases.read_api import indicators_for_stage
    assert set(ca.calendar_editions(document("cmhc-calendar.html"))) == {"2026-09-16", "2026-10-16", "2026-11-17", "2026-12-15"}
    assert release_instant("2026-10-16", family=ca.FAMILY).hour == 12
    assert release_instant("2026-11-17", family=ca.FAMILY).hour == 13
    assert indicators_for_stage(ca.FAMILY, "regular") == {ca.FAMILY}
    assert indicators_for_stage(ca.FAMILY, "advance") == set()


@pytest.mark.parametrize("before,after", [
    (b"Ottawa, October 16, 2026", b"Ottawa, October 15, 2026"),
    (b"all areas in Canada was up", b"urban centres in Canada was up"),
    (b"The total monthly SAAR", b"The six-month trend SAAR"),
    (b"(248,896 units)", b"(not available units)"),
    (b"8:15 AM ET", b"9:15 AM ET"),
    (b"November 17 at", b"November 18 at"),
])
def test_changed_scope_date_or_clock_needs_review(before, after):
    doc = document("cmhc-september2026.html")
    assert before in doc.body
    with pytest.raises(FetchError, match="needs review"):
        ca.parse(replace(doc, body=doc.body.replace(before, after)))


@pytest.mark.parametrize("before,after", [(b"Nov. 17", b"Nov. 18"), (b"Dec. 15", b"Dec. 16")])
def test_calendar_drift_needs_review(before, after):
    doc = document("cmhc-calendar.html")
    assert before in doc.body
    with pytest.raises(FetchError, match="needs review"):
        ca.calendar_editions(replace(doc, body=doc.body.replace(before, after)))


def test_original_monthly_saar_not_six_month_trend_urban_actual_or_revised_history():
    august, = ca.parse(document("cmhc-august2026.html"))
    september, = ca.parse(document("cmhc-september2026.html"))
    assert (august.value, august.period) == (229046, "2026-08")
    assert (september.value, september.period) == (248896, "2026-09")
    assert b"242,223 units" in september.document.body
    assert b"250,411 units" in september.document.body
    assert ca.parse(september.document, since="2026-10") == []
    with pytest.raises(FetchError, match="needs review"):
        ca.parse(replace(september.document, url=august.document.url))


def test_release_gate_delay_grace_and_horizon():
    class Missing(Replay):
        def get(self, provider, url, params=None):
            if url == ca.publication_url("2026-10-16"):
                raise FetchError("cmhc: HTTP 404")
            return super().get(provider, url, params)
    token = RUN_INSTANT.set("2026-09-16T12:14:59Z")
    pending = PENDING_EDITIONS.set({})
    try:
        client = Replay()
        assert ca.fetch(client) == [] and client.requests == []
        RUN_INSTANT.set("2026-10-16T12:14:59Z")
        assert len(ca.fetch(Replay())) == 1
        RUN_INSTANT.set("2026-10-16T12:15:00Z")
        assert len(ca.fetch(Replay())) == 2
        assert len(ca.fetch(Missing())) == 1
        assert (ca.FAMILY, "2026-10-16") in PENDING_EDITIONS.get()
        PENDING_EDITIONS.get().clear()
        RUN_INSTANT.set("2026-10-16T18:15:00Z")
        with pytest.raises(FetchError, match="missing; needs review"):
            ca.fetch(Missing())
        RUN_INSTANT.set("2027-01-01T00:00:00Z")
        with pytest.raises(FetchError, match="horizon exhausted; needs review"):
            ca.fetch(Replay())
    finally:
        RUN_INSTANT.reset(token)
        PENDING_EDITIONS.reset(pending)


def test_daily_card_idempotence_and_changed_source_review(tmp_path, monkeypatch):
    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 24, 18, tzinfo=timezone.utc)
    monkeypatch.setattr(run_daily, "datetime", Clock)
    monkeypatch.setattr(run_daily, "HttpClient", Replay)
    path = tmp_path / "cmhc.sqlite"
    result = run_daily.execute(path, [ca.FAMILY], 24, False, raw_dir=tmp_path / "raw")
    assert result["families"][ca.FAMILY]["status"] == "ok", result
    with open_reader(path) as store:
        card, = store.release_cards("2026-10-16", "2026-10-16", indicators=[ca.FAMILY])["cards"]
        assert (card["reference_period"], card["resolution"]["value"], card["source_class"], card["released_at"]) == (
            "2026-09", 248896, "official", "2026-10-16T12:15:00.000000+00:00")
        assert {r["reference_period"]: r["value"] for r in store.latest_official(ca.FAMILY)} == {"2026-08": 229046, "2026-09": 248896}
    class NewTransportTokens(Replay):
        def get(self, *args, **kwargs):
            return transport_variant(super().get(*args, **kwargs))
    monkeypatch.setattr(run_daily, "HttpClient", NewTransportTokens)
    assert run_daily.execute(path, [ca.FAMILY], 24, False, raw_dir=tmp_path / "raw")["families"][ca.FAMILY]["status"] == "no_change"
    class Changed(Replay):
        def get(self, *args, **kwargs):
            doc = super().get(*args, **kwargs)
            body = doc.body.replace(b"all areas in Canada was", b"urban centres in Canada was")
            return replace(doc, body=body, document_hash=hashlib.sha256(body).hexdigest())
    monkeypatch.setattr(run_daily, "HttpClient", Changed)
    fresh = tmp_path / "review.sqlite"
    result = run_daily.execute(fresh, [ca.FAMILY], 24, False, raw_dir=tmp_path / "raw")
    assert result["families"][ca.FAMILY]["status"] == "needs_review", result
    with open_reader(fresh) as store:
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 0
