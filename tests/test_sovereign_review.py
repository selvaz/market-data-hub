"""F08d review regressions: discovery, bounded reruns and immutable dated history."""
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from market_data_hub import catalog, runner
from market_data_hub.config_loader import get_macro_series, get_sovereign_yields
from market_data_hub.db.connection import get_conn
from market_data_hub.db.upsert import upsert
from market_data_hub.sources import sovereign_yields as cb
from market_data_hub.sovereign_spreads import derive_sovereign_spreads

FIX = Path(__file__).parent / 'fixtures' / 'sovereign_yields'


def spec(symbol):
    return next(e for e in get_sovereign_yields() if e['symbol'] == symbol)


def points(*series):
    return pd.DataFrame([dict(date=pd.Timestamp(d), series_id=s, value=v,
                              unit='%', frequency='D', country=spec(s)['country'],
                              isin={'ITA_BTP10_MOT': 'IT0005729931',
                                    'DEU_BUND10_FA': 'DE000BU2Z072'}.get(s))
                         for s, observations in series for d, v in observations])


@pytest.mark.parametrize('symbol', ['CB_EA_AAA_10Y', 'CB_FR_10Y', 'CB_IT_10Y',
                                   'ITA_BTP10_MOT', 'DEU_BUND10_FA'])
def test_catalog_sovereign_discovery_metadata_search_and_dataset_count(monkeypatch, symbol):
    monkeypatch.setattr(catalog.reader, 'get_coverage', lambda **_: pd.DataFrame())
    monkeypatch.setattr(catalog.reader, 'get_macro_panel_coverage', lambda **_: pd.DataFrame())
    entries = catalog.list_macro_series(with_coverage=False).set_index('series_id')
    assert symbol in entries.index
    r = entries.loc[symbol]
    assert r.source == spec(symbol)['source'] and r.country == spec(symbol)['country']
    assert r.unit == '%' and r.frequency == 'D' and r.max_lag_days == 3
    assert symbol in set(catalog.list_macro_series(frequency='D', with_coverage=False).series_id)
    card = catalog.describe_series(symbol)
    assert card['domain'] == 'macro' and card['series_id'] == symbol
    assert card['source'] == spec(symbol)['source']
    hit = catalog.search(symbol)
    assert hit[['domain', 'key']].to_dict('records') == [{'domain': 'macro', 'key': symbol}]
    assert symbol in set(catalog.search(spec(symbol)['source']).key)
    dataset = next(d for d in catalog.list_datasets() if d['domain'] == 'macro')
    assert dataset['n_series'] == len(get_macro_series()) + len(get_sovereign_yields())


def test_catalog_listed_spread_is_discovered_without_inventing_derived_series(monkeypatch):
    spread = dict(spec('CB_IT_10Y'), symbol='SPREAD_IT_10Y_VS_BUND_FA', unit='bp',
                  source='derived:sovereign_yields', name='Italy 10y spread vs Bund')
    monkeypatch.setattr(catalog, 'get_sovereign_yields', lambda: [spread], raising=False)
    monkeypatch.setattr(catalog.reader, 'get_coverage', lambda **_: pd.DataFrame())
    entries = catalog.list_macro_series(with_coverage=False).set_index('series_id')
    assert spread['symbol'] in entries.index
    assert entries.loc[spread['symbol'], 'unit'] == 'bp'
    assert 'ITA_BTP10_MOT' not in entries.index
    assert catalog.describe_series(spread['symbol'])['domain'] == 'macro'
    assert spread['symbol'] in set(catalog.search('spread vs Bund').key)


