"""F08c recorded plain HTTP bond snapshots; no browser or production DB."""
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from market_data_hub.config_loader import get_sovereign_yields
from market_data_hub.sources import sovereign_yields as cb
from market_data_hub.sovereign_spreads import derive_sovereign_spreads

FIX = Path(__file__).parent / 'fixtures' / 'sovereign_bonds'


def spec(symbol):
    return next(e for e in get_sovereign_yields() if e['symbol'] == symbol)


def transport(monkeypatch):
    from market_data_hub.sources import sovereign_bonds
    monkeypatch.setattr(sovereign_bonds, '_request_date', lambda: date(2026, 10, 11))
    def response(url, params=None, **kwargs):
        if 'Rendite_engl.pdf' in url:
            body = (FIX / 'finanz_prices.pdf').read_bytes()
        elif '/scheda/' in url:
            assert 'IT0005729931-MOTX' in url
            body = (FIX / 'mot_bond.html').read_bytes()
        else:
            body = (FIX / f'mot_list{(params or {}).get("page", 1)}.html').read_bytes()
        return type('Response', (), {'content': body})()
    request = Mock(side_effect=response)
    monkeypatch.setattr(cb, '_request', request)
    return request


@pytest.mark.parametrize('symbol,isin,value,price,maturity', [
    ('ITA_BTP10_MOT', 'IT0005729931', 4.6, 95.64, '2036-10-01'),
    ('DEU_BUND10_FA', 'DE000BU2Z072', 3.47, 96.15, '2036-08-15'),
])
def test_recorded_current_yield_identity_price_and_date(monkeypatch, symbol, isin, value, price, maturity):
    transport(monkeypatch)
    df = cb.fetch_yield(spec(symbol), '2023-01-01', '2026-10-11')
    assert len(df) == 1  # Current snapshot never pretends to provide historical backfill.
    r = df.iloc[0]
    assert r.date == pd.Timestamp('2026-10-09')
    assert r.value == pytest.approx(value) and r.reference_price == pytest.approx(price)
    assert r['isin'] == isin and r.maturity_date == pd.Timestamp(maturity)
    assert r.unit == '%' and r.frequency == 'D'
    assert r.source == spec(symbol)['source']
    assert cb.fetch_yield(spec(symbol), '2023-01-01', '2023-12-31').empty


def test_mot_traverses_all_pages_once_and_caches_in_invocation(monkeypatch):
    request = transport(monkeypatch)
    cache = {}
    for _ in range(2):
        cb.fetch_yield(spec('ITA_BTP10_MOT'), '2026-10-08', '2026-10-11', cache=cache)
    assert request.call_count == 13


@pytest.mark.parametrize('replacement', [
    b'<html>Enodia proof-of-work</html>', b'<html>JS challenge</html>',
    (FIX / 'mot_bond.html').read_bytes().replace(b'4,6</span>', b'-</span>'),
    (FIX / 'mot_bond.html').read_bytes().replace(b'IT0005729931', b'IT0005177909'),
    (FIX / 'mot_bond.html').read_bytes().replace(b'Plain Vanilla', b'Inflation Linked'),
    (FIX / 'mot_bond.html').read_bytes().replace(b'09/10/2026', b'2026-10'),
], ids=['enodia', 'js-challenge', 'missing-yield', 'wrong-isin', 'linker', 'monthly-date'])
def test_mot_changed_missing_challenged_or_wrong_bond_is_unavailable(monkeypatch, replacement):
    request = transport(monkeypatch)
    original = request.side_effect
    def respond(url, **kwargs):
        return type('Response', (), {'content': replacement})() if '/scheda/' in url else original(url, **kwargs)
    request.side_effect = respond
    with pytest.raises(cb.SourceUnavailable):
        cb.fetch_yield(spec('ITA_BTP10_MOT'), '2026-10-08', '2026-10-11')


def test_universe_selection_excludes_linkers_strips_step_up_and_rolls():
    from market_data_hub.sources.sovereign_bonds import select_bond
    bonds = [dict(isin='IT0005729931', maturity_date=pd.Timestamp('2036-10-01')),
             dict(isin='IT0003934657', maturity_date=pd.Timestamp('2037-02-01'))]
    assert select_bond(bonds, date(2026, 10, 9))['isin'] == 'IT0005729931'
    assert select_bond(bonds, date(2027, 1, 1))['isin'] == 'IT0003934657'


