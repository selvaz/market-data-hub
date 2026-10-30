"""Final PR #109 regressions: Toronto schedule, write ledger and old reports."""
from unittest.mock import Mock

import pandas as pd
import pytest

import make_report
from market_data_hub import sovereign_refresh as job
from market_data_hub.db.connection import SCHEMA_VERSION, get_conn
from market_data_hub.sources import sovereign_layers as feeds
from test_sovereign_layers import spec


def snapshot(meta, now):
    return {'s': meta['provider_code'],
            'd': [4.5, now.timestamp()-60, meta['timestamp_timezone']]}


@pytest.mark.parametrize('capture', ['2026-10-19T20:15Z', '2026-12-21T21:15Z'])
def test_toronto_captures_at_existing_1315_pacific_invocation(monkeypatch, tmp_db, capture):
    config = job.catalogue()
    config['series'] = [m for m in config['series'] if m['symbol'].startswith('TV_CA_')]
    config['cross_checks'] = []
    now = pd.Timestamp(capture)
    assert now.tz_convert(config['capture_timezone']).strftime('%H:%M') == config['capture_after'] == '13:15'
    assert now.tz_convert('America/Toronto').strftime('%H:%M') == '16:15'
    assert {m['tenor'] for m in config['series']} == {'2Y', '5Y', '10Y', '30Y'}
    scan = Mock(return_value={'data': [snapshot(m, now) for m in config['series']]})
    monkeypatch.setattr(job, 'catalogue', lambda: config)
    monkeypatch.setattr(feeds, 'utc_now', now.to_pydatetime)
    monkeypatch.setattr(feeds, 'scan', scan)
    with get_conn(tmp_db) as con:
        assert job.refresh(con, {}, 'toronto') == 0
        scan.assert_called_once()
        assert set(scan.call_args.args[0]) == {m['provider_code'] for m in config['series']}
        assert con.execute('SELECT series_id, date FROM macro_series ORDER BY series_id').fetchall() == [
            (sid, now.date()) for sid in sorted(m['symbol'] for m in config['series'])]
        assert con.execute("SELECT count(*) FROM sovereign_snapshot_attempts WHERE status='ok'").fetchone()[0] == 4


def test_sovereign_ledger_preserves_initial_incremental_and_repeat_counts(monkeypatch, tmp_db):
    config = job.catalogue()
    official, market = spec('UST_PAR_10Y'), spec('TV_US_10Y')
    config.update(series=[official, market], cross_checks=[])
    clock = {'now': pd.Timestamp('2026-10-19T20:15Z')}
    observations = [('2026-10-16', 4.0), ('2026-10-19', 4.1)]
    monkeypatch.setattr(job, 'catalogue', lambda: config)
    monkeypatch.setattr(feeds, 'utc_now', lambda: clock['now'].to_pydatetime())
    monkeypatch.setattr(feeds, 'fetch_official', lambda *a, **k: feeds._normalize(official, observations))
    scan = Mock(side_effect=lambda *a, **k: {'data': [snapshot(market, clock['now'])]})
    monkeypatch.setattr(feeds, 'scan', scan)

    def assert_counts(con, run_id, expected):
        assert con.execute(
            'SELECT symbol, rows_added, rows_updated FROM download_log WHERE run_id=? ORDER BY symbol',
            [run_id]).fetchall() == expected
        report = make_report.collect(con)
        assert report['last_run']['run_id'] == run_id
        assert report['last_run']['added'] == sum(row[1] for row in expected)
        assert report['last_run']['updated'] == sum(row[2] for row in expected)

    with get_conn(tmp_db) as con:
        assert job.refresh(con, {}, 'initial') == 0
        assert_counts(con, 'initial', [('TV_US_10Y', 1, 0), ('UST_PAR_10Y', 2, 0)])
        clock['now'] += pd.Timedelta(days=1)
        observations[1] = ('2026-10-19', 4.2)  # revision plus a new observation
        observations.append(('2026-10-20', 4.3))
        assert job.refresh(con, {}, 'incremental') == 0
        assert_counts(con, 'incremental', [('TV_US_10Y', 1, 0), ('UST_PAR_10Y', 1, 2)])
        assert con.execute(
            "SELECT value FROM macro_series WHERE series_id='UST_PAR_10Y' AND date='2026-10-19'").fetchone()[0] == 4.2
        clock['now'] += pd.Timedelta(minutes=1)
        assert job.refresh(con, {}, 'repeat') == 0
        assert_counts(con, 'repeat', [('UST_PAR_10Y', 0, 3)])
        assert scan.call_count == 2
        assert con.execute('SELECT count(*) FROM macro_series').fetchone()[0] == 5


@pytest.mark.parametrize('version', [26, 27])
def test_standalone_read_only_report_supports_pre_v27_database(monkeypatch, tmp_db, tmp_path, version):
    with get_conn(tmp_db) as con:
        if version == 26:
            for table in ('sovereign_cross_checks', 'sovereign_snapshot_attempts', 'sovereign_series_metadata'):
                con.execute(f'DROP TABLE {table}')
            con.execute("UPDATE schema_meta SET value='26' WHERE key='schema_version'")
        else:
            con.execute("INSERT INTO sovereign_cross_checks VALUES "
                        "('2026-10-19', 'BOC_10Y', 'TV_CA_10Y', 20, 10, true, 'benchmark YTM', 'test')")
        job._log(con, 'legacy-report', 'UST_PAR_10Y', 'ok', rows=7, updated=2)
        before = con.execute('SHOW TABLES').fetchall()
    monkeypatch.setattr(make_report.sys, 'argv', ['make_report.py', '--db', tmp_db])
    monkeypatch.setattr(make_report, 'REPORT_DIR', tmp_path/'reports')
    assert make_report.main() == 0
    for suffix in ('html', 'md'):
        content = next((tmp_path/'reports').glob(f'*.{suffix}')).read_text(encoding='utf-8')
        assert 'legacy-report' in content
        assert 'Sovereign yield divergence' in content
        assert ('BOC_10Y' in content) == (version == 27)
    with get_conn(tmp_db, read_only=True) as con:
        assert con.execute('SHOW TABLES').fetchall() == before
        # The 27 case is a current database: it carries whatever version is current.
        expected = version if version == 26 else SCHEMA_VERSION
        assert con.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0] == str(expected)
        report = make_report.collect(con)
        assert report['last_run']['added'] == 7
        assert report['last_run']['updated'] == 2
        assert len(report['sovereign_divergences']) == (1 if version == 27 else 0)
