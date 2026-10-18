"""Two-layer refresh, persistent daily snapshot budget, and explicit comparisons."""
from __future__ import annotations

import json
import math
from datetime import timedelta
from typing import Optional

import pandas as pd

from market_data_hub.config_loader import _load_yaml
from market_data_hub.db.upsert import log_run, record_vintage, upsert
from market_data_hub.sources import sovereign_layers as feeds
from market_data_hub.sources.sovereign_yields import SourceUnavailable
from market_data_hub.coverage.stalled_detector import is_stalled


def catalogue() -> dict:
    return _load_yaml('sovereign_layers.yaml')


def _log(con, run_id, sid, status, error=None, rows=0, source='sovereign_layers'):
    log_run(con, run_id=run_id, started_at=feeds.utc_now(), source=source,
            symbol=sid, rows_added=rows, rows_updated=0, status=status,
            error_msg=error, duration_sec=0)
    print(f'SOVEREIGN {sid}: {status}' + (f' ({error})' if error else ''), flush=True)


def cross_check(con, config: dict, run_id: str, *, start: str, end: str) -> int:
    """Recompute exact-date intersections, including lagged official arrivals."""
    flags = 0
    specs = {m['symbol']: m for m in config['series']}
    for pair in config.get('cross_checks', []):
        if not pair.get('comparability_note'):
            raise ValueError('cross-check requires documented comparable conventions')
        official, market = specs[pair['official']], specs[pair['market']]
        allowed = {'constant maturity YTM, semiannual compounding', 'benchmark bond YTM'}
        if (official['source_class'] != 'official' or market['source_class'] != 'market:tradingview'
                or official['tenor'] != market['tenor'] or official['tenor'] == 'LONG'
                or official['country'] != market['country']
                or official['convention'] not in allowed
                or market['convention'] != 'benchmark bond YTM (vendor methodology not specified)'):
            raise ValueError('cross-check conventions/tenors are not comparable')
        tenor_tolerance = config.get('tolerance_bp_by_tenor', {}).get(official['tenor'],
                                                                    config.get('tolerance_bp', 10))
        tolerance = float(pair.get('tolerance_bp', tenor_tolerance))
        if not math.isfinite(tolerance) or tolerance < 0:
            raise ValueError('negative sovereign tolerance')
        rows = con.execute(
            'SELECT o.date, abs(o.value-m.value)*100 FROM macro_series o '
            'JOIN macro_series m ON o.date=m.date '
            'WHERE o.series_id=? AND m.series_id=? AND o.date BETWEEN ? AND ?',
            [pair['official'], pair['market'], start, end]).fetchall()
        for day, difference in rows:
            flagged = difference > tolerance + 1e-9
            con.execute('INSERT OR REPLACE INTO sovereign_cross_checks VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                        [day, pair['official'], pair['market'], difference, tolerance,
                         flagged, pair['comparability_note'], run_id])
            if flagged:
                flags += 1
                _log(con, run_id, f"{pair['official']}/{pair['market']}", 'divergence',
                     f'{day}: {difference:.3f} bp > {tolerance:g} bp; {pair["comparability_note"]}',
                     source='sovereign_cross_check')
    return flags


def _record_failure(con, run_id: str, sid: str, exc: Exception,
                    source: str = 'sovereign_layers') -> None:
    """A broken logging write must not break the source-isolation boundary."""
    status = 'unavailable' if isinstance(exc, SourceUnavailable) else 'error'
    error = str(exc) if isinstance(exc, SourceUnavailable) else f'{type(exc).__name__}: sovereign collector fault'
    try:
        _log(con, run_id, sid, status, error, source=source)
    except Exception:
        print(f'SOVEREIGN {sid}: {status} ({error}; download_log unavailable)', flush=True)


