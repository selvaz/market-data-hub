# -*- coding: utf-8 -*-
"""Streaming readers for the CFTC's official Historical Compressed COT files.

The public Socrata connector in :mod:`cftc_cot` remains the daily source. These
helpers parse CFTC's annual ZIP archives into that connector's same normalized
column contract so historical rows can be upserted through ``db.upsert``.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, Iterator, List, Mapping, Optional, Tuple

import pandas as pd
import requests

from market_data_hub.sources import cftc_cot


HISTORY_URL = "https://www.cftc.gov/files/dea/history"
ARCHIVE_TIMEOUT = 120
DEFAULT_BATCH_SIZE = 5000


class CFTCArchiveError(ValueError):
    """An official CFTC archive cannot be safely interpreted."""


@dataclass(frozen=True)
class ArchiveSpec:
    report: str
    first_year: int
    last_year: int
    filename: str

    @property
    def url(self) -> str:
        return f"{HISTORY_URL}/{self.filename}"

    @property
    def table(self) -> str:
        return {
            "tff": "cftc_tff_positioning",
            "legacy": "cftc_legacy_positioning",
        }[self.report]


def historical_archive_plan(report: str, start: date, end: date
                            ) -> List[ArchiveSpec]:
    """Return the official ZIP files that cover ``report``'s requested dates.

    CFTC publishes Legacy Futures Only ZIPs by year back to 1986. TFF uses a
    2006-2016 bundle and individual Futures Only ZIP files from 2017 onward.
    """
    if report not in ("tff", "legacy"):
        raise ValueError("report must be 'tff' or 'legacy'")
    if start > end:
        raise ValueError("start date must be on or before end date")

    specs: List[ArchiveSpec] = []
    first_requested_year = start.year
    last_requested_year = end.year
    if report == "legacy":
        annual_first = max(first_requested_year, 1986)
        for year in range(annual_first, last_requested_year + 1):
            specs.append(ArchiveSpec(
                report, year, year, f"deacot{year}.zip"))
        return specs

    if first_requested_year <= 2016 and last_requested_year >= 2006:
        specs.append(ArchiveSpec(
            report, 2006, 2016, "fin_fut_txt_2006_2016.zip"))
    annual_first = max(first_requested_year, 2017)
    for year in range(annual_first, last_requested_year + 1):
        if report == "legacy":
            filename = f"deacot{year}.zip"
        else:
            filename = f"fut_fin_txt_{year}.zip"
        specs.append(ArchiveSpec(report, year, year, filename))
    return specs


def download_archive(url: str, destination: Path,
                     timeout: int = ARCHIVE_TIMEOUT) -> Path:
    """Stream one official ZIP to disk without buffering it in memory."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "market-data-hub/0.1", "Connection": "close"}
    try:
        with requests.get(url, headers=headers, stream=True,
                          timeout=timeout) as response:
            response.raise_for_status()
            with destination.open("wb") as target:
                for block in response.iter_content(chunk_size=1024 * 1024):
                    if block:
                        target.write(block)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination


def _token(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).strip().lower())


_COMMON_ARCHIVE_ALIASES = {
    "marketandexchangenames": "contract_market_name",
    "cftccontractmarketcode": "cftc_contract_market_code",
    "reportdateasyyyymmdd": "report_date_as_yyyy_mm_dd",
    "asofdateinformyyyymmdd": "report_date_as_yyyy_mm_dd",
}

_TFF_ARCHIVE_ALIASES = {
    # The compressed TFF files carry the report family in field names with
    # trailing _All suffixes; Socrata omits those suffixes for these fields.
    "assetmgrpositionslongall": "asset_mgr_positions_long",
    "assetmgrpositionsshortall": "asset_mgr_positions_short",
    "assetmgrpositionsspreadall": "asset_mgr_positions_spread",
    "levmoneypositionslongall": "lev_money_positions_long",
    "levmoneypositionsshortall": "lev_money_positions_short",
    "levmoneypositionsspreadall": "lev_money_positions_spread",
    "otherreptpositionslongall": "other_rept_positions_long",
    "otherreptpositionsshortall": "other_rept_positions_short",
    "otherreptpositionsspreadall": "other_rept_positions_spread",
    "pctofoiassetmgrlongall": "pct_of_oi_asset_mgr_long",
    "pctofoiassetmgrshortall": "pct_of_oi_asset_mgr_short",
    "pctofoilevmoneylongall": "pct_of_oi_lev_money_long",
    "pctofoilevmoneyshortall": "pct_of_oi_lev_money_short",
    "totreptpositionsshortall": "tot_rept_positions_short",
}

_LEGACY_ARCHIVE_ALIASES = {
    "noncommercialpositionslongall": "noncomm_positions_long_all",
    "noncommercialpositionsshortall": "noncomm_positions_short_all",
    "noncommercialpositionsspreadingall": "noncomm_postions_spread_all",
    "commercialpositionslongall": "comm_positions_long_all",
    "commercialpositionsshortall": "comm_positions_short_all",
    "totalreportablepositionslongall": "tot_rept_positions_long_all",
    "totalreportablepositionsshortall": "tot_rept_positions_short",
    "nonreportablepositionslongall": "nonrept_positions_long_all",
    "nonreportablepositionsshortall": "nonrept_positions_short_all",
}


