"""Offline certification against saved real issuer publications and exact editions."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest
import requests

from market_data_hub.macro_releases import open_reader, open_writer, run_daily
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.http import Document, FetchError
from market_data_hub.macro_releases.dol import DOLAdapter
from market_data_hub.macro_releases.eurostat import EurostatAdapter
from market_data_hub.macro_releases.ons import ONSAdapter
from market_data_hub.macro_releases.ons_rates import SERIES, EDITIONS as UK_EDITIONS

ROOT = Path(__file__).parent / "fixtures/macro_releases/m9a2"
GOLDEN = json.loads((ROOT / "golden-set.json").read_text())["cases"]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network forbidden in M9a-2 tests")
    monkeypatch.setattr(requests.Session, "request", forbidden)


def document(name):
    body = (ROOT / name).read_bytes()
    meta = json.loads((ROOT / (name + ".meta.json")).read_text())
    assert hashlib.sha256(body).hexdigest() == meta["sha256"]
    return Document(body, meta["url"], meta["retrieved_at"], meta["sha256"])


def parse(case):
    if case["family"] == "us_jobless_claims":
        return DOLAdapter.parse(document(case["fixture"]), "2026-01")
    if case["family"] == "ea_gdp":
        doc = document(case["fixture"])
        return (EurostatAdapter.parse_gdp_snapshot(doc, case["edition"], "2026-01") if case["stage"] == "update"
                else EurostatAdapter.parse_gdp_publication(doc, "2026-01"))
    return ONSAdapter.parse_rate(document(case["fixture"]), case["series"], "2026-01")


@pytest.mark.parametrize("case", GOLDEN, ids=lambda c: c["indicator"] + ":" + c["period"])
def test_golden_issuer_rates(case, tmp_path):
    row = next(o for o in parse(case) if o.indicator_id == case["indicator"] and o.period == case["period"])
    assert (row.value, row.edition) == (case["value"], case["edition"])
    assert row.provider_vintage and row.document.url == case["source_url"]
    with open_writer(tmp_path / "golden.sqlite") as store:
        persist(store, [row])
        assert store.latest_official(row.indicator_id)[0]["value"] == case["value"]


class Replay:
    def __init__(self, **kwargs):
        pass

    def get(self, provider, url, params=None):
        for cdid, (_, _, _, _, path) in SERIES.items():
            if url == "https://www.ons.gov.uk" + path + "/data":
                assert provider == "ons"
                return document(cdid.lower() + ".json")
        if provider == "eurostat":
            for name in ("ea-calendar.json", "ea-gdp.json", "ea-gdp-flash.html", "ea-gdp-second.html", "ea-gdp-regular.html"):
                if document(name).url.split("?")[0] == url:
                    return document(name)
        if provider == "dol":
            for name in ("dol.pdf", "dol-2026-10-15.pdf", "dol-100126.pdf", "dol-100826.pdf"):
                if document(name).url == url:
                    return document(name)
        raise AssertionError("Unexpected fixture URL: " + url)


def test_uk_daily_official_cards_and_idempotence(tmp_path, monkeypatch):
    monkeypatch.setattr(run_daily, "HttpClient", Replay)
    db = tmp_path / "uk.sqlite"
    result = run_daily.execute(db, list(UK_EDITIONS), 24, False, raw_dir=tmp_path / "none")
    assert result["status"] == "ok", result
    assert all(r["vintages_added"] > 0 for r in result["families"].values())
    with open_reader(db) as store:
        cards = store.release_cards("2026-10-19", "2026-10-23")["cards"]
        assert {c["indicator_id"] for c in cards} == {r[1] for r in SERIES.values()}
        assert all(c["resolution"]["status"] == "official" for c in cards)
    again = run_daily.execute(db, list(UK_EDITIONS), 24, False, raw_dir=tmp_path / "none")
    assert again["status"] == "no_change", again


@pytest.mark.parametrize("field,value", [("cdid", "WRONG"), ("unit", "index"),
    ("title", "CPIH"), ("releaseDate", "2027-01-20T07:00:00Z")])
def test_changed_ons_measure_or_edition_fails_closed(field, value):
    doc = document("d7g7.json")
    payload = doc.json()
    payload["description"][field] = value
    with pytest.raises(FetchError, match="needs review"):
        ONSAdapter.parse_rate(replace(doc, body=json.dumps(payload).encode()), "D7G7")


def test_three_month_windows_are_ending_months_and_not_monthly_rates():
    rows = ONSAdapter.parse_rate(document("mgsx.json"), "MGSX", "2026-07")
    assert [(o.period, o.value) for o in rows] == [("2026-07", 4.9), ("2026-08", 4.7)]
    assert rows[-1].issuer_local_time == "2026-10-20T07:00:00+01:00"
    assert UK_EDITIONS["uk_labour_market"]["2026-12-15"][0] == "2026-10"
    assert UK_EDITIONS["uk_cpi"]["2026-12-16"][0] == "2026-11"


def test_unknown_ons_format_retains_document_in_review_queue(tmp_path, monkeypatch):
    class Changed(Replay):
        def get(self, *args, **kwargs):
            doc = super().get(*args, **kwargs)
            return replace(doc, body=doc.body.replace(b'"unit":"%"', b'"unit":"index"'))
    monkeypatch.setattr(run_daily, "HttpClient", Changed)
    db = tmp_path / "review.sqlite"
    result = run_daily.execute(db, ["uk_cpi"], 24, False, raw_dir=tmp_path / "none")
    assert result["families"]["uk_cpi"]["status"] == "needs_review", result
    with open_reader(db) as store:
        assert store.conn.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0] > 0
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 0


def test_gdp_unchanged_flash_and_later_revision_are_separate_editions(tmp_path, monkeypatch):
    monkeypatch.setattr(run_daily, "HttpClient", Replay)
    db = tmp_path / "gdp.sqlite"
    result = run_daily.execute(db, ["ea_gdp"], 24, False, raw_dir=tmp_path / "none")
    assert result["families"]["ea_gdp"]["status"] == "ok", result
    with open_reader(db) as store:
        rows = store.conn.execute("SELECT estimate_stage,value FROM value_vintages WHERE indicator_id='ea21_gdp_qq' AND reference_period='2026-Q2' ORDER BY issuer_published_at").fetchall()
        assert [tuple(r) for r in rows] == [("preliminary_flash", 0.4), ("flash", 0.4), ("regular", 0.6), ("update", 0.6)]
        cards = store.release_cards("2026-10-19", "2026-10-23")["cards"]
        assert len(cards) == 2 and all(c["resolution"]["status"] == "official" for c in cards)
    again = run_daily.execute(db, ["ea_gdp"], 24, False, raw_dir=tmp_path / "none")
    assert again["status"] == "no_change", again


def test_gdp_dataset_updated_is_not_a_publication_date():
    rows = EurostatAdapter.parse_gdp_snapshot(document("ea-gdp.json"), "2026-10-20")
    assert all(o.edition == "2026-10-20" and o.provider_vintage.startswith("2026-10-22") for o in rows)
    assert all(o.precision == "date_only" for o in rows)
    with pytest.raises(FetchError, match="needs review"):
        EurostatAdapter.parse_gdp_snapshot(document("ea-gdp.json"), "2026-07-30")


def test_gdp_unknown_calendar_edition_and_membership_fail_closed():
    from market_data_hub.macro_releases.eurostat_gdp import calendar_editions
    doc = document("ea-calendar.json")
    with pytest.raises(FetchError, match="needs review"):
        calendar_editions(replace(doc, body=doc.body.replace(b"2026-10-20T11:00Z", b"2026-10-19T11:00Z")))
    doc = document("ea-gdp-flash.html")
    with pytest.raises(FetchError, match="needs review"):
        EurostatAdapter.parse_gdp_publication(replace(doc, body=doc.body.replace(b"Euro area (EA21)", b"Euro area (EA22)")))


def test_weekly_claims_official_card_revisions_and_idempotence(tmp_path, monkeypatch):
    from market_data_hub.macro_releases.catalogue import INDICATORS
    from market_data_hub.macro_releases.read_api import prior_period
    monkeypatch.setattr(run_daily, "HttpClient", Replay)
    db = tmp_path / "claims.sqlite"
    result = run_daily.execute(db, ["us_jobless_claims"], 24, False, raw_dir=tmp_path / "none")
    assert result["families"]["us_jobless_claims"]["status"] == "ok", result
    assert INDICATORS["us_initial_jobless_claims"].frequency == "W"
    assert INDICATORS["us_initial_jobless_claims"].scale == 1000
    assert prior_period("2026-10-17", "W") == "2026-10-10"
    assert prior_period("2026-10-17") == "2026-10-16"  # Daily semantics unchanged.
    assert prior_period("2026-10") == "2026-09"
    with open_reader(db) as store:
        cards = store.release_cards("2026-10-19", "2026-10-23")["cards"]
        assert len(cards) == 1
        card = cards[0]
        assert card["resolution"]["status"] == "official"
        assert card["resolution"]["reference_period"] == "2026-10-17"
        assert card["resolution"]["value"] == 194
        assert card["resolution"]["source"]["issuer_id"] == "DOL"
        rows = store.conn.execute("SELECT value FROM value_vintages WHERE reference_period='2026-10-10' ORDER BY issuer_published_at").fetchall()
        assert [r[0] for r in rows] == [199, 200]
    again = run_daily.execute(db, ["us_jobless_claims"], 24, False, raw_dir=tmp_path / "none")
    assert again["status"] == "no_change", again


def test_claims_unknown_edition_reference_week_and_format_fail_closed():
    from pypdf import PdfReader
    from io import BytesIO
    from market_data_hub.macro_releases.dol import parse_text, EDITIONS
    doc = document("dol.pdf")
    text = PdfReader(BytesIO(doc.body)).pages[0].extract_text()
    for before, after in (("October 22, 2026", "October 23, 2026"),
                          ("October 17", "October 16"),
                          ("initial claims", "continuing claims")):
        with pytest.raises(FetchError, match="needs review"):
            parse_text(text.replace(before, after), doc)
    assert EDITIONS["2026-11-25"][0] == "2026-11-21"
    assert "2026-11-26" not in EDITIONS
    assert EDITIONS["2026-12-31"][0] == "2026-12-26"


def test_all_new_families_replay_together(tmp_path, monkeypatch):
    monkeypatch.setattr(run_daily, "HttpClient", Replay)
    result = run_daily.execute(tmp_path / "new.sqlite", [*UK_EDITIONS, "ea_gdp", "us_jobless_claims"],
                               24, False, raw_dir=tmp_path / "none")
    assert result["status"] == "ok", result
    assert all(r["vintages_added"] > 0 for r in result["families"].values())
    assert all(all(d["status"] == "scheduled" and d["edition"] > "2026-10-24"
                   for d in r["scheduled_editions"]) for r in result["families"].values())
