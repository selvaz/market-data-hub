# -*- coding: utf-8 -*-
"""
sec.py — protected SEC/EDGAR transport + parsers (plan v3.1, Fase 3).

Guardrails baked into the transport (they are the point of this module):

  - host allowlist: only www.sec.gov / data.sec.gov URLs are ever fetched;
  - mandatory User-Agent with a contact (SEC fair-access policy) — settings
    `sec.user_agent` or env SEC_USER_AGENT override the default;
  - client-side throttle well under the SEC's 10 req/s limit;
  - hard cap on response size (streamed), so a pathological or wrong URL
    cannot balloon memory.

Endpoints used (all JSON, no HTML scraping in this phase):
  - company_tickers.json          ticker -> CIK mapping
  - submissions/CIK##########.json  filing metadata
  - api/xbrl/companyfacts/CIK##########.json  all XBRL facts

Parsers return plain DataFrames in the shape of the sec_* tables; DB writes
live in services.financials, never here.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import pandas as pd
import requests

ALLOWED_HOSTS = {"www.sec.gov", "data.sec.gov"}
MAX_RESPONSE_BYTES = 50_000_000          # companyfacts for mega-caps is ~10-20MB
MIN_REQUEST_INTERVAL = 0.15              # ~6.6 req/s, under the SEC's 10 req/s
_DEFAULT_USER_AGENT = "market-data-hub/0.1 (doctor.selva@gmail.com)"

_throttle_lock = threading.Lock()
_last_request_ts = 0.0


class SecTransportError(RuntimeError):
    """Raised for blocked hosts, oversized responses or HTTP failures."""


def _user_agent() -> str:
    ua = os.environ.get("SEC_USER_AGENT")
    if ua:
        return ua
    try:
        from market_data_hub.config_loader import get_settings
        cfg = get_settings().get("sec") or {}
        if cfg.get("user_agent"):
            return str(cfg["user_agent"])
    except Exception:
        pass
    return _DEFAULT_USER_AGENT


def _throttle() -> None:
    global _last_request_ts
    with _throttle_lock:
        wait = MIN_REQUEST_INTERVAL - (time.monotonic() - _last_request_ts)
        if wait > 0:
            time.sleep(wait)
        _last_request_ts = time.monotonic()


def get_json(url: str, timeout: int = 30, retries: int = 3,
             base_sleep: float = 1.0) -> Dict[str, Any]:
    """Fetch a JSON document from an allowlisted SEC host, throttled and
    size-capped. Every SEC byte entering the hub passes through here."""
    host = urlparse(url).hostname
    if host not in ALLOWED_HOSTS:
        raise SecTransportError(f"host {host!r} is not an allowed SEC host "
                                f"({sorted(ALLOWED_HOSTS)})")
    headers = {"User-Agent": _user_agent(),
               "Accept-Encoding": "gzip, deflate",
               "Host": host}
    last_exc: Optional[Exception] = None
    for attempt in range(retries):
        _throttle()
        try:
            with requests.Session() as s:
                r = s.get(url, headers=headers, timeout=timeout, stream=True)
                if r.status_code == 429 or r.status_code >= 500:
                    raise SecTransportError(f"HTTP {r.status_code} from {url}")
                r.raise_for_status()
                chunks, size = [], 0
                for chunk in r.iter_content(chunk_size=1 << 16):
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise SecTransportError(
                            f"response exceeds {MAX_RESPONSE_BYTES} bytes cap "
                            f"({url})")
                    chunks.append(chunk)
            payload: Dict[str, Any] = json.loads(b"".join(chunks))
            return payload
        except SecTransportError as exc:
            if "bytes cap" in str(exc) or "not an allowed" in str(exc):
                raise
            last_exc = exc
        except (requests.RequestException, json.JSONDecodeError) as exc:
            last_exc = exc
        time.sleep(base_sleep * (2 ** attempt))
    raise SecTransportError(f"SEC fetch failed after {retries} attempts: "
                            f"{url} ({last_exc})")


# ------------------------------------------------------------------ endpoints
def normalize_cik(cik: Any) -> str:
    """'320193' / 320193 / '0000320193' -> '0000320193'."""
    digits = str(cik).strip().lstrip("0") or "0"
    if not digits.isdigit():
        raise ValueError(f"invalid CIK {cik!r}")
    return digits.zfill(10)


def fetch_company_tickers() -> Dict[str, Any]:
    return get_json("https://www.sec.gov/files/company_tickers.json")


def fetch_submissions(cik: Any) -> Dict[str, Any]:
    return get_json(f"https://data.sec.gov/submissions/CIK{normalize_cik(cik)}.json")


def fetch_company_facts(cik: Any) -> Dict[str, Any]:
    return get_json("https://data.sec.gov/api/xbrl/companyfacts/"
                    f"CIK{normalize_cik(cik)}.json")


# -------------------------------------------------------------------- parsers
def company_tickers_to_map(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """company_tickers.json -> {TICKER: {cik, name}} (ticker upper-cased)."""
    out: Dict[str, Dict[str, Any]] = {}
    for entry in payload.values():
        out[str(entry["ticker"]).upper()] = {
            "cik": normalize_cik(entry["cik_str"]),
            "name": entry.get("title"),
        }
    return out


def submissions_to_filings(payload: Dict[str, Any],
                           forms: Optional[List[str]] = None) -> pd.DataFrame:
    """submissions JSON -> DataFrame in sec_filings shape (recent filings)."""
    cik = normalize_cik(payload["cik"])
    recent = payload.get("filings", {}).get("recent", {})
    if not recent.get("accessionNumber"):
        return pd.DataFrame()
    df = pd.DataFrame({
        "cik": cik,
        "accession": recent["accessionNumber"],
        "form": recent["form"],
        "filed_date": pd.to_datetime(recent["filingDate"]).date,
        "report_date": [d or None for d in recent.get(
            "reportDate", [None] * len(recent["accessionNumber"]))],
        "primary_doc": recent.get("primaryDocument"),
    })
    df["report_date"] = pd.to_datetime(df["report_date"], errors="coerce").dt.date
    if forms:
        df = df[df["form"].isin(forms)].reset_index(drop=True)
    df["primary_doc_url"] = [
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
        f"{acc.replace('-', '')}/{doc}" if doc else None
        for acc, doc in zip(df["accession"], df["primary_doc"])]
    return df


def company_facts_to_df(payload: Dict[str, Any],
                        taxonomies: Optional[List[str]] = None) -> pd.DataFrame:
    """companyfacts JSON -> long DataFrame in sec_company_facts shape (minus
    fact_id/run_id, added at write time). Facts keep unit, period, fiscal
    year/period, form, accession and filed date — the plan's verifiability
    requirement — exactly as reported."""
    cik = normalize_cik(payload["cik"])
    taxonomies = taxonomies or ["us-gaap", "ifrs-full", "dei"]
    rows: List[Dict[str, Any]] = []
    for taxonomy in taxonomies:
        for concept, body in (payload.get("facts", {}).get(taxonomy) or {}).items():
            for unit, observations in (body.get("units") or {}).items():
                for o in observations:
                    rows.append({
                        "cik": cik, "taxonomy": taxonomy, "concept": concept,
                        "unit": unit, "start_date": o.get("start"),
                        "end_date": o.get("end"), "value": o.get("val"),
                        "fy": o.get("fy"), "fp": o.get("fp"),
                        "form": o.get("form"), "filed_date": o.get("filed"),
                        "accession": o.get("accn"), "frame": o.get("frame"),
                    })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for col in ("start_date", "end_date", "filed_date"):
        df[col] = pd.to_datetime(df[col], errors="coerce").dt.date
    return df
