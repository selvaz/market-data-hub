"""F08b: recorded ECB curves and arithmetic/freshness performed by code."""
from __future__ import annotations

import csv
import importlib
import io
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from market_data_hub.config_loader import get_sovereign_yields
from market_data_hub.sources import sovereign_yields as cb

FIX = Path(__file__).parent / 'fixtures' / 'sovereign_yields'
CURVES = [
    ('CB_EA_AAA_10Y', 'ecb_aaa10.csv', 'G_N_A', 'SR_10Y', 3.51918971),
    ('CB_EA_AAA_2Y', 'ecb_aaa2.csv', 'G_N_A', 'SR_2Y', 3.0331429854),
    ('CB_EA_ALL_10Y', 'ecb_all10.csv', 'G_N_C', 'SR_10Y', 4.1258286142),
    ('CB_EA_ALL_2Y', 'ecb_all2.csv', 'G_N_C', 'SR_2Y', 3.2485820015),
]


def spec(symbol):
    return next(e for e in get_sovereign_yields() if e['symbol'] == symbol)


def ecb_meta(instrument='G_N_A', tenor='SR_10Y'):
    code = f'B.U2.EUR.4F.{instrument}.SV_C_YM.{tenor}'
    return dict(symbol='CB_EA_AAA_10Y', name='ECB AAA 10y', country='EMU',
                source='ECB', provider='ecb', provider_code=code,
                endpoint=f'https://data-api.ecb.europa.eu/service/data/YC/{code}')


@pytest.mark.parametrize('symbol,fixture,instrument,tenor,value', CURVES)
def test_ecb_catalog_and_recorded_daily_backfill(symbol, fixture, instrument, tenor, value):
    meta = spec(symbol)
    assert meta['provider'] == 'ecb' and meta['source'] == 'ECB'
    assert meta['provider_code'] == f'B.U2.EUR.4F.{instrument}.SV_C_YM.{tenor}'
    assert meta['country'] == 'EMU' and meta['frequency'] == 'D'
    assert meta['max_lag_days'] == 3 and meta['lag_calendar'] == 'business'
    assert not meta.get('key_env')
    df = cb.parse_response(meta, (FIX / fixture).read_bytes())
    assert df.date.min() == pd.Timestamp('2023-01-02')
    assert df.date.max() == pd.Timestamp('2026-10-08')
    assert df.value.iloc[-1] == pytest.approx(value)
    assert df.unit.unique().tolist() == ['%']
    assert df.frequency.unique().tolist() == ['D']
    assert df.source.unique().tolist() == ['ECB']


@pytest.mark.parametrize('field,bad', [
    ('KEY', 'YC.B.U2.EUR.4F.G_N_C.SV_C_YM.SR_10Y'), ('FREQ', 'M'),
    ('REF_AREA', 'IT'), ('CURRENCY', 'USD'), ('DATA_TYPE_FM', 'SR_2Y'),
    ('INSTRUMENT_FM', 'G_N_C'), ('UNIT', 'BP'), ('UNIT_MULT', '2'),
    ('TIME_PERIOD', '2026-10'), ('OBS_STATUS', 'F'), ('TIME_FORMAT', 'P1M'),
])
def test_ecb_rejects_wrong_series_monthly_units_and_forecasts(field, bad):
    reader = csv.DictReader(io.StringIO((FIX / 'ecb_aaa10.csv').read_text()))
    rows = list(reader)
    rows[-1][field] = bad
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=reader.fieldnames)
    w.writeheader()
    w.writerows(rows)
    with pytest.raises(cb.SourceUnavailable):
        cb.parse_response(ecb_meta(), out.getvalue().encode())


def test_ecb_query_passes_inclusive_bounds_and_normalizes_business_frequency(monkeypatch):
    request = Mock(return_value=type('Response', (), {'content': (FIX / 'ecb_aaa10.csv').read_bytes()})())
    monkeypatch.setattr(cb, '_request', request)
    df = cb.fetch_yield(ecb_meta(), '2026-10-07', '2026-10-08')
    assert df.date.dt.strftime('%Y-%m-%d').tolist() == ['2026-10-07', '2026-10-08']
    assert request.call_args.kwargs['params'] == {
        'format': 'csvdata', 'startPeriod': '2026-10-07', 'endPeriod': '2026-10-08'}