def test_bond_rows_and_metadata_atomic_idempotent_runner(monkeypatch, tmp_db):
    from market_data_hub import runner
    from market_data_hub.db.connection import get_conn
    transport(monkeypatch)
    entries = [spec('ITA_BTP10_MOT'), spec('DEU_BUND10_FA')]
    monkeypatch.setattr(runner, 'get_sovereign_yields', lambda: entries)
    cfg = {'backfill_start': {'sovereign_yields': '2023-01-01'}, 'http': {'timeout': 10, 'max_retries': 1}}
    with get_conn(tmp_db) as con:
        for run in ['first', 'repeat']:
            assert runner.run_sovereign_yields(con, cfg, run, end='2026-10-11') == 0
        assert con.execute('SELECT count(*) FROM macro_series').fetchone()[0] == 2
        assert con.execute('SELECT count(*) FROM sovereign_bond_metadata').fetchone()[0] == 2
        assert con.execute('SELECT isin FROM sovereign_bond_metadata ORDER BY series_id').fetchall() == [('DE000BU2Z072',), ('IT0005729931',)]
        assert runner.run_sovereign_yields(con, cfg, 'stale', end='2026-10-15') == 2
        con.execute('DROP TABLE sovereign_bond_metadata')
        con.execute('DELETE FROM macro_series')
        con.execute('DELETE FROM macro_series_vintage')
        assert runner.run_sovereign_yields(con, cfg, 'fault', end='2026-10-11') == 1
        assert con.execute('SELECT count(*) FROM macro_series').fetchone()[0] == 0
        assert con.execute('SELECT count(*) FROM macro_series_vintage').fetchone()[0] == 0


@pytest.mark.parametrize('bund_date,reference', [
    ('2026-10-09', 'DEU_BUND10_FA'), ('2026-10-01', 'CB_EA_AAA_10Y'),
    (None, 'CB_EA_AAA_10Y'), ('2026-10-08', 'CB_EA_AAA_10Y'),
])
def test_italy_prefers_fresh_bund_common_date_else_labelled_ecb(bund_date, reference):
    rows = [('ITA_BTP10_MOT', '2026-10-09', 4.6), ('CB_IT_10Y', '2026-08-31', 4.1),
            ('CB_EA_AAA_10Y', '2026-10-09', 3.5)]
    if bund_date:
        rows.append(('DEU_BUND10_FA', bund_date, 3.47))
    df = pd.DataFrame([dict(date=pd.Timestamp(d), series_id=s, value=v, unit='%', frequency='D',
                            country=spec(s)['country']) for s, d, v in rows])
    result = derive_sovereign_spreads(df, get_sovereign_yields(), as_of=date(2026, 10, 11))
    r = result.observations[result.observations.country == 'ITA'].iloc[-1]
    assert r.reference_id == reference and r.country_series_id == 'ITA_BTP10_MOT'
    assert r.value == pytest.approx(113 if reference == 'DEU_BUND10_FA' else 110)
    assert ('Bund' if reference == 'DEU_BUND10_FA' else 'stand-in') in r.series_name
    status = result.status.set_index('country').loc['ITA']
    assert not status.stalled and status.freshness_date == pd.Timestamp('2026-10-09')


def test_additive_metadata_migration_preserves_official_history(tmp_db):
    from market_data_hub.db.connection import get_conn, migrate
    with get_conn(tmp_db) as con:
        con.execute("INSERT INTO macro_series (date,series_id,value) VALUES ('2026-08-31','CB_IT_10Y',4.1)")
        con.execute('DROP TABLE sovereign_bond_metadata')
        con.execute("UPDATE schema_meta SET value='24' WHERE key='schema_version'")
        assert migrate(con) == 25
        assert migrate(con) == 25
        assert con.execute('SELECT value FROM macro_series').fetchone()[0] == 4.1
        assert con.execute('SELECT count(*) FROM sovereign_bond_metadata').fetchone()[0] == 0


def test_current_italy_spread_works_without_official_history_entry():
    catalog = [spec(s) for s in ['ITA_BTP10_MOT', 'DEU_BUND10_FA', 'CB_EA_AAA_10Y']]
    rows = []
    for symbol, value, isin in [('ITA_BTP10_MOT', 4.6, 'IT0005729931'),
                                ('DEU_BUND10_FA', 3.47, 'DE000BU2Z072')]:
        rows.append(dict(date=pd.Timestamp('2026-10-09'), series_id=symbol, value=value,
                         unit='%', frequency='D', country=spec(symbol)['country'], isin=isin))
    result = derive_sovereign_spreads(pd.DataFrame(rows), catalog, as_of=date(2026, 10, 11))
    assert len(result.observations) == 1
    r = result.observations.iloc[0]
    assert r.country_isin == 'IT0005729931' and r.reference_isin == 'DE000BU2Z072'
    assert r.series_id == 'SPREAD_IT_10Y_VS_BUND_FA'


def test_empty_universe_page_is_unavailable(monkeypatch):
    request = transport(monkeypatch)
    original = request.side_effect
    def response(url, params=None, **kwargs):
        if (params or {}).get('page') == 3:
            return type('Response', (), {'content': b'<table><tr><th>Isin</th><th>Descrizione</th><th>Ultimo</th><th>Cedola</th><th>Scadenza</th></tr></table>'})()
        return original(url, params=params, **kwargs)
    request.side_effect = response
    with pytest.raises(cb.SourceUnavailable):
        cb.fetch_yield(spec('ITA_BTP10_MOT'), '2026-10-08', '2026-10-11')


