# -*- coding: utf-8 -*-
"""Forex Factory's CSV, matched against the catalogue and turned into observations.

Single-sourced on purpose. The calendar used to reconcile five scraped
sources (forexfactory, myfxbook, nasdaq, tradays, yahoo) against each other,
and that reconciliation was itself the source of real, worsening
data-quality bugs: cross-source name mismatches, a wrong-scale binding
(nasdaq's bare event names bound to the wrong reading -- caught and
rejected for DEU/CAN/USA, then again for GBR/BRA/AUS), a name-collision bug
in the old cross-source matcher. The current public JSON feed is collected
once per day and accumulated locally, so the rest of this module remains a
single-source matcher without cross-source reconciliation.
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
# lo usa per sapere sotto quale nome il collettore deve scrivere.
FONTI = {
    'forexfactory.csv': ('forexfactory', 'aggregator'),
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
    decisioni su forexfactory -- le altre fonti non collezionano piu' nulla.

    Returns ``(osservazioni, per_fonte, conteggi)``.

    ``conteggi`` counts ROWS OF THE FEED, and it exists because the old counter
    did not. ``scartati`` was incremented inside the indicator x row double
    loop, so a single rejected row was counted once per catalogue entry sharing
    its country: the production feed of 07/09/2026 printed '60 righe respinte'
    for what are, in fact, 5 rows. And the far larger category had no counter at
    all -- of 126 rows, 89 met no matching rule and no ruling, and nothing in
    the run said so. Among them the German preliminary CPI, ISM prices, ADP
    employment and Swiss inflation: the rows a reader would most want to know
    about were the ones the log was silent on.

    Three disjoint buckets, adding up to the row count:

    ``matched``
        produced at least one observation.
    ``ruled_out``
        an explicit decision in the alias table kept it out (rejected, or bound
        to an indicator that is not this row's). Somebody looked at this row.
    ``unseen``
        neither. No rule fired and nobody ever ruled on it. ``match_excludes``
        firing counts here, not as a rejection: an exclusion is a catalogue
        entry saying 'not me', not a decision about the row. Split further into
        ``unseen_uncovered_country`` (the feed's country appears in no
        catalogue entry, so no rule was even evaluated) and the remainder,
        where rules ran and none matched.
    """
    legami = legami or {}
    osservazioni, per_fonte, aggiunti = [], {}, 0
    conteggi = {'rows': 0, 'matched': 0, 'ruled_out': 0, 'unseen': 0,
                'unseen_uncovered_country': 0, 'per_source': {}}
    paesi_catalogo = {p.strip()
                      for voce in catalogo
                      for p in str(voce['country_iso2']).split('|')}
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
        # An empty file says that this source produced nothing, not that its
        # timezone is unmeasurable.  Preserve TimezoneUnknown for a non-empty
        # batch that lacks anchors, where ingesting an assumed instant would
        # still be unsafe.
        scarto = 0.0 if d.empty else measure(d, fonte)
        if scarto:
            print(f'  ({fonte}: {scarto:+.2f} h to UTC, measured from this batch)')
        d['norm'] = d.Evento.apply(lambda e: ' '.join(normalizza(e)))
        n = 0
        # Row-level verdicts, keyed on the frame's index so each row is counted
        # once however many catalogue entries look at it.
        agganciate, con_decisione = set(), set()
        for voce in catalogo:
            iso2 = {p.strip() for p in str(voce['country_iso2']).split('|')}
            sub = d[d.Paese.isin(iso2)]
            if sub.empty:
                continue
            for indice, r in sub.iterrows():
                terna = (fonte, voce['country_iso3'], normalize_name(r.Evento))
                if terna in respinti:
                    con_decisione.add(indice)
                    continue
                legato = legami.get(terna)
                if legato is not None:
                    # Una decisione presa batte la regola, in entrambe le
                    # direzioni: il nome entra sull'indicatore deciso anche se
                    # la regex non lo riconosce, e NON entra su nessun altro
                    # anche se la regex lo riconoscerebbe.
                    if legato != voce['indicator_key']:
                        # A binding to another indicator is still a decision
                        # somebody took about this row; if no catalogue entry
                        # ends up claiming it, that is why.
                        con_decisione.add(indice)
                        continue
                    aggiunti += 1
                elif not regola_ok(r.norm, voce['match_rules'], voce['match_excludes']):
                    continue
                ist, prec = istante(r.Data_Rilascio, r.Orario, scarto)
                if ist is None:
                    continue
                agganciate.add(indice)
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

        # A row that ended up matched is matched, whatever else was decided
        # about it against some other indicator: the buckets are disjoint and
        # 'matched' wins.
        con_decisione -= agganciate
        non_viste = [i for i in d.index
                     if i not in agganciate and i not in con_decisione]
        senza_paese = [i for i in non_viste
                       if d.at[i, 'Paese'] not in paesi_catalogo]
        dettaglio = {'rows': len(d), 'matched': len(agganciate),
                     'ruled_out': len(con_decisione), 'unseen': len(non_viste),
                     'unseen_uncovered_country': len(senza_paese)}
        conteggi['per_source'][fonte] = dettaglio
        for chiave, valore in dettaglio.items():
            conteggi[chiave] += valore

    if conteggi['ruled_out'] or aggiunti:
        print(f'  ({conteggi["ruled_out"]} righe respinte da una decisione, '
              f'{aggiunti} agganciate da decisioni per fonte)')
    return osservazioni, per_fonte, conteggi
