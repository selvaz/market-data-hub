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
            "symbol": "AAPL", "kind": "EQUITY", "name": None,
            "exchange": None, "currency": None, "provider": "yahoo"})
    finally:
        con.close()
    res = _ensure(tmp_db)
    con = get_conn(tmp_db, read_only=True)
    try:
        linked, name = con.execute(
            "SELECT issuer_id, name FROM instruments WHERE instrument_id = ?",
            [cand["instrument_id"]]).fetchone()
    finally:
        con.close()
    assert linked == res["issuer_id"]
    assert name == "Apple Inc."
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
    assert out["mapping"]["version"] == 3

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
    assert out["mapping"]["version"] == 3

    # statement filter + period cap
    out = fin.get_statement("AAPL", statement="balance", periods=1,
                            db_path=tmp_db)
    assert set(out["lines"]) == {"assets", "liabilities", "liabilities_derived", "equity",
                                 "long_term_debt", "short_term_debt",
                                 "current_portion_long_term_debt",
                                 "commercial_paper", "short_term_borrowings"}
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


def _ko_like_facts():
    """KO-like issuer: no plain Liabilities, CostOfGoodsAndServicesSold,
    GrossProfit, OperatingIncomeLoss, LongTermDebtNoncurrent, DebtCurrent and
    LongTermDebtCurrent (a component that must NOT override DebtCurrent)."""
    def dur(val):
        return {"units": {"USD": [{"start": "2024-12-28", "end": "2025-12-31",
                                   "val": val, "fy": 2025, "fp": "FY",
                                   "form": "10-K", "filed": "2026-02-20",
                                   "accn": "acc-ko"}]}}

    def inst(val):
        return {"units": {"USD": [{"end": "2025-12-31", "val": val, "fy": 2025,
                                   "fp": "FY", "form": "10-K",
                                   "filed": "2026-02-20", "accn": "acc-ko"}]}}
    return {"cik": 320193, "facts": {"us-gaap": {
        "CostOfGoodsAndServicesSold": dur(60.0),
        "GrossProfit": dur(40.0),
        "OperatingIncomeLoss": dur(15.0),
        "LongTermDebtNoncurrent": inst(30.0),
        "DebtCurrent": inst(7.0),
        "LongTermDebtCurrent": inst(3.0),
    }}}


def test_mapping_v2_resolves_margin_and_leverage_lines(tmp_db):
    _ensure(tmp_db, fetch_facts=lambda cik: _ko_like_facts())
    out = fin.get_statement("AAPL", db_path=tmp_db)
    assert out["mapping"]["version"] == 3
    ln = out["lines"]
    end = "2025-12-31"
    assert ln["cost_of_revenue"][end]["value"] == 60.0
    assert ln["cost_of_revenue"][end]["concept"] == "CostOfGoodsAndServicesSold"
    assert ln["gross_profit"][end]["value"] == 40.0
    assert ln["operating_income"][end]["value"] == 15.0
    assert ln["long_term_debt"][end]["value"] == 30.0
    # total current debt wins over its component (no double counting)
    assert ln["short_term_debt"][end]["value"] == 7.0
    assert ln["short_term_debt"][end]["concept"] == "DebtCurrent"
    # the component is its own line, never a fallback of the total
    assert ln["current_portion_long_term_debt"][end]["value"] == 3.0
    # no derived lines: absent Liabilities stays empty
    assert ln["liabilities"] == {}
    facts = fin.get_facts("AAPL", line="gross_profit", db_path=tmp_db)
    assert facts["n_returned"] == 1 and facts["mapping"]["version"] == 3


def test_mapping_v1_unchanged_and_explicit(tmp_db):
    from market_data_hub.config_loader import get_xbrl_mapping
    v1 = get_xbrl_mapping(1)
    assert v1["version"] == 1
    assert set(v1["statement_lines"]) == {
        "revenue", "net_income", "assets", "liabilities", "equity",
        "operating_cash_flow"}
    v2 = get_xbrl_mapping(2)
    for key, cfg in v1["statement_lines"].items():
        assert v2["statement_lines"][key] == cfg
    _ensure(tmp_db, fetch_facts=lambda cik: _ko_like_facts())
    out = fin.get_statement("AAPL", mapping_version=1, db_path=tmp_db)
    assert out["mapping"]["version"] == 1
    assert "gross_profit" not in out["lines"]


