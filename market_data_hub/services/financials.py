# -*- coding: utf-8 -*-
"""
services.financials — SEC issuer resolution, filings & facts (plan v3.1 Fase 3).

  - resolve_issuer()            read-only, local: CIK or ticker -> issuer.
  - ensure_filings_and_facts()  write: idempotent job under the writer lock.
                                Resolves the CIK (via SEC company_tickers when
                                not known locally), registers the issuer with
                                a 'cik' alias, ingests filing metadata and the
                                full XBRL company facts (append-only), then
                                refreshes sec_coverage.
  - get_financials_coverage()   read-only, bounded.
  - get_facts()                 read-only, bounded: raw facts, filterable by
                                mapped statement line (xbrl_mapping_vN.yaml).

The mapping is versioned; the tool answer always carries unit, period,
accession and filed date so every number is verifiable (plan §4.2 acceptance).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import duckdb

from market_data_hub.config_loader import get_xbrl_mapping
from market_data_hub.db.connection import get_conn
from market_data_hub.lock import db_write_lock

_FILING_FORMS = ["10-K", "10-Q", "20-F", "8-K"]   # metadata kept for these


class UnknownIssuerError(ValueError):
    """Raised when neither the local DB nor SEC's ticker map knows the query."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stable_id(prefix: str, *parts: str) -> str:
    h = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{h}"


def _fact_id(row: Dict[str, Any]) -> str:
    key = "|".join(str(row.get(k)) for k in (
        "cik", "taxonomy", "concept", "unit", "start_date", "end_date",
        "value", "accession", "frame"))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


# -------------------------------------------------------------------- resolve
def resolve_issuer(query: str, db_path: Optional[str] = None
                   ) -> Optional[Dict[str, Any]]:
    """Local-only lookup: numeric CIK, 'iss_*' id, or a ticker whose listing
    is linked to an issuer / has a cik alias. Read-only, no network — the
    network path to SEC's ticker map lives in ensure_filings_and_facts()."""
    q = query.strip()
    con = get_conn(db_path, read_only=True)
    try:
        params: List[Any]
        if q.isdigit():
            from market_data_hub.sources.sec import normalize_cik
            where, params = "i.cik = ?", [normalize_cik(q)]
        elif q.startswith("iss_"):
            where, params = "i.issuer_id = ?", [q]
        else:
            # ticker -> listing -> instrument -> issuer, or a direct
            # ticker alias recorded on the issuer at ingestion time
            where = ("(i.issuer_id IN (SELECT ins.issuer_id FROM listings l "
                     "JOIN instruments ins USING (instrument_id) "
                     "WHERE upper(l.symbol) = upper(?)) "
                     "OR i.issuer_id IN (SELECT target_id FROM identifier_aliases "
                     "WHERE namespace = 'ticker' AND target_type = 'issuer' "
                     "AND upper(value) = upper(?)))")
            params = [q, q]
        try:
            row = con.execute(f"""
                SELECT i.issuer_id, i.cik, i.name, i.sic, i.fiscal_year_end
                FROM issuers i WHERE {where}
            """, params).fetchone()
        except duckdb.CatalogException:
            row = None                      # pre-v5 DB opened read-only
    finally:
        con.close()
    if row is None:
        return None
    return dict(zip(["issuer_id", "cik", "name", "sic", "fiscal_year_end"], row))


def _resolve_cik_via_sec(query: str) -> Dict[str, Any]:
    """Network resolution against SEC's official ticker map. Only called from
    the ensure_* write path."""
    from market_data_hub.sources import sec
    q = query.strip()
    if q.isdigit():
        return {"cik": sec.normalize_cik(q), "name": None, "ticker": None}
    tickers = sec.company_tickers_to_map(sec.fetch_company_tickers())
    hit = tickers.get(q.upper())
    if hit is None:
        raise UnknownIssuerError(
            f"{query!r} is neither a CIK nor a ticker known to SEC EDGAR")
    return {"cik": hit["cik"], "name": hit["name"], "ticker": q.upper()}


