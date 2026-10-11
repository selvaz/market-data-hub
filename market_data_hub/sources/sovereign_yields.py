"""Daily sovereign yields from official publishers, in percentage points.

No monthly or commercial fallback, interpolation, or forward filling. A reachable
HTML challenge is not data. Unresolved sources fail explicitly as unavailable.
Source definitions and live-probe limitations: docs/f08-sovereign-yields.md.
"""
from __future__ import annotations

import csv
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import timedelta
from html.parser import HTMLParser
from io import BytesIO, StringIO
from typing import Optional
from zipfile import ZipFile

import numpy as np
import pandas as pd
import requests

COLUMNS = ["date", "series_id", "value", "series_name", "unit",
           "frequency", "source", "country"]
PROVIDERS = {"ecb", "bundesbank", "webstat", "webstat_csv", "bdi", "bde", "nbb", "boe", "mof",
             "snb", "riksbank", "ecos", "chinabond", "sarb", "tesouro", "unavailable"}
_GENERIC = "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/data/generic"
_MISSING = {"", "-", "_", ".", "N/E", "N/D", "NA", "N/A", "NaN"}


class SourceUnavailable(RuntimeError):
    """Missing credentials, rejected/changed response, or unresolved endpoint."""


def _request(url: str, *, params: Optional[dict] = None,
             headers: Optional[dict] = None, timeout: int = 30,
             retries: int = 3, base_sleep: float = 1.0) -> requests.Response:
    """Retry transient transport/server failures only. Never expose secret URLs."""
    if retries < 1:
        raise ValueError("retries must be at least 1")
    for attempt in range(retries):
        try:
            with requests.Session() as session:
                response = session.get(url, params=params, timeout=timeout, headers={
                    "User-Agent": "market-data-hub/0.1", "Connection": "close",
                    **(headers or {})})
            if response.status_code >= 400:
                if response.status_code != 429 and response.status_code < 500:
                    raise SourceUnavailable(f"official source returned HTTP {response.status_code}")
                response.raise_for_status()
            return response
        except requests.RequestException:
            if attempt == retries - 1:
                raise SourceUnavailable("official source transport/server failure") from None
            time.sleep(base_sleep * (4 ** attempt))
    raise SourceUnavailable("official source did not respond")


def _text(body: bytes, fallback: str = "cp1252") -> str:
    try:
        return body.decode("utf-8-sig")
    except UnicodeDecodeError:
        return body.decode(fallback)


def _normalize(meta: dict, rows: list) -> pd.DataFrame:
    observations = []
    for raw_date, raw_value in rows:
        if raw_value is None or str(raw_value).strip() in _MISSING:
            continue
        # Never accept a monthly/annual period as the first day of a month.
        if not re.fullmatch(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:T00:00:00)?", str(raw_date)):
            raise SourceUnavailable("expected a daily observation date")
        try:
            d = pd.Timestamp(raw_date)
            value = float(str(raw_value).replace(",", "."))
        except (ValueError, TypeError):
            raise SourceUnavailable("invalid daily date or numeric yield") from None
        if not np.isfinite(value):
            raise SourceUnavailable("non-finite yield")
        observations.append((d, value))
    df = pd.DataFrame(observations, columns=["date", "value"])
    if df.empty:
        return pd.DataFrame(columns=COLUMNS)
    if (df.groupby("date").value.nunique() > 1).any():
        raise SourceUnavailable("conflicting duplicate daily observations")
    df = df.drop_duplicates("date").sort_values("date").reset_index(drop=True)
    for column, value in {"series_id": meta["symbol"], "series_name": meta["name"],
                          "unit": "%", "frequency": "D", "source": meta["source"],
                          "country": meta["country"]}.items():
        df[column] = value
    return df[COLUMNS]


