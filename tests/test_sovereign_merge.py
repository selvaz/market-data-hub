"""F05/F08 merge contracts; mocked collectors and temporary databases only."""
from copy import deepcopy
from datetime import date

import pytest

import run_backfill
import run_daily
import validate_config
from market_data_hub import catalog, config_loader, runner
from market_data_hub.coverage import report
from market_data_hub.db.connection import get_conn


def test_combined_catalog_retains_every_declared_series(tmp_db):
    fred = config_loader.get_fred_series()
    acm = config_loader.get_nyfed_acm_series()
    sovereign = config_loader.get_sovereign_yields()
    assert config_loader.get_macro_series() == fred + acm
    declared = {e['symbol'] for e in fred + acm + sovereign}
    entries = catalog.list_macro_series(with_coverage=False)
    assert set(entries.series_id) == declared and entries.series_id.is_unique
    dataset = next(d for d in catalog.list_datasets() if d['domain'] == 'macro')
    assert dataset['n_series'] == len(declared) == 149
    assert dataset['frequency'] == 'D/W/M/Q/A'
    assert all(name in dataset['description'] for name in ('FRED', 'NY Fed ACM', 'sovereign'))
    for sid in ('NYFED_ACM_TP10_MONTHLY', 'CB_ES_10Y', 'ITA_BTP10_MOT', 'DEU_BUND10_FA'):
        assert catalog.describe_series(sid, db_path=tmp_db)['domain'] == 'macro'
        assert sid in set(catalog.search(sid).key)


def test_coverage_uses_latest_nonnull_publisher_for_acm_and_sovereign(tmp_db, monkeypatch):
    sovereign = next(e for e in config_loader.get_sovereign_yields() if e['symbol'] == 'CB_ES_10Y')
    acm = config_loader.get_nyfed_acm_series()[0]
    monkeypatch.setattr(report, 'get_sovereign_yields', lambda: [sovereign])
    with get_conn(tmp_db) as con:
        for entry in (acm, sovereign):
            for day, source in ((1, 'old-publisher'), (2, None), (3, entry['source']), (4, None)):
                con.execute('INSERT INTO macro_series (date,series_id,value,source) VALUES (?,?,?,?)',
                            [date(2026, 10, day), entry['symbol'], 1.0, source])
        assert report.rebuild_coverage(con, 'merge-coverage') == 2
        publishers = dict(con.execute('SELECT symbol,source FROM coverage_report').fetchall())
    assert publishers == {e['symbol']: e['source'] for e in (acm, sovereign)}


@pytest.mark.parametrize('status', [0, 1, 2, 3])
def test_runner_dispatches_both_collectors_without_changing_exit_aggregation(tmp_db, monkeypatch, status):
    calls = []
    monkeypatch.setattr(runner, 'get_settings', lambda: {
        'backfill_start': {'nyfed_acm': '1961-01-01', 'sovereign_yields': '2023-01-01'},
        'live': {'enabled': False},
    })
    def acm(*args, **kwargs):
        calls.append(('nyfed_acm', kwargs))
    def sovereign(*args, **kwargs):
        calls.append(('sovereign_yields', kwargs))
        return status
    monkeypatch.setattr(runner, 'run_nyfed_acm', acm)
    monkeypatch.setattr(runner, 'run_sovereign_yields', sovereign)
    monkeypatch.setattr(runner, 'rebuild_coverage', lambda *args: 0)
    monkeypatch.setattr(runner, 'rebuild_macro_panel_coverage', lambda *args: 0)
    assert {'nyfed_acm', 'sovereign_yields'} <= set(runner._DEFAULT_SOURCES)
    result = runner.run(mode='backfill', sources=['nyfed_acm', 'sovereign_yields'],
                        end='2026-10-09', db_path=tmp_db)
    assert result == (0 if status == 3 else status)
    assert calls == [
        ('nyfed_acm', {'start_override': '1961-01-01', 'end': '2026-10-09'}),
        ('sovereign_yields', {'start_override': '2023-01-01', 'end': '2026-10-09'}),
    ]
    assert runner.run(sources=['sovereign_yields'], end='2026-10-09', db_path=tmp_db) == status


@pytest.mark.parametrize('entrypoint', [run_daily, run_backfill])
def test_both_cli_source_choices_preserve_degraded_exit(monkeypatch, entrypoint):
    calls = []
    monkeypatch.setattr('sys.argv', [entrypoint.__name__, '--sources', 'nyfed_acm', 'sovereign_yields'])
    def run(**kwargs):
        calls.append(kwargs)
        return 2
    monkeypatch.setattr(entrypoint, 'run', run)
    assert entrypoint.main() == 2
    assert calls[0]['sources'] == ['nyfed_acm', 'sovereign_yields']


def test_backfill_defaults_keep_both_sources(monkeypatch):
    calls = []
    monkeypatch.setattr('sys.argv', ['run_backfill'])
    monkeypatch.setattr(run_backfill, 'run', lambda **kwargs: calls.append(kwargs) or 0)
    assert run_backfill.main() == 0
    assert set(calls[0]['sources']) == set(runner._DEFAULT_SOURCES)


def test_validation_reports_both_counts_and_checks_both_contracts(monkeypatch, capsys):
    assert validate_config.main() == 0
    output = capsys.readouterr().out
    assert 'fred=106' in output and 'nyfed_acm=1' in output and 'sovereign_yields=42' in output
    load = validate_config._y
    def invalid(name):
        config = deepcopy(load(name))
        if name == 'macro_series.yaml':
            config['nyfed_acm'][0]['frequency'] = 'D'
        elif name == 'sovereign_yields.yaml':
            config['series'][0]['max_lag_days'] = 30
        return config
    monkeypatch.setattr(validate_config, '_y', invalid)
    errors = validate_config.validate()
    assert any('NY Fed ACM chart CSV must have frequency M' in e for e in errors)
    assert any('sovereign_yields.yaml' in e and 'max_lag_days must be 3' in e for e in errors)
