# -*- coding: utf-8 -*-
"""Tests for the SEC slice (plan v3.1 Fase 3): transport guardrails, entity
resolution, append-only facts, versioned mapping, bounded readers.

All offline: SEC payloads are minimal fixtures shaped like the real
submissions / companyfacts JSON.
"""
from __future__ import annotations

import json

import pytest

from market_data_hub.db.connection import get_conn
from market_data_hub.services import financials as fin
from market_data_hub.sources import sec

CIK = "0000320193"


def _submissions(n_extra_filing: int = 0):
    acc = ["0000320193-24-000123", "0000320193-24-000081"]
    form = ["10-K", "10-Q"]
    filed = ["2024-11-01", "2024-08-02"]
    report = ["2024-09-28", "2024-06-29"]
    doc = ["aapl-20240928.htm", "aapl-20240629.htm"]
    for i in range(n_extra_filing):
        acc.append(f"0000320193-25-{i:06d}")
        form.append("10-Q")
        filed.append("2025-02-01")
        report.append("2024-12-28")
        doc.append(f"aapl-extra{i}.htm")
    return {"cik": 320193, "filings": {"recent": {
        "accessionNumber": acc, "form": form, "filingDate": filed,
        "reportDate": report, "primaryDocument": doc}}}


def _companyfacts(revenue=391_035_000_000.0):
    return {"cik": 320193, "facts": {"us-gaap": {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            {"start": "2023-10-01", "end": "2024-09-28", "val": revenue,
             "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-11-01",
             "accn": "0000320193-24-000123"},
        ]}},
        "NetIncomeLoss": {"units": {"USD": [
            {"start": "2023-10-01", "end": "2024-09-28", "val": 93_736_000_000.0,
             "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-11-01",
             "accn": "0000320193-24-000123"},
        ]}},
        "Assets": {"units": {"USD": [
            {"end": "2024-09-28", "val": 364_980_000_000.0, "fy": 2024,
             "fp": "FY", "form": "10-K", "filed": "2024-11-01",
             "accn": "0000320193-24-000123"},
        ]}},
    }}}


def _resolve(query):
    return {"cik": CIK, "name": "Apple Inc.", "ticker": "AAPL"}


def _ensure(tmp_db, **kw):
    args = dict(db_path=tmp_db, resolve=_resolve,
                fetch_submissions=lambda cik: _submissions(),
                fetch_facts=lambda cik: _companyfacts())
    args.update(kw)
    return fin.ensure_filings_and_facts("AAPL", **args)


# ------------------------------------------------------------------ transport
def test_transport_rejects_non_sec_hosts():
    with pytest.raises(sec.SecTransportError, match="not an allowed"):
        sec.get_json("https://evil.example.com/companyfacts.json")


def test_normalize_cik():
    assert sec.normalize_cik(320193) == CIK
    assert sec.normalize_cik("0000320193") == CIK
    with pytest.raises(ValueError):
        sec.normalize_cik("AAPL")


# --------------------------------------------------------------------- ensure
def test_ensure_registers_issuer_ingests_and_is_idempotent(tmp_db):
    res = _ensure(tmp_db)
    assert res["status"] == "completed"
    assert res["filings"] == 2
    assert res["new_facts"] == 3
    assert res["issuer_id"].startswith("iss_")

    # same day, same issuer -> job reused, nothing refetched
    res2 = _ensure(tmp_db, fetch_facts=lambda cik: (_ for _ in ()).throw(
        RuntimeError("must not fetch")))
    assert res2["reused"] is True and res2["job_id"] == res["job_id"]

    con = get_conn(tmp_db, read_only=True)
    try:
        assert con.execute("SELECT COUNT(*) FROM issuers").fetchone()[0] == 1
        assert con.execute(
            "SELECT value FROM identifier_aliases WHERE namespace='cik'"
        ).fetchone()[0] == CIK
        cov = con.execute(
            "SELECT n_filings, n_facts, forms FROM sec_coverage").fetchone()
    finally:
        con.close()
    assert cov[0] == 2 and cov[1] == 3 and "10-K" in cov[2]


def test_ensure_survives_legacy_sec_column_order(tmp_db):
    # Regression: a DB migrated from an older schema can have sec_filings with
    # ``updated_at`` positioned BEFORE the run_id columns. A positional
    # ``INSERT ... SELECT`` then writes run_id (a string) into the TIMESTAMP
    # column and raises a ConversionException. The INSERTs must name their
    # target columns so on-disk column order is irrelevant. Fresh-schema tests
    # never caught this because they build the current column order.
    con = get_conn(tmp_db)
    con.execute("DROP TABLE sec_filings")
    con.execute("""
        CREATE TABLE sec_filings (
            cik VARCHAR NOT NULL, accession VARCHAR NOT NULL, form VARCHAR,
            filed_date DATE, report_date DATE, primary_doc VARCHAR,
            primary_doc_url VARCHAR, issuer_id VARCHAR,
            updated_at TIMESTAMP,                       -- legacy: before run ids
            first_seen_run_id VARCHAR, last_seen_run_id VARCHAR,
            PRIMARY KEY (cik, accession))
    """)
    con.close()

    res = _ensure(tmp_db)
    assert res["status"] == "completed" and res["filings"] == 2

    con = get_conn(tmp_db, read_only=True)
    try:
        row = con.execute(
            "SELECT last_seen_run_id, updated_at FROM sec_filings LIMIT 1"
        ).fetchone()
    finally:
        con.close()
    assert row[0].startswith("run_")           # run_id landed in the VARCHAR column
    assert not str(row[1]).startswith("run_")  # updated_at kept a real timestamp


