"""Offline consumer contracts for saved issuer text and dated decisions."""
from dataclasses import replace
import hashlib
from itertools import takewhile
from pathlib import Path
import sqlite3

import pytest
import requests

from market_data_hub.macro_releases import ReadAPI, open_reader, open_writer
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.catalogue import CENTRAL_BANKS, LATAM_FAMILIES
from market_data_hub.macro_releases.documents import put_document
from market_data_hub.macro_releases.http import Document
from test_macro_read_api import observation
from test_macro_m9c import GOLDEN, parse

BEFORE = "2026-10-28T00:00:00Z"
AFTER = "2026-10-30T00:00:00Z"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("read API must never use the network")
    monkeypatch.setattr(requests.Session, "request", forbidden)


def doc(body, seen="2026-10-29T13:00:00Z", url="https://issuer.example/gdp"):
    return Document(body, url, seen, hashlib.sha256(body).hexdigest())


def gdp(document, stage="advance"):
    return replace(observation("us_gdp_qq_saar", "us_gdp", "2026-Q3", 3.0,
                               "2026-10-29", stage, "BEA"), document=document)


def test_saved_html_by_event_and_period_readonly(tmp_path):
    db = tmp_path / "docs.sqlite"
    document = doc(b"<html><style>hidden</style><h1>GDP Q3 advance</h1><p>Growth &amp; demand.</p><script>hidden</script></html>")
    with open_writer(db) as store:
        persist(store, [gdp(document)])
    before = db.read_bytes()
    with open_reader(db) as store:
        api = ReadAPI(store)
        by_event = api.release_documents("BEA:us_gdp:advance:2026-10-29", as_known_at=AFTER)
        by_period = store.release_documents(indicator="us_gdp_qq_saar", reference_period="2026-Q3", as_known_at=AFTER)
        assert by_event["documents"] == by_period["documents"]
        saved, = by_event["documents"]
        assert saved["text"] == "GDP Q3 advance Growth & demand."
        assert saved["url"] == document.url
        assert saved["retrieved_at"] == "2026-10-29T13:00:00.000000+00:00"
        assert saved["document_hash"] == document.identity_hash
        assert saved["content_type"] == "text/html"
        assert saved["content_type_basis"] == "inferred_from_saved_body"
        assert saved["text_missing_reason"] is None
        assert not saved["truncated"] and not by_event["empty"]
        assert store.conn.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            store.conn.execute("DELETE FROM documents")
    assert db.read_bytes() == before


def test_pdf_shared_budget_and_all_editions(tmp_path):
    pdf = (Path(__file__).parent / "fixtures/macro_releases/bea/advance-release.pdf").read_bytes()
    with open_writer(tmp_path / "pdf.sqlite") as store:
        persist(store, [gdp(doc(pdf)), gdp(doc(b"<p>Second estimate</p>", seen="2026-10-29T14:00:00Z"), "second")])
        result = store.release_documents(indicator="us_gdp_qq_saar", reference_period="2026-Q3", max_chars=80, as_known_at=AFTER)
        first, second = result["documents"]
        assert first["content_type"] == "application/pdf"
        assert first["text"].strip() and first["text_missing_reason"] is None
        assert first["text_chars"] > 80 and first["truncated"]
        assert second["text"] == "" and second["truncated"]
        assert sum(len(d["text"]) for d in result["documents"]) == 80


@pytest.mark.parametrize("body_seen, link_seen", [
    ("2026-10-29T13:00:00Z", "2026-10-27T13:00:00Z"),
    ("2026-10-27T13:00:00Z", "2026-10-29T13:00:00Z"),
])
def test_documents_knowledge_cutoff_applies_to_body_and_link(tmp_path, body_seen, link_seen):
    with open_writer(tmp_path / "cutoff.sqlite") as store:
        document = doc(b"<p>Saved later</p>", seen=body_seen)
        # Content addressing keeps the first saved document metadata even if
        # another reading links the same hash with a different knowledge time.
        with store.transaction():
            put_document(store, document)
        persist(store, [replace(gdp(replace(document, retrieved_at=link_seen)), published_at="2026-10-27T12:30:00Z")])
        query = dict(indicator="us_gdp_qq_saar", reference_period="2026-Q3")
        early = store.release_documents(**query, as_known_at=BEFORE)
        assert early["documents"] == []
        assert early["empty"][0]["reason"] == "document_not_found"
        assert store.release_documents(**query, as_known_at=max(body_seen, link_seen))["documents"]


def test_missing_unlinked_and_mirror_documents_explained(tmp_path):
    with open_writer(tmp_path / "missing.sqlite") as store:
        document = doc(b"<p>Unlinked body at the same URL</p>")
        with store.transaction():
            put_document(store, document)
        persist(store, [replace(gdp(doc(b"<p>Mirror</p>")), source="FRED")])
        for result in (store.release_documents("missing", as_known_at=AFTER),
                       store.release_documents(indicator="us_gdp_qq_saar", reference_period="2026-Q3", as_known_at=AFTER)):
            assert not result["documents"]
            assert result["empty"][0]["reason"] == "document_not_found"
            assert result["empty"][0]["detail"]


@pytest.mark.parametrize("body, reason", [(b"%PDF-broken", "text_extraction_failed"), (b"<html></html>", "no_extractable_text")])
def test_unreadable_text_keeps_metadata(tmp_path, body, reason):
    with open_writer(tmp_path / "broken.sqlite") as store:
        persist(store, [gdp(doc(body))])
        result = store.release_documents(indicator="us_gdp_qq_saar", reference_period="2026-Q3", as_known_at=AFTER)
        saved, = result["documents"]
        assert saved["text"] == "" and saved["text_missing_reason"] == reason
        assert saved["document_hash"]


