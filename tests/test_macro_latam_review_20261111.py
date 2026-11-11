"""Nov 2026 Latin American publications stuck in needs_review, and the review trail.

The fixtures in fixtures/macro_releases/latam_2026_11 are reconstructed text
extracts (see each file's 'provenance'): the issuer hosts were not reachable
from the session that wrote them. Only the cells listed in 'tested' carry the
diagnosed values of the live documents.
"""
import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from test_macro_m9i import GOLDEN, Replay, document

from market_data_hub.macro_releases import (
    banxico_policy,
    bcb_policy,
    inegi_cpi,
    open_writer,
    run_daily,
    run_log,
)
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.derived import percent_change
from market_data_hub.macro_releases.http import Document, FetchError
from market_data_hub.macro_releases.report import collect_report, render_html
from market_data_hub.macro_releases.schedule import RUN_INSTANT

ROOT = Path(__file__).parent / "fixtures/macro_releases"
NOV = ROOT / "latam_2026_11"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network forbidden")
    monkeypatch.setattr(requests.Session, "request", forbidden)
    token = RUN_INSTANT.set("2026-11-11T18:00:00Z")
    yield
    RUN_INSTANT.reset(token)


def extract(name, module, monkeypatch, *, text=None):
    """A PDF stand-in whose text layer is the stored extract."""
    data = json.loads((NOV / name).read_text(encoding="utf-8"))
    body = b"%PDF-1.7 text-extract fixture " + name.encode()
    monkeypatch.setattr(module, "publication_text", lambda doc: (text or data["text"], "pdf_table"))
    return Document(body, data["url"], data["retrieved_at"], hashlib.sha256(body).hexdigest()), data


def stored(folder, name):
    body = (ROOT / folder / name).read_bytes()
    meta = json.loads((ROOT / folder / (name + ".meta.json")).read_text())
    assert hashlib.sha256(body).hexdigest() == meta["document_hash"]
    return Document(body, meta["url"], meta["retrieved_at"], meta["document_hash"])


def test_fixtures_declare_they_are_reconstructed():
    for path in NOV.glob("*.json"):
        meta = json.loads(path.read_text(encoding="utf-8"))
        if path.name == "bcb-nov.json":
            continue  # API body; provenance is in its .meta.json
        assert "NOT a live capture" in meta["provenance"] and meta["tested"]


# 1. INEGI: the October bulletin has 7 pages.
def test_inegi_october_bulletin_with_seven_pages(monkeypatch):
    doc, _ = extract("inegi-oct.json", inegi_cpi, monkeypatch)
    rows = {r.indicator_id: r for r in inegi_cpi.parse(doc)}
    assert rows["mx_cpi_yy"].value == 3.75 and rows["mx_core_cpi_yy"].value == 3.80
    assert {r.period for r in rows.values()} == {"2026-10"}
    assert {r.edition for r in rows.values()} == {"2026-11-09"}


@pytest.mark.parametrize("stamp", ["Página 1/7", "Pagina 1/7", "Página 1/12", "Pagina 1/6"])
def test_inegi_page_stamp_accepts_any_page_count_with_or_without_accent(monkeypatch, stamp):
    data = json.loads((NOV / "inegi-oct.json").read_text(encoding="utf-8"))
    text = data["text"].replace("Página 1/7", stamp, 1)
    doc, _ = extract("inegi-oct.json", inegi_cpi, monkeypatch, text=text)
    assert len(inegi_cpi.parse(doc)) == 2


def test_inegi_still_requires_first_page_stamp(monkeypatch):
    data = json.loads((NOV / "inegi-oct.json").read_text(encoding="utf-8"))
    doc, _ = extract("inegi-oct.json", inegi_cpi, monkeypatch,
                     text=data["text"].replace("Página 1/7", "Página 2/7", 1))
    with pytest.raises(FetchError, match="needs review"):
        inegi_cpi.parse(doc)


def test_inegi_archive_url_is_year_agnostic():
    assert inegi_cpi.archive_url("2026-11-09").endswith("/2026/inpc/inpc_2q2026_11.pdf")
    assert inegi_cpi.archive_url("2027-01-08").endswith("/2027/inpc/inpc_2q2027_01.pdf")


# 2. BCB: the 2026-11-04 statement says "taxa Selic".
def test_bcb_hold_statement_worded_taxa_selic():
    doc = stored("latam_2026_11", "bcb-nov.json")
    previous = stored("m9i", "bcb-sep.json")
    row, = bcb_policy.parse(doc, previous)
    assert (row.edition, row.value) == ("2026-11-04", 13.75)
    assert row.policy_decision["rates"]["selic_target"]["change_bp"] == 0


def test_bcb_still_rejects_rate_disagreement():
    doc = stored("latam_2026_11", "bcb-nov.json")
    changed = replace(doc, body=doc.body.replace(b"decidiu manter a taxa Selic em 13,75%",
                                               b"decidiu manter a taxa Selic em 13,50%"))
    with pytest.raises(FetchError, match="needs review"):
        bcb_policy.statement(changed)