def test_mapping_v2_totals_vs_components_ko_shape(tmp_db):
    """KO FY2025 shape: no DebtCurrent, no CostOfRevenue total; components
    stay on their own lines and totals stay empty (never a partial sum)."""
    def inst(v):
        return {"units": {"USD": [{"end": "2025-12-31", "val": v, "fy": 2025,
                                   "fp": "FY", "form": "10-K",
                                   "filed": "2026-02-20", "accn": "a"}]}}

    def dur(v):
        return {"units": {"USD": [{"start": "2025-01-01", "end": "2025-12-31",
                                   "val": v, "fy": 2025, "fp": "FY",
                                   "form": "10-K", "filed": "2026-02-20",
                                   "accn": "a"}]}}
    facts = {"cik": 320193, "facts": {"us-gaap": {
        "CostOfGoodsSold": dur(5.0), "CostOfServices": dur(2.0),
        "LongTermDebtAndCapitalLeaseObligations": inst(42.0),
        "LongTermDebt": inst(44.0),
        "LongTermDebtAndCapitalLeaseObligationsCurrent": inst(1.8),
        "CommercialPaper": inst(1.5), "OtherShortTermBorrowings": inst(0.06),
    }}}
    _ensure(tmp_db, fetch_facts=lambda cik: facts)
    ln = fin.get_statement("AAPL", db_path=tmp_db)["lines"]
    end = "2025-12-31"
    assert ln["cost_of_revenue"] == {} and ln["short_term_debt"] == {}
    assert ln["cost_of_goods_sold"][end]["value"] == 5.0
    assert ln["cost_of_services"][end]["value"] == 2.0
    # non-current concept beats LongTermDebt (which includes the current part)
    assert ln["long_term_debt"][end]["value"] == 42.0
    assert ln["current_portion_long_term_debt"][end]["value"] == 1.8
    assert ln["commercial_paper"][end]["value"] == 1.5
    assert ln["short_term_borrowings"][end]["value"] == 0.06


def test_tool_layer_accepts_mapping_version(monkeypatch):
    from market_data_hub import agent_tools as at
    seen = {}
    monkeypatch.setattr(fin, "get_statement",
                        lambda q, **kw: seen.update(st=kw) or {})
    monkeypatch.setattr(fin, "get_facts",
                        lambda q, **kw: seen.update(fa=kw) or {})
    at.tool_get_statement("AAPL")
    at.tool_get_financial_facts("AAPL")
    assert seen["st"]["mapping_version"] == 3
    assert seen["fa"]["mapping_version"] == 3
    at.tool_get_statement("AAPL", mapping_version=1)
    at.tool_get_financial_facts("AAPL", mapping_version=1)
    assert seen["st"]["mapping_version"] == 1
    assert seen["fa"]["mapping_version"] == 1


# ------------------------------------------------- SEC resolution (D63/D65/D67)
def _facts_for(cik_int, *, annual=True, ni_year=2024, assets_year=2024):
    form = "10-K" if annual else "8-K"
    return {"cik": cik_int, "facts": {"us-gaap": {
        "NetIncomeLoss": {"units": {"USD": [
            {"start": f"{ni_year - 1}-01-01", "end": f"{ni_year - 1}-12-31",
             "val": 3116.0, "fy": ni_year - 1, "fp": "FY", "form": form,
             "filed": f"{ni_year}-02-01", "accn": f"a-ni-{ni_year}"}]}},
        "Assets": {"units": {"USD": [
            {"end": f"{assets_year - 1}-12-31", "val": 900.0,
             "fy": assets_year - 1, "fp": "FY", "form": form,
             "filed": f"{assets_year}-02-01", "accn": f"a-as-{assets_year}"}]}},
    }}}


