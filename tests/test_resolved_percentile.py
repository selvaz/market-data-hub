from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from market_data_hub import reader, resolved_rates


@pytest.fixture
def percentile_fixture(tmp_path, monkeypatch):
    path = str(tmp_path / 'percentile.duckdb')
    con = duckdb.connect(path)
    con.execute('CREATE TABLE macro_series (date DATE, series_id VARCHAR, value DOUBLE)')
    sources = [
        {'series_id': 'DAILY', 'role': 'primary', 'convention': 'bond',
         'frequency': 'D', 'max_age_business_days': 3},
        {'series_id': 'IRLTLT01DEM156N', 'role': 'fallback', 'convention': 'monthly',
         'frequency': 'M', 'max_age_business_days': 90},
    ]
    spec = {'concept': 'DE_10Y', 'sources': sources}
    monkeypatch.setattr(resolved_rates, 'load_resolved_rates',
                        lambda: ({'tolerance_bp': 10}, {'DE_10Y': spec}))
    yield path, con, spec
    con.close()


def insert(con, rows):
    con.executemany('INSERT INTO macro_series VALUES (?, ?, ?)', rows)


def test_basis_estimated_and_window_cutoff(percentile_fixture):
    path, con, _ = percentile_fixture
    rows = []
    for i, stamp in enumerate(pd.date_range('2024-01-01', periods=24, freq='MS')):
        rows.extend([(stamp.date(), 'IRLTLT01DEM156N', float(i)),
                     (stamp.date(), 'DAILY', float(i) + 0.5)])
    rows += [(date(2025, 12, 31), 'DAILY', 24.5),
             (date(2026, 1, 1), 'IRLTLT01DEM156N', 1000.0)]
    insert(con, rows)
    con.close()
    result = reader.read_resolved_percentile('DE_10Y', as_of='2025-12-31', window_years=1, db_path=path)
    assert result['status'] == 'ok' and result['basis_bp'] == pytest.approx(100)
    assert result['basis_month'] == '2025-12' and result['basis_series'] == {'DAILY': 2}
    assert result['overlap_months'] == 12 and result['months_used'] == 12
    assert result['first_month'] == '2025-01' and result['last_month'] == '2025-12'
    assert result['oecd_last_month'] == '2025-12' and result['percentile'] == 100
    assert result['current_series_id'] == result['basis_series_id'] == 'DAILY'


def test_basis_not_estimated_without_common_month(percentile_fixture):
    path, con, _ = percentile_fixture
    rows = []
    for stamp in pd.date_range('2025-01-01', periods=11, freq='MS'):
        rows.append((stamp.date(), 'IRLTLT01DEM156N', 1.0))
    rows.append((date(2025, 12, 31), 'DAILY', 2.0))
    insert(con, rows)
    con.close()
    result = reader.read_resolved_percentile('DE_10Y', as_of='2025-12-31', db_path=path)
    assert result['status'] == 'basis_not_estimated' and result['basis_bp'] is None
    assert result['overlap_months'] == 0 and result['adjusted_value'] == 2.0
    assert result['basis_month'] is None and result['basis_series'] is None
    assert result['percentile'] == 100 and result['basis_reason']


@pytest.mark.parametrize('months,expected', [(35, 'unavailable'), (36, 'basis_not_estimated')])
def test_daily_fallback_requires_36_months(percentile_fixture, months, expected):
    path, con, spec = percentile_fixture
    spec['sources'].pop()
    stamps = pd.date_range(end='2025-12-01', periods=months, freq='MS')
    rows = [(stamp.date(), 'DAILY', float(i)) for i, stamp in enumerate(stamps)]
    rows.append((date(2025, 12, 31), 'DAILY', 100.0))
    insert(con, rows)
    con.close()
    result = reader.read_resolved_percentile('DE_10Y', as_of='2025-12-31', db_path=path)
    assert result['status'] == expected and result['months_used'] == months
    assert result['history_series_id'] == 'DAILY'
    assert result['history_method'] == 'single daily source monthly means'
    if months < 36:
        assert result['percentile'] is None and '36' in result['reason']
    else:
        assert result['percentile'] == 100 and result['basis_reason']


def test_explicit_swiss_and_chinese_chains():
    _, table = resolved_rates.load_resolved_rates()
    assert table['CH_10Y']['sources'][0]['series_id'] == 'CB_CH_10Y'
    assert table['CN_10Y']['sources'][0]['series_id'] == 'CB_CN_10Y'
    assert next(s for s in table['CH_2Y']['sources'] if s['series_id'] == 'CB_CH_2Y')['role'] == 'history_only'


def test_all_other_official_series_are_accounted_for():
    from market_data_hub.config_loader import _load_yaml

    _, table = resolved_rates.load_resolved_rates()
    included = {source['series_id'] for entry in table.values() for source in entry['sources']}
    docs = (Path(__file__).resolve().parents[1] / 'docs/method/resolved-rates.md').read_text(encoding='utf-8')
    prefixes = ('CB_', 'BOE_GILT_PAR_', 'MOF_JGB_', 'BOC_', 'UST_PAR_')
    inventory = [entry['symbol'] for name in ('sovereign_yields.yaml', 'sovereign_layers.yaml')
                 for entry in _load_yaml(name)['series'] if entry['symbol'].startswith(prefixes)]
    assert all(sid in included or f'`{sid}`' in docs for sid in inventory)