def test_facts_are_append_only_on_restatement(tmp_db):
    _ensure(tmp_db)
    # force=True re-runs the job; a restated revenue value must APPEND a new
    # row, never overwrite the original fact
    res = _ensure(tmp_db, force=True,
                  fetch_facts=lambda cik: _companyfacts(revenue=390_000_000_000.0))
    assert res["new_facts"] == 1
    con = get_conn(tmp_db, read_only=True)
    try:
        vals = [r[0] for r in con.execute("""
            SELECT value FROM sec_company_facts
            WHERE concept = 'RevenueFromContractWithCustomerExcludingAssessedTax'
            ORDER BY value
        """).fetchall()]
    finally:
        con.close()
    assert vals == [390_000_000_000.0, 391_035_000_000.0]


def test_ensure_links_existing_listing_to_issuer(tmp_db):
    from market_data_hub.services import prices as svc
    con = get_conn(tmp_db)
    try:
        cand = svc._register_listing(con, {
            "symbol": "AAPL", "kind": "EQUITY", "name": "Apple",
            "exchange": None, "currency": None, "provider": "yahoo"})
    finally:
        con.close()
    res = _ensure(tmp_db)
    con = get_conn(tmp_db, read_only=True)
    try:
        linked = con.execute(
            "SELECT issuer_id FROM instruments WHERE instrument_id = ?",
            [cand["instrument_id"]]).fetchone()[0]
    finally:
        con.close()
    assert linked == res["issuer_id"]
    # resolve by ticker now walks listing -> instrument -> issuer, locally
    assert fin.resolve_issuer("AAPL", db_path=tmp_db)["cik"] == CIK


# -------------------------------------------------------------------- readers
def test_get_facts_mapped_line_and_bounds(tmp_db):
    _ensure(tmp_db)
    out = fin.get_facts("AAPL", line="revenue", db_path=tmp_db)
    assert out["n_returned"] == 1
    fact = out["facts"][0]
    # verifiability: unit, period, accession, filed date all present
    assert fact["unit"] == "USD"
    assert fact["accession"] == "0000320193-24-000123"
    assert fact["filed_date"].startswith("2024-11-01")
    assert fact["end_date"].startswith("2024-09-28")
    assert out["mapping"]["version"] == 1

    out = fin.get_facts("AAPL", line="not_a_line", db_path=tmp_db)
    assert "unknown statement line" in out["error"]

    # hard cap regardless of the requested limit
    out = fin.get_facts("AAPL", limit=10_000, db_path=tmp_db)
    assert out["n_returned"] <= fin._MAX_FACT_ROWS


def test_reader_on_unknown_issuer_points_to_ensure(tmp_db):
    out = fin.get_facts("MSFT", db_path=tmp_db)
    assert "ensure_filings_and_facts" in out["error"]
    assert fin.get_financials_coverage("MSFT", db_path=tmp_db) == []


def test_filing_run_provenance_survives_reingestion(tmp_db):
    """Audit CA-08: re-ingesting the same filing must NOT overwrite its run
    provenance — first_seen_run_id stays, last_seen_run_id advances."""
    first = _ensure(tmp_db)
    second = _ensure(tmp_db, force=True)
    assert second["run_id"] != first["run_id"]

    con = get_conn(tmp_db, read_only=True)
    try:
        rows = con.execute("""
            SELECT accession, first_seen_run_id, last_seen_run_id
            FROM sec_filings ORDER BY accession
        """).fetchall()
    finally:
        con.close()
    assert rows, "no filings ingested"
    for _accn, first_seen, last_seen in rows:
        assert first_seen == first["run_id"]
        assert last_seen == second["run_id"]


def test_facts_write_failure_rolls_back_filings_too(tmp_db, monkeypatch):
    """Audit CA-06 fault injection (SEC): filings and facts commit in ONE
    transaction — a failure between them leaves neither materialized."""
    def fetch_facts_boom(cik):
        raise RuntimeError("facts fetch boom")

    with pytest.raises(RuntimeError, match="facts fetch boom"):
        _ensure(tmp_db, fetch_facts=fetch_facts_boom)

    con = get_conn(tmp_db, read_only=True)
    try:
        n_filings = con.execute("SELECT COUNT(*) FROM sec_filings").fetchone()[0]
        n_facts = con.execute("SELECT COUNT(*) FROM sec_company_facts").fetchone()[0]
        job = con.execute("SELECT status FROM ingestion_jobs").fetchone()[0]
    finally:
        con.close()
    # the fetch failed in phase 2: NOTHING was written, job is error
    assert n_filings == 0 and n_facts == 0
    assert job == "error"


