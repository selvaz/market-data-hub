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

Resolution rule (D63/D67): SEC's company_tickers map is authoritative, except
where config/sec_overrides.yaml pins a ticker to the filer that holds the 10-K
history (XOM: SEC points at a new holding-company filer with no annual
statements); reads of a pinned ticker follow the pin. get_statement falls
back ONLY through an explicit cik_predecessors entry when the resolved filer
has no annual facts, and reports it under "resolution". Class-share tickers are matched as BRK.B and BRK-B alike.

The mapping is versioned; the tool answer always carries unit, period,
accession and filed date so every number is verifiable (plan §4.2 acceptance).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

import duckdb

from market_data_hub.config_loader import get_xbrl_mapping
from market_data_hub.db.connection import get_conn
from market_data_hub.lock import db_write_lock

_FILING_FORMS = ["10-K", "10-Q", "20-F", "8-K"]   # metadata kept for these
_ANNUAL_FORMS = ("10-K", "20-F", "10-K/A", "20-F/A")
_ANNUAL_FORMS_SQL = "(" + ",".join(f"'{f}'" for f in _ANNUAL_FORMS) + ")"
_STALE_DAYS = 200      # a line whose latest period trails the issuer's latest
                       # fiscal year end by more than this is flagged stale


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
    network path to SEC's ticker map lives in ensure_filings_and_facts().
    A ticker pinned in sec_overrides.yaml resolves ONLY to the pinned CIK's
    issuer (None until it is ingested), so reads agree with ingestion."""
    q = query.strip()
    con = get_conn(db_path, read_only=True)
    try:
        params: List[Any]
        if q.isdigit():
            from market_data_hub.sources.sec import normalize_cik
            where, params = "i.cik = ?", [normalize_cik(q)]
        elif q.startswith("iss_"):
            where, params = "i.issuer_id = ?", [q]
        elif _override_cik(q):
            # explicit pin (sec_overrides.yaml): reads agree with ingestion
            where, params = "i.cik = ?", [_override_cik(q)]
        else:
            # ticker -> listing -> instrument -> issuer, or a direct
            # ticker alias recorded on the issuer at ingestion time
            from market_data_hub.sources.sec import ticker_variants
            variants = ticker_variants(q)
            ph = ",".join("?" * len(variants))
            where = ("(i.issuer_id IN (SELECT ins.issuer_id FROM listings l "
                     "JOIN instruments ins USING (instrument_id) "
                     f"WHERE upper(l.symbol) IN ({ph})) "
                     "OR i.issuer_id IN (SELECT target_id FROM identifier_aliases "
                     "WHERE namespace = 'ticker' AND target_type = 'issuer' "
                     f"AND upper(value) IN ({ph})))")
            params = variants + variants
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
    pinned = _override_cik(q)
    tickers = sec.company_tickers_to_map(sec.fetch_company_tickers())
    hit = next((tickers[v] for v in sec.ticker_variants(q) if v in tickers),
               None)
    if hit is None:
        raise UnknownIssuerError(
            f"{query!r} is neither a CIK nor a ticker known to SEC EDGAR")
    out = {"cik": hit["cik"], "name": hit["name"], "ticker": q.upper()}
    if pinned and pinned != hit["cik"]:
        # SEC points at a different (new) filer; the pin holds the 10-K history
        out.update({"cik": pinned, "sec_cik": hit["cik"], "override": True})
    return out


def _override_cik(ticker: str) -> Optional[str]:
    """Pinned CIK for a ticker (config/sec_overrides.yaml), any spelling."""
    from market_data_hub.config_loader import get_sec_overrides
    from market_data_hub.sources.sec import ticker_variants
    pins = get_sec_overrides()["ticker_cik"]
    return next((pins[v] for v in ticker_variants(ticker) if v in pins), None)


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
    if name:        # fill a missing name only; never overwrite an existing one
        con.execute("UPDATE issuers SET name = ?, updated_at = ? "
                    "WHERE issuer_id = ? AND name IS NULL",
                    [name, now, issuer_id])
    con.execute("""
        INSERT INTO identifier_aliases (namespace, value, target_type,
                                        target_id, valid_from, valid_to, updated_at)
        SELECT 'cik', ?, 'issuer', ?, NULL, NULL, ?
        WHERE NOT EXISTS (SELECT 1 FROM identifier_aliases
                          WHERE namespace = 'cik' AND value = ?
                            AND target_type = 'issuer' AND target_id = ?)
    """, [cik, issuer_id, now, cik, issuer_id])
    if ticker:
        from market_data_hub.sources.sec import ticker_variants
        variants = ticker_variants(ticker)       # BRK.B and BRK-B alike
        for v in variants:
            con.execute("""
                INSERT INTO identifier_aliases (namespace, value, target_type,
                                                target_id, valid_from, valid_to,
                                                updated_at)
                SELECT 'ticker', ?, 'issuer', ?, NULL, NULL, ?
                WHERE NOT EXISTS (SELECT 1 FROM identifier_aliases
                                  WHERE namespace = 'ticker' AND value = ?
                                    AND target_type = 'issuer' AND target_id = ?)
            """, [v, issuer_id, now, v, issuer_id])
        ph = ",".join("?" * len(variants))
        con.execute(f"""
            UPDATE instruments SET issuer_id = ?, updated_at = ?
            WHERE issuer_id IS NULL AND instrument_id IN (
                SELECT instrument_id FROM listings
                WHERE upper(symbol) IN ({ph}))
        """, [issuer_id, now] + variants)
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

    pinned = None if query.strip().isdigit() else _override_cik(query)
    local = resolve_issuer(query, db_path=db_path)
    pin_info: Dict[str, Any] = {}
    if pinned:
        # explicit pin (config/sec_overrides.yaml): the filer with the 10-K
        # history beats both a stale local alias and SEC's new-filer mapping
        ident = {"cik": pinned, "name": None, "ticker": query.strip().upper()}
        if local and local.get("cik") == pinned:
            ident["name"] = local.get("name")
        try:    # what SEC itself says (network; best effort)
            sec_hit = resolve(query)
            sec_cik = sec_hit.get("sec_cik") or sec_hit.get("cik")
            ident["name"] = ident["name"] or sec_hit.get("name")
        except Exception:
            sec_cik = None
        pin_info = {"override": True, "sec_cik": sec_cik}
    elif local and local.get("cik"):
        ident = {"cik": local["cik"], "name": local.get("name"),
                 "ticker": None if query.strip().isdigit() else query.strip().upper()}
    else:
        ident = resolve(query)              # may raise UnknownIssuerError
    cik = ident["cik"]

    now = _now()
    req = {"kind": "sec_facts", "cik": cik, "as_of": now.date().isoformat()}
    request_hash = hashlib.sha256(
        json.dumps(req, sort_keys=True).encode()).hexdigest()

    # ---- phase 1 (short lock): issuer identity + job/run as 'running'
    # (audit CA-06: the EDGAR fetches never run while holding the writer lock)
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
                        "filings": 0, "new_facts": 0, **pin_info}

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
        finally:
            con.close()

    # ---- phase 2 (NO lock): EDGAR fetches + normalization
    try:
        filings = sec.submissions_to_filings(
            fetch_submissions(cik), forms=_FILING_FORMS)
        if not filings.empty:
            filings = filings.drop(columns=[c for c in filings.columns
                                            if c not in (
                "cik", "accession", "form", "filed_date", "report_date",
                "primary_doc", "primary_doc_url")])
            filings["issuer_id"] = issuer_id

        facts = sec.company_facts_to_df(fetch_facts(cik))
        if not facts.empty:
            facts = facts.copy()
            facts["fact_id"] = [_fact_id(r) for r in facts.to_dict("records")]
            facts = facts.drop_duplicates(subset=["fact_id"])
            facts["run_id"] = run_id
            facts["created_at"] = now
    except Exception as exc:
        _mark_sec_job_error(db_path, job_id, run_id, exc)
        raise

    # ---- phase 3 (short lock): all writes in ONE transaction
    with db_write_lock(db_path):
        con = get_conn(db_path)
        try:
            con.execute("BEGIN TRANSACTION")
            try:
                n_filings = 0
                if not filings.empty:
                    con.register("_sf", filings)
                    # CA-08: run provenance survives re-ingestion — update
                    # existing rows (first_seen untouched, last_seen advances),
                    # insert only genuinely new accessions.
                    con.execute("""
                        UPDATE sec_filings SET
                            form = s.form, filed_date = s.filed_date,
                            report_date = s.report_date,
                            primary_doc = s.primary_doc,
                            primary_doc_url = s.primary_doc_url,
                            issuer_id = s.issuer_id,
                            last_seen_run_id = ?, updated_at = ?
                        FROM _sf s
                        WHERE sec_filings.cik = s.cik
                          AND sec_filings.accession = s.accession
                    """, [run_id, now])
                    con.execute("""
                        INSERT INTO sec_filings
                            (cik, accession, form, filed_date, report_date,
                             primary_doc, primary_doc_url, issuer_id,
                             first_seen_run_id, last_seen_run_id, updated_at)
                        SELECT s.cik, s.accession, s.form, s.filed_date,
                               s.report_date, s.primary_doc, s.primary_doc_url,
                               s.issuer_id, ?, ?, ?
                        FROM _sf s
                        WHERE NOT EXISTS (SELECT 1 FROM sec_filings f
                                          WHERE f.cik = s.cik
                                            AND f.accession = s.accession)
                    """, [run_id, run_id, now])
                    con.unregister("_sf")
                    n_filings = int(len(filings))

                new_facts = 0
                if not facts.empty:
                    con.register("_scf", facts)
                    # append-only: anti-join on fact_id, never UPDATE/REPLACE
                    new_facts = con.execute("""
                        SELECT COUNT(*) FROM _scf s
                        WHERE NOT EXISTS (SELECT 1 FROM sec_company_facts f
                                          WHERE f.fact_id = s.fact_id)
                    """).fetchone()[0]
                    con.execute("""
                        INSERT INTO sec_company_facts
                            (fact_id, cik, taxonomy, concept, unit,
                             start_date, end_date, value, fy, fp, form,
                             filed_date, accession, frame, run_id, created_at)
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
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
            return {"job_id": job_id, "run_id": run_id,
                    "status": "completed", "issuer_id": issuer_id,
                    "cik": cik, "reused": False,
                    "filings": n_filings, "new_facts": int(new_facts),
                    **pin_info}
        except Exception as exc:
            _mark_sec_job_error(db_path, job_id, run_id, exc, con=con)
            raise
        finally:
            con.close()