def _register_issuer(con, cik: str, name: Optional[str],
                     ticker: Optional[str]) -> str:
    """issuers row + 'cik' alias; links instruments.issuer_id for the ticker's
    listings when they exist. Deterministic id -> idempotent."""
    now = _now()
    issuer_id = _stable_id("iss", "cik", cik)
    con.execute("""
        INSERT INTO issuers (issuer_id, cik, name, sic, fiscal_year_end,
                             created_at, updated_at)
        SELECT ?, ?, ?, NULL, NULL, ?, ?
        WHERE NOT EXISTS (SELECT 1 FROM issuers WHERE issuer_id = ?)
    """, [issuer_id, cik, name, now, now, issuer_id])
    con.execute("""
        INSERT INTO identifier_aliases (namespace, value, target_type,
                                        target_id, valid_from, valid_to, updated_at)
        SELECT 'cik', ?, 'issuer', ?, NULL, NULL, ?
        WHERE NOT EXISTS (SELECT 1 FROM identifier_aliases
                          WHERE namespace = 'cik' AND value = ?
                            AND target_type = 'issuer' AND target_id = ?)
    """, [cik, issuer_id, now, cik, issuer_id])
    if ticker:
        con.execute("""
            INSERT INTO identifier_aliases (namespace, value, target_type,
                                            target_id, valid_from, valid_to,
                                            updated_at)
            SELECT 'ticker', ?, 'issuer', ?, NULL, NULL, ?
            WHERE NOT EXISTS (SELECT 1 FROM identifier_aliases
                              WHERE namespace = 'ticker' AND value = ?
                                AND target_type = 'issuer' AND target_id = ?)
        """, [ticker, issuer_id, now, ticker, issuer_id])
        con.execute("""
            UPDATE instruments SET issuer_id = ?, updated_at = ?
            WHERE issuer_id IS NULL AND instrument_id IN (
                SELECT instrument_id FROM listings WHERE upper(symbol) = upper(?))
        """, [issuer_id, now, ticker])
    return issuer_id


