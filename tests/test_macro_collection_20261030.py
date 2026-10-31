"""Offline regressions for the 30 October collection failures, using issuer bytes."""
from dataclasses import replace
from datetime import datetime
import hashlib
from html import unescape
import json
from pathlib import Path
import re
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit

import pytest
import requests

from market_data_hub.macro_releases import boj_policy, ecb_housing as ecb
from market_data_hub.macro_releases import eurostat_activity, open_reader, run_daily
from market_data_hub.macro_releases.http import Document, FetchError
from market_data_hub.macro_releases.schedule import RUN_INSTANT
from market_data_hub.macro_releases.tier1_publications import publication_text

ROOT = Path(__file__).parent / "fixtures/macro_releases"
NEW = ROOT / "collection_20261030"
RUN_AT = "2026-10-30T13:30:25+00:00"


def document(path):
    body = path.read_bytes()
    meta = json.loads(path.with_name(path.name + ".meta.json").read_text())
    assert hashlib.sha256(body).hexdigest() == meta["sha256"]
    return Document(body, meta["url"], meta["retrieved_at"], meta["sha256"])


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network forbidden: saved issuer evidence only")
    monkeypatch.setattr(requests.Session, "request", forbidden)
    monkeypatch.setattr(run_daily, "now", lambda: datetime.fromisoformat(RUN_AT))
    token = RUN_INSTANT.set(RUN_AT)
    yield
    RUN_INSTANT.reset(token)


class Replay:
    def __init__(self, **kwargs):
        self.urls = {}
        for directory in (NEW, ROOT / "needs_review_20261029", ROOT / "m9c", ROOT / "m9f", ROOT / "m9b"):
            for path in sorted(directory.glob("*.meta.json")):
                meta = json.loads(path.read_text())
                parts = urlsplit(meta["url"])
                url = urlunsplit(parts._replace(query=""))
                params = meta.get("params", dict(parse_qsl(parts.query)))
                self.urls.setdefault((url, json.dumps(params, sort_keys=True)), path.with_suffix("").with_suffix(""))
        # The saved EC PDF metadata has canonical URLs without the listing's
        # filename query. Accept only aliases explicitly present in that page.
        listing = document(NEW / "ec-latest.html")
        for href in re.findall(r'href="([^"]+)"', listing.body.decode()):
            parts = urlsplit(urljoin(listing.url, unescape(href)))
            url = urlunsplit(parts._replace(query=""))
            canonical = self.urls.get((url, "{}"))
            if canonical:
                self.urls[url, json.dumps(dict(parse_qsl(parts.query)), sort_keys=True)] = canonical

    def get(self, provider, url, params=None):
        key = url, json.dumps(params or {}, sort_keys=True)
        assert key in self.urls, "Unexpected issuer request: " + url
        return document(self.urls[key])


def test_boj_october_rate_vote_is_separate_from_etf_vote():
    doc = document(NEW / "boj-october.pdf")
    previous = document(ROOT / "m9c/boj-sept.pdf")
    row, = boj_policy.parse(doc, previous, feed=document(NEW / "boj-rss.xml"))
    assert (row.edition, row.period, row.value) == ("2026-10-30", "2026-10-30", 1.25)
    assert row.policy_decision["vote_split"] == {"for": 8, "against": 1}
    assert row.policy_decision["rates"]["overnight_call_rate"] == {
        "rate": 1.25, "change_bp": 0.0, "change_basis": "consecutive_issuer_statements"}
    assert row.published_at == "2026-10-30T04:00:00+00:00"
    assert row.precision == "exact"
    without_feed, = boj_policy.parse(doc, previous)
    assert without_feed.precision == "date_only"


@pytest.mark.parametrize("before,after", [
    ("Guideline for money market operations (an 8-1 majority vote)",
     "Guideline for money market operations (vote unavailable)"),
    ("Guideline for money market operations", "Sales of exchange-traded funds"),
    ("decided to set the following guideline", "discussed a possible guideline"),
    ("October 30, 202 6", "October 31, 202 6"),
])
def test_boj_unverifiable_rate_section_stays_review(before, after):
    doc = document(NEW / "boj-october.pdf")
    body, _ = publication_text(doc)
    assert before in body
    with pytest.raises(FetchError, match="needs review"):
        boj_policy.parse(replace(doc, body=body.replace(before, after).encode()),
                         document(ROOT / "m9c/boj-sept.pdf"))


@pytest.mark.parametrize("family", [ecb.LOANS, ecb.COST])
def test_ecb_forward_calendar_and_dated_publications(family):
    found = ecb.calendar_editions(document(NEW / "ecb-calendar.html"), family)
    assert min(found) > "2026-10-30"
    rows = ecb.fetch(Replay(), family, "2026-08")
    expected = ("2026-10-27", "2026-09", 3.0) if family == ecb.LOANS else ("2026-10-01", "2026-08", 3.6)
    row = max(rows, key=lambda r: r.edition)
    assert (row.edition, row.period, row.value) == expected


@pytest.mark.parametrize("family,date", [(ecb.LOANS, b"26/11/2026 10:00"), (ecb.COST, b"02/11/2026 10:00")])
def test_ecb_changed_future_calendar_stays_review(family, date):
    doc = document(NEW / "ecb-calendar.html")
    assert date in doc.body
    for changed in (date.replace(b"10:00", b"09:00"), date.replace(b"2026", b"2025")):
        with pytest.raises(FetchError, match="needs review"):
            ecb.calendar_editions(replace(doc, body=doc.body.replace(date, changed)), family)


def test_eurostat_october_unemployment_uses_reviewed_bp_publication():
    doc = document(NEW / "ea-unemployment-october.html")
    assert eurostat_activity.publication_url("ea_unemployment", "2026-10-30") == doc.url
    rows = eurostat_activity.fetch(Replay(), "ea_unemployment", "2026-09")
    row, = rows
    assert (row.edition, row.period, row.value) == ("2026-10-30", "2026-09", 6.5)
    assert row.indicator_id == "ea_unemployment_rate"


@pytest.mark.parametrize("before,after", [
    (b"2026-10-30", b"2026-10-31"),
    (b"Euro area (EA21)", b"Euro area (EA22)"),
    (b"persons aged 15 to 74", b"persons aged 15 to 64"),
])
def test_eurostat_changed_october_metadata_stays_review(before, after):
    doc = document(NEW / "ea-unemployment-october.html")
    assert before in doc.body
    with pytest.raises(FetchError, match="needs review"):
        eurostat_activity.parse(replace(doc, body=doc.body.replace(before, after)), "ea_unemployment")


def test_daily_six_families_are_official_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(run_daily, "HttpClient", Replay)
    path = tmp_path / "macro.sqlite"
    families = ["boj_policy", "ea_unemployment", ecb.LOANS, ecb.COST, "ea_ec_surveys", "us_fhfa_hpi"]
    result = run_daily.execute(path, families, 24, False, raw_dir=tmp_path / "empty")
    assert all(r["status"] == "ok" for r in result["families"].values()), json.dumps(result, indent=2)
    with open_reader(path) as store:
        cards = store.release_cards("2026-10-30", "2026-10-30", indicators=["boj_policy_rate_decision"])["cards"]
        card, = cards
        assert card["resolution"]["status"] == "official"
        assert card["resolution"]["value"] == 1.25
        assert card["policy_decision"]["vote_split"] == {"for": 8, "against": 1}
        assert store.conn.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0] == 0
    again = run_daily.execute(path, families, 24, False, raw_dir=tmp_path / "empty")
    assert all(r["status"] == "no_change" for r in again["families"].values()), again
