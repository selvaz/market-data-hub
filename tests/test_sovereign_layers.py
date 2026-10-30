"""Offline regression tests. No live API calls and no production database."""
from datetime import datetime, timezone
import json
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest
import requests

from market_data_hub import sovereign_refresh as job
from market_data_hub.db.connection import SCHEMA_VERSION, get_conn, migrate
from market_data_hub.db.upsert import upsert
from market_data_hub.sources import sovereign_layers as feeds
from market_data_hub.sources.sovereign_yields import SourceUnavailable

FIX = Path(__file__).parent / 'fixtures' / 'sovereign_layers'
NOW = datetime(2026, 10, 18, 21, tzinfo=timezone.utc)


def spec(sid):
    return next(e for e in job.catalogue()['series'] if e['symbol'] == sid)


@pytest.mark.parametrize('sid,file,last', [
    ('UST_PAR_10Y', 'ust_2026.csv', '2026-10-16'), ('MOF_JGB_10Y', 'mof_current.csv', '2026-10-15'),
    ('BOE_GILT_PAR_10Y', 'boe_10Y_live.csv', '2026-10-14'), ('BOC_10Y', 'boc_10YR.json', '2026-10-15'),
])
def test_official_fixtures(sid, file, last):
    frame = feeds.parse_official(spec(sid), (FIX / file).read_bytes())
    assert not frame.empty
    assert frame.unit.unique().tolist() == ['%']
    assert frame.series_id.unique().tolist() == [sid]
    assert frame.date.max() == pd.Timestamp(last)
    assert not frame.date.duplicated().any()


@pytest.mark.parametrize('sid', ['UST_PAR_10Y', 'MOF_JGB_10Y', 'BOE_GILT_PAR_10Y', 'BOC_10Y'])
def test_wrong_schema_fails(sid):
    with pytest.raises(SourceUnavailable):
        feeds.parse_official(spec(sid), b'<html>Access Denied</html>')


def test_boe_does_not_accept_zero_coupon_or_invent_missing_tenors():
    with pytest.raises(SourceUnavailable):
        feeds.parse_official(spec('BOE_GILT_PAR_10Y'), b'DATE,IUDMNZC\n16 Oct 2026,5.4\n')
    ids = {e['symbol'] for e in job.catalogue()['series']}
    assert 'BOE_GILT_PAR_2Y' not in ids and 'BOE_GILT_PAR_30Y' not in ids
    assert 'BOC_LONG' in ids and 'BOC_30Y' not in ids


def test_weekend_capture_uses_provider_friday():
    data = json.loads((FIX / 'tv_snapshot_minute.json').read_text())
    for row in data['data']:
        cc, t = row['s'][4:6], str(int(row['s'][6:8])) + 'Y'
        frame = feeds.parse_snapshot(spec(f'TV_{cc}_{t}'), row, NOW)
        # The Indonesian 10y was already stale on Friday. Preserve its
        # Thursday source date; a Sunday capture must not relabel it.
        expected = '2026-10-15' if row['s'] == 'TVC:ID10Y' else '2026-10-16'
        assert frame.date.iloc[0] == pd.Timestamp(expected)


@pytest.mark.parametrize('values', [[5, None, 'UTC'], [float('inf'), 1792105200, 'UTC'],
                                  [5, 99999999999999999, 'UTC'], [5, True, 'UTC']])
def test_tv_invalid_fields_rejected(values):
    with pytest.raises(SourceUnavailable):
        feeds.parse_snapshot(spec('TV_US_10Y'), {'s':'TVC:US10Y', 'd':values}, NOW)


def test_sunday_reopened_bar_is_deferred():
    now = datetime(2026, 10, 19, 0, tzinfo=timezone.utc)  # Sunday 20:00 New York
    row = {'s':'TVC:US10Y', 'd':[5, pd.Timestamp('2026-10-18T23:30Z').timestamp(), 'America/New_York']}
    assert feeds.parse_snapshot(spec('TV_US_10Y'), row, now).empty