def refresh(con, cfg: dict, run_id: str, *, start_override: Optional[str] = None,
            end: Optional[str] = None, backfill: bool = False) -> int:
    config = catalogue()
    now = feeds.utc_now()
    end = end or now.date().isoformat()
    cache = {}
    degraded = False
    entries = [e for e in config['series'] if e.get('enabled', True)]

    def register(meta):
        con.execute('INSERT OR REPLACE INTO sovereign_series_metadata VALUES (?, ?)',
                    [meta['symbol'], json.dumps(meta, sort_keys=True)])

    for meta in [e for e in entries if e['source_class'] == 'official']:
        sid = meta['symbol']
        try:
            register(meta)
            last = con.execute('SELECT max(date) FROM macro_series WHERE series_id=?', [sid]).fetchone()[0]
            earliest = max(meta['history_start'], meta.get('backfill_start', meta['history_start']))
            start = max(earliest, start_override or
                        ((last-timedelta(days=7)).isoformat() if last and not backfill else earliest))
            if start > end:
                _log(con, run_id, sid, 'noop', 'requested range precedes series start')
                continue
            http = cfg.get('http', {})
            frame = feeds.fetch_official(meta, start, end, cache=cache,
                                         timeout=http.get('timeout', 30),
                                         retries=http.get('max_retries', 3),
                                         base_sleep=http.get('retry_base_sleep', 1))
            if not frame.empty:
                con.execute('BEGIN TRANSACTION')
                try:
                    added, _ = upsert(con, 'macro_series', frame, outer_txn=True)
                    record_vintage(con, 'macro_series', frame, now.date().isoformat(), run_id=run_id)
                    con.execute('COMMIT')
                except Exception:
                    con.execute('ROLLBACK')
                    raise
            else:
                added = 0
            latest = max([d for d in [last, frame.date.max().date() if not frame.empty else None]
                          if d is not None], default=None)
            stale = is_stalled(latest, 'D', as_of=pd.Timestamp(end).date(),
                               max_lag_days=3, lag_calendar='business')
            degraded |= stale
            _log(con, run_id, sid, 'stalled' if stale else 'ok' if not frame.empty else 'empty',
                 'official observations exceed three business days' if stale else None, rows=added)
        except Exception as exc:
            degraded = True
            _record_failure(con, run_id, sid, exc)

    # Match the existing 09:00 / 13:15 Pacific invocations year-round. The early
    # run logs a deferral without spending the daily request budget. All markets
    # use one Pacific capture day; their national date may already be tomorrow.
    capture_at = feeds.utc_now()
    capture_now = pd.Timestamp(capture_at).tz_convert(config.get('capture_timezone', 'America/Los_Angeles'))
    capture_day = capture_now.date()
    capture_after = config.get('capture_after', '13:15')
    eligible = []
    for meta in [e for e in entries if e['source_class'] == 'market:tradingview']:
        sid = meta['symbol']
        try:
            register(meta)
            if end < capture_day.isoformat():
                _log(con, run_id, sid, 'noop', 'historical end cannot fetch a current snapshot')
                continue
            if capture_now.strftime('%H:%M') < capture_after:
                _log(con, run_id, sid, 'deferred',
                     f"daily snapshot at {capture_after} {config.get('capture_timezone', 'America/Los_Angeles')}")
                continue
            captured = con.execute(
                'SELECT status FROM sovereign_snapshot_attempts WHERE series_id=? AND capture_date=?',
                [sid, capture_day]).fetchone()
            if captured and captured[0] in {'unavailable', 'error', 'stalled', 'attempted'}:
                degraded = True
            if not captured:
                con.execute('INSERT INTO sovereign_snapshot_attempts VALUES (?, ?, ?, ?)',
                            [sid, capture_day, capture_at, 'attempted'])
                eligible.append(meta)
        except Exception as exc:
            degraded = True
            _record_failure(con, run_id, sid, exc)
    if eligible:
        scan_error = None
        try:
            payload = feeds.scan([m['provider_code'] for m in eligible],
                                 timeout=cfg.get('http', {}).get('timeout', 30),
                                 min_interval=config.get('min_interval_seconds', 1))
            scanned_at = feeds.utc_now()
            rows = {r['s']: r for r in payload['data']}
            if len(rows) != len(payload['data']):
                raise SourceUnavailable('duplicate TradingView tickers')
        except Exception as exc:
            scan_error = exc
            rows = {}
        for meta in eligible:
            sid = meta['symbol']
            added = 0
            try:
                if scan_error is not None:
                    raise scan_error
                frame = feeds.parse_snapshot(meta, rows.get(meta['provider_code'], {}), scanned_at)
                if not frame.empty:
                    day = frame.date.iloc[0]
                    exists = con.execute('SELECT 1 FROM macro_series WHERE series_id=? AND date=?',
                                         [sid, day]).fetchone()
                    if not exists:
                        added, _ = upsert(con, 'macro_series', frame)
                        record_vintage(con, 'macro_series', frame, now.date().isoformat(), run_id=run_id)
                status, error = 'ok' if not frame.empty else 'deferred', None
                latest = con.execute('SELECT max(date) FROM macro_series WHERE series_id=?', [sid]).fetchone()[0]
                if status == 'ok' and is_stalled(latest, 'D', as_of=pd.Timestamp(end).date(),
                                                 max_lag_days=3, lag_calendar='business'):
                    status, error = 'stalled', 'TradingView bar exceeds three business days'
                    degraded = True
            except Exception as exc:
                degraded = True
                status = 'unavailable' if isinstance(exc, SourceUnavailable) else 'error'
                error = str(exc) if isinstance(exc, SourceUnavailable) else f'{type(exc).__name__}: sovereign collector fault'
            try:
                con.execute('UPDATE sovereign_snapshot_attempts SET status=? WHERE series_id=? AND capture_date=?',
                            [status, sid, capture_day])
                _log(con, run_id, sid, status, error, rows=added)
            except Exception as exc:
                degraded = True
                _record_failure(con, run_id, sid, exc)
    try:
        cross_check(con, config, run_id,
                    start=start_override or (pd.Timestamp(end)-pd.Timedelta(days=40)).date().isoformat(), end=end)
    except Exception as exc:
        degraded = True
        _record_failure(con, run_id, '*', exc, source='sovereign_cross_check')
    return 2 if degraded else 0