# ------------------------------------------------------------- get_statement
def _multi_year_facts():
    """3 fiscal years of revenue: FY2022 under the legacy 'Revenues' concept,
    FY2023-24 under the modern one; FY2023 restated in the FY2024 10-K; plus
    a quarterly stub and an instant Assets fact."""
    modern = "RevenueFromContractWithCustomerExcludingAssessedTax"
    return {"cik": 320193, "facts": {"us-gaap": {
        modern: {"units": {"USD": [
            # FY2023 as originally filed...
            {"start": "2022-10-02", "end": "2023-09-30", "val": 100.0,
             "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2023-11-03",
             "accn": "acc-2023"},
            # ...and restated in the FY2024 10-K (comparative)
            {"start": "2022-10-02", "end": "2023-09-30", "val": 101.0,
             "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-11-01",
             "accn": "acc-2024"},
            {"start": "2023-10-01", "end": "2024-09-28", "val": 120.0,
             "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-11-01",
             "accn": "acc-2024"},
            # quarterly stub in a 10-K context: must be excluded (<330 days)
            {"start": "2024-06-30", "end": "2024-09-28", "val": 30.0,
             "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-11-01",
             "accn": "acc-2024"},
        ]}},
        "Revenues": {"units": {"USD": [
            # legacy concept covers FY2022 only; must FILL that period but
            # not override the modern concept's FY2023/24
            {"start": "2021-09-26", "end": "2022-09-24", "val": 90.0,
             "fy": 2022, "fp": "FY", "form": "10-K", "filed": "2022-10-28",
             "accn": "acc-2022"},
            {"start": "2022-10-02", "end": "2023-09-30", "val": 999.0,
             "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2023-11-03",
             "accn": "acc-2023"},
        ]}},
        "Assets": {"units": {"USD": [
            {"end": "2024-09-28", "val": 500.0, "fy": 2024, "fp": "FY",
             "form": "10-K", "filed": "2024-11-01", "accn": "acc-2024"},
        ]}},
    }}}


def test_get_statement_periods_restatement_and_concept_fallback(tmp_db):
    _ensure(tmp_db, fetch_facts=lambda cik: _multi_year_facts())
    out = fin.get_statement("AAPL", db_path=tmp_db)
    rev = out["lines"]["revenue"]

    assert list(rev) == ["2024-09-28", "2023-09-30", "2022-09-24"]
    # restatement wins on read (latest filed_date), original stays in facts
    assert rev["2023-09-30"]["value"] == 101.0
    assert rev["2023-09-30"]["accession"] == "acc-2024"
    # concept priority: modern concept's 101.0 beats legacy 999.0
    # legacy concept fills the period the modern one lacks
    assert rev["2022-09-24"]["value"] == 90.0
    assert rev["2022-09-24"]["concept"] == "Revenues"
    # quarterly stub excluded
    assert 30.0 not in [v["value"] for v in rev.values()]
    # instant (balance) line present with no duration filter applied
    assert out["lines"]["assets"]["2024-09-28"]["value"] == 500.0
    assert out["mapping"]["version"] == 1

    # statement filter + period cap
    out = fin.get_statement("AAPL", statement="balance", periods=1,
                            db_path=tmp_db)
    assert set(out["lines"]) == {"assets", "liabilities", "equity"}
    assert len(out["lines"]["assets"]) == 1


def _amended_10ka_facts():
    """FY2023 revenue originally filed under 10-K, then restated via a
    10-K/A amendment (later filed_date) -- the amendment must win on read."""
    modern = "RevenueFromContractWithCustomerExcludingAssessedTax"
    return {"cik": 320193, "facts": {"us-gaap": {modern: {"units": {"USD": [
        {"start": "2022-10-02", "end": "2023-09-30", "val": 100.0,
         "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2023-11-03",
         "accn": "acc-2023"},
        {"start": "2022-10-02", "end": "2023-09-30", "val": 105.0,
         "fy": 2023, "fp": "FY", "form": "10-K/A", "filed": "2024-01-15",
         "accn": "acc-2023-amend"},
    ]}}}}}


def test_get_statement_includes_amended_annual_forms(tmp_db):
    """Codex P2: statement reads must include 10-K/A (and 20-F/A) so a
    restatement filed as an amendment supersedes the original on read."""
    _ensure(tmp_db, fetch_facts=lambda cik: _amended_10ka_facts())
    out = fin.get_statement("AAPL", db_path=tmp_db)
    rev = out["lines"]["revenue"]["2023-09-30"]
    assert rev["value"] == 105.0
    assert rev["accession"] == "acc-2023-amend"


# ----------------------------------------------------------------- tool layer
def test_tool_layer_gating(tmp_db):
    from market_data_hub import agent_tools as at
    out = json.loads(at.tool_ensure_financials("AAPL"))
    assert "allow_write" in out["error"]
    out = json.loads(at.tool_get_financials_coverage())
    assert out == []
