# -*- coding: utf-8 -*-
"""What the catalogue is allowed to say about the Forex Factory feed.

The counting instrumentation in ``consolidate.raccogli`` splits every feed row
into three: matched, ruled out by an explicit decision, and never looked at.
On the production feed of 07/09/2026 the third bucket held 89 of 126 rows --
71% -- and among them the German preliminary CPI, the ISM prices index, ADP
employment, Swiss inflation and the whole Reserve Bank of New Zealand block.
The catalogue had stopped being a description of that feed.

These tests fix in place what the coverage pass decided, and they are written
around the one failure that widening a catalogue can cause: two different
releases landing on one indicator. That is not a hypothetical -- it is the
wrong-scale binding this package's history is full of -- and the names it
happens between are one word apart. Core against headline, flash against
final, m/m against y/y: each pair gets a case here, on the real names the feed
actually publishes.
"""
import duckdb
import pandas as pd
import pytest

from market_data_hub.db import connection as cx
from market_data_hub.econ_calendar import (
    load_catalog_rows,
    make_event_id,
    upsert_indicators,
)
from market_data_hub.econ_calendar.aliases import (
    load_aliases,
    load_rejections,
    load_seed,
)
from market_data_hub.econ_calendar.catalog import AREA_ISO3
from market_data_hub.econ_calendar.collect.consolidate import raccogli
from market_data_hub.econ_calendar.collect.myfxbook import COLONNE


@pytest.fixture()
def con():
    c = duckdb.connect(":memory:")
    cx.migrate(c)
    yield c
    c.close()


def _feed(tmp_path, righe):
    """Write rows in the feed's own shape. Each row is (paese, evento)."""
    quadro = [dict(zip(COLONNE, ['2026-09-03', '12:30', paese, 'low', evento,
                                 'N/D', '', '0.3%', '0.2%', '', 'ForexFactory']))
              for paese, evento in righe]
    pd.DataFrame(quadro, columns=COLONNE).to_csv(
        tmp_path / 'forexfactory.csv', index=False, encoding='utf-8-sig')


def _lega(tmp_path, monkeypatch, righe, con=None):
    """(indicator_key, source_event_name) pairs the catalogue produces."""
    _feed(tmp_path, righe)
    monkeypatch.chdir(tmp_path)
    catalogo = load_catalog_rows()
    if con is None:
        osservazioni, _, _ = raccogli(catalogo)
    else:
        upsert_indicators(con, catalogo)
        load_seed(con)
        osservazioni, _, _ = raccogli(catalogo, load_rejections(con), load_aliases(con))
    return {(o.indicator_key, o.source_event_name) for o in osservazioni}


# Every indicator the coverage pass added, against the exact row of the
# 07/09/2026 production feed it was written for. An entry that does not appear
# here was not added, and an entry here that stops matching has lost the row
# that justified it.
NUOVI = [
    ('us_adp', 'US', 'ADP Non-Farm Employment Change'),
    ('us_ism_prices', 'US', 'ISM Manufacturing Prices'),
    ('us_pmi_mfg', 'US', 'Final Manufacturing PMI'),
    ('us_pmi_svc', 'US', 'Final Services PMI'),
    ('us_factory', 'US', 'Factory Orders m/m'),
    ('cn_pmi_nbs_svc', 'CN', 'Non-Manufacturing PMI'),
    ('cn_pmi_caixin_mfg', 'CN', 'RatingDog Manufacturing PMI'),
    ('cn_pmi_caixin_svc', 'CN', 'RatingDog Services PMI'),
    ('ez_employment', 'EU', 'Final Employment Change q/q'),
    ('de_cpi_mm', 'DE', 'German Prelim CPI m/m'),
    ('de_pmi_mfg', 'DE', 'German Final Manufacturing PMI'),
    ('de_pmi_svc', 'DE', 'German Final Services PMI'),
    ('de_retail', 'DE', 'German Retail Sales m/m'),
    ('jp_earnings', 'JP', 'Average Cash Earnings y/y'),
    ('ca_pmi_mfg', 'CA', 'Manufacturing PMI'),
    ('ch_cpi_mm', 'CH', 'CPI m/m'),
    ('ch_gdp', 'CH', 'GDP q/q'),
    ('ch_unemp', 'CH', 'Unemployment Rate'),
    ('ch_pmi_mfg', 'CH', 'Manufacturing PMI'),
    ('nz_rbnz', 'NZ', 'Official Cash Rate'),
]