# --------------------------------------------------------------------- ensure
def ensure_filings_and_facts(query: str, *, requester: str = "python",
                             db_path: Optional[str] = None,
                             fetch_submissions=None, fetch_facts=None,
                             resolve=None, force: bool = False
                             ) -> Dict[str, Any]:
    """Idempotent SEC ingestion job for one issuer (metadata + company facts).

    The request hash includes the as-of DATE, so within the same day the
    completed job is reused (no re-download), while tomorrow's call refreshes.
    Facts are append-only: re-ingestion inserts only rows whose fact_id is new.
    """
    from market_data_hub.sources import sec

    fetch_submissions = fetch_submissions or sec.fetch_submissions
    fetch_facts = fetch_facts or sec.fetch_company_facts
    resolve = resolve or _resolve_cik_via_sec

    local = resolve_issuer(query, db_path=db_path)
    if local and local.get("cik"):
        ident = {"cik": local["cik"], "name": local.get("name"),
                 "ticker": None if query.strip().isdigit() else query.strip().upper()}
    else:
        ident = resolve(query)              # may raise UnknownIssuerError
    cik = ident["cik"]

    now = _now()
    req = {"kind": "sec_facts", "cik": cik, "as_of": now.date().isoformat()}
    request_hash = hashlib.sha256(
        json.dumps(req, sort_keys=True).encode()).hexdigest()

    with db_write_lock(db_path):
        con = get_conn(db_path)
        try:
            issuer_id = _register_issuer(con, cik, ident.get("name"),
                                         ident.get("ticker"))

            row = con.execute(
                "SELECT job_id, status, run_id FROM ingestion_jobs "
                "WHERE request_hash = ?", [request_hash]).fetchone()
            if row and row[1] == "completed" and not force:
                return {"job_id": row[0], "run_id": row[2],
                        "status": "completed", "issuer_id": issuer_id,
                        "cik": cik, "reused": True,
                        "filings": 0, "new_facts": 0}

            if row:
                job_id = row[0]
                con.execute("UPDATE ingestion_jobs SET status = 'running', "
                            "updated_at = ? WHERE job_id = ?", [now, job_id])
            else:
                job_id = f"job_{uuid.uuid4().hex[:12]}"
                con.execute("""
                    INSERT INTO ingestion_jobs (job_id, request_hash, kind,
                        request_json, status, run_id, requester, created_at,
                        updated_at)
                    VALUES (?, ?, 'sec_facts', ?, 'running', NULL, ?, ?, ?)
                """, [job_id, request_hash, json.dumps(req), requester, now, now])

            run_id = f"run_{uuid.uuid4().hex[:12]}"
            con.execute("""
                INSERT INTO ingestion_runs (run_id, kind, input_json, provider,
                    provider_reason, status, attempts, started_at)
                VALUES (?, 'sec_facts', ?, 'sec_edgar',
                        'sole provider for SEC filings/facts', 'running', 1, ?)
            """, [run_id, json.dumps(req), now])

            try:
                filings = sec.submissions_to_filings(
                    fetch_submissions(cik), forms=_FILING_FORMS)
                n_filings = 0
                if not filings.empty:
                    filings = filings.drop(columns=[c for c in filings.columns
                                                    if c not in (
                        "cik", "accession", "form", "filed_date", "report_date",
                        "primary_doc", "primary_doc_url")])
                    filings["issuer_id"] = issuer_id
                    filings["run_id"] = run_id
                    filings["updated_at"] = now
                    con.register("_sf", filings)
                    con.execute("""
                        INSERT OR REPLACE INTO sec_filings
                        SELECT cik, accession, form, filed_date, report_date,
                               primary_doc, primary_doc_url, issuer_id, run_id,
                               updated_at
                        FROM _sf
                    """)
                    con.unregister("_sf")
                    n_filings = int(len(filings))

                facts = sec.company_facts_to_df(fetch_facts(cik))
                new_facts = 0
                if not facts.empty:
                    facts = facts.copy()
                    facts["fact_id"] = [
                        _fact_id(r) for r in facts.to_dict("records")]
                    facts = facts.drop_duplicates(subset=["fact_id"])
                    facts["run_id"] = run_id
                    facts["created_at"] = now
                    con.register("_scf", facts)
                    # append-only: anti-join on fact_id, never UPDATE/REPLACE
                    new_facts = con.execute("""
                        SELECT COUNT(*) FROM _scf s
                        WHERE NOT EXISTS (SELECT 1 FROM sec_company_facts f
                                          WHERE f.fact_id = s.fact_id)
                    """).fetchone()[0]
                    con.execute("""
                        INSERT INTO sec_company_facts
                        SELECT fact_id, cik, taxonomy, concept, unit,
                               start_date, end_date, value, fy, fp, form,
                               filed_date, accession, frame, run_id, created_at
                        FROM _scf s
                        WHERE NOT EXISTS (SELECT 1 FROM sec_company_facts f
                                          WHERE f.fact_id = s.fact_id)
                    """)
                    con.unregister("_scf")

                _refresh_coverage(con, cik, issuer_id, ident.get("name"),
                                  run_id, now)

                fin = _now()
                con.execute("UPDATE ingestion_runs SET status = 'completed', "
                            "rows_written = ?, finished_at = ? WHERE run_id = ?",
                            [n_filings + int(new_facts), fin, run_id])
                con.execute("UPDATE ingestion_jobs SET status = 'completed', "
                            "run_id = ?, error_msg = NULL, updated_at = ? "
                            "WHERE job_id = ?", [run_id, fin, job_id])
                return {"job_id": job_id, "run_id": run_id,
                        "status": "completed", "issuer_id": issuer_id,
                        "cik": cik, "reused": False,
                        "filings": n_filings, "new_facts": int(new_facts)}
            except Exception as exc:
                fin = _now()
                con.execute("UPDATE ingestion_runs SET status = 'error', "
                            "error_msg = ?, finished_at = ? WHERE run_id = ?",
                            [str(exc), fin, run_id])
                con.execute("UPDATE ingestion_jobs SET status = 'error', "
                            "error_msg = ?, run_id = ?, updated_at = ? "
                            "WHERE job_id = ?", [str(exc), run_id, fin, job_id])
                raise
        finally:
            con.close()