def test_scan_rate_limit_and_no_retry(monkeypatch):
    response = Mock()
    response.json.return_value = {'data': []}
    post = Mock(return_value=response)
    sleep = Mock()
    monkeypatch.setattr(feeds.requests, 'post', post)
    monkeypatch.setattr(feeds.time, 'monotonic', lambda: 100.)
    monkeypatch.setattr(feeds.time, 'sleep', sleep)
    monkeypatch.setattr(feeds, '_last_request', 100.)
    feeds.scan(['TVC:US10Y'])
    assert post.call_args.kwargs['headers']['User-Agent'] == feeds.USER_AGENT
    sleep.assert_called_once_with(1.)
    post.side_effect = requests.ConnectionError('test')
    with pytest.raises(SourceUnavailable):
        feeds.scan(['TVC:US10Y'])
    assert post.call_count == 2


def test_curve_cache_annual_backfill_and_incremental_filter(monkeypatch):
    request = Mock(return_value=Mock(content=(FIX/'ust_2026.csv').read_bytes()))
    monkeypatch.setattr(feeds, '_request', request)
    cache = {}
    for sid in ['UST_PAR_2Y', 'UST_PAR_10Y']:
        frame = feeds.fetch_official(spec(sid), '2026-10-10', '2026-10-16', cache=cache)
        assert frame.date.min() >= pd.Timestamp('2026-10-10')
        assert frame.date.max() <= pd.Timestamp('2026-10-16')
    assert request.call_count == 1
    assert '/2026/all' in request.call_args.args[0]


def test_failed_treasury_chunk_returns_no_partial_series(monkeypatch):
    request = Mock(side_effect=[Mock(content=(FIX/'ust_1990.csv').read_bytes()),
                               SourceUnavailable('failed year')])
    monkeypatch.setattr(feeds, '_request', request)
    with pytest.raises(SourceUnavailable):
        feeds.fetch_official(spec('UST_PAR_10Y'), '1990-01-01', '1991-12-31')


def test_mof_history_current_and_boc_dates(monkeypatch):
    monkeypatch.setattr(feeds, 'utc_now', lambda: NOW)
    calls = []
    def request(url, **kwargs):
        calls.append((url, kwargs.get('params')))
        name = 'mof_history.csv' if 'historical' in url else 'mof_current.csv'
        return Mock(content=(FIX/name).read_bytes())
    monkeypatch.setattr(feeds, '_request', request)
    frame = feeds.fetch_official(spec('MOF_JGB_10Y'), '1974-01-01', '2026-10-18')
    assert len(calls) == 2 and frame.date.min().year == 1986
    calls.clear()
    monkeypatch.setattr(feeds, '_request', Mock(return_value=Mock(content=(FIX/'boc_2YR.json').read_bytes())))
    frame = feeds.fetch_official(spec('BOC_2Y'), '2026-10-01', '2026-10-16')
    assert frame.date.min() >= pd.Timestamp('2026-10-01')
    assert feeds._request.call_args.kwargs['params'] == {'start_date':'2026-10-01', 'end_date':'2026-10-16'}