# 3. Banxico: majority cut with split digits; vote recorded from the statement.
def banxico_doc(monkeypatch, text=None):
    doc, data = extract("banxico-nov.json", banxico_policy, monkeypatch, text=text)
    return doc, {"2026-11-05": data["url"]}, data


def test_banxico_majority_cut_records_vote_and_dissenters(monkeypatch):
    doc, archives, _ = banxico_doc(monkeypatch)
    row, = banxico_policy.parse(doc, archives=archives)
    assert (row.edition, row.value, row.printed) == ("2026-11-05", 6.25, "6.25")
    policy = row.policy_decision
    assert policy["rates"]["overnight_target"]["change_bp"] == -25
    assert policy["vote_split"] == {"for": 3, "against": 2}
    assert policy["vote"] == {"decision": "majority", "for": 3, "against": 2,
                              "dissenters": ["Galia Borja", "Jonathan Heath"],
                              "dissent_preference": "mantener"}


def test_banxico_majority_with_reducir_and_en_contra_wording(monkeypatch):
    _, _, data = banxico_doc(monkeypatch)
    text = (data["text"].replace("decidió disminuir", "decidió reducir")
            .replace("decidió por mayoría disminuir", "decidió por mayoria reducir")
            .replace("Votaron por mantener el objetivo para la Tasa de Interés Interbancaria a un día en 6.50% "
                     "Galia Borja y Jonathan Heath.",
                     "Votaron a favor Victoria Rodríguez Ceja, Omar Mejía Castelazo y José Gabriel Cuadra García. "
                     "Votaron en contra Galia Borja y Jonathan Heath, quienes preferían mantener el objetivo."))
    doc, archives, _ = banxico_doc(monkeypatch, text)
    row, = banxico_policy.parse(doc, archives=archives)
    assert row.policy_decision["vote"]["dissenters"] == ["Galia Borja", "Jonathan Heath"]
    assert row.policy_decision["vote_split"] == {"for": 3, "against": 2}


def test_banxico_majority_without_named_dissent_needs_review(monkeypatch):
    _, _, data = banxico_doc(monkeypatch)
    text = data["text"].split("Votaron por mantener")[0]
    doc, archives, _ = banxico_doc(monkeypatch, text)
    with pytest.raises(FetchError, match="needs review"):
        banxico_policy.parse(doc, archives=archives)


def test_banxico_unanimous_statements_keep_full_board_vote():
    doc = stored("m9i", "banxico-sep.pdf")
    row, = banxico_policy.parse(doc)
    assert row.policy_decision["vote_split"] == {"for": 5, "against": 0}
    assert row.policy_decision["vote"]["decision"] == "unanimous"
    assert row.policy_decision["vote"]["dissenters"] == []


def test_banxico_rejoins_only_split_rates():
    assert banxico_policy.rejoin_numbers("a un nivel de 6. 25%.") == "a un nivel de 6.25%."
    assert banxico_policy.rejoin_numbers("trimestre de 2026. 25 puntos") == "trimestre de 2026. 25 puntos"


# 4-6. Reason trail, review-queue closure and the report.
INEGI_SEP = next(c for c in GOLDEN if c["family"] == "mx_cpi" and c["edition"] == "2026-10-08")


def clock(monkeypatch, instant):
    class Clock:
        @staticmethod
        def now(tz):
            return instant
    monkeypatch.setattr(run_daily, "datetime", Clock)
    # Run-log timestamps follow the replayed clock, not the wall clock.
    monkeypatch.setattr(run_log, "now", lambda: (instant + timedelta(minutes=5)).isoformat(timespec="microseconds"))


def test_parser_rejection_reason_is_persisted_and_reported(tmp_path, monkeypatch):
    class Client(Replay):
        def get(self, provider, url, params=None):
            if url == INEGI_SEP["source_url"]:
                return replace(document(INEGI_SEP["fixture"]), body=b"unknown format")
            return super().get(provider, url, params)
    monkeypatch.setattr(run_daily, "HttpClient", Client)
    clock(monkeypatch, datetime(2026, 10, 27, 18, tzinfo=timezone.utc))
    target = tmp_path / "reasons.sqlite"
    result = run_daily.execute(target, ["mx_cpi"], 24, False, raw_dir=tmp_path / "none")
    family = result["families"]["mx_cpi"]
    assert family["status"] == "needs_review" and family["error_category"] == "parser_rejected"
    with open_writer(target) as store:
        row = store.conn.execute("SELECT * FROM collect_run_family_reviews").fetchone()
    reasons = json.loads(row["review_reasons"])
    assert row["error_category"] == "parser_rejected"
    assert reasons == [{"reason": "edition_needs_review:2026-10-08", "edition": "2026-10-08",
                        "category": "parser_rejected",
                        "message": "INEGI monthly bulletin origin/format needs review"}]
    report = collect_report(target, now=datetime(2026, 11, 11, tzinfo=timezone.utc))
    issue, = report["collection_issues"]
    assert issue["error_category"] == "parser_rejected" and issue["review_reasons"] == reasons
    due = {(r["family"], r["edition"]): r for r in report["due_without_vintage"]}
    assert due["mx_cpi", "2026-10-08"]["label"] == "parser-rejected"
    html = render_html(report)
    assert "INEGI monthly bulletin origin/format needs review" in html
    assert "No reason recorded" not in html
    assert "Due editions with no vintage" in html


