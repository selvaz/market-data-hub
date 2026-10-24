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