@pytest.mark.parametrize('failure', [False, True])
def test_refresh_idempotent_failure_budget_and_no_splice(monkeypatch, tmp_db, failure):
    config = job.catalogue()
    config['series'] = [spec(sid) for sid in ['UST_PAR_10Y', 'TV_US_10Y']]
    config['cross_checks'] = []
    monkeypatch.setattr(job, 'catalogue', lambda: config)
    monkeypatch.setattr(feeds, 'utc_now', lambda: NOW)
    fetch = Mock(return_value=feeds.parse_official(spec('UST_PAR_10Y'), (FIX/'ust_2026.csv').read_bytes()))
    monkeypatch.setattr(feeds, 'fetch_official', fetch)
    scan = Mock(side_effect=SourceUnavailable('scanner changed')) if failure else Mock(
        return_value=json.loads((FIX/'tv_snapshot_minute.json').read_text()))
    monkeypatch.setattr(feeds, 'scan', scan)
    with get_conn(tmp_db) as con:
        # Existing FRED/CB rows are not rewritten or used to fill new IDs.
        con.execute("INSERT INTO macro_series(date,series_id,value,source) VALUES ('2026-10-16','DGS10',99,'fred')")
        first_status = job.refresh(con, {}, 'first', end='2026-10-18')
        rows = con.execute('SELECT date,series_id,value,source FROM macro_series ORDER BY series_id,date').fetchall()
        job.refresh(con, {}, 'second', end='2026-10-18')
        assert con.execute('SELECT date,series_id,value,source FROM macro_series ORDER BY series_id,date').fetchall() == rows
        assert scan.call_count == 1
        assert fetch.call_args.args[1] == '2026-10-09'
        job.refresh(con, {}, 'backfill', end='2026-10-18', backfill=True)
        assert fetch.call_args.args[1] == '1990-01-02'
        assert con.execute("SELECT value FROM macro_series WHERE series_id='DGS10'").fetchone()[0] == 99
        assert con.execute('SELECT count(*) FROM sovereign_series_metadata').fetchone()[0] == 2
        if failure:
            assert first_status == 2
            assert con.execute("SELECT count(*) FROM macro_series WHERE series_id='TV_US_10Y'").fetchone()[0] == 0
            assert con.execute("SELECT status FROM download_log WHERE symbol='TV_US_10Y'").fetchone()[0] == 'unavailable'
        else:
            assert first_status == 0
            assert con.execute("SELECT date FROM macro_series WHERE series_id='TV_US_10Y'").fetchone()[0].isoformat() == '2026-10-16'


def test_divergence_exact_date_threshold_and_report(tmp_db, monkeypatch):
    from make_report import collect, render_html, render_md
    config = job.catalogue()
    config['cross_checks'] = [p for p in config['cross_checks'] if p['official'] == 'MOF_JGB_10Y']
    official = spec('MOF_JGB_10Y')
    market = spec('TV_JP_10Y')
    with get_conn(tmp_db) as con:
        for meta, rows in [(official, [('2026-10-15',3),('2026-10-16',3)]),
                           (market, [('2026-10-16',3.11),('2026-10-17',9)])]:
            upsert(con, 'macro_series', feeds._normalize(meta, rows))
        assert job.cross_check(con, config, 'test', start='2026-10-01', end='2026-10-18') == 1
        assert con.execute('SELECT count(*) FROM sovereign_cross_checks').fetchone()[0] == 1
        d = collect(con)
        assert 'MOF_JGB_10Y' in render_html(d) and 'MOF_JGB_10Y' in render_md(d)
        with monkeypatch.context() as patch:
            patch.setattr(pd.DataFrame, 'to_markdown', Mock(side_effect=ImportError('no tabulate')))
            assert 'MOF_JGB_10Y' in render_md(d)
        config['tolerance_bp_by_tenor']['10Y'] = 11
        assert job.cross_check(con, config, 'repeat', start='2026-10-01', end='2026-10-18') == 0
        assert not con.execute('SELECT flagged FROM sovereign_cross_checks').fetchone()[0]


def test_migration_is_idempotent_and_config_has_no_splice(tmp_db):
    with get_conn(tmp_db) as con:
        con.execute("UPDATE schema_meta SET value='26' WHERE key='schema_version'")
        assert migrate(con) == SCHEMA_VERSION and migrate(con) == SCHEMA_VERSION
    entries = job.catalogue()['series']
    assert len({e['symbol'] for e in entries}) == len(entries)
    assert all(e['unit'] == '%' and e['publication_lag'] and e['convention'] for e in entries)
    assert not any('fallback' in e or 'splice' in e for e in entries)
    pairs = job.catalogue()['cross_checks']
    for p in pairs:
        assert spec(p['official'])['tenor'] == spec(p['market'])['tenor']
        assert 'par' not in spec(p['official'])['convention']