def _refresh_coverage(con, cik: str, issuer_id: str, name: Optional[str],
                      run_id: str, now: datetime) -> None:
    con.execute("""
        INSERT OR REPLACE INTO sec_coverage
        SELECT ?, ?, ?,
               (SELECT COUNT(*) FROM sec_filings WHERE cik = ?),
               (SELECT COUNT(*) FROM sec_company_facts WHERE cik = ?),
               (SELECT string_agg(DISTINCT form, ',' ORDER BY form)
                  FROM sec_filings WHERE cik = ?),
               (SELECT MAX(filed_date) FROM sec_filings WHERE cik = ?),
               datediff('day',
                        (SELECT MAX(filed_date) FROM sec_filings WHERE cik = ?),
                        current_date),
               ?, ?
    """, [cik, issuer_id, name, cik, cik, cik, cik, cik, run_id, now])


# -------------------------------------------------------------------- readers
_MAX_FACT_ROWS = 100        # plan §5.3: facts/statement <= 100 rows
_MAX_PERIODS = 12           # plan §5.3: statement <= 12 periods


def get_financials_coverage(query: Optional[str] = None,
                            db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """sec_coverage rows (all issuers, or one resolved via resolve_issuer)."""
    con = get_conn(db_path, read_only=True)
    try:
        try:
            if query:
                iss = resolve_issuer(query, db_path=db_path)
                if iss is None:
                    return []
                df = con.execute("SELECT * FROM sec_coverage WHERE issuer_id = ?",
                                 [iss["issuer_id"]]).fetch_df()
            else:
                df = con.execute(
                    "SELECT * FROM sec_coverage ORDER BY last_filed DESC"
                ).fetch_df()
        except duckdb.CatalogException:
            return []
    finally:
        con.close()
    return json.loads(df.to_json(orient="records", date_format="iso"))


def get_statement(query: str, statement: Optional[str] = None,
                  periods: int = 8, mapping_version: int = 1,
                  db_path: Optional[str] = None) -> Dict[str, Any]:
    """Standardized ANNUAL statement lines for one issuer, derived on read
    from the append-only facts through the versioned mapping (plan Fase 4).

    Semantics per line: concepts are tried in mapping order and a later
    concept only fills periods the earlier ones lack; for each period
    (end_date) the row with the LATEST filed_date wins (restatements
    supersede on read, history stays intact in sec_company_facts). Duration
    lines (income/cash flow) require a ~annual window so quarterly and
    comparative stubs are excluded; balance lines are instants.

    Output is bounded (max 12 periods) and carries provenance per value:
    concept, accession, filed date — plus the mapping version used.
    """
    iss = resolve_issuer(query, db_path=db_path)
    if iss is None:
        return {"error": f"issuer {query!r} not in the hub; run "
                         f"ensure_filings_and_facts first", "lines": {}}
    mapping = get_xbrl_mapping(mapping_version)
    lines_cfg = mapping.get("statement_lines") or {}
    if statement:
        lines_cfg = {k: v for k, v in lines_cfg.items()
                     if v.get("statement") == statement}
        if not lines_cfg:
            return {"error": f"no mapped lines for statement {statement!r}",
                    "lines": {}}
    periods = max(1, min(int(periods), _MAX_PERIODS))

    con = get_conn(db_path, read_only=True)
    try:
        lines: Dict[str, Dict[str, Any]] = {}
        all_ends: set = set()
        for line_key, cfg in lines_cfg.items():
            per_period: Dict[str, Dict[str, Any]] = {}
            for concept in cfg["concepts"]:
                rows = con.execute("""
                    SELECT end_date, value, concept, accession, filed_date,
                           start_date
                    FROM sec_company_facts
                    WHERE cik = ? AND concept = ? AND unit = 'USD'
                      AND form IN ('10-K', '20-F', '10-K/A', '20-F/A')
                      AND (start_date IS NULL
                           OR datediff('day', start_date, end_date) BETWEEN 330 AND 400)
                    QUALIFY row_number() OVER (
                        PARTITION BY end_date ORDER BY filed_date DESC) = 1
                    ORDER BY end_date DESC
                """, [iss["cik"], concept]).fetchall()
                for end, value, cpt, accn, filed, _start in rows:
                    key = end.isoformat()
                    if key not in per_period:   # earlier concept wins
                        per_period[key] = {
                            "value": value, "concept": cpt,
                            "accession": accn,
                            "filed_date": filed.isoformat() if filed else None}
            keep = sorted(per_period, reverse=True)[:periods]
            lines[line_key] = {k: per_period[k] for k in keep}
            all_ends.update(keep)
    finally:
        con.close()
    return {"issuer": iss,
            "mapping": {"version": mapping.get("version"),
                        "taxonomy": mapping.get("taxonomy")},
            "frequency": "annual",
            "periods": sorted(all_ends, reverse=True)[:periods],
            "lines": lines}


def get_facts(query: str, line: Optional[str] = None,
              concepts: Optional[List[str]] = None,
              forms: Optional[List[str]] = None,
              limit: int = _MAX_FACT_ROWS, mapping_version: int = 1,
              db_path: Optional[str] = None) -> Dict[str, Any]:
    """Bounded raw facts for one issuer, newest filings first.

    line: a mapped statement line key ('revenue', 'net_income', ...) resolved
    through the versioned mapping; alternatively pass explicit concepts.
    Every row carries unit, period, form, accession and filed_date.
    """
    iss = resolve_issuer(query, db_path=db_path)
    if iss is None:
        return {"error": f"issuer {query!r} not in the hub; run "
                         f"ensure_filings_and_facts first", "facts": []}
    mapping_used = None
    if line:
        mapping = get_xbrl_mapping(mapping_version)
        entry = (mapping.get("statement_lines") or {}).get(line)
        if entry is None:
            return {"error": f"unknown statement line {line!r} in mapping "
                             f"v{mapping_version}", "facts": []}
        concepts = entry["concepts"]
        mapping_used = {"version": mapping.get("version"), "line": line,
                        "concepts": concepts}
    limit = max(1, min(int(limit), _MAX_FACT_ROWS))

    where = ["cik = ?"]
    params: List[Any] = [iss["cik"]]
    if concepts:
        where.append(f"concept IN ({','.join('?' * len(concepts))})")
        params.extend(concepts)
    if forms:
        where.append(f"form IN ({','.join('?' * len(forms))})")
        params.extend(forms)

    con = get_conn(db_path, read_only=True)
    try:
        total = con.execute(
            f"SELECT COUNT(*) FROM sec_company_facts WHERE {' AND '.join(where)}",
            params).fetchone()[0]
        df = con.execute(f"""
            SELECT taxonomy, concept, unit, start_date, end_date, value,
                   fy, fp, form, filed_date, accession
            FROM sec_company_facts WHERE {' AND '.join(where)}
            ORDER BY end_date DESC, filed_date DESC
            LIMIT {limit}
        """, params).fetch_df()
    finally:
        con.close()
    return {"issuer": iss, "mapping": mapping_used,
            "n_total": int(total), "n_returned": int(len(df)),
            "truncated": bool(total > len(df)),
            "facts": json.loads(df.to_json(orient="records", date_format="iso"))}