def _subs(cik_int):
    return {"cik": cik_int, "filings": {"recent": {
        "accessionNumber": [f"{cik_int}-24-000001"], "form": ["10-K"],
        "filingDate": ["2024-02-01"], "reportDate": ["2023-12-31"],
        "primaryDocument": ["x.htm"]}}}


def test_get_statement_flags_stale_lines(tmp_db):
    # net income only up to FY2012 while assets reach FY2023 (MA / AT&T shape)
    facts = _facts_for(320193, ni_year=2013, assets_year=2024)
    _ensure(tmp_db, fetch_facts=lambda cik: facts)
    out = fin.get_statement("AAPL", db_path=tmp_db)
    assert out["latest_period"] == "2023-12-31"
    ni = out["line_status"]["net_income"]
    assert ni == {"latest_period": "2012-12-31", "stale": True,
                  "stale_since": "2012-12-31"}
    assert out["line_status"]["assets"] == {"latest_period": "2023-12-31",
                                            "stale": False}
    assert any("net_income" in w for w in out["warnings"])
    # empty lines are neither stale nor dropped
    assert out["line_status"]["revenue"] == {"latest_period": None,
                                             "stale": False}


def test_get_statement_current_lines_have_no_warnings(tmp_db):
    facts = _facts_for(320193, ni_year=2024, assets_year=2024)
    _ensure(tmp_db, fetch_facts=lambda cik: facts)
    out = fin.get_statement("AAPL", db_path=tmp_db)
    assert "warnings" not in out
    assert not any(v["stale"] for v in out["line_status"].values())


def test_statement_docstring_documents_staleness():
    from market_data_hub import agent_tools
    doc = agent_tools.tool_get_statement.__doc__
    assert "stale" in doc and "line_status" in doc


def test_ticker_variants_class_shares():
    assert sec.ticker_variants("BRK.B") == ["BRK-B", "BRK.B"]
    assert sec.ticker_variants("brk-b") == ["BRK-B", "BRK.B"]
    assert sec.ticker_variants("XOM") == ["XOM"]


_TICKER_PAYLOAD = {
    "0": {"cik_str": 1067983, "ticker": "BRK-B", "title": "BERKSHIRE HATHAWAY"},
    "1": {"cik_str": 2115436, "ticker": "XOM", "title": "Exxon Mobil Corp"},
}


def test_resolve_cik_via_sec_normalises_class_share(monkeypatch):
    monkeypatch.setattr(sec, "fetch_company_tickers", lambda: _TICKER_PAYLOAD)
    hit = fin._resolve_cik_via_sec("BRK.B")
    assert hit["cik"] == "0001067983"
    assert hit["ticker"] == "BRK.B"


def test_resolve_cik_via_sec_applies_pin_over_new_filer(monkeypatch):
    monkeypatch.setattr(sec, "fetch_company_tickers", lambda: _TICKER_PAYLOAD)
    hit = fin._resolve_cik_via_sec("XOM")
    assert hit["cik"] == "0000034088"          # historical filer with 10-Ks
    assert hit["sec_cik"] == "0002115436"      # what SEC says, exposed
    assert hit["override"] is True


def test_ensure_by_class_share_ticker_links_listing(tmp_db, monkeypatch):
    from market_data_hub.services import prices as svc
    monkeypatch.setattr(sec, "fetch_company_tickers", lambda: _TICKER_PAYLOAD)
    con = get_conn(tmp_db)
    try:
        cand = svc._register_listing(con, {
            "symbol": "BRK-B", "kind": "EQUITY", "name": "Berkshire",
            "exchange": None, "currency": None, "provider": "yahoo"})
    finally:
        con.close()
    res = fin.ensure_filings_and_facts(
        "BRK.B", db_path=tmp_db,
        fetch_submissions=lambda cik: _subs(1067983),
        fetch_facts=lambda cik: _facts_for(1067983))
    assert res["cik"] == "0001067983"
    con = get_conn(tmp_db, read_only=True)
    try:
        linked = con.execute(
            "SELECT issuer_id FROM instruments WHERE instrument_id = ?",
            [cand["instrument_id"]]).fetchone()[0]
    finally:
        con.close()
    assert linked == res["issuer_id"]
    # both spellings resolve locally
    assert fin.resolve_issuer("BRK.B", db_path=tmp_db)["cik"] == "0001067983"
    assert fin.resolve_issuer("BRK-B", db_path=tmp_db)["cik"] == "0001067983"