def test_ecb_invalid_catalog_contract_is_rejected(monkeypatch):
    import validate_config
    original = validate_config._y
    entry = dict(ecb_meta(), enabled=True, unit='%', frequency='D', max_lag_days=3,
                 lag_calendar='business', country='DEU')
    monkeypatch.setattr(validate_config, '_y', lambda name: {'series': [entry]}
                        if name == 'sovereign_yields.yaml' else original(name))
    assert any('ECB' in error for error in validate_config.validate())


def france_meta():
    return dict(spec('CB_FR_10Y'), provider='webstat_csv',
                endpoint='https://webstat.banque-france.fr/export/csv/fr/catalog/FM/FM.D.FR.EUR.FR2.BB.FRMOYTEC10.HSTA',
                key_env=None)


def test_france_recorded_no_key_csv_history_and_missing_weekend():
    df = cb.parse_response(france_meta(), (FIX / 'webstat_tec10.csv').read_bytes())
    assert df.date.min() == pd.Timestamp('2023-01-02')
    assert df.date.max() == pd.Timestamp('2026-10-09')
    assert df.value.iloc[-1] == pytest.approx(4.795)
    assert pd.Timestamp('2026-10-04') not in set(df.date)
    assert df.unit.unique().tolist() == ['%']


def test_france_catalog_uses_verified_csv_without_key(monkeypatch):
    meta = spec('CB_FR_10Y')
    assert meta['provider'] == 'webstat_csv'
    assert not meta.get('key_env')
    monkeypatch.delenv('WEBSTAT_API_KEY', raising=False)
    request = Mock(return_value=type('Response', (), {'content': (FIX / 'webstat_tec10.csv').read_bytes()})())
    monkeypatch.setattr(cb, '_request', request)
    df = cb.fetch_yield(meta, '2026-10-08', '2026-10-09')
    assert df.date.dt.strftime('%Y-%m-%d').tolist() == ['2026-10-08', '2026-10-09']
    assert request.call_count == 1 and request.call_args.kwargs['headers'] is None


@pytest.mark.parametrize('field,bad', [('series_key', 'wrong'), ('FREQ', 'M'), ('UNIT', 'BP'),
                                       ('UNIT_MULT', '2'), ('CURRENCY', 'USD'), ('REF_AREA', 'IT'),
                                       ('time_period_end', '2026-10-31'), ('OBS_STATUS', 'F')])
def test_france_csv_rejects_wrong_metadata_and_forecasts(field, bad):
    reader = csv.DictReader(io.StringIO((FIX / 'webstat_tec10.csv').read_text(encoding='utf-8-sig')), delimiter=';')
    rows = list(reader)
    rows[0][field] = bad
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=reader.fieldnames, delimiter=';')
    w.writeheader()
    w.writerows(rows)
    with pytest.raises(cb.SourceUnavailable):
        cb.parse_response(france_meta(), out.getvalue().encode())


@pytest.fixture
def spreads():
    return importlib.import_module('market_data_hub.sovereign_spreads')


def observations(*series):
    rows = []
    for symbol, points in series:
        meta = spec(symbol)
        for when, value in points:
            rows.append(dict(date=pd.Timestamp(when), series_id=symbol, value=value,
                             unit='%', frequency='D', country=meta['country']))
    return pd.DataFrame(rows, columns=['date', 'series_id', 'value', 'unit', 'frequency', 'country'])


def calculate(spreads, df, as_of='2026-10-11', catalog=None):
    return spreads.derive_sovereign_spreads(df, catalog or get_sovereign_yields(),
                                           as_of=date.fromisoformat(as_of))


def test_spreads_exact_common_dates_bp_and_older_leg_freshness(spreads):
    df = observations(
        ('CB_EA_AAA_10Y', [('2026-10-07', 3.5), ('2026-10-08', 3.6)]),
        ('CB_ES_10Y', [('2026-10-07', 4.096)]),
        ('CB_BE_10Y', [('2026-10-08', 4.2), ('2026-10-09', 4.31)]),
        ('CB_GB_10Y', [('2026-10-08', 5.4)]),
    )
    result = calculate(spreads, df)
    out = result.observations.set_index('country')
    assert out.loc['ESP', 'value'] == pytest.approx(59.6)
    assert out.loc['BEL', 'value'] == pytest.approx(60)
    assert set(out.index) == {'ESP', 'BEL'}
    assert out.loc['BEL', 'date'] == pd.Timestamp('2026-10-08')
    assert set(out.unit) == {'bp'} and set(out.frequency) == {'D'}
    assert set(out.reference_id) == {'CB_EA_AAA_10Y'}
    status = result.status.set_index('country')
    assert status.loc['ESP', 'freshness_date'] == pd.Timestamp('2026-10-07')
    assert status.loc['BEL', 'freshness_date'] == pd.Timestamp('2026-10-08')
    assert not status.loc['BEL', 'stalled']
    assert status.loc['ITA', 'reason'] == 'missing_leg'


