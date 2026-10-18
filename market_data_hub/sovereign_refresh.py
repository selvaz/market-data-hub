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
        tolerance = float(pair.get('tolerance_bp', config.get('tolerance_bp', 10)))
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


def refresh(con, cfg: dict, run_id: str, *, start_override: Optional[str] = None,
            end: Optional[str] = None, backfill: bool = False) -> int:
    config = catalogue()
    now = feeds.utc_now()
    end = end or now.date().isoformat()
    cache = {}
    degraded = False
    entries = config['series']
    for meta in entries:
        con.execute('INSERT OR REPLACE INTO sovereign_series_metadata VALUES (?, ?)',
                    [meta['symbol'], json.dumps(meta, sort_keys=True)])
    for meta in [e for e in entries if e['source_class'] == 'official']:
        sid = meta['symbol']
        last = con.execute('SELECT max(date) FROM macro_series WHERE series_id=?', [sid]).fetchone()[0]
        earliest = meta.get('backfill_start', meta['history_start'])
        start = start_override or (max(earliest, (last-timedelta(days=7)).isoformat())
                                   if last and not backfill else earliest)
        if start > end:
            continue
        try:
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
        except SourceUnavailable as exc:
            degraded = True
            _log(con, run_id, sid, 'unavailable', str(exc))

    # This log is persistent across processes. The runner's existing DB write
    # lock serializes this claim and snapshot writes. Even failures get one
    # attempt per eligible symbol per local capture day.
    eligible = []
    for meta in [e for e in entries if e['source_class'] == 'market:tradingview']:
        local = pd.Timestamp(now).tz_convert(meta['timezone'])
        if end < local.date().isoformat():
            continue  # A historical --end cannot fetch a historical snapshot.
        if local.weekday() < 5 and local.strftime('%H:%M') < meta['close_after']:
            continue
        captured = con.execute('SELECT status FROM sovereign_snapshot_attempts WHERE series_id=? AND capture_date=?',
                               [meta['symbol'], local.date()]).fetchone()
        if captured and captured[0] in {'unavailable', 'stalled', 'attempted'}:
            degraded = True
        if not captured:
            eligible.append(meta)
            con.execute('INSERT INTO sovereign_snapshot_attempts VALUES (?, ?, ?, ?)',
                        [meta['symbol'], local.date(), now, 'attempted'])
    if eligible:
        try:
            payload = feeds.scan([m['provider_code'] for m in eligible],
                                 timeout=cfg.get('http', {}).get('timeout', 30),
                                 min_interval=config.get('min_interval_seconds', 1))
            rows = {r['s']: r for r in payload['data']}
            if len(rows) != len(payload['data']):
                raise SourceUnavailable('duplicate TradingView tickers')
        except (SourceUnavailable, KeyError, TypeError):
            rows = {}
        for meta in eligible:
            try:
                frame = feeds.parse_snapshot(meta, rows.get(meta['provider_code'], {}), now)
                # A snapshot is immutable for a trading date; reruns on Sunday
                # cannot rewrite Friday or create Sunday observations.
                if not frame.empty:
                    day = frame.date.iloc[0]
                    exists = con.execute('SELECT 1 FROM macro_series WHERE series_id=? AND date=?',
                                         [meta['symbol'], day]).fetchone()
                    if not exists:
                        upsert(con, 'macro_series', frame)
                        record_vintage(con, 'macro_series', frame, now.date().isoformat(), run_id=run_id)
                status, error = 'ok' if not frame.empty else 'deferred', None
            except SourceUnavailable as exc:
                degraded = True
                status, error = 'unavailable', str(exc)
            con.execute('UPDATE sovereign_snapshot_attempts SET status=? WHERE series_id=? AND capture_date=?',
                        [status, meta['symbol'], pd.Timestamp(now).tz_convert(meta['timezone']).date()])
            latest = con.execute('SELECT max(date) FROM macro_series WHERE series_id=?',
                                 [meta['symbol']]).fetchone()[0]
            if status == 'ok' and is_stalled(latest, 'D', as_of=pd.Timestamp(end).date(),
                                             max_lag_days=3, lag_calendar='business'):
                status, error = 'stalled', 'TradingView bar exceeds three business days'
                degraded = True
                con.execute('UPDATE sovereign_snapshot_attempts SET status=? WHERE series_id=? AND capture_date=?',
                            [status, meta['symbol'], pd.Timestamp(now).tz_convert(meta['timezone']).date()])
            _log(con, run_id, meta['symbol'], status, error)
    cross_check(con, config, run_id, start=start_override or (pd.Timestamp(end)-pd.Timedelta(days=40)).date().isoformat(),
                end=end)
    return 2 if degraded else 0
