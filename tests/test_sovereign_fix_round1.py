"""Review regressions: publisher floors, daily schedule and source isolation."""
from copy import deepcopy
from datetime import datetime
import json
from unittest.mock import Mock

import pandas as pd
import pytest

from market_data_hub import runner, sovereign_refresh as job
from market_data_hub.db.connection import get_conn
from market_data_hub.db.upsert import upsert
from market_data_hub.sources import sovereign_layers as feeds, sovereign_yields as official
from test_sovereign_layers import FIX, NOW, spec


@pytest.mark.parametrize('sid,file', [
    ('UST_PAR_10Y', 'ust_2026.csv'), ('MOF_JGB_10Y', 'mof_current.csv'),
    ('BOE_GILT_PAR_10Y', 'boe_10Y_live.csv'), ('BOC_10Y', 'boc_10YR.json'),
])
def test_every_official_request_uses_hub_user_agent(monkeypatch, sid, file):
    reply = Mock(status_code=200, content=(FIX/file).read_bytes())
    session = Mock()
    session.get.return_value = reply
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(official.requests, 'Session', Mock(return_value=session))
    monkeypatch.setattr(feeds, 'utc_now', lambda: NOW)
    frame = feeds.fetch_official(spec(sid), '2026-10-01', '2026-10-18')
    assert not frame.empty and session.get.call_count >= 1
    for call in session.get.call_args_list:
        assert call.kwargs['headers']['User-Agent'] == official.USER_AGENT == feeds.USER_AGENT


@pytest.mark.parametrize('sid', ['UST_PAR_10Y', 'MOF_JGB_10Y', 'BOE_GILT_PAR_10Y', 'BOC_10Y'])
def test_pre_source_history_range_makes_no_request(monkeypatch, sid):
    request = Mock(side_effect=AssertionError('pre-source request'))
    monkeypatch.setattr(feeds, '_request', request)
    assert feeds.fetch_official(spec(sid), '1800-01-01', '1899-12-31').empty
    request.assert_not_called()


def test_live_boe_history_loads_then_refreshes_without_permanent_degradation(monkeypatch, tmp_db):
    config = job.catalogue()
    config['series'] = [e for e in config['series'] if e['provider'] == 'boe']
    config['cross_checks'] = []
    by_code = {m['provider_code']: m for m in config['series']}
    def replay(url, *, params, **kwargs):
        meta = by_code[params['SeriesCodes']]
        return Mock(content=(FIX/f"boe_{meta['tenor']}_live.csv").read_bytes())
    request = Mock(side_effect=replay)
    monkeypatch.setattr(job, 'catalogue', lambda: config)
    monkeypatch.setattr(feeds, 'utc_now', lambda: NOW)
    monkeypatch.setattr(feeds, '_request', request)
    with get_conn(tmp_db) as con:
        assert job.refresh(con, {}, 'boe-backfill') == 0
        assert con.execute('SELECT count(*) FROM macro_series').fetchone()[0] == 23400
        for call in request.call_args_list:
            meta = by_code[call.kwargs['params']['SeriesCodes']]
            assert call.kwargs['params']['Datefrom'] == pd.Timestamp(meta['history_start']).strftime('%d/%b/%Y')
        request.reset_mock()
        assert job.refresh(con, {}, 'boe-incremental') == 0
        assert con.execute('SELECT count(*) FROM macro_series').fetchone()[0] == 23400
        assert request.call_count == 3
        assert {c.kwargs['params']['Datefrom'] for c in request.call_args_list} == {'07/Oct/2026'}
        assert con.execute("SELECT count(*) FROM download_log WHERE status IN ('error','unavailable','stalled')").fetchone()[0] == 0