def test_basis_uses_composed_history_when_current_has_no_overlap(percentile_fixture):
    path, con, spec = percentile_fixture
    spec['sources'].insert(1, {'series_id': 'SECONDARY', 'role': 'secondary',
                               'convention': 'vendor', 'frequency': 'D',
                               'max_age_business_days': 3})
    rows = []
    for stamp in pd.date_range('2025-01-01', periods=11, freq='MS'):
        rows.extend([(stamp.date(), 'IRLTLT01DEM156N', 1.0), (stamp.date(), 'DAILY', 1.25)])
    rows.append((date(2025, 12, 31), 'SECONDARY', 2.0))
    insert(con, rows)
    con.close()
    result = reader.read_resolved_percentile('DE_10Y', as_of='2025-12-31', db_path=path)
    assert result['current_series_id'] == 'SECONDARY' and result['basis_series_id'] == 'DAILY'
    assert result['status'] == 'ok' and result['basis_bp'] == pytest.approx(25)
    assert result['basis_month'] == '2025-11' and result['basis_series'] == {'DAILY': 1}


def test_composed_history_uses_latest_month_even_when_current_source_is_absent(percentile_fixture):
    path, con, spec = percentile_fixture
    spec['sources'].insert(1, {'series_id': 'SECONDARY', 'role': 'secondary',
                               'convention': 'vendor', 'frequency': 'D',
                               'max_age_business_days': 3})
    rows = []
    for stamp in pd.date_range('2025-01-01', periods=12, freq='MS'):
        rows.extend([(stamp.date(), 'IRLTLT01DEM156N', 1.0), (stamp.date(), 'DAILY', 1.25)])
    rows += [(date(2025, 10, 15), 'SECONDARY', 1.6),
             (date(2025, 12, 31), 'SECONDARY', 2.0)]
    # The current month also overlaps; make a separate source point before
    # the cutoff month by dropping the December OECD observation.
    rows = [r for r in rows if not (r[0] == date(2025, 12, 1) and r[1] == 'IRLTLT01DEM156N')]
    insert(con, rows)
    con.close()
    result = reader.read_resolved_percentile('DE_10Y', as_of='2025-12-31', db_path=path)
    assert result['status'] == 'ok' and result['basis_series'] == {'DAILY': 1}
    assert result['basis_month'] == '2025-11' and result['overlap_months'] == 11
    assert result['basis_bp'] == pytest.approx(25) and result['basis_reason'] is None


def test_italian_history_levels_with_composed_daily_sources(tmp_path, monkeypatch):
    path = str(tmp_path / 'italy.duckdb')
    con = duckdb.connect(path)
    con.execute('CREATE TABLE macro_series (date DATE, series_id VARCHAR, value DOUBLE)')
    rows = [
        ('2026-08-01', 'IRLTLT01ITM156N', 3.0),
        ('2026-08-03', 'CB_IT_10Y', 3.1),
        ('2026-08-04', 'CB_IT_10Y', 3.2),
        ('2026-08-04', 'ITA_BTP10_MOT', 3.4),
        ('2026-08-05', 'CB_IT_10Y', 3.2),
        ('2026-09-30', 'CB_IT_10Y', 3.3),
        ('2026-10-09', 'ITA_BTP10_MOT', 3.5),
        ('2026-11-06', 'TV_IT_10Y', 4.0),
    ]
    con.executemany('INSERT INTO macro_series VALUES (?, ?, ?)', rows)
    con.close()
    sources = [dict(series_id=sid, role=role, frequency=frequency, convention='yield',
                    max_age_business_days=90)
               for sid, role, frequency in [
                   ('ITA_BTP10_MOT', 'primary', 'D'), ('TV_IT_10Y', 'secondary', 'D'),
                   ('CB_IT_10Y', 'history_only', 'D'), ('IRLTLT01ITM156N', 'fallback', 'M')]]
    spec = {'concept': 'IT_10Y', 'sources': sources}
    monkeypatch.setattr(resolved_rates, 'load_resolved_rates',
                        lambda: ({'tolerance_bp': 10}, {'IT_10Y': spec}))
    result = reader.read_resolved_percentile('IT_10Y', as_of='2026-11-06', db_path=path)
    assert result['current_series_id'] == 'TV_IT_10Y' and result['basis_month'] == '2026-08'
    assert result['basis_series'] == {'CB_IT_10Y': 2, 'ITA_BTP10_MOT': 1}
    assert result['basis_series_id'] is None
    assert result['basis_bp'] == pytest.approx(((3.1 + 3.4 + 3.2) / 3 - 3.0) * 100)
    assert result['status'] == 'ok'


def test_longest_daily_source_is_single_history(percentile_fixture):
    path, con, spec = percentile_fixture
    spec['sources'].pop()
    spec['sources'].append({'series_id': 'LONG', 'role': 'history_only', 'convention': 'bond',
                            'frequency': 'D', 'max_age_business_days': 3})
    rows = [(stamp.date(), 'LONG', 1.0)
            for stamp in pd.date_range('2022-01-01', periods=40, freq='MS')]
    rows += [(date(2025, 12, 31), 'DAILY', 2.0)]
    insert(con, rows)
    con.close()
    result = reader.read_resolved_percentile('DE_10Y', as_of='2025-12-31', db_path=path)
    assert result['history_series_id'] == 'LONG' and result['months_used'] == 40
    assert result['status'] == 'basis_not_estimated' and result['percentile'] == 100