def test_us_cpi_yy_october_2026_is_an_issuer_gap_never_computed(tmp_path):
    with open_writer(tmp_path / "empty.sqlite"):
        pass
    report = collect_report(tmp_path / "empty.sqlite", now=datetime(2026, 11, 11, tzinfo=timezone.utc))
    gaps = [r for r in report["due_without_vintage"] if r["category"] == "issuer_gap"]
    assert {(r["indicator_id"], r["reference_period"], r["label"]) for r in gaps} == {
        ("us_cpi_headline_yy", "2026-10", "issuer did not publish"),
        ("us_cpi_core_yy", "2026-10", "issuer did not publish")}
    assert all("2025 federal government shutdown" in r["reason"] for r in gaps)
    assert "issuer did not publish" in render_html(report)
    before = collect_report(tmp_path / "empty.sqlite", now=datetime(2026, 11, 9, tzinfo=timezone.utc))
    assert not [r for r in before["due_without_vintage"] if r["category"] == "issuer_gap"]
    with pytest.raises(ValueError, match="never computed"):
        percent_change(None, SimpleNamespace(indicator_id="us_cpi_headline_yy", period="2026-10"), 1, 2)


def flagged_review(store, recorded_at="2026-10-27T19:00:00.000000+00:00"):
    """A needs-review capture of the Sep INEGI bulletin and its review row."""
    doc = document(INEGI_SEP["fixture"])
    rows = [replace(r, needs_review=True) for r in inegi_cpi.parse(doc)]
    with store.batch():
        persist(store, rows)
        store.conn.execute("INSERT INTO review_queue VALUES ('rev-1','mx_cpi','old-run',?,?,?,'pending')",
                           (doc.identity_hash, json.dumps(["sanity_band_exceeded:mx_cpi_yy"]), recorded_at))
        store.conn.execute("INSERT INTO review_queue VALUES ('rev-2','mx_cpi','old-run',?,?,?,'pending')",
                           (doc.identity_hash, json.dumps(["unrelated_later"]), "2099-01-01T00:00:00.000000+00:00"))


def test_clean_run_resolves_superseded_review_rows(tmp_path, monkeypatch):
    target = tmp_path / "reviews.sqlite"
    with open_writer(target) as store:
        flagged_review(store)
        assert store.conn.execute(
            "SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 0  # flagged values never certified
    monkeypatch.setattr(run_daily, "HttpClient", Replay)
    clock(monkeypatch, datetime(2026, 10, 28, 18, tzinfo=timezone.utc))
    result = run_daily.execute(target, ["mx_cpi"], 24, False, raw_dir=tmp_path / "none")
    assert result["families"]["mx_cpi"]["status"] == "ok", result
    with open_writer(target) as store:
        rows = {r["id"]: dict(r) for r in store.conn.execute("SELECT * FROM review_queue_current")}
        # The original append-only rows are untouched.
        assert {r[0] for r in store.conn.execute("SELECT status FROM review_queue")} == {"pending"}
    assert rows["rev-1"]["status"] == "resolved"
    assert rows["rev-1"]["resolved_run_id"] == result["run_id"]
    assert rows["rev-1"]["resolution_basis"] == "same_edition_verified"
    assert rows["rev-2"]["status"] == "pending"  # recorded after the clean run
    report = collect_report(target, now=datetime(2026, 11, 11, tzinfo=timezone.utc))
    assert report["open_reviews"] == []  # rev-2 is not yet recorded at this cutoff
    later = collect_report(target, now=datetime(2099, 1, 2, tzinfo=timezone.utc))
    assert [r["id"] for r in later["open_reviews"]] == ["rev-2"]
    assert "unrelated_later" in render_html(later)


def test_migration_backfill_closes_already_superseded_rows(tmp_path, monkeypatch):
    target = tmp_path / "backfill.sqlite"
    with open_writer(target) as store:
        flagged_review(store)
    monkeypatch.setattr(run_daily, "HttpClient", Replay)
    clock(monkeypatch, datetime(2026, 10, 28, 18, tzinfo=timezone.utc))
    result = run_daily.execute(target, ["mx_cpi"], 24, False, raw_dir=tmp_path / "none")
    # Rewind to a schema-9 store: drop only what migration 010 adds.
    with sqlite3.connect(target) as conn:
        for statement in ("DROP VIEW review_queue_current", "DROP VIEW review_queue_supersessions",
                          "DROP TABLE review_queue_resolutions", "DROP TABLE collect_run_family_reviews",
                          "DELETE FROM schema_compatibility WHERE schema_version=10", "PRAGMA user_version=9"):
            conn.execute(statement)
    with open_writer(target) as store:
        resolved = [dict(r) for r in store.conn.execute("SELECT * FROM review_queue_resolutions")]
    assert [(r["review_id"], r["run_id"], r["status"]) for r in resolved] == [
        ("rev-1", result["run_id"], "resolved")]