@pytest.mark.parametrize('morning,afternoon', [
    ('2026-10-19T16:00Z', '2026-10-19T20:15Z'),  # 09:00 / 13:15 PDT
    ('2026-12-21T17:00Z', '2026-12-21T21:15Z'),  # 09:00 / 13:15 PST
])
def test_existing_daily_invocations_capture_all_markets(monkeypatch, tmp_db, morning, afternoon):
    config = job.catalogue()
    config['series'] = [e for e in config['series'] if e['provider'] == 'tradingview']
    config['cross_checks'] = []
    clock = {'now': pd.Timestamp(morning).to_pydatetime()}
    payload = json.loads((FIX/'tv_snapshot_minute.json').read_text())
    def scan(tickers, **kwargs):
        assert set(tickers) == {e['provider_code'] for e in config['series']}
        result = deepcopy(payload)
        target_day = pd.Timestamp(clock['now']).tz_convert('America/Los_Angeles').date()
        for row in result['data']:
            observed = pd.Timestamp(row['d'][1], unit='s', tz='UTC').tz_convert(row['d'][2])
            shifted = observed + pd.Timedelta(days=(target_day-observed.date()).days)
            row['d'][1] = min(shifted.timestamp(), clock['now'].timestamp()-60)
        return result
    request = Mock(side_effect=scan)
    monkeypatch.setattr(job, 'catalogue', lambda: config)
    monkeypatch.setattr(feeds, 'utc_now', lambda: clock['now'])
    monkeypatch.setattr(feeds, 'scan', request)
    with get_conn(tmp_db) as con:
        assert job.refresh(con, {}, 'early') == 0
        request.assert_not_called()
        assert con.execute('SELECT count(*) FROM sovereign_snapshot_attempts').fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM download_log WHERE status='deferred'").fetchone()[0] == 106
        clock['now'] = pd.Timestamp(afternoon).to_pydatetime()
        assert job.refresh(con, {}, 'daily') == 0
        assert con.execute('SELECT count(*) FROM macro_series').fetchone()[0] == 106
        assert con.execute('SELECT count(*) FROM sovereign_snapshot_attempts').fetchone()[0] == 106
        assert con.execute('SELECT min(date), max(date) FROM macro_series').fetchone() == (
            clock['now'].date(), clock['now'].date())
        # Asia is already on the next national date; none is skipped for that.
        assert pd.Timestamp(clock['now']).tz_convert('Asia/Tokyo').date() > clock['now'].date()
        assert job.refresh(con, {}, 'repeat') == 0
        assert request.call_count == 1
        assert con.execute('SELECT count(*) FROM macro_series').fetchone()[0] == 106


def test_sunday_reopening_does_not_degrade_job(monkeypatch, tmp_db):
    config = job.catalogue()
    config['series'] = [spec('TV_US_10Y')]
    config['cross_checks'] = []
    now = datetime.fromisoformat('2026-10-19T00:00:00+00:00')
    monkeypatch.setattr(job, 'catalogue', lambda: config)
    monkeypatch.setattr(feeds, 'utc_now', lambda: now)
    monkeypatch.setattr(feeds, 'scan', lambda *a, **k: {'data': [
        {'s':'TVC:US10Y', 'd':[5, pd.Timestamp('2026-10-18T23:30Z').timestamp(), 'America/New_York']}]})
    with get_conn(tmp_db) as con:
        assert job.refresh(con, {}, 'sunday') == 0
        assert con.execute('SELECT count(*) FROM macro_series').fetchone()[0] == 0
        assert con.execute('SELECT status FROM download_log').fetchone()[0] == 'deferred'


def test_capture_uses_current_clock_after_official_and_scanner_latency(monkeypatch, tmp_db):
    config = job.catalogue()
    config['series'] = [spec('UST_PAR_10Y'), spec('TV_US_10Y')]
    config['cross_checks'] = []
    clock = {'now': pd.Timestamp('2026-10-19T16:00Z').to_pydatetime()}
    def fetch(meta, *args, **kwargs):
        clock['now'] = pd.Timestamp('2026-10-19T20:15Z').to_pydatetime()
        return feeds._normalize(meta, [('2026-10-19', 3)])
    def scan(*args, **kwargs):
        clock['now'] = pd.Timestamp('2026-10-19T20:17Z').to_pydatetime()
        return {'data': [{'s':'TVC:US10Y', 'd': [5, clock['now'].timestamp(), 'America/New_York']}]}
    monkeypatch.setattr(job, 'catalogue', lambda: config)
    monkeypatch.setattr(feeds, 'utc_now', lambda: clock['now'])
    monkeypatch.setattr(feeds, 'fetch_official', fetch)
    monkeypatch.setattr(feeds, 'scan', scan)
    with get_conn(tmp_db) as con:
        assert job.refresh(con, {}, 'latency') == 0
        assert con.execute("SELECT date FROM macro_series WHERE series_id='TV_US_10Y'").fetchone()[0].isoformat() == '2026-10-19'


