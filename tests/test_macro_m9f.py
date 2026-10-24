"""Offline issuer publications, edition gates, review retention and read cards."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest
import requests

from market_data_hub.macro_releases import open_reader, open_writer, run_daily
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.housing import PARSERS
from market_data_hub.macro_releases.http import Document, FetchError
from market_data_hub.macro_releases.schedule import RUN_INSTANT

ROOT = Path(__file__).parent / "fixtures/macro_releases/m9f"
GOLDEN = json.loads((ROOT / "golden-set.json").read_text())["cases"]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network forbidden in M9f tests")
    monkeypatch.setattr(requests.Session, "request", forbidden)
    token = RUN_INSTANT.set(datetime(2026, 10, 24, 18, tzinfo=timezone.utc))
    yield
    RUN_INSTANT.reset(token)


def document(name):
    body = (ROOT / name).read_bytes()
    meta = json.loads((ROOT / (name + ".meta.json")).read_text())
    assert hashlib.sha256(body).hexdigest() == meta["sha256"]
    return Document(body, meta["url"], meta["retrieved_at"], meta["sha256"])


@pytest.mark.parametrize("case", GOLDEN, ids=lambda c: c["family"] + ":" + c["edition"] + ":" + c["indicator"])
def test_golden(case, tmp_path):
    rows = PARSERS[case["family"]](document(case["fixture"]), case["family"])
    row = next(o for o in rows if o.indicator_id == case["indicator"] and o.period == case["period"])
    assert (row.value, row.edition, row.stage) == (case["value"], case["edition"], "regular")
    assert row.document.url == case["source_url"]
    with open_writer(tmp_path / "golden.sqlite") as store:
        persist(store, [row])
        assert store.latest_official(row.indicator_id)[0]["value"] == row.value
        cards = store.release_cards(row.edition, row.edition, indicators=[row.indicator_id])["cards"]
        assert cards[0]["resolution"]["status"] == "official"


def test_unknown_fhfa_date_fails_closed():
    with pytest.raises(FetchError, match="needs review"):
        from market_data_hub.macro_releases.housing_editions import reviewed
        reviewed("us_fhfa_hpi", "2027-01-01")


def test_every_housing_family_has_two_distinct_golden_editions_and_calendar():
    from market_data_hub.macro_releases.catalogue import HOUSING_FAMILIES
    from market_data_hub.macro_releases.housing_editions import EDITIONS, SCHEDULES
    for family in HOUSING_FAMILIES:
        assert len({c["edition"] for c in GOLDEN if c["family"] == family}) >= 2
        assert SCHEDULES[family].startswith("https://")
        assert max(EDITIONS[family]) <= "2026-12-31"


class ConstructionReplay:
    def __init__(self, **kwargs):
        self.requests = []

    def get(self, provider, url, params=None):
        from market_data_hub.macro_releases.census_construction import URL
        assert provider == "census" and url == URL and not params
        self.requests.append(url)
        return document("construction-current.pdf")


def test_construction_daily_latest_card_schedules_and_idempotence(tmp_path, monkeypatch):
    monkeypatch.setattr(run_daily, "HttpClient", ConstructionReplay)
    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 24, 18, tzinfo=timezone.utc)
    monkeypatch.setattr(run_daily, "datetime", Clock)
    target = tmp_path / "construction.sqlite"
    first = run_daily.execute(target, ["us_construction_spending"], 24, False, raw_dir=tmp_path / "none")
    result = first["families"]["us_construction_spending"]
    assert (result["status"], result["vintages_added"]) == ("ok", 1), first
    assert [e["edition"] for e in result["scheduled_editions"]] == ["2026-11-02", "2026-12-01"]
    with open_reader(target) as store:
        card, = store.release_cards("2026-10-01", "2026-10-01",
                                    indicators=["us_construction_spending_mm"])["cards"]
        assert (card["family"], card["reference_period"], card["released_at"]) == (
            "us_construction_spending", "2026-08", "2026-10-01T14:00:00.000000+00:00")
        assert (card["resolution"]["status"], card["resolution"]["value"]) == ("official", 0.9)
    second = run_daily.execute(target, ["us_construction_spending"], 24, False, raw_dir=tmp_path / "none")
    assert second["families"]["us_construction_spending"]["status"] == "no_change", second


@pytest.mark.parametrize("before,after", [
    ("OCTOBER 1, 2026", "OCTOBER 2, 2026"),
    ("10:00 AM EDT", "09:00 AM EDT"),
    ("10:00 AM EDT", "10:00 AM EST"),
    ("MONTHLY CONSTRUCTION SPENDING, AUGUST 2026", "MONTHLY CONSTRUCTION SPENDING, JULY 2026"),
    ("spending during August 2026", "spending during July 2026"),
    ("seasonally adjusted annual rate of $2,203.1", "unadjusted annual rate of $2,203.1"),
    ("the revised July estimate", "the revised June estimate"),
    ("Total Construction", "Residential Construction"),
])
def test_construction_changed_metadata_or_measure_fails_closed(before, after):
    from market_data_hub.macro_releases.census_construction import parse
    from market_data_hub.macro_releases.tier1_publications import publication_text
    doc = document("construction-current.pdf")
    body, _ = publication_text(doc)
    assert before in body
    changed = replace(doc, body=body.replace(before, after).encode())
    with pytest.raises(FetchError, match="needs review"):
        parse(changed)


def test_construction_release_instant_gates_network_and_early_content():
    from market_data_hub.macro_releases.census_construction import fetch
    client = ConstructionReplay()
    token = RUN_INSTANT.set("2026-08-03T13:59:59Z")
    try:
        assert fetch(client, "us_construction_spending", "2026-01") == []
        assert client.requests == []
    finally:
        RUN_INSTANT.reset(token)
    token = RUN_INSTANT.set("2026-10-01T13:59:59Z")
    try:
        assert fetch(client, "us_construction_spending", "2026-01") == []
    finally:
        RUN_INSTANT.reset(token)
    token = RUN_INSTANT.set("2026-10-01T14:00:00Z")
    try:
        assert fetch(client, "us_construction_spending", "2026-01")[0].value == 0.9
    finally:
        RUN_INSTANT.reset(token)
    from market_data_hub.macro_releases.schedule import edition_status
    assert edition_status("2026-12-01", "2026-12-01T14:59:59Z", family="us_construction_spending") == "scheduled"
    assert edition_status("2026-12-01", "2026-12-01T15:00:00Z", family="us_construction_spending") == "due"


def test_construction_stale_current_page_has_only_publication_grace():
    from market_data_hub.macro_releases.census_construction import fetch
    from market_data_hub.macro_releases.schedule import PENDING_EDITIONS
    pending = PENDING_EDITIONS.set({})
    token = RUN_INSTANT.set("2026-11-02T15:00:00Z")
    try:
        assert fetch(ConstructionReplay(), "us_construction_spending", "2026-01")[0].edition == "2026-10-01"
        assert ("us_construction_spending", "2026-11-02") in PENDING_EDITIONS.get()
    finally:
        RUN_INSTANT.reset(token)
        PENDING_EDITIONS.reset(pending)
    token = RUN_INSTANT.set("2026-11-02T21:00:00Z")
    try:
        with pytest.raises(FetchError, match="needs review"):
            fetch(ConstructionReplay(), "us_construction_spending", "2026-01")
    finally:
        RUN_INSTANT.reset(token)


def test_construction_review_retains_changed_document_without_vintage(tmp_path, monkeypatch):
    from market_data_hub.macro_releases.tier1_publications import publication_text
    class Changed(ConstructionReplay):
        def get(self, *args, **kwargs):
            doc = super().get(*args, **kwargs)
            body, _ = publication_text(doc)
            changed = body.replace("Total Construction", "Residential Construction").encode()
            return replace(doc, body=changed, document_hash=hashlib.sha256(changed).hexdigest())
    monkeypatch.setattr(run_daily, "HttpClient", Changed)
    result = run_daily.execute(tmp_path / "review.sqlite", ["us_construction_spending"], 24, False,
                               raw_dir=tmp_path / "none")
    assert result["families"]["us_construction_spending"]["status"] == "needs_review", result
    with open_reader(tmp_path / "review.sqlite") as store:
        assert store.conn.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0] == 1
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 0


def test_construction_concept_and_exact_consensus_alias(tmp_path):
    from market_data_hub.indicator_cards import indicator_card
    from market_data_hub.macro_releases.consensus import Catalogue
    from market_data_hub.macro_releases.catalogue import INDICATORS
    assert indicator_card("cat:us_construction_spending_mm").key == "us_construction_spending"
    with open_writer(tmp_path / "aliases.sqlite") as store:
        for indicator in INDICATORS.values():
            store.put(indicator)
        catalogue = Catalogue(store)
        alias, reason = catalogue.match({"country": "USD"}, "Construction Spending m/m")
        assert reason is None and alias["indicator_id"] == "us_construction_spending_mm"
        assert catalogue.match({"country": "USD"}, "Residential Construction Spending m/m")[0] is None
        assert catalogue.match({"country": "CAD"}, "Construction Spending m/m")[0] is None