@pytest.mark.parametrize('chiave, paese, evento', NUOVI)
def test_each_new_indicator_catches_the_row_it_was_written_for(
        tmp_path, monkeypatch, chiave, paese, evento):
    legati = _lega(tmp_path, monkeypatch, [(paese, evento)])
    assert (chiave, evento) in legati, f'{chiave} no longer matches {evento!r}'
    # and it is the ONLY indicator that took the row: a name matched twice is a
    # release written into two different series.
    assert len(legati) == 1, f'{evento!r} was claimed by more than one indicator: {legati}'


def test_the_whole_new_block_lands_one_row_on_one_indicator(tmp_path, monkeypatch):
    """The rows above, all in one feed. Individually each one matches; together
    they must still partition, because the exclusions of one entry are what keep
    its neighbour's row out and those only get exercised side by side."""
    legati = _lega(tmp_path, monkeypatch, [(p, e) for _, p, e in NUOVI])
    assert legati == {(k, e) for k, _, e in NUOVI}


# ------------------------------------------------ core against headline -----
def test_core_and_headline_do_not_take_each_other_s_row(tmp_path, monkeypatch):
    """One word apart, published in the same minute, different series.

    The euro-area pair is the live one: Forex Factory prints 'Core CPI Flash
    Estimate y/y' and 'CPI Flash Estimate y/y' together at month end, and the
    headline rule (hicp|cpi;yy) matches the core name too unless 'core' is
    excluded. Swiss CPI is the pair the coverage pass added.
    """
    legati = _lega(tmp_path, monkeypatch, [
        ('EU', 'CPI Flash Estimate y/y'),
        ('EU', 'Core CPI Flash Estimate y/y'),
        ('CH', 'CPI m/m'),
        ('CH', 'Core CPI m/m'),
    ])
    assert legati == {
        ('ez_hicp_yy', 'CPI Flash Estimate y/y'),
        ('ez_core_hicp', 'Core CPI Flash Estimate y/y'),
        ('ch_cpi_mm', 'CPI m/m'),
    }
    # No core indicator exists for Switzerland, and the headline one must not
    # stand in for it: an uncovered release is a hole, a wrong one is a lie.
    assert not any(e == 'Core CPI m/m' for _, e in legati)


def test_monthly_and_annual_do_not_take_each_other_s_row(tmp_path, monkeypatch):
    """The same release published twice in the same minute under two transforms.

    This is the shape the alias table already rejects for US Average Hourly
    Earnings, and the reason de_cpi_mm exists as its own entry rather than as a
    widening of de_cpi_yy: 'German Prelim CPI m/m' and 'German Prelim CPI y/y'
    are one publication and two numbers, and one indicator carrying both would
    put the second on top of the first.
    """
    legati = _lega(tmp_path, monkeypatch, [
        ('DE', 'German Prelim CPI m/m'),
        ('DE', 'German Prelim CPI y/y'),
        ('CH', 'CPI m/m'),
        ('CH', 'CPI y/y'),
    ])
    assert legati == {
        ('de_cpi_mm', 'German Prelim CPI m/m'),
        ('de_cpi_yy', 'German Prelim CPI y/y'),
        ('ch_cpi_mm', 'CPI m/m'),
        ('ch_cpi_yy', 'CPI y/y'),
    }