@pytest.mark.parametrize('failure', ['official', 'official_write', 'tradingview', 'whole_step', 'log_write'])
def test_unexpected_failure_keeps_remaining_daily_steps_running(monkeypatch, tmp_db, failure):
    config = job.catalogue()
    config['series'] = [spec(s) for s in ['UST_PAR_10Y', 'BOC_10Y', 'TV_US_10Y']]
    config['cross_checks'] = []
    monkeypatch.setattr(job, 'catalogue', lambda: config)
    monkeypatch.setattr(feeds, 'utc_now', lambda: NOW)
    def fetch(meta, *args, **kwargs):
        if failure == 'official' and meta['symbol'] == 'UST_PAR_10Y':
            raise RuntimeError('injected source failure')
        return feeds._normalize(meta, [('2026-10-16', 3)])
    monkeypatch.setattr(feeds, 'fetch_official', fetch)
    scan = Mock(return_value=json.loads((FIX/'tv_snapshot_minute.json').read_text()))
    if failure == 'tradingview':
        scan.side_effect = ValueError('unexpected scanner failure')
    monkeypatch.setattr(feeds, 'scan', scan)
    if failure == 'official_write':
        real_upsert = job.upsert
        def write(con, table, frame, **kwargs):
            if frame.series_id.iloc[0] == 'UST_PAR_10Y':
                raise RuntimeError('injected database statement failure')
            return real_upsert(con, table, frame, **kwargs)
        monkeypatch.setattr(job, 'upsert', write)
    if failure in {'whole_step', 'log_write'}:
        monkeypatch.setattr(job, 'refresh', Mock(side_effect=KeyError('unexpected step failure')))
    if failure == 'log_write':
        monkeypatch.setattr(runner, 'log_run', Mock(side_effect=RuntimeError('log failure')))
    calls = []
    monkeypatch.setattr(runner, 'get_settings', lambda: {
        'backfill_start': {}, 'live': {'enabled': True}})
    monkeypatch.setattr(runner, 'get_countries', lambda: [])
    def step(name, result=None):
        def call(*args, **kwargs):
            calls.append(name)
            return result
        return call
    monkeypatch.setattr(runner, 'run_sovereign_yields', step('old', 0))
    for attr in ['run_binance', 'run_macro_panel', 'run_factors', 'run_live']:
        monkeypatch.setattr(runner, attr, step(attr))
    monkeypatch.setattr(runner, 'rebuild_coverage', step('coverage', 0))
    monkeypatch.setattr(runner, 'rebuild_macro_panel_coverage', step('macro_coverage', 0))
    result = runner.run(sources=['sovereign_yields', 'binance', 'macro_panel', 'factors'],
                        end='2026-10-18', db_path=tmp_db)
    assert result == 2
    assert calls == ['old', 'run_binance', 'run_macro_panel', 'run_factors', 'run_live',
                     'coverage', 'macro_coverage']
    if failure != 'log_write':
        with get_conn(tmp_db) as con:
            assert con.execute("SELECT count(*) FROM download_log WHERE status='error'").fetchone()[0] >= 1
            if failure in {'official', 'official_write'}:
                # A failed series cannot prevent the next official or TV row.
                assert set(con.execute('SELECT series_id FROM macro_series').fetch_df().series_id) == {
                    'BOC_10Y', 'TV_US_10Y'}


def test_bad_market_row_does_not_stop_other_market(monkeypatch, tmp_db):
    config = job.catalogue()
    config['series'] = [spec(s) for s in ['TV_US_10Y', 'TV_JP_10Y']]
    config['cross_checks'] = []
    monkeypatch.setattr(job, 'catalogue', lambda: config)
    monkeypatch.setattr(feeds, 'utc_now', lambda: NOW)
    monkeypatch.setattr(feeds, 'scan', lambda *a, **k: json.loads((FIX/'tv_snapshot_minute.json').read_text()))
    parse = feeds.parse_snapshot
    def fail_one(meta, row, now):
        if meta['symbol'] == 'TV_US_10Y':
            raise ArithmeticError('unexpected parser failure')
        return parse(meta, row, now)
    monkeypatch.setattr(feeds, 'parse_snapshot', fail_one)
    with get_conn(tmp_db) as con:
        assert job.refresh(con, {}, 'fault') == 2
        assert con.execute('SELECT series_id FROM macro_series').fetchone()[0] == 'TV_JP_10Y'


def test_cross_check_uses_tenor_tolerances_and_pair_override(tmp_db):
    config = job.catalogue()
    config['cross_checks'] = [p for p in config['cross_checks'] if p['official'].startswith('MOF_')]
    with get_conn(tmp_db) as con:
        for p in config['cross_checks']:
            for sid, value in [(p['official'], 3), (p['market'], 3.08)]:
                upsert(con, 'macro_series', feeds._normalize(spec(sid), [('2026-10-16', value)]))
        assert job.cross_check(con, config, 'tenors', start='2026-10-16', end='2026-10-16') == 2
        rows = con.execute('SELECT official_id,tolerance_bp,flagged FROM sovereign_cross_checks').fetchall()
        assert {sid: (tol, flag) for sid, tol, flag in rows} == {
            'MOF_JGB_2Y': (5, True), 'MOF_JGB_5Y': (7.5, True),
            'MOF_JGB_10Y': (10, False), 'MOF_JGB_30Y': (15, False)}
        next(p for p in config['cross_checks'] if p['official'] == 'MOF_JGB_2Y')['tolerance_bp'] = 9
        assert job.cross_check(con, config, 'override', start='2026-10-16', end='2026-10-16') == 1