@pytest.mark.parametrize('sid', ['UST_PAR_10Y', 'MOF_JGB_2Y', 'BOC_LONG', 'BOC_10Y'])
def test_reject_noncomparable_cross_check(tmp_db, sid):
    config = job.catalogue()
    config['cross_checks'] = [{'official':sid, 'market':'TV_US_10Y',
                              'comparability_note':'must not be sufficient on its own'}]
    with get_conn(tmp_db) as con, pytest.raises(ValueError, match='not comparable'):
        job.cross_check(con, config, 'test', start='2026-10-01', end='2026-10-18')


@pytest.mark.parametrize('tenor,first,count', [
    ('5Y', '1993-12-01', 8305), ('10Y', '1993-11-01', 8327), ('20Y', '2000-01-04', 6768),
])
def test_boe_backfill_range_and_real_denial(monkeypatch, tenor, first, count):
    meta = spec('BOE_GILT_PAR_' + tenor)
    assert meta['history_start'] == meta['backfill_start'] == first
    request = Mock(return_value=Mock(content=(FIX/f'boe_{tenor}_live.csv').read_bytes()))
    monkeypatch.setattr(feeds, '_request', request)
    frame = feeds.fetch_official(meta, '1900-01-01', '2026-10-18')
    assert len(frame) == count and frame.date.min() == pd.Timestamp(first)
    assert frame.date.max() == pd.Timestamp('2026-10-14')
    assert request.call_args.kwargs['params']['Datefrom'] == pd.Timestamp(first).strftime('%d/%b/%Y')
    assert feeds.fetch_official(meta, '1900-01-01', '1970-01-01').empty
    assert request.call_count == 1
    with pytest.raises(SourceUnavailable):
        feeds.parse_official(spec('BOE_GILT_PAR_10Y'), (FIX/'boe_10Y.csv').read_bytes())


def test_runner_daily_path_includes_new_layers(monkeypatch, tmp_db):
    from market_data_hub import runner
    from market_data_hub.config_loader import get_settings
    call = Mock(return_value=0)
    monkeypatch.setattr(job, 'refresh', call)
    assert 'sovereign_layers' in runner._DEFAULT_SOURCES
    assert runner.run(sources=['sovereign_layers'], db_path=tmp_db) == 0
    assert call.call_args.args[1] == get_settings()
    assert call.call_args.kwargs['backfill'] is False
    assert runner.run(mode='backfill', sources=['sovereign_layers'], db_path=tmp_db) == 0
    assert call.call_args.kwargs['backfill'] is True


def test_catalog_exposes_layer_and_convention(tmp_db):
    from market_data_hub import catalog
    for sid in ['UST_PAR_10Y', 'TV_US_10Y']:
        card = catalog.describe_series(sid, db_path=tmp_db)
        assert card['domain'] == 'macro'
        assert card['source_class'] == spec(sid)['source_class']
        assert card['convention'] and card['publication_lag']
    assert 'TV_JP_10Y' in set(catalog.search('TV_JP_10Y').key)


def test_failed_layers_remain_visible_in_coverage(tmp_db):
    from market_data_hub.coverage.report import rebuild_coverage
    with get_conn(tmp_db) as con:
        job._log(con, 'test', 'BOE_GILT_PAR_10Y', 'unavailable', 'recorded HTTP 403')
        rebuild_coverage(con, 'test')
        assert con.execute("SELECT status FROM coverage_report WHERE symbol='BOE_GILT_PAR_10Y'").fetchone()[0] == 'unavailable'
        assert con.execute("SELECT count(*) FROM coverage_report WHERE symbol LIKE 'TV_%'").fetchone()[0] == 106
