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


# ----------------------------------------------------------------- tool layer
def test_tool_layer_gating(tmp_db):
    from market_data_hub import agent_tools as at
    out = json.loads(at.tool_ensure_financials("AAPL"))
    assert "allow_write" in out["error"]
    out = json.loads(at.tool_get_financials_coverage())
    assert out == []