def policy_obs(indicator, family, edition, rate, delta, seen=None):
    facts = {"decision_date": edition, "rates": {"policy": {"rate": rate, "change_bp": delta}},
             "vote_split": {"for": 9, "against": 0}}
    obs = observation(indicator, family, edition, rate, edition, "final",
                      source=(CENTRAL_BANKS.get(family) or LATAM_FAMILIES[family])[0].issuer_id,
                      seen=seen or edition + "T18:01:00Z", policy_decision=facts)
    return obs


@pytest.mark.parametrize("family, measures", list(CENTRAL_BANKS.items()) +
                         [(f, LATAM_FAMILIES[f]) for f in ("banxico_policy", "bcb_policy")])
def test_decision_history_all_eight_banks_order_changes_and_hold_streak(tmp_path, family, measures):
    indicator = measures[0].id
    observations = [policy_obs(indicator, family, edition, rate, delta) for edition, rate, delta in
                    (("2026-06-01", 4.25, -25), ("2026-07-01", 4.0, -25),
                     ("2026-08-01", 4.0, 0), ("2026-09-01", 4.0, 0))]
    db = tmp_path / "decisions.sqlite"
    with open_writer(db) as store:
        persist(store, list(reversed(observations)))
    with open_reader(db) as store:
        result = store.decision_history(indicator, as_known_at=AFTER)
        rows = result["decisions"]
        assert [r["decision_date"] for r in rows] == ["2026-09-01", "2026-08-01", "2026-07-01", "2026-06-01"]
        assert [r["rate"] for r in rows] == [4, 4, 4, 4.25]
        assert [r["change_bp"] for r in rows] == [0, 0, -25, -25]
        assert len(list(takewhile(lambda r: r["change_bp"] == 0, rows))) == 2
        assert next(r["decision_date"] for r in rows if r["change_bp"]) == "2026-07-01"
        assert sum(r["change_bp"] for r in rows) == -50
        assert rows[0]["vote_split"] == {"for": 9, "against": 0}
        assert not result["has_more"] and not result["empty"]
        limited = store.decision_history(indicator, limit=2, as_known_at=AFTER)
        assert limited["decisions"] == rows[:2] and limited["has_more"]


@pytest.mark.parametrize("case", GOLDEN, ids=lambda c: c["family"] + c["edition"])
def test_parsed_statement_history_preserves_ranges_rates_and_votes(tmp_path, case):
    obs = parse(case)
    with open_writer(tmp_path / "golden.sqlite") as store:
        persist(store, [obs])
        row, = store.decision_history(obs.indicator_id, as_known_at=AFTER)["decisions"]
        assert row["rates"] == case["rates"]
        assert row["vote_split"] == case["vote_split"]
        assert row["change_bp"] == next(iter(case["rates"].values()))["change_bp"]
        assert row["rate"] == obs.value


def test_decision_cutoff_corrections_deduplication_and_mirrors(tmp_path):
    indicator = "fed_funds_rate_decision"
    original = policy_obs(indicator, "fomc", "2026-09-01", 4.0, 0)
    correction = policy_obs(indicator, "fomc", "2026-09-01", 4.25, 25, seen="2026-10-29T00:00:00Z")
    late_older = policy_obs(indicator, "fomc", "2026-08-01", 4, -25, seen="2026-10-29T00:00:00Z")
    future = replace(policy_obs(indicator, "fomc", "2026-11-01", 5, 75),
                     document=replace(original.document, retrieved_at="2026-10-27T00:00:00Z"))
    mirror = replace(policy_obs(indicator, "fomc", "2026-07-01", 9, 500), source="FRED")
    with open_writer(tmp_path / "corrections.sqlite") as store:
        persist(store, [original, correction, late_older, future, mirror])
        early = store.decision_history(indicator, as_known_at=BEFORE)["decisions"]
        assert len(early) == 1 and early[0]["rate"] == 4 and early[0]["change_bp"] == 0
        late = store.decision_history(indicator, as_known_at=AFTER)["decisions"]
        assert len(late) == 2 and late[0]["rate"] == 4.25 and late[0]["change_bp"] == 25
        assert late[1]["decision_date"] == "2026-08-01"


def test_empty_decision_history_explained(tmp_path):
    with open_writer(tmp_path / "empty.sqlite") as store:
        assert store.decision_history("fed_funds_rate_decision")["empty"][0]["reason"] == "decision_history_not_found"
        assert store.decision_history("us_gdp_qq_saar")["empty"][0]["reason"] == "not_covered"


@pytest.mark.parametrize("kwargs", [{}, {"indicator": "us_gdp_qq_saar"},
                         {"release_event_id": "event", "indicator": "us_gdp_qq_saar"},
                         {"release_event_id": "event", "max_chars": 0},
                         {"release_event_id": "event", "max_chars": True}])
def test_document_arguments(tmp_path, kwargs):
    with open_writer(tmp_path / "args.sqlite") as store:
        with pytest.raises(ValueError):
            store.release_documents(**kwargs)


@pytest.mark.parametrize("limit", [0, -1, 1.5, True])
def test_decision_limit_validation(tmp_path, limit):
    with open_writer(tmp_path / "args.sqlite") as store:
        with pytest.raises(ValueError):
            store.decision_history("fed_funds_rate_decision", limit=limit)