def _mark_sec_job_error(db_path: Optional[str], job_id: str, run_id: str,
                        exc: Exception, con=None) -> None:
    """Record the failure on job + run (autocommit, outside any transaction)."""
    fin = _now()

    def _write(c) -> None:
        c.execute("UPDATE ingestion_runs SET status = 'error', "
                  "error_msg = ?, finished_at = ? WHERE run_id = ?",
                  [str(exc), fin, run_id])
        c.execute("UPDATE ingestion_jobs SET status = 'error', "
                  "error_msg = ?, run_id = ?, updated_at = ? "
                  "WHERE job_id = ?", [str(exc), run_id, fin, job_id])

    if con is not None:
        _write(con)
        return
    with db_write_lock(db_path):
        own = get_conn(db_path)
        try:
            _write(own)
        finally:
            own.close()


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
    """sec_coverage rows (all issuers, or one resolved via resolve_issuer).
    For a ticker pinned in sec_overrides.yaml each row carries `resolution`
    (override, pinned_cik, used_cik)."""
    pin_res = None
    con = get_conn(db_path, read_only=True)
    try:
        try:
            if query:
                iss = resolve_issuer(query, db_path=db_path)
                if iss is None:
                    return []
                df = con.execute("SELECT * FROM sec_coverage WHERE issuer_id = ?",
                                 [iss["issuer_id"]]).fetch_df()
                pin_res = _with_pin_disclosure(query, iss)
            else:
                df = con.execute(
                    "SELECT * FROM sec_coverage ORDER BY last_filed DESC"
                ).fetch_df()
        except duckdb.CatalogException:
            return []
    finally:
        con.close()
    rows = json.loads(df.to_json(orient="records", date_format="iso"))
    if pin_res:                 # each row says the ticker was resolved by a pin
        rows = [{**r, "resolution": pin_res} for r in rows]
    return rows


