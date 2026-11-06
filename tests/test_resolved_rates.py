from copy import deepcopy
from datetime import date

import duckdb
import pandas as pd
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


def add_observation(db, day, sid, value):
    con = duckdb.connect(db)
    try:
        con.execute('INSERT INTO macro_series VALUES (?, ?, ?)', [day, sid, value])
    finally:
        con.close()


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
    add_observation(db, '2026-11-05', 'A', 2.0)
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
    assert result['alternatives'][0]['basis_bp'] is None
    assert result['alternatives'][0]['voted'] is False
    table['sources'][0]['max_age_business_days'] = 0
    assert reader.read_resolved_rate('DE_10Y', as_of='2026-11-06', db_path=db)['series_id'] == 'MONTHLY'


def test_same_convention_fallback_does_not_vote(db, table):
    add_observation(db, '2026-11-05', 'A', 2.0)
    table['sources'][0]['max_age_business_days'] = 5
    table['sources'][1]['role'] = 'fallback'
    result = reader.read_resolved_rate('DE_10Y', as_of='2026-11-06', db_path=db)
    assert result['status'] == 'primary'
    assert all(not item['voted'] for item in result['alternatives'])


def test_different_conventions_do_not_vote_even_with_pair_tolerance(db, table):
    add_observation(db, '2026-11-05', 'A', 2.0)
    table['sources'][0]['max_age_business_days'] = 5
    table['sources'][0]['convention'] = 'constant maturity YTM'
    table['sources'][1]['convention'] = 'benchmark bond YTM'
    table['sources'][2]['role'] = 'history_only'
    result = reader.read_resolved_rate('DE_10Y', as_of='2026-11-06', db_path=db)
    assert result['status'] == 'primary' and result['alternatives'][0]['basis_bp'] == pytest.approx(50)
    assert not result['alternatives'][0]['voted']
    table['pair_tolerances'] = [{'series_ids': ['A', 'B'], 'tolerance_bp': 10, 'note': 'reviewed'}]
    assert reader.read_resolved_rate('DE_10Y', as_of='2026-11-06', db_path=db)['status'] == 'primary'
    table['sources'][1]['convention'] = 'constant maturity YTM'
    assert reader.read_resolved_rate('DE_10Y', as_of='2026-11-06', db_path=db)['status'] == 'disputed'
    table['pair_tolerances'][0]['tolerance_bp'] = 60
    assert reader.read_resolved_rate('DE_10Y', as_of='2026-11-06', db_path=db)['status'] == 'primary'


def test_as_of_and_single_source_history(db, table):
    table['sources'][0]['max_age_business_days'] = 5
    result = reader.read_resolved_rate('DE_10Y', as_of='2026-11-04', db_path=db)
    assert result['value'] == 2.0 and result['series_id'] == 'A'
    frame = reader.read_resolved_history('DE_10Y', single_source='A', as_of='2026-11-06', db_path=db)
    assert frame.series_id.tolist() == ['A'] and frame.value.tolist() == [2.0]
    frame = reader.read_resolved_history('DE_10Y', series_id='B', as_of='2026-11-06', db_path=db)
    assert frame.series_id.tolist() == ['B']
    with pytest.raises(ValueError):
        reader.read_resolved_history('DE_10Y', series_id='OTHER', db_path=db)


def test_latest_secondary_then_primary_replaces(db, table):
    table['sources'][0]['max_age_business_days'] = 5
    table['sources'][1]['convention'] = 'benchmark'
    table['sources'][2]['role'] = 'history_only'
    result = reader.read_resolved_rate('DE_10Y', as_of='2026-11-06', db_path=db)
    assert (result['series_id'], result['status'], result['observation_date']) == (
        'B', 'latest:B', date(2026, 11, 5))
    assert reader.read_resolved_history('DE_10Y', as_of='2026-11-06', db_path=db).iloc[-1].series_id == 'B'
    add_observation(db, '2026-11-05', 'A', 2.4)
    result = reader.read_resolved_rate('DE_10Y', as_of='2026-11-06', db_path=db)
    assert result['series_id'] == 'A' and result['status'] == 'primary'
    assert result['value'] == 2.4
    assert reader.read_resolved_history('DE_10Y', as_of='2026-11-06', db_path=db).iloc[-1].series_id == 'A'