def test_xom_pin_ingests_historical_filer_and_statement_has_lines(
        tmp_db, monkeypatch):
    monkeypatch.setattr(sec, "fetch_company_tickers", lambda: _TICKER_PAYLOAD)
    seen = []

    def subs(cik):
        seen.append(cik)
        return _subs(34088)
    res = fin.ensure_filings_and_facts(
        "XOM", db_path=tmp_db, fetch_submissions=subs,
        fetch_facts=lambda cik: _facts_for(34088))
    assert res["cik"] == "0000034088" and seen == ["0000034088"]
    out = fin.get_statement("XOM", db_path=tmp_db)
    assert out["issuer"]["cik"] == "0000034088"
    assert out["lines"]["assets"]


def _holdco_then_predecessor(tmp_db):
    """Holdco ingested first (8-K facts only), then the predecessor filer
    with the 10-K history; both carry the ticker alias ZZZ."""
    _ensure(tmp_db, resolve=lambda q: {"cik": "0002115436", "name": "HoldCo",
                                       "ticker": "ZZZ"},
            fetch_submissions=lambda cik: _subs(2115436),
            fetch_facts=lambda cik: _facts_for(2115436, annual=False))
    fin.ensure_filings_and_facts(
        "0000034088", db_path=tmp_db,
        fetch_submissions=lambda cik: _subs(34088),
        fetch_facts=lambda cik: _facts_for(34088))
    con = get_conn(tmp_db)
    try:
        iss = con.execute("SELECT issuer_id FROM issuers WHERE cik = ?",
                          ["0000034088"]).fetchone()[0]
        con.execute("INSERT INTO identifier_aliases VALUES "
                    "('ticker','ZZZ','issuer',?,NULL,NULL,now())", [iss])
    finally:
        con.close()


def test_get_statement_without_any_annual_history_says_so(tmp_db):
    _ensure(tmp_db, resolve=lambda q: {"cik": "0002115436", "name": "HoldCo",
                                       "ticker": "ZZZ"},
            fetch_submissions=lambda cik: _subs(2115436),
            fetch_facts=lambda cik: _facts_for(2115436, annual=False))
    out = fin.get_statement("ZZZ", db_path=tmp_db)
    assert out["resolution"]["fallback"] is False
    assert out["lines"]["assets"] == {}


def test_get_statement_falls_back_only_via_configured_predecessor(tmp_db):
    _holdco_then_predecessor(tmp_db)
    # 0002115436 has a cik_predecessors entry -> explicit fallback
    out = fin.get_statement("0002115436", db_path=tmp_db)
    res = out["resolution"]
    assert (res["resolved_cik"], res["used_cik"], res["fallback"]) == (
        "0002115436", "0000034088", True)
    assert out["issuer"]["cik"] == "0000034088"
    assert out["lines"]["assets"]


def test_no_generic_shared_ticker_fallback(tmp_db):
    """A reused ticker must not reach an unrelated old issuer: two issuers
    share ZZZ, neither has a cik_predecessors entry -> no fallback, and the
    ticker resolves as before (no 'has annual facts' preference)."""
    _ensure(tmp_db, resolve=lambda q: {"cik": "0000999991", "name": "NewCo",
                                       "ticker": "ZZZ"},
            fetch_submissions=lambda cik: _subs(999991),
            fetch_facts=lambda cik: _facts_for(999991, annual=False))
    fin.ensure_filings_and_facts(
        "0000888881", db_path=tmp_db,
        fetch_submissions=lambda cik: _subs(888881),
        fetch_facts=lambda cik: _facts_for(888881))
    con = get_conn(tmp_db)
    try:
        iss = con.execute("SELECT issuer_id FROM issuers WHERE cik = ?",
                          ["0000888881"]).fetchone()[0]
        con.execute("INSERT INTO identifier_aliases VALUES "
                    "('ticker','ZZZ','issuer',?,NULL,NULL,now())", [iss])
    finally:
        con.close()
    out = fin.get_statement("0000999991", db_path=tmp_db)
    assert out["issuer"]["cik"] == "0000999991"
    assert out["resolution"]["fallback"] is False
    assert out["lines"]["assets"] == {}


