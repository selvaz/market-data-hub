# -*- coding: utf-8 -*-
"""Prova integrata: i dati veri delle cinque fonti attraverso la pipeline dell'hub.

Non e' uno unit test: e' il giro completo. Le regole di riconoscimento vengono
lette dal catalogo dell'hub (non dalla watchlist di lavoro), cosi' si verifica
anche che siano sopravvissute al passaggio in YAML.

Il collettore normalizza, l'hub ingerisce: e' la divisione di responsabilita'
del disegno, quindi qui il matching sta fuori dall'hub, come sara' in
LazyCrawler.
"""
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

import pandas as pd

from .. import (
    CalendarObservation,
)
from ..aliases import normalize_name
from .matching import normalizza
from .timezones import measure

ANNO = 2026
MESI = {m: i for i, m in enumerate(
    ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'], 1)}
# Offsets for the sources that do not publish in UTC. The two used to share a
# constant of 7 and a warning to re-measure after every clock change. They do
# not share a cause, and only one of them needed the constant:
#
# Tradays renders its times in JavaScript from the *viewer's* clock, so the 7
# was this server's Pacific offset in summer, not a property of Tradays.
# Measured by forcing the browser through three zones: 197 of 203 events moved,
# and under UTC the times were the true UTC ones. `setup_browser()` now pins the
# browser to UTC, so there is nothing left to add -- and nothing left to break
# on 1 November.
#
# MyFXBook renders server-side and ignores what the browser declares (0 of 235
# events moved), so its offset is real and stays a measurement. It is *not*
# written here as a constant: `timezones.measure()` derives it from the batch
# being ingested, using releases whose UTC time is publicly fixed, and refuses
# the batch when the anchors are missing or disagree. A number that has to be
# right and cannot be checked is the thing this replaces.
SCARTO_TRADAYS = 0

# file scaricato -> (nome fonte, provenienza)
#
# Un file per fonte, chiamato come la fonte. Prima i nomi erano quelli via via
# usciti dalle prove -- 'tradays_con_paese', 'myfxbook_iso', 'nasdaq_completo' --
# e due di essi erano prodotti da script diversi da quelli che sembravano:
# 'tradays_con_paese' veniva da un secondo passaggio che risolveva il paese
# dall'href, e 'myfxbook_iso' da una conversione valuta->ISO che stava altrove.
# Chi leggeva l'elenco degli scaricatori otteneva file che il consolidamento
# non guardava. Ora ogni scaricatore scrive il proprio, gia' nella forma finale.
FONTI = {
    'tradays.csv': ('tradays', 'aggregator'),
    'yahoo.csv': ('yahoo', 'aggregator'),
    'nasdaq.csv': ('nasdaq', 'aggregator'),
    'forexfactory.csv': ('forexfactory', 'aggregator'),
    'myfxbook.csv': ('myfxbook', 'aggregator'),
}


def regola_ok(nome_norm, richiesti, esclusi):
    for gruppo in str(richiesti or '').split(';'):
        alt = [x.strip() for x in gruppo.split('|') if x.strip()]
        if alt and not any(a in nome_norm for a in alt):
            return False
    return not any(e.strip() and e.strip() in nome_norm
                   for e in str(esclusi or '').split('|'))


def istante(fonte, data, orario, scarto=0.0):
    """Riporta a UTC, che e' l'unico modo per confrontare fonti diverse.

    `scarto` are the hours to add to reach UTC, measured from this very batch by
    `timezones.measure()` rather than written down here. It applies to the
    sources whose own clock has to be taken as given; nasdaq and yahoo carry
    their zone in the data and ignore it.

    Nasdaq: il campo si chiama 'gmt' ma NON e' GMT, e' ora di New York. Provato
    su orari noti -- il CPI USA risulta alle 08:30, che e' l'orario ET del
    rilascio (12:30 UTC), e l'istogramma degli eventi americani ha i picchi a
    08:30 e 10:00. Trattarlo come UTC sfasa ogni riga di 4-5 ore e, sugli
    eventi asiatici, sposta la giornata.
    """
    data, orario = str(data).strip(), str(orario).strip()
    try:
        if fonte == 'nasdaq':
            # La data e' anche avanti di un giorno rispetto al rilascio: l'API
            # interrogata su ?date=X restituisce gli eventi del giorno PRIMA.
            # Verificato su quattro date indipendenti e note -- CPI USA di
            # luglio (12 ago), decisione RBA (11), sussidi del giovedi (13),
            # Sentix del lunedi (10) -- tutte e quattro a +1.
            g = datetime.strptime(data, '%Y-%m-%d') - timedelta(days=1)
            o = datetime.strptime(orario, '%H:%M')
            locale = g + timedelta(hours=o.hour, minutes=o.minute)
            return locale.replace(tzinfo=ZoneInfo('America/New_York')).astimezone(
                timezone.utc).replace(tzinfo=None), 'minute'
        if fonte == 'tradays':
            g = datetime.strptime(f"{data.split(',')[0].strip()} {ANNO}", '%d %B %Y')
            o = datetime.strptime(orario, '%H:%M')
            return g + timedelta(hours=o.hour + scarto, minutes=o.minute), 'minute'
        if fonte == 'yahoo':
            g = datetime.strptime(data, '%Y-%m-%d')
            o = datetime.strptime(orario.replace(' UTC', '').strip(), '%I:%M %p')
            # '12:00 AM UTC' su 537 righe di 1620: non e' un orario, e' cio'
            # che Yahoo scrive quando l'orario non lo sa. Spacciarlo per
            # mezzanotte esatta fa credere all'hub di conoscere il minuto, e
            # `known_from` finisce per dichiarare pubblico un dato ore prima
            # che uscisse -- l'unico errore che il ponte esiste per evitare.
            if (o.hour, o.minute) == (0, 0):
                return g, 'day'
            return g + timedelta(hours=o.hour, minutes=o.minute), 'minute'
        if fonte == 'myfxbook':
            g = datetime.strptime(data, '%Y-%m-%d')
            o = datetime.strptime(orario, '%H:%M')
            return g + timedelta(hours=o.hour + scarto,
                                 minutes=o.minute), 'minute'
        g = datetime.strptime(data, '%Y-%m-%d')
        if orario and re.match(r'^\d{1,2}:\d{2}$', orario):
            o = datetime.strptime(orario, '%H:%M')
            return g + timedelta(hours=o.hour, minutes=o.minute), 'minute'
        return g, 'day'
    except ValueError:
        pass
    # Nothing parsed as a time, so this is a date and only a date. The offset is
    # deliberately NOT applied: shifting midnight by the source's offset invents
    # an hour the source never published, and the row goes on to declare itself
    # 'day' precision anyway. Better a date that is honest about knowing no time.
    for f, s in (('%Y-%m-%d', data), ('%d %B %Y', f"{data.split(',')[0].strip()} {ANNO}")):
        try:
            return datetime.strptime(s, f), 'day'
        except ValueError:
            continue
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


def raccogli(catalogo, respinti=frozenset(), legami=None):
    """Le regole propongono, le decisioni per fonte dispongono.

    `respinti` sono le terne (fonte, paese, nome) che qualcuno ha guardato e
    tenuto fuori: stesso nome, trasformazione diversa. Nasdaq pubblica
    'Housing Starts' come livello in milioni di unita' dove tutti gli altri
    danno la variazione mensile, e nessuna esclusione sul nome puo' separarli
    perche' nel nome non c'e' niente da separare.
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
                    # anche se la regex lo riconoscerebbe. Senza il secondo
                    # verso, legare 'CPI' di Nasdaq al CPI indiano lo lascia
                    # comunque cadere anche su ogni altro indicatore indiano
                    # la cui regola contenga 'cpi'.
                    if legato != voce['indicator_key']:
                        continue
                    aggiunti += 1
                elif not regola_ok(r.norm, voce['match_rules'], voce['match_excludes']):
                    continue
                ist, prec = istante(fonte, r.Data_Rilascio, r.Orario, scarto)
                if ist is None:
                    continue
                periodo = r.get('Periodo_Riferimento', '')
                periodo = None if periodo in ('N/D', '', 'nan') else periodo
                def pulisci(v):
                    v = str(r.get(v, '')).strip()
                    return None if v in ('', 'N/D', '-', 'nan') else v
                osservazioni.append(CalendarObservation(
                    indicator_key=voce['indicator_key'],
                    country_iso3=voce['country_iso3'],
                    source=fonte, provenance=prov,
                    source_event_name=r.Evento,
                    release_utc=ist, release_precision=prec,
                    reference_period=periodo,
                    reference_date=fine_periodo(periodo, ist.date()),
                    actual=pulisci('Attuale'), consensus=pulisci('Previsto'),
                    previous=pulisci('Precedente'), revised_from=pulisci('Revisione'),
                    impact=pulisci('Importanza'),
                    vintage_date=date(2026, 8, 14),
                ))
                n += 1
        per_fonte[fonte] = n
    if scartati or aggiunti:
        print(f'  ({scartati} righe respinte, {aggiunti} agganciate da decisioni per fonte)')
    return osservazioni, per_fonte