# ------------------------------------------------- flash against final ------
def test_flash_and_final_are_two_events_and_not_one_overwritten(tmp_path, monkeypatch):
    """Where this catalogue puts the flash/final line, and why it holds.

    Prelim and final are the SAME series measured twice, weeks apart, and the
    convention here is one indicator carrying both, tagged ``flash_final`` so
    ``cadence_violations`` allows the second print. That is only safe because
    identity is (indicator, release DAY): two releases on two days are two
    events, and neither can overwrite the other. What would collide is two
    releases on ONE day, which is the m/m against y/y case above.

    So the assertion is not that flash and final are separate indicators -- they
    are deliberately not -- but that they are separate events, and that the tag
    that keeps the cadence report quiet is actually present on every entry that
    publishes twice.
    """
    _feed(tmp_path, [('DE', 'German Prelim CPI m/m')])
    monkeypatch.chdir(tmp_path)
    catalogo = load_catalog_rows()
    prelim, _, _ = raccogli(catalogo)

    _feed(tmp_path, [('DE', 'German Final CPI m/m')])
    finale, _, _ = raccogli(catalogo)

    assert [o.indicator_key for o in prelim] == ['de_cpi_mm']
    assert [o.indicator_key for o in finale] == ['de_cpi_mm']
    # same day in this fixture, so same event; the days differ in reality and
    # the identity function is what makes that two events rather than one.
    assert make_event_id('de_cpi_mm', prelim[0].release_utc) == \
        make_event_id('de_cpi_mm', finale[0].release_utc)
    assert make_event_id('de_cpi_mm', prelim[0].release_utc) != \
        make_event_id('de_cpi_mm', prelim[0].release_utc.replace(day=25))

    voci = {v['indicator_key']: v for v in catalogo}
    for chiave in ('de_cpi_mm', 'de_pmi_mfg', 'de_pmi_svc', 'us_pmi_mfg',
                   'us_pmi_svc', 'ez_employment'):
        assert 'flash_final' in (voci[chiave]['tags'] or ''), (
            f'{chiave} publishes a flash and a final; without the tag the '
            f'cadence report calls its second print a violation every period')


def test_the_national_pmis_do_not_reach_across_the_border(tmp_path, monkeypatch):
    """'Manufacturing PMI' is the name in five countries at once here.

    The country filter is what separates them, and it does the whole job: the
    rules are deliberately identical. This is the test that says so, because if
    the filter ever stopped applying the failure would be silent -- every PMI
    row landing on every PMI indicator.
    """
    legati = _lega(tmp_path, monkeypatch, [
        ('CH', 'Manufacturing PMI'),
        ('CA', 'Manufacturing PMI'),
        ('EU', 'Final Manufacturing PMI'),
        ('DE', 'German Final Manufacturing PMI'),
        ('US', 'Final Manufacturing PMI'),
    ])
    assert legati == {
        ('ch_pmi_mfg', 'Manufacturing PMI'),
        ('ca_pmi_mfg', 'Manufacturing PMI'),
        ('ez_pmi_mfg', 'Final Manufacturing PMI'),
        ('de_pmi_mfg', 'German Final Manufacturing PMI'),
        ('us_pmi_mfg', 'Final Manufacturing PMI'),
    }


def test_the_ism_keeps_its_own_rows_and_the_new_us_pmis_take_the_rest(
        tmp_path, monkeypatch):
    """Four American survey rows, published within hours of each other, whose
    names share every word that matters. The ISM entries are T1 and the S&P
    Global ones T3, so a swap here would not merely mislabel a number, it would
    move it between tiers -- and T1 is what the web validation pass spends money
    checking."""
    legati = _lega(tmp_path, monkeypatch, [
        ('US', 'ISM Manufacturing PMI'),
        ('US', 'ISM Services PMI'),
        ('US', 'Final Manufacturing PMI'),
        ('US', 'Final Services PMI'),
        ('US', 'ISM Manufacturing Prices'),
        ('US', 'ISM Services Prices'),
    ])
    assert legati == {
        ('us_ism_mfg', 'ISM Manufacturing PMI'),
        ('us_ism_svc', 'ISM Services PMI'),
        ('us_pmi_mfg', 'Final Manufacturing PMI'),
        ('us_pmi_svc', 'Final Services PMI'),
        ('us_ism_prices', 'ISM Manufacturing Prices'),
    }
    # The services prices index is a different survey and has no entry: it must
    # stay out rather than be filed as the manufacturing one.
    assert not any(e == 'ISM Services Prices' for _, e in legati)