def test_pinned_ticker_reads_follow_the_pin(tmp_db):
    # XOM not ingested under the pinned CIK yet -> nothing local to return
    _ensure(tmp_db, resolve=lambda q: {"cik": "0002115436", "name": "HoldCo",
                                       "ticker": "XOM"},
            fetch_submissions=lambda cik: _subs(2115436),
            fetch_facts=lambda cik: _facts_for(2115436, annual=False))
    assert fin.resolve_issuer("XOM", db_path=tmp_db) is None
    fin.ensure_filings_and_facts(
        "0000034088", db_path=tmp_db,
        fetch_submissions=lambda cik: _subs(34088),
        fetch_facts=lambda cik: _facts_for(34088))
    assert fin.resolve_issuer("XOM", db_path=tmp_db)["cik"] == "0000034088"


def test_ensure_returns_pin_provenance(tmp_db, monkeypatch):
    monkeypatch.setattr(sec, "fetch_company_tickers", lambda: _TICKER_PAYLOAD)
    res = fin.ensure_filings_and_facts(
        "XOM", db_path=tmp_db, fetch_submissions=lambda cik: _subs(34088),
        fetch_facts=lambda cik: _facts_for(34088))
    assert res["override"] is True
    assert res["sec_cik"] == "0002115436"
    again = fin.ensure_filings_and_facts(
        "XOM", db_path=tmp_db, fetch_submissions=lambda cik: _subs(34088),
        fetch_facts=lambda cik: _facts_for(34088))
    assert again["reused"] and again["override"] and \
        again["sec_cik"] == "0002115436"


def test_staleness_uses_all_statements_even_when_filtered(tmp_db):
    # income reaches FY2012 only; balance sheet reaches FY2023
    facts = _facts_for(320193, ni_year=2013, assets_year=2024)
    _ensure(tmp_db, fetch_facts=lambda cik: facts)
    out = fin.get_statement("AAPL", statement="income", db_path=tmp_db)
    assert out["latest_period"] == "2023-12-31"
    assert out["line_status"]["net_income"]["stale"] is True
    assert "assets" not in out["lines"]
    assert out["periods"] == ["2012-12-31"]       # requested lines only


def _ni_assets_facts(ni_end, assets_end):
    return {"cik": 320193, "facts": {"us-gaap": {
        "NetIncomeLoss": {"units": {"USD": [
            {"start": ni_end.replace(ni_end[:4], str(int(ni_end[:4]) - 1)),
             "end": ni_end, "val": 1.0, "fy": 2020, "fp": "FY",
             "form": "10-K", "filed": "2025-03-01", "accn": "a1"}]}},
        "Assets": {"units": {"USD": [
            {"end": assets_end, "val": 2.0, "fy": 2020, "fp": "FY",
             "form": "10-K", "filed": "2025-03-01", "accn": "a2"}]}},
    }}}


def test_stale_boundary_is_200_days(tmp_db):
    # income 2023-12-31; assets 2024-07-18 is exactly 200 days later: fresh
    _ensure(tmp_db, fetch_facts=lambda c: _ni_assets_facts(
        "2023-12-31", "2024-07-18"))
    out = fin.get_statement("AAPL", db_path=tmp_db)
    assert out["line_status"]["net_income"]["stale"] is False


def test_stale_boundary_201_days_is_stale(tmp_db):
    _ensure(tmp_db, fetch_facts=lambda c: _ni_assets_facts(
        "2023-12-31", "2024-07-19"))
    out = fin.get_statement("AAPL", db_path=tmp_db)
    assert out["line_status"]["net_income"]["stale"] is True