def test_same_convention_on_different_dates_does_not_dispute(db, table):
    table['sources'][0]['max_age_business_days'] = 5
    table['sources'][2]['role'] = 'history_only'
    result = reader.read_resolved_rate('DE_10Y', as_of='2026-11-06', db_path=db)
    assert result['status'] == 'latest:B'
    assert result['alternatives'][0]['basis_bp'] is None
    assert not result['alternatives'][0]['voted']


def test_italian_composed_history_and_monthly_priority(tmp_path, monkeypatch):
    path = str(tmp_path / 'italy.duckdb')
    con = duckdb.connect(path)
    con.execute('CREATE TABLE macro_series (date DATE, series_id VARCHAR, value DOUBLE)')
    con.executemany('INSERT INTO macro_series VALUES (?, ?, ?)', [
        ('2026-09-30', 'CB_IT_10Y', 3.0),
        ('2026-10-09', 'ITA_BTP10_MOT', 3.2),
        ('2026-10-09', 'OECD', 9.0),
        ('2026-10-10', 'OECD', 3.3),
        ('2026-10-12', 'TV_IT_10Y', 3.4),
        ('2026-10-12', 'ITA_BTP10_MOT', 3.3),
        ('2026-10-13', 'TV_IT_10Y', 3.5),
    ])
    con.close()
    sources = [dict(series_id=sid, role=role, convention=conv, frequency=freq,
                    max_age_business_days=90)
               for sid, role, conv, freq in [
                   ('ITA_BTP10_MOT', 'primary', 'bond', 'D'),
                   ('TV_IT_10Y', 'secondary', 'vendor', 'D'),
                   ('CB_IT_10Y', 'history_only', 'central bank', 'D'),
                   ('OECD', 'fallback', 'monthly', 'M')]]
    spec = {'concept': 'IT_10Y', 'sources': sources}
    monkeypatch.setattr(resolved_rates, 'load_resolved_rates',
                        lambda: ({'tolerance_bp': 10}, {'IT_10Y': spec}))
    result = reader.read_resolved_rate('IT_10Y', as_of='2026-10-13', db_path=path)
    assert result['status'] == 'latest:TV_IT_10Y' and result['value'] == 3.5
    history = reader.read_resolved_history('IT_10Y', as_of='2026-10-13', db_path=path)
    assert history.series_id.tolist() == ['CB_IT_10Y', 'ITA_BTP10_MOT', 'OECD',
                                          'ITA_BTP10_MOT', 'TV_IT_10Y']
    assert history.role.tolist()[0] == 'history_only'
    assert history.attrs['source_counts'] == {'ITA_BTP10_MOT': 2, 'CB_IT_10Y': 1,
                                               'OECD': 1, 'TV_IT_10Y': 1}
    assert history.mixed_source.tolist() == [False, True, True, True, True]
    assert pd.isna(history.iloc[1].basis_bp)
    assert history.iloc[-1].basis_bp == pytest.approx(10)
    assert history.iloc[-1].basis_date == date(2026, 10, 12)
    assert history.iloc[1].value == 3.2
    just_tv = reader.read_resolved_history('IT_10Y', single_source='TV_IT_10Y',
                                           as_of='2026-10-13', db_path=path)
    assert just_tv.series_id.tolist() == ['TV_IT_10Y', 'TV_IT_10Y']
    assert just_tv.attrs['source_counts'] == {'TV_IT_10Y': 2}
    # A filtered read still knows the source and overlap preceding its first point.
    filtered = reader.read_resolved_history('IT_10Y', start='2026-10-13',
                                            as_of='2026-10-13', db_path=path)
    assert filtered.iloc[0].mixed_source and filtered.iloc[0].basis_bp == pytest.approx(10)


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
    invalid['concepts'][0]['sources'].append(dict(source, series_id='B', role='secondary',
                                                  convention='par', comparability_note='basis'))
    with pytest.raises(ValueError, match='pair tolerance requires matching'):
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
