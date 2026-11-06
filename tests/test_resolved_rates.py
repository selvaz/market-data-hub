from copy import deepcopy
from datetime import date

import duckdb
import pytest

from market_data_hub import reader, resolved_rates


@pytest.mark.parametrize('observation,cutoff,expected', [
    ('2026-11-03', '2026-11-03', 0),
    ('2026-11-03', '2026-11-04', 1),
    ('2026-11-03', '2026-11-07', 3),
    ('2026-11-06', '2026-11-09', 1),
    ('2026-11-07', '2026-11-09', 1),
])
def test_business_day_age(observation, cutoff, expected):
    assert reader._resolved_business_day_age(date.fromisoformat(observation),
                                              date.fromisoformat(cutoff)) == expected


def test_business_day_age_rejects_future_observation():
    with pytest.raises(ValueError, match='precedes observation'):
        reader._resolved_business_day_age(date(2026, 11, 4), date(2026, 11, 3))


@pytest.fixture
def db(tmp_path):
    path = tmp_path / 'rates.duckdb'
    con = duckdb.connect(str(path))
    con.execute('CREATE TABLE macro_series (date DATE, series_id VARCHAR, value DOUBLE)')
    con.executemany('INSERT INTO macro_series VALUES (?, ?, ?)', [
        ('2026-11-02', 'A', 2.0), ('2026-11-05', 'B', 2.5),
        ('2026-11-05', 'C', 2.51), ('2026-11-09', 'A', 9.0),
        ('2026-09-01', 'MONTHLY', 2.5),
    ])
    con.close()
    return str(path)


@pytest.fixture
def table(monkeypatch):
    sources = [dict(series_id=s, max_age_business_days=3, convention='yield', role=role)
               for s, role in [('A', 'primary'), ('B', 'secondary'), ('C', 'secondary')]]
    spec = {'concept': 'DE_10Y', 'sources': sources}
    config = {'tolerance_bp': 10, 'concepts': [spec]}
    monkeypatch.setattr(resolved_rates, 'load_resolved_rates', lambda: (config, {'DE_10Y': spec}))
    return spec


def test_stale_or_missing_primary_falls_back(db, table):
    result = reader.read_resolved_rate('DE_10Y', as_of='2026-11-06', db_path=db)
    assert result['series_id'] == 'B' and result['status'] == 'fallback:B'
    assert result['age_business_days'] == 1 and 'stale' in result['reason']
    table['sources'][0]['series_id'] = 'MISSING'
    result = reader.read_resolved_rate('DE_10Y', as_of='2026-11-06', db_path=db)
    assert result['series_id'] == 'B' and 'missing' in result['reason']


def test_unavailable_and_history_only(db, table):
    for source in table['sources']:
        source['role'] = 'history_only'
    result = reader.read_resolved_rate('DE_10Y', as_of='2026-11-06', db_path=db)
    assert result['status'] == 'unavailable' and result['value'] is None


def test_majority_and_dispute(db, table):
    table['sources'][0]['max_age_business_days'] = 5
    result = reader.read_resolved_rate('DE_10Y', as_of='2026-11-06', db_path=db)
    assert result['series_id'] == 'B' and result['status'] == 'fallback:B'
    assert len(result['other_fresh_candidates']) == 2 and 'majority' in result['reason']
    table['sources'].pop()
    result = reader.read_resolved_rate('DE_10Y', as_of='2026-11-06', db_path=db)
    assert result['series_id'] == 'A' and result['status'] == 'disputed'


def test_fallback_never_votes_against_fresh_daily(db, table):
    table['sources'][0]['max_age_business_days'] = 5
    table['sources'][1]['role'] = 'fallback'
    table['sources'][1]['series_id'] = 'MONTHLY'
    table['sources'][1]['frequency'] = 'M'
    table['sources'][1]['max_age_business_days'] = 90
    table['sources'][2]['role'] = 'history_only'
    result = reader.read_resolved_rate('DE_10Y', as_of='2026-11-06', db_path=db)
    assert result['status'] == 'primary' and result['series_id'] == 'A'
    assert result['alternatives'][0]['basis_bp'] == pytest.approx(50)
    assert result['alternatives'][0]['voted'] is False
    table['sources'][0]['max_age_business_days'] = 0
    assert reader.read_resolved_rate('DE_10Y', as_of='2026-11-06', db_path=db)['series_id'] == 'MONTHLY'


def test_same_convention_fallback_does_not_vote(db, table):
    table['sources'][0]['max_age_business_days'] = 5
    table['sources'][1]['role'] = 'fallback'
    result = reader.read_resolved_rate('DE_10Y', as_of='2026-11-06', db_path=db)
    assert result['status'] == 'primary'
    assert all(not item['voted'] for item in result['alternatives'])