@pytest.mark.parametrize('with_other_work', [False, True])
def test_runner_incremental_start_after_end_is_noop_not_fault(monkeypatch, tmp_db, with_other_work):
    entries = [spec('CB_FR_10Y')]
    if with_other_work:
        entries.append(spec('CB_ES_10Y'))
    monkeypatch.setattr(runner, 'get_sovereign_yields', lambda: entries)
    frame = cb.parse_response(spec('CB_ES_10Y'), (FIX / 'bde.csv').read_bytes())
    frame = frame[frame.date == pd.Timestamp('2026-10-07')]
    def bounded_fetch(entry, start, end, **kwargs):
        if pd.Timestamp(start) > pd.Timestamp(end):
            raise ValueError('start must not be after end')
        return frame
    fetch = Mock(side_effect=bounded_fetch)
    monkeypatch.setattr(cb, 'fetch_yield', fetch)
    cfg = {'backfill_start': {'sovereign_yields': '2023-01-01'},
           'http': {'timeout': 10, 'max_retries': 1}}
    with get_conn(tmp_db) as con:
        # The current DB is ahead of the historical/as-of request.
        upsert(con, 'macro_series', pd.DataFrame([dict(date=pd.Timestamp('2026-11-01'),
                                                     series_id='CB_FR_10Y', value=4.8)]))
        result = runner.run_sovereign_yields(con, cfg, 'past-end', end='2026-10-08')
        assert result == (0 if with_other_work else 3)
        assert fetch.call_count == int(with_other_work)
        if with_other_work:
            assert fetch.call_args.args[0]['symbol'] == 'CB_ES_10Y'
        row = con.execute("SELECT status,rows_added,rows_updated,error_msg FROM download_log WHERE symbol='CB_FR_10Y'").fetchone()
        assert row == ('noop', 0, 0, None)
        assert con.execute("SELECT count(*) FROM macro_series WHERE series_id='CB_FR_10Y'").fetchone()[0] == 1


def test_spread_dated_reference_history_survives_asof_staleness_and_outage():
    data = points(
        ('ITA_BTP10_MOT', [('2026-10-08', 4.5), ('2026-10-09', 4.6), ('2026-10-12', 4.7)]),
        ('DEU_BUND10_FA', [('2026-10-08', 3.4), ('2026-10-09', 3.47)]),
        ('CB_EA_AAA_10Y', [('2026-10-08', 3.5), ('2026-10-09', 3.52), ('2026-10-12', 3.6)]),
    )
    early = derive_sovereign_spreads(data, get_sovereign_yields(), as_of=date(2026, 10, 9))
    later = derive_sovereign_spreads(data, get_sovereign_yields(), as_of=date(2026, 10, 19))
    early_it = early.observations[early.observations.country == 'ITA'].reset_index(drop=True)
    later_it = later.observations[later.observations.country == 'ITA'].reset_index(drop=True)
    pd.testing.assert_frame_equal(early_it, later_it[later_it.date <= pd.Timestamp('2026-10-09')].reset_index(drop=True))
    assert later_it.reference_id.tolist() == ['DEU_BUND10_FA', 'DEU_BUND10_FA', 'CB_EA_AAA_10Y']
    assert later_it.value.tolist() == pytest.approx([110, 113, 110])
    assert later_it.reference_isin.tolist()[:2] == ['DE000BU2Z072'] * 2
    status = later.status[later.status.country == 'ITA']
    assert len(status) == 1
    assert status.iloc[0].reference_id == 'CB_EA_AAA_10Y' and status.iloc[0].stalled
    assert status.iloc[0].last_spread_date == pd.Timestamp('2026-10-12')


def test_spread_italy_country_leg_is_selected_per_date_without_losing_official_history():
    data = points(
        ('CB_IT_10Y', [('2026-08-31', 4.1), ('2026-10-09', 9)]),
        ('ITA_BTP10_MOT', [('2026-10-09', 4.6)]),
        ('DEU_BUND10_FA', [('2026-10-09', 3.47)]),
        ('CB_EA_AAA_10Y', [('2026-08-31', 3.3), ('2026-10-09', 3.5)]),
    )
    result = derive_sovereign_spreads(data, get_sovereign_yields(), as_of=date(2026, 10, 11))
    it = result.observations[result.observations.country == 'ITA']
    assert it.country_series_id.tolist() == ['CB_IT_10Y', 'ITA_BTP10_MOT']
    assert it.reference_id.tolist() == ['CB_EA_AAA_10Y', 'DEU_BUND10_FA']
    assert it.value.tolist() == pytest.approx([80, 113])


