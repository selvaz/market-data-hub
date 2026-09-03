# -*- coding: utf-8 -*-
"""MyFXBook's CSV, matched against the catalogue and turned into observations.

Single-sourced on purpose. The calendar used to reconcile five scraped
sources (forexfactory, myfxbook, nasdaq, tradays, yahoo) against each other,
and that reconciliation was itself the source of real, worsening
data-quality bugs: cross-source name mismatches, a wrong-scale binding
(nasdaq's bare event names bound to the wrong reading -- caught and
rejected for DEU/CAN/USA, then again for GBR/BRA/AUS), a name-collision bug
in the old cross-source matcher. MyFXBook alone gets 76% real
(non-"N/D") reference-period coverage plus a native importance tag on every
row -- better than the five-source pipeline's 31%, from one source, with
none of the reconciliation machinery. Reconciling sources that already
agreed on nothing worth keeping was never the win it looked like.
"""
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from .. import CalendarObservation
from ..aliases import normalize_name
from .matching import normalizza
from .timezones import giorno_di, measure

MESI = {m: i for i, m in enumerate(
    ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'], 1)}

# file scaricato -> (nome fonte, provenienza). Un solo file oggi, ma la forma
# resta un dict: raccogli() lo legge in un ciclo generico, e run_econ_calendar.py
# lo usa per sapere sotto quale nome myfxbook.scarica() deve scrivere.
FONTI = {
    'myfxbook.csv': ('myfxbook', 'aggregator'),
}


def regola_ok(nome_norm, richiesti, esclusi):
    for gruppo in str(richiesti or '').split(';'):
        alt = [x.strip() for x in gruppo.split('|') if x.strip()]
        if alt and not any(a in nome_norm for a in alt):
            return False
    return not any(e.strip() and e.strip() in nome_norm
                   for e in str(esclusi or '').split('|'))


def istante(data, orario, scarto=0.0):
    """Riporta a UTC un istante MyFXBook, che e' l'unico modo per confrontarlo
    con il catalogo (i cui orari, come tutto il resto, sono pensati in UTC).

    `scarto` are the hours to add to reach UTC, measured from this very batch
    by `timezones.measure()` rather than written down here: MyFXBook renders
    server-side and ignores what a browser declares, so its offset is real
    and has to be derived, not assumed.
    """
    data, orario = str(data).strip(), str(orario).strip()
    try:
        g = datetime.strptime(data, '%Y-%m-%d')
        o = datetime.strptime(orario, '%H:%M')
        return g + timedelta(hours=o.hour + scarto, minutes=o.minute), 'minute'
    except ValueError:
        pass
    # Nothing parsed as a time, so this is a date and only a date. The offset
    # is deliberately NOT applied: shifting midnight by the source's offset
    # invents an hour the source never published, and the row goes on to
    # declare itself 'day' precision anyway. Better a date that is honest
    # about knowing no time.
    giorno = giorno_di(data)
    if giorno is not None:
        return datetime(giorno.year, giorno.month, giorno.day), 'day'
    return None, 'day'


def fine_periodo(periodo, riferimento):
    """'Jul' / 'Q2' -> data di fine periodo, per il join con macro_panel.date."""
    if not periodo or periodo in ('N/D', 'nan', ''):
        return None
    p = str(periodo).strip()
    if p in ('-', 'N/A'):
        return None
    anno = riferimento.year
    # 'Jun/27': periodo settimanale (sussidi USA), gia' una data esatta
    m = re.match(r'^([A-Z][a-z]{2})/(\d{1,2})$', p)
    if m and m.group(1) in MESI:
        mese, giorno = MESI[m.group(1)], int(m.group(2))
        if mese > riferimento.month:
            anno -= 1
        return date(anno, mese, giorno)
    m = re.match(r'^Q([1-4])$', p, re.I)
    if m:
        mese = int(m.group(1)) * 3
    elif p[:3].title() in MESI:
        mese = MESI[p[:3].title()]
    else:
        return None
    # il periodo precede sempre il rilascio: se il mese e' successivo, e' l'anno prima
    if mese > riferimento.month:
        anno -= 1
    ultimo = (date(anno + (mese == 12), (mese % 12) + 1, 1) - timedelta(days=1))
    return ultimo


def _pulisci(r, colonna):
    v = str(r.get(colonna, '')).strip()
    return None if v in ('', 'N/D', '-', 'nan') else v


def raccogli(catalogo, respinti=frozenset(), legami=None):
    """Le regole propongono, le decisioni per fonte dispongono.

    `respinti` sono le terne (fonte, paese, nome) che qualcuno ha guardato e
    tenuto fuori: stesso nome, trasformazione diversa. `legami` e' l'opposto:
    un nome legato a un indicatore anche quando la regex non lo riconosce (o
    NON legato a nessun altro anche se la regex lo riconoscerebbe). Entrambi
    vengono da `config/econ_calendar_aliases.yaml`, e oggi contengono solo
    decisioni su myfxbook -- le altre fonti non collezionano piu' nulla.
    """
    legami = legami or {}
    osservazioni, per_fonte, scartati, aggiunti = [], {}, 0, 0
    for file, (fonte, prov) in FONTI.items():
        if not Path(file).exists():
            print(f'  (assente: {file})')
            continue
        d = pd.read_csv(file).fillna('').map(str)
        # What timezone this batch is in, asked of the batch rather than assumed.
        # measure() raises when the anchors are absent or disagree, and that
        # refusal is the point: an undated batch ingested anyway is a wrong
        # release instant, which is the one error the point-in-time bridge
        # exists to prevent.
        scarto = measure(d, fonte)
        if scarto:
            print(f'  ({fonte}: {scarto:+.2f} h to UTC, measured from this batch)')
        d['norm'] = d.Evento.apply(lambda e: ' '.join(normalizza(e)))
        n = 0
        for voce in catalogo:
            iso2 = {p.strip() for p in str(voce['country_iso2']).split('|')}
            sub = d[d.Paese.isin(iso2)]
            if sub.empty:
                continue
            for _, r in sub.iterrows():
                terna = (fonte, voce['country_iso3'], normalize_name(r.Evento))
                if terna in respinti:
                    scartati += 1
                    continue
                legato = legami.get(terna)
                if legato is not None:
                    # Una decisione presa batte la regola, in entrambe le
                    # direzioni: il nome entra sull'indicatore deciso anche se
                    # la regex non lo riconosce, e NON entra su nessun altro
                    # anche se la regex lo riconoscerebbe.
                    if legato != voce['indicator_key']:
                        continue
                    aggiunti += 1
                elif not regola_ok(r.norm, voce['match_rules'], voce['match_excludes']):
                    continue
                ist, prec = istante(r.Data_Rilascio, r.Orario, scarto)
                if ist is None:
                    continue
                periodo = r.get('Periodo_Riferimento', '')
                periodo = None if periodo in ('N/D', '', 'nan') else periodo
                osservazioni.append(CalendarObservation(
                    indicator_key=voce['indicator_key'],
                    country_iso3=voce['country_iso3'],
                    source=fonte, provenance=prov,
                    source_event_name=r.Evento,
                    release_utc=ist, release_precision=prec,
                    reference_period=periodo,
                    reference_date=fine_periodo(periodo, ist.date()),
                    actual=_pulisci(r, 'Attuale'), consensus=_pulisci(r, 'Previsto'),
                    previous=_pulisci(r, 'Precedente'), revised_from=_pulisci(r, 'Revisione'),
                    impact=_pulisci(r, 'Importanza'),
                    # vintage_date is deliberately left to its default, which is
                    # today in UTC. It was pinned to a single collection day,
                    # so every later run overwrote the same
                    # (event_id, source, vintage_date) row instead of adding a
                    # new one -- revisions were invisible and an as-of query
                    # could return a value from before it had been collected.
                ))
                n += 1
        per_fonte[fonte] = n
    if scartati or aggiunti:
        print(f'  ({scartati} righe respinte, {aggiunti} agganciate da decisioni per fonte)')
    return osservazioni, per_fonte