def _read_concepts(con, cik: str,
                   concepts: List[str]) -> Dict[str, Dict[str, Any]]:
    """period end -> {value, concept, accession, filed_date}: concepts tried in
    order (earlier wins), latest filed row per period (annual 10-K/20-F)."""
    per_period: Dict[str, Dict[str, Any]] = {}
    for concept in concepts:
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
        """, [cik, concept]).fetchall()
        for end, value, cpt, accn, filed, _start in rows:
            key = end.isoformat()
            if key not in per_period:   # earlier concept wins
                per_period[key] = {
                    "value": value, "concept": cpt, "accession": accn,
                    "filed_date": filed.isoformat() if filed else None}
    return per_period


def _derive_line(con, cik: str, dcfg: Dict[str, Any],
                 filed_lines: Dict[str, Dict[str, Any]]
                 ) -> Dict[str, Dict[str, Any]]:
    """Derived line (mapping v3 `derived_lines`): minuend - subtrahend per
    period, only where the `only_if_absent` filed line has no value. Each
    value is labelled derived and carries the inputs' provenance."""
    absent = dcfg.get("only_if_absent")
    have = filed_lines.get(absent, {}) if absent else {}
    minu = _read_concepts(con, cik, dcfg["minuend"])
    subs = [(c, _read_concepts(con, cik, [c])) for c in dcfg["subtrahends"]]
    out: Dict[str, Dict[str, Any]] = {}
    for end, m in minu.items():
        if end in have:
            continue
        for _c, per in subs:                    # first with data wins
            sub = per.get(end)
            if sub is None:
                continue
            out[end] = {
                "value": m["value"] - sub["value"],
                "derived": True,
                "formula": dcfg["formula"],
                "inputs": {"minuend": m, "subtrahend": sub}}
            break
    return out