@pytest.mark.parametrize('old_leg', ['CB_EA_AAA_10Y', 'CB_ES_10Y'])
def test_stale_either_leg_cannot_be_presented_as_fresh(spreads, old_leg):
    df = observations(
        ('CB_EA_AAA_10Y', [('2026-10-05', 3.5)] + ([] if old_leg == 'CB_EA_AAA_10Y' else [('2026-10-09', 3.6)])),
        ('CB_ES_10Y', [('2026-10-05', 4)] + ([] if old_leg == 'CB_ES_10Y' else [('2026-10-09', 4.1)])),
    )
    status = calculate(spreads, df).status.set_index('country').loc['ESP']
    assert status.stalled and status.reason == 'stale'
    assert status.freshness_date == pd.Timestamp('2026-10-05')


def test_fresh_legs_but_old_common_date_are_stalled(spreads):
    df = observations(
        ('CB_EA_AAA_10Y', [('2026-09-30', 3.5), ('2026-10-08', 3.6)]),
        ('CB_ES_10Y', [('2026-09-30', 4), ('2026-10-09', 4.1)]),
    )
    status = calculate(spreads, df).status.set_index('country').loc['ESP']
    assert status.stalled and status.last_spread_date == pd.Timestamp('2026-09-30')


def test_holidays_three_business_days_and_fourth_day_boundary(spreads):
    df = observations(('CB_EA_AAA_10Y', [('2026-10-05', 3.5)]),
                      ('CB_ES_10Y', [('2026-10-05', 4)]))
    catalog = [dict(e, holidays=['2026-10-06']) for e in get_sovereign_yields()]
    assert not calculate(spreads, df, catalog=catalog).status.set_index('country').loc['ESP', 'stalled']
    assert calculate(spreads, df, as_of='2026-10-12', catalog=catalog).status.set_index('country').loc['ESP', 'stalled']


def test_no_overlap_or_reference_stays_unavailable_without_forward_fill(spreads):
    df = observations(('CB_EA_AAA_10Y', [('2026-10-08', 3.5)]),
                      ('CB_ES_10Y', [('2026-10-07', 4)]))
    result = calculate(spreads, df)
    assert result.observations.empty
    assert result.status.set_index('country').loc['ESP', 'reason'] == 'no_common_date'
    df = df[df.series_id != 'CB_EA_AAA_10Y']
    result = calculate(spreads, df)
    assert result.observations.empty and result.status.stalled.all()


@pytest.mark.parametrize('field,bad', [('unit', 'bp'), ('frequency', 'M'), ('value', float('inf')),
                                       ('country', 'USA'), ('date', pd.Timestamp('2026-10-08 12:00'))])
def test_spread_rejects_incompatible_or_invalid_input(spreads, field, bad):
    df = observations(('CB_EA_AAA_10Y', [('2026-10-08', 3.5)]),
                      ('CB_ES_10Y', [('2026-10-08', 4)]))
    df.loc[1, field] = bad
    with pytest.raises(ValueError):
        calculate(spreads, df)


def test_revisions_must_be_resolved_and_future_observations_are_excluded(spreads):
    df = observations(('CB_EA_AAA_10Y', [('2026-10-08', 3.5)]),
                      ('CB_ES_10Y', [('2026-10-08', 4), ('2026-10-12', 9)]))
    assert calculate(spreads, df).observations.value.tolist() == [50]
    df = pd.concat([df, df.iloc[[1]]])
    # Identical repeated rows are harmless; conflicting revisions are not.
    assert calculate(spreads, df).observations.value.tolist() == [50]
    df.iloc[-1, df.columns.get_loc('value')] = 4.2
    with pytest.raises(ValueError, match='conflicting'):
        calculate(spreads, df)