def _header_mapping(report: str, headers: Optional[List[str]]) -> Dict[str, str]:
    if report == "tff":
        rename = cftc_cot._TFF_RENAME
        aliases = dict(_COMMON_ARCHIVE_ALIASES, **_TFF_ARCHIVE_ALIASES)
    elif report == "legacy":
        rename = cftc_cot._LEGACY_RENAME
        aliases = dict(_COMMON_ARCHIVE_ALIASES, **_LEGACY_ARCHIVE_ALIASES)
    else:
        raise ValueError("report must be 'tff' or 'legacy'")
    if not headers:
        raise CFTCArchiveError("CSV has no header row")

    # ``open_interest_all`` is a passthrough field in both Socrata mappings,
    # so it does not occur as a key in the rename dictionaries.
    api_fields = set(rename) | {"open_interest_all"}
    api_field_by_token = {_token(field): field for field in api_fields}
    header_to_api: Dict[str, str] = {}
    for header in headers:
        field_token = _token(header)
        api_field = aliases.get(field_token, api_field_by_token.get(field_token))
        if api_field:
            header_to_api[header] = api_field

    found = set(header_to_api.values())
    missing = sorted((set(rename) | {"open_interest_all"}) - found)
    if missing:
        raise CFTCArchiveError(
            f"{report} CSV is missing required fields: {', '.join(missing)}")
    return header_to_api


def _iter_api_rows(path: Path, report: str
                   ) -> Iterator[Tuple[Dict[str, str], int]]:
    """Yield CFTC header fields remapped to the existing Socrata raw names."""
    try:
        with zipfile.ZipFile(path) as archive:
            members = [info for info in archive.infolist()
                       if not info.is_dir()
                       and info.filename.lower().endswith((".csv", ".txt"))]
            if len(members) != 1:
                raise CFTCArchiveError(
                    "ZIP must contain exactly one .csv or .txt data member")
            with archive.open(members[0]) as raw:
                with io.TextIOWrapper(raw, encoding="utf-8-sig",
                                      errors="replace", newline="") as text:
                    reader = csv.DictReader(text)
                    header_to_api = _header_mapping(report, reader.fieldnames)
                    variant_header = next(
                        (name for name in reader.fieldnames or []
                         if _token(name) == "futonlyorcombined"), None)
                    for row_number, raw_row in enumerate(reader, start=2):
                        if None in raw_row or any(
                                value is None for value in raw_row.values()):
                            raise CFTCArchiveError(
                                f"malformed CSV row {row_number}: field count differs from header")
                        if variant_header:
                            variant = (raw_row.get(variant_header) or "").strip().lower()
                            if variant and variant not in ("futonly", "futures only"):
                                raise CFTCArchiveError(
                                    f"unexpected report variant {variant!r} on row {row_number}")
                        api_row = {
                            api_name: raw_row[header]
                            for header, api_name in header_to_api.items()
                        }
                        yield api_row, row_number
    except CFTCArchiveError:
        raise
    except (OSError, zipfile.BadZipFile, csv.Error, UnicodeError) as exc:
        raise CFTCArchiveError(f"cannot read CFTC ZIP {path}: {exc}") from exc


def _report_date(row: Mapping[str, str], row_number: int) -> date:
    raw_date = row.get("report_date_as_yyyy_mm_dd", "").strip()
    try:
        parsed = pd.to_datetime(raw_date, errors="raise")
        if pd.isna(parsed):
            raise ValueError("empty date")
        return parsed.date()
    except Exception as exc:
        raise CFTCArchiveError(
            f"invalid report date {raw_date!r} on CSV row {row_number}") from exc


def count_archive_rows(path: Path, report: str, start: date, end: date
                       ) -> Dict[int, int]:
    """Validate an archive and count eligible upsert candidates by report year.

    The scan holds at most one parsed CSV row and a small year counter in
    memory. It is also used as the validation pass before any database write.
    """
    counts: Counter = Counter()
    for row, row_number in _iter_api_rows(Path(path), report):
        report_date = _report_date(row, row_number)
        if not row.get("contract_market_name", "").strip():
            raise CFTCArchiveError(
                f"missing contract name on CSV row {row_number}")
        if not row.get("cftc_contract_market_code", "").strip():
            raise CFTCArchiveError(
                f"missing contract code on CSV row {row_number}")
        if start <= report_date <= end:
            counts[report_date.year] += 1
    return dict(counts)


def iter_archive_batches(path: Path, report: str, start: date, end: date,
                         batch_size: int = DEFAULT_BATCH_SIZE
                         ) -> Iterator[pd.DataFrame]:
    """Yield normalized frames no larger than ``batch_size`` rows.

    Call :func:`count_archive_rows` first to validate the complete archive;
    this second streaming pass then yields bounded batches for upserting.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if report == "tff":
        rename, columns, source = (
            cftc_cot._TFF_RENAME, cftc_cot._TFF_COLS, "cftc_tff")
    elif report == "legacy":
        rename, columns, source = (
            cftc_cot._LEGACY_RENAME, cftc_cot._LEGACY_COLS, "cftc_legacy")
    else:
        raise ValueError("report must be 'tff' or 'legacy'")

    rows: List[Dict[str, str]] = []
    for row, row_number in _iter_api_rows(Path(path), report):
        report_date = _report_date(row, row_number)
        if start <= report_date <= end:
            # Standardize both the old month/day timestamp and modern ISO
            # representations before pandas sees a batch. This also makes a
            # revised archive containing both styles safe to parse.
            row["report_date_as_yyyy_mm_dd"] = report_date.isoformat()
            rows.append(row)
            if len(rows) >= batch_size:
                yield cftc_cot._normalize(rows, rename, columns, source)
                rows = []
    if rows:
        yield cftc_cot._normalize(rows, rename, columns, source)