def test_fiscal_year_change_does_not_flag_prior_full_year(tmp_db):
    # FYE moved from Sept to Dec: last full-year duration ends 2024-09-28, the
    # transition-period balance sheet is 2024-12-31 (94 days later)
    _ensure(tmp_db, fetch_facts=lambda c: _ni_assets_facts(
        "2024-09-28", "2024-12-31"))
    out = fin.get_statement("AAPL", db_path=tmp_db)
    assert out["latest_period"] == "2024-12-31"
    assert out["line_status"]["net_income"]["stale"] is False
    assert "warnings" not in out


def test_pinned_read_exposes_override_and_ingestion_keeps_sec_name(
        tmp_db, monkeypatch):
    payload = {"1": {"cik_str": 2115436, "ticker": "XOM",
                     "title": "Exxon Mobil Corp"}}
    monkeypatch.setattr(sec, "fetch_company_tickers", lambda: payload)
    res = fin.ensure_filings_and_facts(
        "XOM", db_path=tmp_db, fetch_submissions=lambda cik: _subs(34088),
        fetch_facts=lambda cik: _facts_for(34088))
    assert res["override"] is True
    # SEC-provided name is kept on first ingestion of the pinned filer
    assert fin.resolve_issuer("XOM", db_path=tmp_db)["name"] == "Exxon Mobil Corp"
    out = fin.get_statement("XOM", db_path=tmp_db)
    r = out["resolution"]
    assert r["override"] is True and r["pinned_cik"] == "0000034088"
    assert r["used_cik"] == "0000034088" and r["fallback"] is False
    # an unpinned ticker/CIK read carries no resolution when nothing happened
    assert "resolution" not in fin.get_statement("0000034088", db_path=tmp_db)


def test_sec_cik_reported_when_sec_returns_the_pinned_cik(tmp_db, monkeypatch):
    payload = {"1": {"cik_str": 34088, "ticker": "XOM", "title": "Exxon"}}
    monkeypatch.setattr(sec, "fetch_company_tickers", lambda: payload)
    res = fin.ensure_filings_and_facts(
        "XOM", db_path=tmp_db, fetch_submissions=lambda cik: _subs(34088),
        fetch_facts=lambda cik: _facts_for(34088))
    assert res["sec_cik"] == "0000034088"


def test_register_issuer_fills_null_name_only(tmp_db):
    con = get_conn(tmp_db)
    try:
        iid = fin._register_issuer(con, "0000000042", None, None)
        fin._register_issuer(con, "0000000042", "Filled Inc", None)
        fin._register_issuer(con, "0000000042", "Other Name", None)
        name = con.execute("SELECT name FROM issuers WHERE issuer_id = ?",
                           [iid]).fetchone()[0]
    finally:
        con.close()
    assert name == "Filled Inc"


def _ingest_pinned_xom(tmp_db, monkeypatch):
    payload = {"1": {"cik_str": 2115436, "ticker": "XOM", "title": "Exxon"}}
    monkeypatch.setattr(sec, "fetch_company_tickers", lambda: payload)
    fin.ensure_filings_and_facts(
        "XOM", db_path=tmp_db, fetch_submissions=lambda cik: _subs(34088),
        fetch_facts=lambda cik: _facts_for(34088))


def test_get_facts_and_coverage_disclose_pin(tmp_db, monkeypatch):
    _ingest_pinned_xom(tmp_db, monkeypatch)
    facts = fin.get_facts("XOM", db_path=tmp_db)
    r = facts["resolution"]
    assert r["override"] is True and r["pinned_cik"] == "0000034088"
    assert r["used_cik"] == "0000034088"
    cov = fin.get_financials_coverage("XOM", db_path=tmp_db)
    assert cov and all(c["resolution"]["override"] is True for c in cov)
    assert cov[0]["resolution"]["pinned_cik"] == "0000034088"
    # reads by CIK or without a query carry no pin disclosure
    assert "resolution" not in fin.get_facts("0000034088", db_path=tmp_db)
    assert all("resolution" not in c for c in
               fin.get_financials_coverage("0000034088", db_path=tmp_db))
    assert all("resolution" not in c for c in
               fin.get_financials_coverage(db_path=tmp_db))