def test_the_nbs_and_the_private_china_pmis_stay_apart(tmp_path, monkeypatch):
    """The official survey and the S&P Global one disagree on purpose, and the
    divergence is the signal. The private survey changed sponsor -- Caixin to
    RatingDog -- and the feed changed the name with it, which is why the rule
    now carries both words."""
    legati = _lega(tmp_path, monkeypatch, [
        ('CN', 'Manufacturing PMI'),
        ('CN', 'Non-Manufacturing PMI'),
        ('CN', 'RatingDog Manufacturing PMI'),
        ('CN', 'RatingDog Services PMI'),
        ('CN', 'Caixin Manufacturing PMI'),
    ])
    assert legati == {
        ('cn_pmi_nbs_mfg', 'Manufacturing PMI'),
        ('cn_pmi_nbs_svc', 'Non-Manufacturing PMI'),
        ('cn_pmi_caixin_mfg', 'RatingDog Manufacturing PMI'),
        ('cn_pmi_caixin_svc', 'RatingDog Services PMI'),
        ('cn_pmi_caixin_mfg', 'Caixin Manufacturing PMI'),
    }


# --------------------------------------------- commentary, ruled out --------
COMMENTO = [
    ('US', 'FOMC Member Waller Speaks'),
    ('US', 'Beige Book'),
    ('GB', 'MPC Member Pill Speaks'),
    ('AU', 'RBA Assist Gov Hunter Speaks'),
    ('NZ', 'RBNZ Press Conference'),
    ('NZ', 'RBNZ Rate Statement'),
    ('NZ', 'RBNZ Monetary Policy Statement'),
    ('JP', '10-y Bond Auction'),
]


def test_commentary_is_refused_by_a_ruling_not_left_unseen(con, tmp_path, monkeypatch):
    """The distinction the whole coverage pass is about.

    A speech, a press conference, a rate statement without a number and a bond
    auction are legitimate discards -- but 'discarded' and 'nobody ever looked'
    are different states, and only the first is a decision. Each of these rows
    has a named ruling in ``econ_calendar_aliases.yaml``, so each has to appear
    in ``ruled_out``; not one of them may fall into ``unseen``.
    """
    _feed(tmp_path, COMMENTO)
    monkeypatch.chdir(tmp_path)
    catalogo = load_catalog_rows()
    upsert_indicators(con, catalogo)
    load_seed(con)
    osservazioni, _, conteggi = raccogli(catalogo, load_rejections(con), load_aliases(con))

    assert osservazioni == []
    assert conteggi['rows'] == len(COMMENTO)
    assert conteggi['ruled_out'] == len(COMMENTO)
    assert conteggi['unseen'] == 0


def test_the_rbnz_decision_survives_the_block_of_commentary_around_it(
        con, tmp_path, monkeypatch):
    """New Zealand publishes the rate, the rate statement, the policy statement
    and the press conference in the same hour, and three of the four carry no
    number. Only the first may produce an event -- and the three rulings that
    say so were dead letters until nz_rbnz existed, because raccogli() builds
    the (source, country, name) triple from a CATALOGUE row: a country with no
    entry has no triple, so not even a decision about it can be consulted."""
    _feed(tmp_path, [('NZ', 'Official Cash Rate'),
                     ('NZ', 'RBNZ Rate Statement'),
                     ('NZ', 'RBNZ Monetary Policy Statement'),
                     ('NZ', 'RBNZ Press Conference'),
                     ('NZ', 'RBNZ Gov Breman Speaks')])
    monkeypatch.chdir(tmp_path)
    catalogo = load_catalog_rows()
    upsert_indicators(con, catalogo)
    load_seed(con)
    osservazioni, _, conteggi = raccogli(catalogo, load_rejections(con), load_aliases(con))

    assert [(o.indicator_key, o.source_event_name) for o in osservazioni] == \
        [('nz_rbnz', 'Official Cash Rate')]
    assert (conteggi['matched'], conteggi['ruled_out'], conteggi['unseen']) == (1, 4, 0)