def get_statement(query: str, statement: Optional[str] = None,
                  periods: int = 8, mapping_version: int = 3,
                  db_path: Optional[str] = None) -> Dict[str, Any]:
    """Standardized ANNUAL statement lines for one issuer, derived on read
    from the append-only facts through the versioned mapping (plan Fase 4).

    Semantics per line: concepts are tried in mapping order and a later
    concept only fills periods the earlier ones lack; for each period
    (end_date) the row with the LATEST filed_date wins (restatements
    supersede on read, history stays intact in sec_company_facts). Duration
    lines (income/cash flow) require a ~annual window so quarterly and
    comparative stubs are excluded; balance lines are instants.

    Derived lines (mapping v3, `derived_lines`): e.g. `liabilities_derived` =
    LiabilitiesAndStockholdersEquity - equity, filled only for periods where
    the filed `liabilities` line is absent, never overwriting a filed value.
    Every derived value has `derived: true`, the `formula` and `inputs`
    (concept, accession, filed date of each input). Callers must say it is
    derived, not filed.

    Output is bounded (max 12 periods) and carries provenance per value:
    concept, accession, filed date — plus the mapping version used.

    Staleness (D65): `line_status[line]` gives `latest_period` and `stale`.
    A line is stale when its latest period trails the response-level
    `latest_period` (the issuer's latest fiscal year end across ALL mapped lines of every
    statement, even when `statement` filters the output)
    by more than _STALE_DAYS; it then also carries `stale_since` (that period
    end) and the response lists a warning. Stale values must not be presented
    as current.

    Resolution (D63): when the resolved filer has no annual facts and
    sec_overrides.yaml names a predecessor CIK that has, that predecessor is
    used; `resolution` says which CIK was resolved, which was used, and why.
    A ticker pinned in sec_overrides.yaml always gets `resolution` with
    `override: true` and `pinned_cik`, even when no fallback was needed.

    Limit: staleness uses one 200-day tolerance. A fiscal-year-end change
    whose transition gap exceeds it flags the older full-year lines as stale
    (conservative: the caller sees the period and decides).
    """
    iss = resolve_issuer(query, db_path=db_path)
    if iss is None:
        return {"error": f"issuer {query!r} not in the hub; run "
                         f"ensure_filings_and_facts first", "lines": {}}
    iss, resolution = _annual_history_filer(iss, db_path)
    resolution = _with_pin_disclosure(query, iss, resolution)
    mapping = get_xbrl_mapping(mapping_version)
    all_cfg = mapping.get("statement_lines") or {}
    lines_cfg = all_cfg
    if statement:
        lines_cfg = {k: v for k, v in all_cfg.items()
                     if v.get("statement") == statement}
        if not lines_cfg:
            return {"error": f"no mapped lines for statement {statement!r}",
                    "lines": {}}
    periods = max(1, min(int(periods), _MAX_PERIODS))

    con = get_conn(db_path, read_only=True)
    try:
        lines: Dict[str, Dict[str, Any]] = {}
        all_ends: set = set()
        issuer_latest_all: Optional[str] = None
        filed_all: Dict[str, Dict[str, Any]] = {}
        for line_key, cfg in all_cfg.items():
            per_period = _read_concepts(con, iss["cik"], cfg["concepts"])
            filed_all[line_key] = per_period
            keep = sorted(per_period, reverse=True)[:periods]
            if per_period:
                top = max(per_period)
                if issuer_latest_all is None or top > issuer_latest_all:
                    issuer_latest_all = top
            if line_key not in lines_cfg:
                continue            # read only to anchor the staleness check
            lines[line_key] = {k: per_period[k] for k in keep}
            all_ends.update(keep)
        for dkey, dcfg in (mapping.get("derived_lines") or {}).items():
            if statement and dcfg.get("statement") != statement:
                continue
            dper = _derive_line(con, iss["cik"], dcfg, filed_all)
            dkeep = sorted(dper, reverse=True)[:periods]
            lines[dkey] = {k: dper[k] for k in dkeep}
            all_ends.update(dkeep)
    finally:
        con.close()

    # staleness: compare each returned line's latest period with the issuer's
    # latest period across ALL mapped lines (lines are never dropped)
    issuer_latest = issuer_latest_all
    line_status: Dict[str, Dict[str, Any]] = {}
    warnings: List[str] = []
    for line_key, per in lines.items():
        latest = max(per) if per else None
        stale = bool(latest and issuer_latest and (
            date.fromisoformat(issuer_latest) - date.fromisoformat(latest)
        ).days > _STALE_DAYS)
        st: Dict[str, Any] = {"latest_period": latest, "stale": stale}
        if stale:
            st["stale_since"] = latest
            warnings.append(
                f"{line_key}: latest value is for period {latest}, older than "
                f"the issuer's latest fiscal year {issuer_latest}; not current")
        line_status[line_key] = st
    out: Dict[str, Any] = {
        "issuer": iss,
        "mapping": {"version": mapping.get("version"),
                    "taxonomy": mapping.get("taxonomy")},
        "frequency": "annual",
        "latest_period": issuer_latest,
        "periods": sorted(all_ends, reverse=True)[:periods],
        "lines": lines,
        "line_status": line_status}
    if warnings:
        out["warnings"] = warnings
    if resolution:
        out["resolution"] = resolution
    return out