def test_agent_tools_pass_pin_disclosure_through(tmp_db, monkeypatch):
    from market_data_hub import agent_tools
    _ingest_pinned_xom(tmp_db, monkeypatch)
    facts = json.loads(agent_tools.tool_get_financial_facts("XOM"))
    assert facts["resolution"]["override"] is True
    cov = json.loads(agent_tools.tool_get_financials_coverage("XOM"))
    assert cov[0]["resolution"]["pinned_cik"] == "0000034088"
    stmt = json.loads(agent_tools.tool_get_statement("XOM"))
    assert stmt["resolution"]["override"] is True


# ------------------------------------------------- mapping v3: derived lines
def _amzn_like_facts(with_liabilities=False, nci=True):
    def inst(val, accn="acc-amzn", filed="2026-02-06"):
        return {"units": {"USD": [{"end": "2025-12-31", "val": val,
                                   "fy": 2025, "fp": "FY", "form": "10-K",
                                   "filed": filed, "accn": accn}]}}
    g = {"LiabilitiesAndStockholdersEquity": inst(1000.0),
         "StockholdersEquity": inst(400.0, "acc-eq", "2026-02-07")}
    if nci:
        g["StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"] = inst(410.0)
    if with_liabilities:
        g["Liabilities"] = inst(555.0)
    return {"cik": 320193, "facts": {"us-gaap": g}}


def test_mapping_v3_derived_liabilities_with_provenance(tmp_db):
    _ensure(tmp_db, fetch_facts=lambda cik: _amzn_like_facts())
    out = fin.get_statement("AAPL", db_path=tmp_db)
    assert out["mapping"]["version"] == 3
    assert out["lines"]["liabilities"] == {}
    d = out["lines"]["liabilities_derived"]["2025-12-31"]
    assert d["value"] == 590.0                      # 1000 - 410 (incl. NCI)
    assert d["derived"] is True
    assert d["formula"] == "LiabilitiesAndStockholdersEquity - equity"
    mi, su = d["inputs"]["minuend"], d["inputs"]["subtrahend"]
    assert mi["concept"] == "LiabilitiesAndStockholdersEquity"
    assert mi["accession"] == "acc-amzn" and mi["filed_date"] == "2026-02-06"
    assert su["concept"] == ("StockholdersEquityIncludingPortionAttributable"
                             "ToNoncontrollingInterest")
    assert su["value"] == 410.0
    assert out["line_status"]["liabilities_derived"]["stale"] is False
    # statement filter keeps the derived balance line, drops it for income
    assert "liabilities_derived" in fin.get_statement(
        "AAPL", statement="balance", db_path=tmp_db)["lines"]
    assert "liabilities_derived" not in fin.get_statement(
        "AAPL", statement="income", db_path=tmp_db)["lines"]


def test_mapping_v3_derived_falls_back_to_stockholders_equity(tmp_db):
    _ensure(tmp_db, fetch_facts=lambda cik: _amzn_like_facts(nci=False))
    d = fin.get_statement("AAPL", db_path=tmp_db)[
        "lines"]["liabilities_derived"]["2025-12-31"]
    assert d["value"] == 600.0
    assert d["inputs"]["subtrahend"]["concept"] == "StockholdersEquity"
    assert d["inputs"]["subtrahend"]["accession"] == "acc-eq"


def test_mapping_v3_no_derived_when_liabilities_filed(tmp_db):
    _ensure(tmp_db, fetch_facts=lambda cik: _amzn_like_facts(
        with_liabilities=True))
    out = fin.get_statement("AAPL", db_path=tmp_db)
    assert out["lines"]["liabilities"]["2025-12-31"]["value"] == 555.0
    assert "derived" not in out["lines"]["liabilities"]["2025-12-31"]
    assert out["lines"]["liabilities_derived"] == {}