def test_the_exclusions_alone_keep_commentary_off_the_rbnz_decision(
        tmp_path, monkeypatch):
    """The second lock, checked without the alias table: if a ruling were ever
    deleted from the YAML, the rows it covered must fall back to unseen, never
    onto the decision itself."""
    legati = _lega(tmp_path, monkeypatch, [('NZ', 'Official Cash Rate'),
                                           ('NZ', 'RBNZ Rate Statement'),
                                           ('NZ', 'RBNZ Gov Breman Speaks')])
    assert legati == {('nz_rbnz', 'Official Cash Rate')}


# ------------------------------------------------------ shape of the file ---
def test_the_catalogue_has_the_shape_the_loader_and_the_matcher_assume(con):
    """There was no test on the shape of econ_calendar.yaml, only on what it
    happens to contain: ``len(rows) > 100`` and three spot checks. Adding
    nineteen entries by hand is exactly the occasion on which a missing field
    or a mistyped archetype gets in, and both fail silently -- an unknown
    archetype flattens to a null value_type, an empty match_rules matches every
    row of its country.
    """
    righe = load_catalog_rows()
    chiavi = [r['indicator_key'] for r in righe]
    assert len(set(chiavi)) == len(chiavi), 'duplicate indicator_key'

    for r in righe:
        dove = r['indicator_key']
        assert r['name'] and r['area'] and r['category'], f'{dove}: empty identity'
        assert r['country_iso2'] in AREA_ISO3, f'{dove}: {r["country_iso2"]!r}'
        assert r['criticality'] in {'T1', 'T2', 'T3'}, f'{dove}: {r["criticality"]!r}'
        assert r['frequency'] in {'W', 'M', 'Q', 'A', 'E'}, f'{dove}: {r["frequency"]!r}'
        assert r['nature'] in {'leading', 'coincident', 'lagging', 'policy'}, dove
        # 'policy' is the nature of a decision, and the only nature a decision
        # can have: the twelve rate entries all carried it and the thirteenth
        # came in as 'leading' until this said so.
        assert (r['nature'] == 'policy') == (r['category'] == 'Monetary policy'), dove
        assert r['data_type'] in {'hard', 'soft', None}, dove
        assert r['side'] in {'demand', 'supply', None}, dove
        # the archetype resolved: a key with no archetype flattens to nulls and
        # the indicator arrives in the database with no unit and no description
        assert r['archetype'] and r['value_type'] and r['description'], \
            f'{dove}: archetype {r["archetype"]!r} did not resolve'
        # an empty rule is not a permissive rule, it is a rule that matches
        # every row of the country
        assert (r['match_rules'] or '').strip(), f'{dove}: empty match_rules'

    # macro_series_id is the bridge to the hub's own history and has rules of
    # its own (macro_bridge.py: pure levels, same unit, no transform). None of
    # the coverage entries claims one.
    nuovi = {'us_adp', 'us_ism_prices', 'us_pmi_mfg', 'us_pmi_svc', 'us_factory',
             'cn_pmi_nbs_svc', 'ez_employment', 'de_cpi_mm', 'de_pmi_mfg',
             'de_pmi_svc', 'de_retail', 'jp_earnings', 'ca_pmi_mfg', 'ch_cpi_mm',
             'ch_cpi_yy', 'ch_gdp', 'ch_unemp', 'ch_pmi_mfg', 'nz_rbnz'}
    assert nuovi <= set(chiavi)
    assert all(r['macro_series_id'] is None for r in righe
               if r['indicator_key'] in nuovi)