def _sdmx(meta: dict, body: bytes) -> list:
    root = ET.fromstring(body)
    ns = {"g": _GENERIC}
    selected = []
    for series in root.findall(".//g:Series", ns):
        key = {x.get("id"): x.get("value") for x in series.findall("g:SeriesKey/g:Value", ns)}
        if key.get("FREQ") != "D":
            continue
        if any(key.get(k) != v for k, v in meta.get("dimensions", {}).items()):
            continue
        selected.append(series)
    if len(selected) != 1:
        raise SourceUnavailable("expected exactly one matching daily SDMX series")
    rows = []
    for obs in selected[0].findall("g:Obs", ns):
        dimension = obs.find("g:ObsDimension", ns)
        value = obs.find("g:ObsValue", ns)
        if dimension is None or value is None:
            raise SourceUnavailable("missing SDMX observation fields")
        rows.append((dimension.get("value"), value.get("value")))
    return rows


class _Tables(HTMLParser):
    """Read original HTML table cells without executing publisher scripts."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.current = None
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.current = []
        elif tag == "tr" and self.current is not None:
            self.row = []
        elif tag in {"td", "th"} and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.cell is not None:
            self.row.append("".join(self.cell).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.current.append(self.row)
            self.row = None
        elif tag == "table" and self.current is not None:
            self.tables.append(self.current)
            self.current = None


def parse_response(meta: dict, body: bytes) -> pd.DataFrame:
    """Validate publisher format/tenor, then produce the canonical macro rows."""
    provider = meta["provider"]
    try:
        # MoF's English CSV has a CP932-encoded footnote, even though its
        # data/header fields are ASCII. Keep decoding strict and provider-specific.
        text = _text(body, "cp932" if provider == "mof" else "cp1252") if not body.startswith(b"PK") else ""
        if provider == "ecb":
            code = meta["provider_code"]
            if not re.fullmatch(r"B\.U2\.EUR\.4F\.G_N_[AC]\.SV_C_YM\.SR_(?:2|10)Y", code):
                raise SourceUnavailable("unsupported ECB yield curve key")
            dimensions = dict(zip(
                ["FREQ", "REF_AREA", "CURRENCY", "PROVIDER_FM", "INSTRUMENT_FM",
                 "PROVIDER_FM_ID", "DATA_TYPE_FM"], code.split(".")))
            reader = csv.DictReader(StringIO(text))
            expected = {**dimensions, "KEY": f"YC.{code}", "UNIT": "PCPA",
                        "UNIT_MULT": "0", "TIME_FORMAT": "P1D"}
            if not set(expected) | {"TIME_PERIOD", "OBS_VALUE", "OBS_STATUS"} <= set(reader.fieldnames or ()):
                raise SourceUnavailable("unexpected ECB yield curve CSV header")
            rows = []
            for r in reader:
                if any(r[k] != v for k, v in expected.items()) or r["OBS_STATUS"] not in {"A", "P"}:
                    raise SourceUnavailable("unexpected ECB series, frequency, unit, or observation status")
                rows.append((r["TIME_PERIOD"], r["OBS_VALUE"]))
        elif provider in {"bundesbank", "nbb"}:
            rows = _sdmx(meta, body)
        elif provider == "boe":
            reader = csv.DictReader(StringIO(text))
            if reader.fieldnames != ["DATE", meta["provider_code"]]:
                raise SourceUnavailable("unexpected BoE CSV series")
            rows = [(pd.to_datetime(r["DATE"], format="%d %b %Y").date().isoformat(),
                     r[meta["provider_code"]]) for r in reader]
        elif provider == "mof":
            lines = text.splitlines()
            header = next((i for i, line in enumerate(lines) if line.startswith("Date,")), None)
            if header is None or "Unit : %" not in text[:300]:
                raise SourceUnavailable("unexpected JGB header or unit")
            reader = csv.DictReader(StringIO("\n".join(lines[header:])))
            if meta["provider_code"] not in reader.fieldnames:
                raise SourceUnavailable("missing JGB maturity")
            rows = [(r["Date"], r[meta["provider_code"]]) for r in reader
                    if re.fullmatch(r"\d{4}/\d{1,2}/\d{1,2}", r["Date"] or "")]
        elif provider == "bdi":
            if body.startswith(b"PK"):
                with ZipFile(BytesIO(body)) as archive:
                    members = [n for n in archive.namelist() if n.lower().endswith(".csv")]
                    if len(members) != 1:
                        raise SourceUnavailable("unexpected BDS ZIP members")
                    text = _text(archive.read(members[0]))
            reader = csv.DictReader(StringIO(text), delimiter=";")
            if not reader.fieldnames or reader.fieldnames[0] != "DATA_OSS" or meta["provider_code"] not in reader.fieldnames:
                raise SourceUnavailable("unexpected BDS daily benchmark columns")
            rows = [(r["DATA_OSS"], r[meta["provider_code"]]) for r in reader]
        elif provider == "bde":
            table = list(csv.reader(StringIO(text)))
            if not table or not table[0][0].startswith("CÓDIGO DE LA SERIE"):
                raise SourceUnavailable("unexpected BdE header")
            column = table[0].index(meta["provider_code"])
            metadata = {r[0]: r[column] for r in table[1:6]}
            if metadata.get("FRECUENCIA") != "DIARIA" or metadata.get("DESCRIPCIÓN DE LAS UNIDADES") != "Porcentaje":
                raise SourceUnavailable("BdE series must be daily, in percent")
            months = dict(zip("ENE FEB MAR ABR MAY JUN JUL AGO SEP OCT NOV DIC".split(), range(1, 13)))
            rows = []
            for r in table[6:]:
                match = re.fullmatch(r"(\d{2}) ([A-Z]{3}) (\d{4})", r[0])
                if match:
                    day, month, year = match.groups()
                    rows.append((f"{year}-{months[month]:02d}-{day}", r[column]))
        elif provider == "snb":
            if text.lstrip().startswith("{"):
                payload = json.loads(text)
                if meta["provider_code"] != "10J" or "r10" not in payload:
                    raise SourceUnavailable("SNB current rates contain only the 10y sovereign tenor")
                rows = []
                for stamp, value in payload["r10"]:
                    d = pd.to_datetime(stamp, unit="ms", utc=True)
                    if d != d.normalize():
                        raise SourceUnavailable("SNB chart timestamp must be a daily UTC date")
                    rows.append((d.date().isoformat(), value))
            else:
                lines = text.splitlines()
                header = next((i for i, line in enumerate(lines) if line.startswith('"Date";')), None)
                if header is None or '"rendeiduebd"' not in text[:100]:
                    raise SourceUnavailable("expected daily SNB cube rendeiduebd")
                reader = csv.DictReader(StringIO("\n".join(lines[header:])), delimiter=";")
                rows = [(r["Date"], r["Value"]) for r in reader
                        if r["D0"] == "CHF" and r["D1"] == meta["provider_code"]]
        elif provider == "riksbank":
            payload = json.loads(text)
            if not isinstance(payload, list):
                raise SourceUnavailable("unexpected SWEA response")
            rows = [(r["date"], r["value"]) for r in payload]
        elif provider == "ecos":
            payload = json.loads(text)
            if "StatisticSearch" not in payload:
                raise SourceUnavailable("ECOS rejected query or credentials")
            observations = payload["StatisticSearch"]["row"]
            for r in observations:
                if (r["STAT_CODE"] != meta["dataset"] or r["ITEM_CODE1"] != meta["provider_code"]
                        or r["UNIT_NAME"] != "Percent Per Annum" or not re.fullmatch(r"\d{8}", r["TIME"])):
                    raise SourceUnavailable("unexpected ECOS series, daily period, or unit")
            rows = [(pd.to_datetime(r["TIME"], format="%Y%m%d").date().isoformat(), r["DATA_VALUE"])
                    for r in observations]
        elif provider == "webstat_csv":
            reader = csv.DictReader(StringIO(text), delimiter=";")
            code = meta["provider_code"]
            if not re.fullmatch(r"FM\.D\.FR\.EUR\.FR2\.BB\.FRMOYTEC10\.HSTA", code):
                raise SourceUnavailable("unsupported Webstat TEC10 key")
            expected = dict(zip(["FREQ", "REF_AREA", "CURRENCY", "PROVIDER_FM",
                                 "INSTRUMENT_FM", "PROVIDER_FM_ID", "DATA_TYPE_FM"], code.split(".")[1:]))
            expected.update(series_key=code, dataset_id="FM", UNIT="PC", UNIT_MULT="0")
            required = set(expected) | {"time_period", "time_period_start", "time_period_end", "obs_value", "OBS_STATUS"}
            if not required <= set(reader.fieldnames or ()):
                raise SourceUnavailable("unexpected Webstat CSV header")
            rows = []
            for r in reader:
                period = r["time_period"]
                if (any(r[k] != v for k, v in expected.items())
                        or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", period)
                        or r["time_period_start"] != period or r["time_period_end"] != period
                        or r["OBS_STATUS"] not in {"A", "P", "M"}
                        or (r["OBS_STATUS"] == "M" and r["obs_value"].strip() not in _MISSING)):
                    raise SourceUnavailable("unexpected Webstat daily period, metadata, or observation status")
                rows.append((period, r["obs_value"]))
        elif provider == "webstat":
            payload = json.loads(text)
            if not isinstance(payload, list):
                raise SourceUnavailable("Webstat rejected query or API key")
            if any(r["series_key"] != meta["provider_code"] for r in payload):
                raise SourceUnavailable("unexpected Webstat series")
            for r in payload:
                period = r["time_period"]
                if (not re.fullmatch(r"\d{4}-\d{2}-\d{2}", period)
                        or r["time_period_start"][:10] != period
                        or r["time_period_end"][:10] != period
                        or r.get("obs_status") in {"F", "U"}):
                    raise SourceUnavailable("Webstat period must be daily, observed rather than forecast")
            rows = [(r["time_period_start"][:10], r["obs_value"]) for r in payload]
        elif provider == "chinabond":
            parser = _Tables()
            parser.feed(text)
            matches = [t for t in parser.tables if t and t[0][:2] == ["Yield Curve Name", "Date"]]
            if len(matches) != 1 or matches[0][0] != ["Yield Curve Name", "Date", "3M", "6M", "1Y", "3Y", "5Y", "7Y", "10Y", "30Y"]:
                raise SourceUnavailable("unexpected ChinaBond history table")
            table = matches[0]
            column = table[0].index(meta["provider_code"])
            if any(r[0] != "ChinaBond Government Bond Yield Curve" for r in table[1:]):
                raise SourceUnavailable("ChinaBond response contains a non-government curve")
            rows = [(r[1], r[column]) for r in table[1:]]
        elif provider == "sarb":
            payload = json.loads(text)
            if not isinstance(payload, list) or any(meta["bond_label"] not in r["Timeseries"] for r in payload):
                raise SourceUnavailable("unexpected SARB bond")
            rows = [(r["Period"], r["Value"]) for r in payload]
        elif provider == "tesouro":
            reader = csv.DictReader(StringIO(text), delimiter=";")
            required = {"Tipo Titulo", "Data Vencimento", "Data Base", "Taxa Compra Manha"}
            if not required <= set(reader.fieldnames or ()):
                raise SourceUnavailable("unexpected Tesouro daily price/rate CSV")
            maturity = pd.Timestamp(meta["maturity"]).strftime("%d/%m/%Y")
            rows = [(pd.to_datetime(r["Data Base"], format="%d/%m/%Y").date().isoformat(),
                     r["Taxa Compra Manha"]) for r in reader
                    if r["Tipo Titulo"] == meta["provider_code"] and r["Data Vencimento"] == maturity]
        else:
            raise SourceUnavailable(meta.get("unavailable_reason", "daily endpoint unresolved"))
        return _normalize(meta, rows)
    except SourceUnavailable:
        raise
    except (ValueError, KeyError, IndexError, TypeError, ET.ParseError) as exc:
        raise SourceUnavailable(f"invalid {provider} response ({type(exc).__name__})") from None


def _chunks(start: str, end: str):
    """ChinaBond limits a history query to one year; use <=365 inclusive days."""
    current, final = pd.Timestamp(start).date(), pd.Timestamp(end).date()
    while current <= final:
        stop = min(current + timedelta(days=364), final)
        yield current.isoformat(), stop.isoformat()
        current = stop + timedelta(days=1)


def fetch_yield(meta: dict, start: str, end: str, *, timeout: int = 30,
                retries: int = 3, base_sleep: float = 1.0,
                cache: Optional[dict] = None) -> pd.DataFrame:
    """Inclusive range, including historical backfill. Returns only original dates.

    Cache is scoped to one runner invocation, never a shared/persistent data cache.
    It avoids downloading the same multi-maturity official export for each tenor.
    """
    first, last = pd.Timestamp(start), pd.Timestamp(end)
    if first > last:
        raise ValueError("start must not be after end")
    key_env = meta.get("key_env")
    key = os.environ.get(key_env, "") if key_env else ""
    if key_env and not key.strip():
        raise SourceUnavailable(f"missing {key_env}")
    if meta["provider"] == "unavailable":
        raise SourceUnavailable(meta["unavailable_reason"])
    provider = meta["provider"]
    cache = {} if cache is None else cache

    def get(url, params=None, headers=None):
        cache_key = (url, json.dumps(params, sort_keys=True), json.dumps(headers, sort_keys=True))
        if cache_key not in cache:
            try:
                cache[cache_key] = _request(url, params=params, headers=headers, timeout=timeout,
                                           retries=retries, base_sleep=base_sleep).content
            except requests.RequestException:
                # Protect callers even when an alternate transport raises a raw exception.
                raise SourceUnavailable("official source transport failure") from None
        return cache[cache_key]

    url = meta["endpoint"]
    code = meta.get("provider_code")
    if provider == "ecb":
        bodies = [get(url, {"format": "csvdata", "startPeriod": start, "endPeriod": end})]
    elif provider in {"bundesbank", "nbb"}:
        bodies = [get(url, {"startPeriod": start, "endPeriod": end},
                      {"Accept": "application/vnd.sdmx.genericdata+xml;version=2.1"})]
    elif provider == "boe":
        bodies = [get(url, {"csv.x": "yes", "Datefrom": first.strftime("%d/%b/%Y"),
                           "Dateto": last.strftime("%d/%b/%Y"), "SeriesCodes": code,
                           "CSVF": "TN", "UsingCodes": "Y", "VPD": "Y", "VFD": "N"})]
    elif provider == "mof":
        bodies = []
        # The current export contains this month, irrespective of the requested
        # end date. Even a short range within a historical month needs the archive.
        current_month = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize().replace(day=1)
        if first < current_month:
            bodies.append(get(meta["history_endpoint"]))
        bodies.append(get(url))
    elif provider == "riksbank":
        bodies = [get(f"{url}/{code}/{start}/{end}")]
    elif provider == "chinabond":
        bodies = [get(url, {"startDate": a, "endDate": b, "gjqx": "10",
                           "qxId": "hzsylqx", "locale": "en_US"}) for a, b in _chunks(start, end)]
    elif provider == "sarb":
        bodies = [get(f"{url}/{code}/{start}/{end}")]
    elif provider == "snb":
        bodies = [get(url, {"fromDate": start, "toDate": end})]
        if meta.get("current_endpoint"):
            bodies.append(get(meta["current_endpoint"]))
    elif provider == "webstat":
        bodies = [get(url, {"refine": f"series_key:{code}", "order_by": "time_period_start",
                           "where": f"time_period_start >= date'{start}' and time_period_start <= date'{end}'"},
                      {"Authorization": f"Apikey {key}"})]
    elif provider == "ecos":
        bodies = []
        offset = 1
        while True:
            body = get(f"{url}/{key}/json/en/{offset}/{offset+999}/{meta['dataset']}/D/"
                       f"{first.strftime('%Y%m%d')}/{last.strftime('%Y%m%d')}/{code}")
            # Validate each page before trusting its row count. Missing data is not zero.
            parse_response(meta, body)
            page = json.loads(_text(body))["StatisticSearch"]
            bodies.append(body)
            count = len(page["row"])
            total = int(page["list_total_count"])
            if offset - 1 + count >= total:
                break
            if count == 0:
                raise SourceUnavailable("ECOS incomplete pagination")
            offset += count
    else:
        bodies = [get(url)]
    frames = [parse_response(meta, body) for body in bodies]
    df = pd.concat([f for f in frames if not f.empty], ignore_index=True) if any(not f.empty for f in frames) else pd.DataFrame(columns=COLUMNS)
    if df.empty:
        return df
    # Current JGB/SNB export wins on overlap with official historical releases.
    df = df.drop_duplicates(["date", "series_id"], keep="last")
    return df[(df.date >= first) & (df.date <= last)].sort_values("date").reset_index(drop=True)