def test_spread_old_bund_row_is_stale_without_changing_reference():
    data = points(('ITA_BTP10_MOT', [('2026-10-09', 4.6)]),
                  ('DEU_BUND10_FA', [('2026-10-09', 3.47)]),
                  ('CB_EA_AAA_10Y', [('2026-10-09', 3.5), ('2026-10-19', 3.6)]))
    result = derive_sovereign_spreads(data, get_sovereign_yields(), as_of=date(2026, 10, 19))
    it = result.observations[result.observations.country == 'ITA']
    assert it.reference_id.tolist() == ['DEU_BUND10_FA']
    status = result.status.set_index('country').loc['ITA']
    assert status.reference_id == 'DEU_BUND10_FA' and status.stalled


def test_jgb_historical_only_never_requests_unrelated_current_export(monkeypatch):
    meta = spec('CB_JP_10Y')
    def request(url, **kwargs):
        if url == meta['endpoint']:
            raise cb.SourceUnavailable('current export outage')
        return type('Response', (), {'content': (FIX / 'jgb_history.csv').read_bytes()})()
    fetch = Mock(side_effect=request)
    monkeypatch.setattr(cb, '_request', fetch)
    frame = cb.fetch_yield(meta, '2023-01-01', '2023-01-10')
    assert not frame.empty and frame.date.max().year == 2023
    assert [c.args[0] for c in fetch.call_args_list] == [meta['history_endpoint']]


@pytest.mark.parametrize('start,end,expected', [
    ('2023-01-01', '2026-09-30', ['history']),
    ('2023-01-01', '2026-10-01', ['history', 'current']),
    ('2026-10-01', '2026-10-08', ['current']),
])
def test_jgb_request_selection_at_current_month_boundary(monkeypatch, start, end, expected):
    meta = spec('CB_JP_10Y')
    monkeypatch.setattr(pd.Timestamp, 'now', lambda tz=None: pd.Timestamp('2026-10-11', tz=tz))
    def request(url, **kwargs):
        name = 'jgb_history.csv' if url == meta['history_endpoint'] else 'jgb_current.csv'
        return type('Response', (), {'content': (FIX / name).read_bytes()})()
    fetch = Mock(side_effect=request)
    monkeypatch.setattr(cb, '_request', fetch)
    cb.fetch_yield(meta, start, end)
    assert ['history' if c.args[0] == meta['history_endpoint'] else 'current'
            for c in fetch.call_args_list] == expected


def test_catalog_sovereign_coverage_and_declared_frequency_without_reading_production(monkeypatch, tmp_db):
    with get_conn(tmp_db) as con:
        con.execute("INSERT INTO coverage_report (symbol,source,asset_class,last_date,obs_count,freq_detected,lag_days,stalled,coverage_score,status) VALUES ('ITA_BTP10_MOT','Borsa Italiana','MACRO','2026-10-09',1,'D',2,FALSE,100,'ok')")
    df = catalog.list_macro_series(frequency='D', category='RATES', db_path=tmp_db).set_index('series_id')
    assert set(e['symbol'] for e in get_sovereign_yields()) <= set(df.index)
    assert df.loc['ITA_BTP10_MOT', 'obs_count'] == 1
    assert not df.loc['ITA_BTP10_MOT', 'stalled']
    assert pd.isna(df.loc['CB_IT_10Y', 'last_date'])
    assert df.loc['CB_PT_10Y', 'provider'] == 'unavailable'


def test_runner_explicit_reversed_bounds_still_fault(monkeypatch, tmp_db):
    monkeypatch.setattr(runner, 'get_sovereign_yields', lambda: [spec('CB_FR_10Y')])
    request = Mock(side_effect=AssertionError('no HTTP for invalid bounds'))
    monkeypatch.setattr(cb, '_request', request)
    cfg = {'backfill_start': {'sovereign_yields': '2023-01-01'}, 'http': {'timeout': 10, 'max_retries': 1}}
    with get_conn(tmp_db) as con:
        assert runner.run_sovereign_yields(con, cfg, 'bad-range', start_override='2026-10-09', end='2026-10-08') == 1
    request.assert_not_called()