def _with_pin_disclosure(query: str, iss: Dict[str, Any],
                         resolution: Optional[Dict[str, Any]] = None
                         ) -> Optional[Dict[str, Any]]:
    """Shared by every local read: when `query` is a ticker pinned in
    sec_overrides.yaml, return a `resolution` block with override=True,
    pinned_cik and used_cik (merged into `resolution` if a fallback already
    produced one); otherwise return `resolution` unchanged."""
    q = query.strip()
    pin = None if q.isdigit() or q.startswith("iss_") else _override_cik(q)
    if not pin:
        return resolution
    base = resolution or {"resolved_cik": iss["cik"], "used_cik": iss["cik"],
                          "fallback": False,
                          "reason": "ticker pinned in sec_overrides.yaml"}
    return {**base, "override": True, "pinned_cik": pin}


def _annual_history_filer(iss: Dict[str, Any], db_path: Optional[str]):
    """(issuer, resolution). Keep the resolved filer when it holds annual
    facts; otherwise fall back ONLY through an explicit cik_predecessors
    entry in sec_overrides.yaml (never by shared tickers or names: a reused
    ticker must not resolve to an unrelated old issuer)."""
    from market_data_hub.config_loader import get_sec_overrides
    con = get_conn(db_path, read_only=True)
    try:
        def has_annual(cik: str) -> bool:
            return con.execute(
                "SELECT 1 FROM sec_company_facts WHERE cik = ? "
                f"AND form IN {_ANNUAL_FORMS_SQL} LIMIT 1", [cik]
            ).fetchone() is not None

        if has_annual(iss["cik"]):
            return iss, None
        cik = get_sec_overrides()["cik_predecessors"].get(iss["cik"])
        if cik and cik != iss["cik"] and has_annual(cik):
            row = con.execute(
                "SELECT issuer_id, cik, name, sic, fiscal_year_end "
                "FROM issuers WHERE cik = ?", [cik]).fetchone()
            used = (dict(zip(["issuer_id", "cik", "name", "sic",
                              "fiscal_year_end"], row)) if row
                    else {"issuer_id": None, "cik": cik, "name": None,
                          "sic": None, "fiscal_year_end": None})
            return used, {
                "resolved_cik": iss["cik"], "used_cik": cik,
                "fallback": True,
                "reason": "resolved filer has no annual (10-K/20-F) facts; "
                          "using configured predecessor (sec_overrides.yaml)"}
    finally:
        con.close()
    return iss, {"resolved_cik": iss["cik"], "used_cik": iss["cik"],
                 "fallback": False,
                 "reason": "resolved filer has no annual facts and no "
                           "configured predecessor holds any"}


def get_facts(query: str, line: Optional[str] = None,
              concepts: Optional[List[str]] = None,
              forms: Optional[List[str]] = None,
              limit: int = _MAX_FACT_ROWS, mapping_version: int = 3,
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
        dentry = (mapping.get("derived_lines") or {}).get(line)
        if entry is None and dentry is not None:
            entry = {"concepts": list(dentry["minuend"])
                     + list(dentry["subtrahends"])}
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
    out: Dict[str, Any] = {
        "issuer": iss, "mapping": mapping_used,
        "n_total": int(total), "n_returned": int(len(df)),
        "truncated": bool(total > len(df)),
        "facts": json.loads(df.to_json(orient="records", date_format="iso"))}
    resolution = _with_pin_disclosure(query, iss)
    if resolution:
        out["resolution"] = resolution
    return out