def test_malformed_universe_date_is_unavailable(monkeypatch):
    request = transport(monkeypatch)
    original = request.side_effect
    def response(url, params=None, **kwargs):
        r = original(url, params=params, **kwargs)
        if (params or {}).get('page') == 12:
            r.content = r.content.replace(b'01/02/2032', b'not-a-date')
        return r
    request.side_effect = response
    with pytest.raises(cb.SourceUnavailable):
        cb.fetch_yield(spec('ITA_BTP10_MOT'), '2026-10-08', '2026-10-11')


def test_repeated_universe_page_is_unavailable(monkeypatch):
    request = transport(monkeypatch)
    original = request.side_effect
    def response(url, params=None, **kwargs):
        return original(url, params={'page': 2} if (params or {}).get('page') == 3 else params, **kwargs)
    request.side_effect = response
    with pytest.raises(cb.SourceUnavailable, match='duplicate'):
        cb.fetch_yield(spec('ITA_BTP10_MOT'), '2026-10-08', '2026-10-11')


def test_italy_no_reference_overlap_is_stalled_without_forward_fill():
    rows = [('ITA_BTP10_MOT', '2026-10-09', 4.6), ('CB_EA_AAA_10Y', '2026-10-08', 3.5)]
    data = pd.DataFrame([dict(date=pd.Timestamp(d), series_id=s, value=v, unit='%', frequency='D',
                              country=spec(s)['country']) for s, d, v in rows])
    result = derive_sovereign_spreads(data, get_sovereign_yields(), as_of=date(2026, 10, 11))
    r = result.status.set_index('country').loc['ITA']
    assert r.stalled and r.reason == 'no_common_date'
    assert r.reference_id == 'CB_EA_AAA_10Y' and r.freshness_date == pd.Timestamp('2026-10-08')
    assert result.observations[result.observations.country == 'ITA'].empty


@pytest.mark.parametrize('field,bad', [('source', 'Commercial aggregator'), ('country', 'ESP'),
                                     ('history_mode', 'backfillable'), ('selection_rule', 'last_price')])
def test_bond_config_rejects_false_publisher_country_and_history(monkeypatch, field, bad):
    import validate_config
    original = validate_config._y
    entry = dict(spec('ITA_BTP10_MOT'), **{field: bad})
    monkeypatch.setattr(validate_config, '_y', lambda name: {'series': [entry]}
                        if name == 'sovereign_yields.yaml' else original(name))
    assert any('bond snapshot' in e for e in validate_config.validate())


@pytest.mark.parametrize('description,eligible', [
    ('Btp Fx 4% Oct36 Eur', True), ('Btp Tf 2,25% St36 Eur', True),
    ('Btp-1nv29 5,25%', True), ('Btp Green Fx 4% Oct36 Eur', True),
    ('Btpi Tf 4% Oct36 Eur', False), ('Btpstripital Zc Oct36 Eur', False),
    ('Btp Italia Oct36 Eur', False), ('Btp Valore Oct36 Eur', False),
    ("Btp Piu\u0027 Sc Oct36 Eur", False), ('Btp Futura Oct36 Eur', False),
])
def test_nominal_fixed_universe_type_filter(description, eligible):
    from market_data_hub.sources.sovereign_bonds import _mot_list
    # Recorded full last-page table, changing only the type being tested.
    body = (FIX / 'mot_list12.html').read_bytes().replace(b'Btp Fx 3.95% Feb32 Eur', description.encode())
    bonds, _ = _mot_list(body)
    assert bool(bonds) == eligible


@pytest.mark.parametrize('when,winner', [('2026-02-28', 'AA'), ('2026-03-01', 'BB'), ('2028-02-29', 'CC')])
def test_roll_ties_earlier_maturity_and_leap_day(when, winner):
    from market_data_hub.sources.sovereign_bonds import select_bond
    bonds = [dict(isin=isin, maturity_date=pd.Timestamp(maturity))
             for isin, maturity in [('AA', '2036-02-27'), ('BB', '2036-02-29'), ('CC', '2038-02-28')]]
    assert select_bond(bonds, date.fromisoformat(when))['isin'] == winner


@pytest.mark.parametrize('old,new', [('Yield', 'Return'), ('in %', 'in bp'),
                                     ('09.10.2026', '2026-10'), ('96.150', 'missing')])
def test_changed_finanzagentur_pdf_is_unavailable(monkeypatch, old, new):
    from pypdf import PdfReader
    from market_data_hub.sources.sovereign_bonds import parse_finanzagentur
    text = '\n'.join(p.extract_text() for p in PdfReader(FIX / 'finanz_prices.pdf').pages).replace(old, new)
    monkeypatch.setattr('pypdf.PdfReader', lambda _: type('Reader', (), {
        'pages': [type('Page', (), {'extract_text': lambda _: text})()]})())
    with pytest.raises(cb.SourceUnavailable):
        parse_finanzagentur(spec('DEU_BUND10_FA'), b'%PDF-recorded-text-mutation')
