"""Issuer histories and a separate, dated TradingView snapshot. Never splice."""
from __future__ import annotations

import csv
import json
import math
import threading
import time
from datetime import datetime, timezone
from io import StringIO
from typing import Optional

import pandas as pd
import requests

from .sovereign_yields import SourceUnavailable, _normalize, _request, parse_response

TREASURY = 'https://home.treasury.gov/resource-center/data-chart-center/interest-rates'
SCANNER = 'https://scanner.tradingview.com/global/scan'
_gate = threading.Lock()
_last_request = 0.0


def parse_official(meta: dict, body: bytes) -> pd.DataFrame:
    """Reject changed schemas, missing series, non-daily dates and non-finite data."""
    try:
        if meta['provider'] == 'mof':
            return parse_response(meta, body)
        if meta['provider'] == 'boe':
            reader = csv.DictReader(StringIO(body.decode('utf-8-sig')))
            code = meta['provider_code']
            if not reader.fieldnames or reader.fieldnames[0] != 'DATE' or code not in reader.fieldnames:
                raise SourceUnavailable('unexpected BoE CSV series')
            rows = [(datetime.strptime(r['DATE'], '%d %b %Y').date().isoformat(), r[code])
                    for r in reader]
        elif meta['provider'] == 'ust':
            reader = csv.DictReader(StringIO(body.decode('utf-8-sig')))
            if not reader.fieldnames or reader.fieldnames[0] != 'Date':
                raise SourceUnavailable('unexpected Treasury CSV header')
            code = meta['provider_code']
            # New tenors did not exist in earlier annual exports.
            if code not in reader.fieldnames:
                return _normalize(meta, [])
            rows = [(datetime.strptime(r['Date'], '%m/%d/%Y').date().isoformat(), r[code])
                    for r in reader]
        elif meta['provider'] == 'boc':
            data = json.loads(body)
            code = meta['provider_code']
            detail = data['seriesDetail'][code]
            if detail['dimension']['key'] != 'd':
                raise SourceUnavailable('BoC is not a daily series')
            metadata = detail.get('metadata', {})
            if metadata.get('unit', 'Percent') != 'Percent':
                raise SourceUnavailable('BoC unit changed')
            rows = [(r['d'], r.get(code, {}).get('v')) for r in data['observations']]
        else:
            raise SourceUnavailable('unrecognized official provider')
        return _normalize(meta, rows)
    except (ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise SourceUnavailable(f'changed official response ({type(exc).__name__})') from None


def fetch_official(meta: dict, start: str, end: str, *, cache: Optional[dict] = None,
                   timeout: int = 30, retries: int = 3, base_sleep: float = 1) -> pd.DataFrame:
    """Full source history on first run; seven-day revision tail thereafter.

    Shared response cache downloads each curve once, even with many tenors.
    A failed chunk raises before returning any rows for this series.
    """
    cache = {} if cache is None else cache

    def get(url, params=None):
        key = (url, json.dumps(params, sort_keys=True))
        if key not in cache:
            try:
                cache[key] = _request(url, params=params, timeout=timeout, retries=retries,
                                      base_sleep=base_sleep).content
            except SourceUnavailable as exc:
                cache[key] = exc
        if isinstance(cache[key], Exception):
            raise cache[key]
        return cache[key]

    provider = meta['provider']
    if provider == 'ust':
        bodies = [get(f'{TREASURY}/daily-treasury-rates.csv/{year}/all',
                      {'type': 'daily_treasury_yield_curve', 'field_tdr_date_value': year,
                       '_format': 'csv'})
                  for year in range(max(1990, pd.Timestamp(start).year), pd.Timestamp(end).year + 1)]
    elif provider == 'mof':
        month = pd.Timestamp(utc_now()).tz_convert('UTC').tz_localize(None).normalize().replace(day=1)
        bodies = ([get(meta['history_endpoint'])] if pd.Timestamp(start) < month else [])
        if pd.Timestamp(end) >= month:
            bodies.append(get(meta['endpoint']))
    elif provider == 'boe':
        bodies = [get(meta['endpoint'], {'csv.x': 'yes',
                  'Datefrom': pd.Timestamp(start).strftime('%d/%b/%Y'),
                  'Dateto': pd.Timestamp(end).strftime('%d/%b/%Y'),
                  'SeriesCodes': meta['provider_code'], 'CSVF': 'TN', 'UsingCodes': 'Y',
                  'VPD': 'Y', 'VFD': 'N'})]
    elif provider == 'boc':
        bodies = [get(f"{meta['endpoint']}/{meta['provider_code']}/json",
                      {'start_date': start, 'end_date': end})]
    else:
        raise SourceUnavailable('unrecognized official provider')
    frames = [parse_official(meta, body) for body in bodies]
    if not frames:
        return _normalize(meta, [])
    frame = pd.concat(frames, ignore_index=True).drop_duplicates('date', keep='last')
    return frame[(frame.date >= pd.Timestamp(start)) & (frame.date <= pd.Timestamp(end))]


def scan(tickers: list[str], *, timeout: int = 30, min_interval: float = 1) -> dict:
    """One batched request, no aggressive retries; requests matches hub transport."""
    global _last_request
    with _gate:
        delay = max(1, min_interval) - (time.monotonic() - _last_request)
        if delay > 0:
            time.sleep(delay)
        _last_request = time.monotonic()
        try:
            r = requests.post(SCANNER, json={'symbols': {'tickers': tickers},
                              'columns': ['close', 'time|1', 'timezone']}, timeout=timeout,
                              headers={'User-Agent': 'market-data-hub/sovereign-yields'})
            r.raise_for_status()
            data: dict = r.json()
            if not isinstance(data.get('data'), list):
                raise ValueError('missing scanner data')
            return data
        except (requests.RequestException, ValueError, AttributeError):
            raise SourceUnavailable('TradingView scanner failed or schema changed') from None


def parse_snapshot(meta: dict, row: dict, now: datetime) -> pd.DataFrame:
    """Use the provider's latest bar date, never the capture date.

    Use the latest minute bar (time|1): the daily bar's time is its OPEN,
    which can be the preceding evening for US instruments.
    The scanner's timezone defines its session date (even for overseas bonds).
    The national timezone separately controls the after-close capture gate.
    No timestamp means no write; never guess Friday by subtracting weekdays.
    """
    try:
        if row['s'] != meta['provider_code'] or len(row['d']) != 3:
            raise ValueError('ticker/columns changed')
        value, stamp, quote_zone = row['d']
        if isinstance(value, bool) or not math.isfinite(float(value)):
            raise ValueError('invalid close')
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or not math.isfinite(stamp):
            raise ValueError('missing bar timestamp')
        local_now = pd.Timestamp(now).tz_convert(meta['timezone'])
        if quote_zone != meta['timestamp_timezone']:
            raise ValueError('provider session timezone changed')
        bar = pd.Timestamp(stamp, unit='s', tz='UTC').tz_convert(quote_zone)
        if bar > pd.Timestamp(now) or bar.weekday() >= 5:
            raise ValueError('invalid trading date')
        if bar.date() == local_now.date() and local_now.strftime('%H:%M') < meta['close_after']:
            return _normalize(meta, [])
        return _normalize(meta, [(bar.date().isoformat(), value)])
    except (ValueError, TypeError, KeyError, OverflowError):
        raise SourceUnavailable('TradingView missing/invalid close or trading timestamp') from None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
