"""Offline certification of unchanged, hashed central-bank statements."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest
import requests

from market_data_hub.macro_releases import open_reader, open_writer
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.http import Document, FetchError
from market_data_hub.macro_releases.policy import due_editions
from market_data_hub.macro_releases.schedule import RUN_AT, RUN_DATE

ROOT = Path(__file__).parent / "fixtures/macro_releases/m9c"
GOLDEN = json.loads((ROOT / "golden-set.json").read_text())["cases"]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network forbidden in M9c tests")
    monkeypatch.setattr(requests.Session, "request", forbidden)
    date_token = RUN_DATE.set("2026-10-24")
    time_token = RUN_AT.set(datetime(2026, 10, 24, 18, tzinfo=timezone.utc))
    yield
    RUN_DATE.reset(date_token)
    RUN_AT.reset(time_token)


def document(name):
    body = (ROOT / name).read_bytes()
    meta = json.loads((ROOT / (name + ".meta.json")).read_text())
    assert hashlib.sha256(body).hexdigest() == meta["sha256"]
    return Document(body, meta["url"], meta["retrieved_at"], meta["sha256"])


def parse(case):
    if case["family"] == "boj_policy":
        from market_data_hub.macro_releases.boj_policy import parse
        return parse(document(case["fixture"]), document(case["previous_fixture"]))[0]
    if case["family"] == "pboc_lpr":
        from market_data_hub.macro_releases.pboc_lpr import parse
        return parse(document(case["fixture"]), document(case["previous_fixture"]))[0]
    from market_data_hub.macro_releases import fomc, ecb_policy, boe_policy, boc_policy
    return {"fomc": fomc, "ecb_policy": ecb_policy, "boe_policy": boe_policy, "boc_policy": boc_policy}[case["family"]].parse(document(case["fixture"]))[0]


@pytest.mark.parametrize("case", GOLDEN, ids=lambda c: c["family"] + ":" + c["edition"])
def test_golden_official_card(case, tmp_path):
    obs = parse(case)
    assert obs.edition == obs.period == case["edition"]
    assert obs.document.url == case["source_url"]
    assert obs.policy_decision["rates"] == case["rates"]
    assert obs.policy_decision["vote_split"] == case["vote_split"]
    assert obs.policy_decision["publication_precision"] == case["precision"]
    db = tmp_path / "golden.sqlite"
    with open_writer(db) as store:
        persist(store, [obs])
    with open_reader(db) as store:
        cards = store.release_cards("2026-01-01", "2026-12-31")["cards"]
        assert len(cards) == 1
        assert cards[0]["resolution"]["status"] == "official"
        assert cards[0]["policy_decision"] == obs.policy_decision
        assert cards[0]["resolution"]["policy_decision"] == obs.policy_decision


def test_fomc_issuer_unknown_edition_and_format_fail_closed():
    from market_data_hub.macro_releases.fomc import parse
    doc = document("fed-sept.html")
    for before, after in ((b"September 16, 2026", b"September 17, 2026"),
                          (b"target range for the federal funds rate", b"unexpected rate convention"),
                          (b"2:00 p.m.", b"1:00 p.m.")):
        assert before in doc.body
        with pytest.raises(FetchError, match="needs review"):
            parse(replace(doc, body=doc.body.replace(before, after)))
    with pytest.raises(FetchError, match="needs review"):
        parse(replace(doc, url="https://news.example/statement"))


def test_fomc_due_at_exact_issuer_instant():
    date = RUN_DATE.set("2026-09-16")
    before = RUN_AT.set(datetime(2026, 9, 16, 17, 59, 59, tzinfo=timezone.utc))
    try:
        assert "2026-09-16" not in due_editions("fomc", "2026-09")
        RUN_AT.set(datetime(2026, 9, 16, 18, tzinfo=timezone.utc))
        assert "2026-09-16" in due_editions("fomc", "2026-09")
    finally:
        RUN_DATE.reset(date)
        RUN_AT.reset(before)


class Replay:
    def __init__(self, **kwargs):
        self.mapping = json.loads((ROOT / "publication-replay.json").read_text())

    def get(self, provider, url, params=None):
        assert not params
        assert url in self.mapping, "Unexpected or future issuer request: " + url
        return document(self.mapping[url])


def test_all_40_decisions_daily_collection_and_idempotence(tmp_path, monkeypatch):
    from market_data_hub.macro_releases import run_daily
    from market_data_hub.macro_releases.catalogue import CENTRAL_BANKS
    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 24, 18, tzinfo=timezone.utc)
    monkeypatch.setattr(run_daily, "datetime", Clock)
    monkeypatch.setattr(run_daily, "HttpClient", Replay)
    db = tmp_path / "all.sqlite"
    first = run_daily.execute(db, list(CENTRAL_BANKS), 24, False, raw_dir=tmp_path / "none")
    assert all(r["status"] == "ok" for r in first["families"].values()), first
    assert sum(r["vintages_added"] for r in first["families"].values()) == 40
    with open_reader(db) as store:
        cards = store.release_cards("2026-01-01", "2026-12-31")["cards"]
        assert len(cards) == 40
        assert all(c["resolution"]["status"] == "official" and c["policy_decision"] for c in cards)
        for family in CENTRAL_BANKS:
            latest = max((c for c in cards if c["family"] == family), key=lambda c: c["reference_period"])
            assert latest["policy_decision"]["source_url"].startswith("https://")
            if family == "boj_policy":
                assert latest["policy_decision"]["published_at"] is None
                assert latest["publication_precision"] == "date_only"
                assert latest["policy_decision"]["time_missing_reason"]
            else:
                assert latest["policy_decision"]["published_at"]
                assert latest["publication_precision"] == "exact"
    again = run_daily.execute(db, list(CENTRAL_BANKS), 24, False, raw_dir=tmp_path / "none")
    assert again["status"] == "no_change", again
    assert all(r["vintages_added"] == 0 for r in again["families"].values())


@pytest.mark.parametrize("family,edition,instant", [
    ("fomc", "2026-12-09", "2026-12-09T19:00:00+00:00"),
    ("ecb_policy", "2026-12-17", "2026-12-17T13:15:00+00:00"),
    ("boe_policy", "2026-12-17", "2026-12-17T12:00:00+00:00"),
    ("boc_policy", "2026-12-09", "2026-12-09T14:45:00+00:00"),
    ("pboc_lpr", "2026-12-21", "2026-12-21T01:00:00+00:00"),
])
def test_release_instants_including_winter_dst(family, edition, instant):
    from datetime import timedelta
    stamp = datetime.fromisoformat(instant)
    RUN_DATE.set(edition)
    RUN_AT.set(stamp - timedelta(seconds=1))
    assert edition not in due_editions(family, edition[:7])
    RUN_AT.set(stamp)
    assert edition in due_editions(family, edition[:7])


def test_policy_vote_correction_retains_new_vintage_and_historical_knowledge(tmp_path):
    case = next(c for c in GOLDEN if c["fixture"] == "fed-july.html")
    original = parse(case)
    raw = original.document.body + b"<!-- issuer correction -->"
    corrected_doc = replace(original.document, body=raw, document_hash=hashlib.sha256(raw).hexdigest(),
                            retrieved_at="2026-10-25T18:00:00Z")
    correction = replace(original, document=corrected_doc,
                         policy_decision=dict(original.policy_decision, vote_split={"for": 10, "against": 2}))
    db = tmp_path / "correction.sqlite"
    with open_writer(db) as store:
        persist(store, [original, original, correction])
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 2
        assert store.conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0] == 2
    with open_reader(db) as store:
        old = store.release_cards("2026-07-01", "2026-07-31", as_known_at="2026-10-24T23:59:59Z")["cards"][0]
        new = store.release_cards("2026-07-01", "2026-07-31", as_known_at="2026-10-26T00:00:00Z")["cards"][0]
        assert old["policy_decision"]["vote_split"] == {"for": 9, "against": 3}
        assert new["policy_decision"]["vote_split"] == {"for": 10, "against": 2}


@pytest.mark.parametrize("family,fixture,previous", [
    ("boj_policy", "boj-sept.pdf", "boj-june.pdf"),
    ("pboc_lpr", "pboc-oct.html", "pboc-aug.html"),
])
def test_changes_require_consecutive_official_statements(family, fixture, previous):
    from market_data_hub.macro_releases import boj_policy, pboc_lpr
    with pytest.raises(FetchError, match="needs review"):
        {"boj_policy": boj_policy, "pboc_lpr": pboc_lpr}[family].parse(document(fixture), document(previous))


def test_boj_meeting_hours_never_become_publication_time():
    from market_data_hub.macro_releases.boj_policy import parse
    row = parse(document("boj-july.pdf"), document("boj-june.pdf"), feed=document("boj-rss.xml"))[0]
    assert row.precision == "date_only"
    assert row.policy_decision["published_at"] is None
    assert "Meeting end time is not a release time" in row.policy_decision["time_missing_reason"]


def test_boj_only_matching_issuer_rss_item_establishes_variable_clock():
    from market_data_hub.macro_releases.boj_policy import parse
    raw = b'<rss><channel><item><link>http://www.boj.or.jp/en/mopo/mpmdeci/mpr_2026/k260731a.pdf</link><pubDate>Fri, 31 Jul 2026 12:09:30 +0900</pubDate></item></channel></rss>'
    feed = replace(document("boj-rss.xml"), body=raw)
    row = parse(document("boj-july.pdf"), document("boj-june.pdf"), feed=feed)[0]
    assert row.precision == "exact"
    assert row.policy_decision["published_at"] == "2026-07-31T03:09:30+00:00"
    assert row.policy_decision["time_missing_reason"] is None
    with pytest.raises(FetchError, match="needs review"):
        parse(document("boj-july.pdf"), document("boj-june.pdf"), feed=replace(feed, body=raw.replace(b'31 Jul', b'30 Jul')))


def test_each_bank_has_saved_calendar_and_two_issuer_golden_cases():
    from market_data_hub.macro_releases.catalogue import CENTRAL_BANKS
    from market_data_hub.macro_releases.policy_editions import EDITIONS, SCHEDULES
    sources = json.loads((ROOT / "calendar-sources.json").read_text())
    for family in CENTRAL_BANKS:
        assert len([c for c in GOLDEN if c["family"] == family]) >= 2
        assert len(EDITIONS[family]) == (12 if family == "pboc_lpr" else 8)
        assert max(EDITIONS[family]) <= "2026-12-31"
        assert sources[family]["source_url"] == SCHEDULES[family]
        assert document(sources[family]["fixture"]).url == SCHEDULES[family]


@pytest.mark.parametrize("family,fixture,before,after", [
    ("ecb_policy", "ecb-sept.html", b"10 September 2026", b"11 September 2026"),
    ("boe_policy", "boe-sept.html", b"17 September 2026", b"18 September 2026"),
    ("boc_policy", "boc-sept.html", b"September 2, 2026", b"September 3, 2026"),
])
def test_unknown_edition_never_becomes_official(family, fixture, before, after):
    from market_data_hub.macro_releases import ecb_policy, boe_policy, boc_policy
    module = {"ecb_policy": ecb_policy, "boe_policy": boe_policy, "boc_policy": boc_policy}[family]
    doc = document(fixture)
    assert before in doc.body
    with pytest.raises(FetchError, match="needs review"):
        module.parse(replace(doc, body=doc.body.replace(before, after)))


def test_changed_format_is_retained_for_review_without_official_vintage(tmp_path, monkeypatch):
    from market_data_hub.macro_releases import run_daily
    class Changed(Replay):
        def get(self, provider, url, params=None):
            doc = super().get(provider, url, params)
            return replace(doc, body=doc.body.replace(b"target for the overnight rate", b"unknown policy convention"))
    monkeypatch.setattr(run_daily, "HttpClient", Changed)
    db = tmp_path / "review.sqlite"
    result = run_daily.execute(db, ["boc_policy"], 24, False, raw_dir=tmp_path / "none")
    assert result["families"]["boc_policy"]["status"] == "needs_review", result
    with open_reader(db) as store:
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 0
        assert store.conn.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0] > 0


def test_emergency_decision_and_expired_calendar_need_review():
    from market_data_hub.macro_releases.policy import PolicyAdapter, reviewed
    with pytest.raises(FetchError, match="needs review"):
        reviewed("boj_policy", "2026-08-03")
    RUN_DATE.set("2027-01-01")
    with pytest.raises(FetchError, match="needs review"):
        PolicyAdapter(Replay()).fetch(["boc_policy"], "2026-01")


def test_fomc_all_dissent_clauses_and_oxford_comma_names():
    from market_data_hub.macro_releases.fomc import vote_split
    text = ("Voting for the monetary policy action were Ada Alpha, Chair; Ben Beta, Vice Chair. "
            "Voting against this action were Cy Gamma, who preferred a cut; and Dee Delta, "
            "Eve Epsilon, and Flo Zeta, who preferred different guidance. For media inquiries")
    assert vote_split(text) == {"for": 2, "against": 4}
    for changed in (text.replace("Dee Delta", "Ada Alpha"),
                    text.replace("Eve Epsilon", "Dee Delta"),
                    text.replace("; and Dee Delta", "; unidentified clause; and Dee Delta"),
                    "approved the following statement for release by a 2-3 vote: " + text):
        with pytest.raises(FetchError, match="needs review"):
            vote_split(changed)


def test_fomc_numeric_split_agrees_with_named_dissenters():
    from market_data_hub.macro_releases.fomc import parse
    doc = document("fed-july.html")
    # A complete three-name dissent list cannot certify a numerical 9-2 split.
    from market_data_hub.macro_releases.tier1_publications import publication_text
    text = publication_text(doc)[0]
    import re
    changed = re.sub(r"9\s*[^0-9]{1,12}?\s*3 vote", "9-2 vote", text)
    assert changed != text
    with pytest.raises(FetchError, match="needs review"):
        parse(replace(doc, body=changed.encode()))