def test_mapping_v1_v2_have_no_derived_lines(tmp_db):
    from market_data_hub.config_loader import get_xbrl_mapping
    v2, v3 = get_xbrl_mapping(2), get_xbrl_mapping(3)
    assert "derived_lines" not in get_xbrl_mapping(1)
    assert "derived_lines" not in v2
    assert v3["statement_lines"] == v2["statement_lines"]
    _ensure(tmp_db, fetch_facts=lambda cik: _amzn_like_facts())
    for ver in (1, 2):
        out = fin.get_statement("AAPL", mapping_version=ver, db_path=tmp_db)
        assert "liabilities_derived" not in out["lines"]
        assert out["mapping"]["version"] == ver
    facts = fin.get_facts("AAPL", line="liabilities_derived", db_path=tmp_db)
    assert facts["n_returned"] >= 2 and facts["mapping"]["version"] == 3


def _liab_facts(**concepts):
    """concept -> list of (unit, end, start, val, filed, accn) entries."""
    g = {}
    for name, entries in concepts.items():
        units = {}
        for unit, end, start, val, filed, accn in entries:
            row = {"end": end, "val": val, "fy": 2025, "fp": "FY",
                   "form": "10-K", "filed": filed, "accn": accn}
            if start:
                row["start"] = start
            units.setdefault(unit, []).append(row)
        g[name] = {"units": units}
    return {"cik": 320193, "facts": {"us-gaap": g}}


def _derived(tmp_db, **concepts):
    _ensure(tmp_db, fetch_facts=lambda cik: _liab_facts(**concepts))
    return fin.get_statement("AAPL", db_path=tmp_db)["lines"][
        "liabilities_derived"]


_E = "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"


def test_derived_requires_same_end_date(tmp_db):
    out = _derived(
        tmp_db,
        LiabilitiesAndStockholdersEquity=[
            ("USD", "2025-12-31", None, 1000.0, "2026-02-06", "a1")],
        **{_E: [("USD", "2024-12-31", None, 400.0, "2026-02-06", "a1")]})
    assert out == {}


def test_derived_uses_latest_filed_restatement(tmp_db):
    out = _derived(
        tmp_db,
        LiabilitiesAndStockholdersEquity=[
            ("USD", "2025-12-31", None, 1000.0, "2026-02-06", "a1"),
            ("USD", "2025-12-31", None, 1100.0, "2026-05-01", "a2")],
        **{_E: [("USD", "2025-12-31", None, 400.0, "2026-02-06", "a1")]})
    d = out["2025-12-31"]
    assert d["value"] == 700.0
    assert d["inputs"]["minuend"]["accession"] == "a2"


def test_derived_ignores_non_usd_inputs(tmp_db):
    out = _derived(
        tmp_db,
        LiabilitiesAndStockholdersEquity=[
            ("USD", "2025-12-31", None, 1000.0, "2026-02-06", "a1")],
        **{_E: [("EUR", "2025-12-31", None, 400.0, "2026-02-06", "a1")]})
    assert out == {}


def test_derived_ignores_duration_facts(tmp_db):
    # a later-filed annual-duration fact with the same end date must not win
    out = _derived(
        tmp_db,
        LiabilitiesAndStockholdersEquity=[
            ("USD", "2025-12-31", None, 1000.0, "2026-02-06", "a1"),
            ("USD", "2025-12-31", "2025-01-01", 5000.0, "2026-05-01", "a2")],
        **{_E: [
            ("USD", "2025-12-31", None, 400.0, "2026-02-06", "a1"),
            ("USD", "2025-12-31", "2025-01-01", 9.0, "2026-05-01", "a2")]})
    d = out["2025-12-31"]
    assert d["value"] == 600.0
    assert d["inputs"]["minuend"]["accession"] == "a1"
    assert d["inputs"]["subtrahend"]["accession"] == "a1"


def test_derived_duration_only_input_gives_nothing(tmp_db):
    out = _derived(
        tmp_db,
        LiabilitiesAndStockholdersEquity=[
            ("USD", "2025-12-31", "2025-01-01", 5000.0, "2026-05-01", "a2")],
        **{_E: [("USD", "2025-12-31", None, 400.0, "2026-02-06", "a1")]})
    assert out == {}