def test_newly_available_euro_country_is_derived_automatically(spreads):
    df = observations(('CB_EA_AAA_10Y', [('2026-10-08', 3.5)]),
                      ('CB_FR_10Y', [('2026-10-08', 4.79)]),
                      ('CB_IT_10Y', [('2026-10-08', 4.3)]))
    result = calculate(spreads, df)
    assert dict(zip(result.observations.country, result.observations.value)) == pytest.approx({'FRA': 129, 'ITA': 80})


def test_new_euro_member_excludes_pre_adoption_local_currency_history(spreads):
    # No Bulgarian feed is added here. A future EUR feed must not derive its
    # earlier BGN quotes against the EUR reference (ECB accession: 2026-01-01).
    catalog = [spec('CB_EA_AAA_10Y'), dict(spec('CB_ES_10Y'), symbol='CB_BG_10Y', country='BGR')]
    df = observations(('CB_EA_AAA_10Y', [('2025-12-30', 3), ('2026-10-08', 3.5)]),
                      ('CB_ES_10Y', [('2025-12-30', 4), ('2026-10-08', 4.5)]))
    df.loc[df.series_id == 'CB_ES_10Y', ['series_id', 'country']] = ['CB_BG_10Y', 'BGR']
    result = calculate(spreads, df, catalog=catalog)
    assert result.observations.date.tolist() == [pd.Timestamp('2026-10-08')]
    assert result.observations.value.tolist() == [100]


def test_ecb_empty_challenge_and_duplicate_rows(spreads):
    # Challenge rejection is part of ingestion, before any derived arithmetic.
    with pytest.raises(cb.SourceUnavailable):
        cb.parse_response(ecb_meta(), b'<html>proof of work</html>')
    result = calculate(spreads, observations())
    assert result.observations.empty and result.status.stalled.all()


def test_new_daily_feeds_use_existing_runner_upserts_and_revision_tracking(monkeypatch, tmp_db):
    from market_data_hub import runner
    from market_data_hub.db.connection import get_conn
    pairs = {symbol: fixture for symbol, fixture, *_ in CURVES}
    pairs['CB_FR_10Y'] = 'webstat_tec10.csv'
    entries = [spec(symbol) for symbol in pairs]
    monkeypatch.setattr(runner, 'get_sovereign_yields', lambda: entries)
    payloads = {entry['endpoint']: (FIX / pairs[entry['symbol']]).read_bytes() for entry in entries}
    request = Mock(side_effect=lambda url, **kwargs: type('Response', (), {'content': payloads[url]})())
    monkeypatch.setattr(cb, '_request', request)
    cfg = {'backfill_start': {'sovereign_yields': '2023-01-01'},
           'http': {'timeout': 10, 'max_retries': 1}}
    with get_conn(tmp_db) as con:
        assert runner.run_sovereign_yields(con, cfg, 'ecb-first', end='2026-10-11') == 0
        before = con.execute('SELECT count(*) FROM macro_series').fetchone()[0]
        assert before == 20  # Four 4-observation curves and four nonmissing French quotes.
        assert runner.run_sovereign_yields(con, cfg, 'ecb-repeat', end='2026-10-11') == 0
        assert con.execute('SELECT count(*) FROM macro_series').fetchone()[0] == before
        rows = con.execute('SELECT DISTINCT source, country, unit, frequency FROM macro_series ORDER BY country').fetchall()
        assert rows == [('ECB', 'EMU', '%', 'D'), ('Banque de France', 'FRA', '%', 'D')]
        meta = spec('CB_EA_AAA_10Y')
        revised = cb.parse_response(meta, payloads[meta['endpoint']])
        revised.loc[revised.date == revised.date.max(), 'value'] = 3.52
        monkeypatch.setattr(cb, 'fetch_yield', lambda *a, **k: revised)
        monkeypatch.setattr(runner, 'get_sovereign_yields', lambda: [meta])
        assert runner.run_sovereign_yields(con, cfg, 'ecb-revision', end='2026-10-11') == 0
        assert con.execute('SELECT count(*) FROM macro_series').fetchone()[0] == before
        row = con.execute('SELECT value FROM macro_series WHERE series_id=? ORDER BY date DESC LIMIT 1', [meta['symbol']]).fetchone()
        assert row[0] == 3.52