def test_different_conventions_are_alternatives_unless_pair_tolerance(db, table):
    table['sources'][0]['max_age_business_days'] = 5
    table['sources'][0]['convention'] = 'constant maturity YTM'
    table['sources'][1]['convention'] = 'benchmark bond YTM'
    table['sources'][2]['role'] = 'history_only'
    result = reader.read_resolved_rate('DE_10Y', as_of='2026-11-06', db_path=db)
    assert result['status'] == 'primary' and result['alternatives'][0]['basis_bp'] == pytest.approx(50)
    assert not result['alternatives'][0]['voted']
    table['pair_tolerances'] = [{'series_ids': ['A', 'B'], 'tolerance_bp': 10, 'note': 'reviewed'}]
    assert reader.read_resolved_rate('DE_10Y', as_of='2026-11-06', db_path=db)['status'] == 'disputed'
    table['pair_tolerances'][0]['tolerance_bp'] = 60
    assert reader.read_resolved_rate('DE_10Y', as_of='2026-11-06', db_path=db)['status'] == 'primary'


def test_as_of_and_history_never_splices(db, table):
    table['sources'][0]['max_age_business_days'] = 5
    result = reader.read_resolved_rate('DE_10Y', as_of='2026-11-04', db_path=db)
    assert result['value'] == 2.0 and result['series_id'] == 'A'
    frame = reader.read_resolved_history('DE_10Y', as_of='2026-11-06', db_path=db)
    assert frame.series_id.tolist() == ['A'] and frame.value.tolist() == [2.0]
    frame = reader.read_resolved_history('DE_10Y', series_id='B', as_of='2026-11-06', db_path=db)
    assert frame.series_id.tolist() == ['B']
    with pytest.raises(ValueError):
        reader.read_resolved_history('DE_10Y', series_id='OTHER', db_path=db)


def test_weekend_cutoff_keeps_source_at_age_limit(db, table):
    table['sources'][0]['series_id'] = 'B'
    table['sources'][0]['max_age_business_days'] = 1
    table['sources'][1:]= []
    result = reader.read_resolved_rate('DE_10Y', as_of='2026-11-07', db_path=db)
    assert result['status'] == 'primary' and result['age_business_days'] == 1


def test_missing_database_is_not_created(tmp_path):
    path = tmp_path / 'missing.duckdb'
    with pytest.raises(FileNotFoundError):
        reader.read_resolved_rate('DE_10Y', db_path=str(path))
    assert not path.exists()


def test_validator_errors():
    config, _ = resolved_rates.load_resolved_rates()
    known = {'A': {}, 'B': {}}
    source = {'series_id': 'A', 'role': 'primary', 'convention': 'bond YTM',
              'max_age_business_days': 3}
    simple = {'tolerance_bp': 10, 'concepts': [{'concept': 'DE_10Y', 'sources': [source]}]}
    assert resolved_rates.validate_resolved_rates(simple, known)
    duplicate = deepcopy(simple)
    duplicate['concepts'].append(deepcopy(duplicate['concepts'][0]))
    with pytest.raises(ValueError, match='duplicate concept'):
        resolved_rates.validate_resolved_rates(duplicate, known)
    invalid = deepcopy(simple)
    invalid['concepts'][0]['sources'][0]['series_id'] = 'UNKNOWN'
    with pytest.raises(ValueError, match='unknown series'):
        resolved_rates.validate_resolved_rates(invalid, known)
    invalid = deepcopy(simple)
    invalid['concepts'][0]['sources'].append(dict(source, series_id='B', role='secondary', convention='par'))
    with pytest.raises(ValueError, match='comparability note'):
        resolved_rates.validate_resolved_rates(invalid, known)
    invalid['concepts'][0]['sources'][1].update(convention='fitted zero-coupon', comparability_note='note')
    with pytest.raises(ValueError, match='incompatible'):
        resolved_rates.validate_resolved_rates(invalid, known)
    invalid = deepcopy(simple)
    invalid['concepts'][0]['pair_tolerances'] = [{'series_ids': ['A', 'B'], 'tolerance_bp': 10, 'note': 'reviewed'}]
    with pytest.raises(ValueError, match='invalid pair'):
        resolved_rates.validate_resolved_rates(invalid, known)
    assert len(config['concepts']) >= 100


def test_duplicate_yaml_keys_are_rejected(tmp_path):
    path = tmp_path / 'duplicate.yaml'
    path.write_text('concepts: []\nconcepts: []\n', encoding='utf-8')
    with pytest.raises(ValueError, match='duplicate configuration key'):
        resolved_rates.load_resolved_rates(path)


def test_curve_cannot_be_mislabeled_as_observed():
    config = {'tolerance_bp': 10, 'concepts': [{'concept': 'DE_10Y', 'sources': [
        {'series_id': 'X', 'role': 'primary', 'convention': 'bond YTM',
         'max_age_business_days': 3}]}]}
    with pytest.raises(ValueError, match='misrepresents'):
        resolved_rates.validate_resolved_rates(config, {'X': {'name': '10y zero-coupon yield'}})
