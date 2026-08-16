# -*- coding: utf-8 -*-
"""Forex Factory, estrazione dallo stato interno della pagina.

Perche' non dal DOM: nel DOM l'impatto e' codificato solo in una classe CSS
(icon--ff-impact-yel) e l'orario e' una stringa nel fuso del sito, mentre
window.calendarComponentStates espone impactName e 'dateline', il timestamp
unix dell'evento - quindi niente ricostruzione dei fusi.

Limite noto e non aggirabile: solo la settimana corrente. Qualsiasi URL con
?week= fa scattare la verifica anti-bot di Cloudflare, e anche le frecce di
paginazione della pagina rimbalzano sulla settimana corrente. Per avere
profondita' storica questo script va eseguito una volta a settimana e i
risultati accumulati (vedi --accumula).
"""
import argparse
import time
from datetime import datetime, timezone

import pandas as pd

from .browser import setup_browser

# Forex Factory usa codici propri (CH = China, non Svizzera): la valuta e'
# piu' affidabile del campo country per risalire al paese.
VALUTA_PAESE = {'USD': 'US', 'EUR': 'EU', 'GBP': 'GB', 'JPY': 'JP', 'CNY': 'CN',
                'AUD': 'AU', 'CAD': 'CA', 'NZD': 'NZ', 'CHF': 'CH'}

ESTRAI = """
  const s = window.calendarComponentStates;
  if (!s) return null;
  const st = s[Object.keys(s)[0]];
  if (!st || !Array.isArray(st.days)) return null;
  const out = [];
  for (const g of st.days) {
    for (const e of (g.events || [])) {
      out.push({
        id: e.id, nome: e.name, valuta: e.currency, paeseFF: e.country,
        impatto: e.impactName, impattoTitolo: e.impactTitle,
        dateline: e.dateline, orarioEtichetta: e.timeLabel, data: e.date,
        attuale: e.actual, previsto: e.forecast, precedente: e.previous,
        revisione: e.revision, url: e.url
      });
    }
  }
  return out;
"""


def scarica(attesa=12):
    d = setup_browser()
    try:
        d.get('https://www.forexfactory.com/calendar')
        time.sleep(attesa)
        testo = d.execute_script("return document.body.innerText.slice(0, 200);")
        if 'security verification' in testo.lower() or 'Just a moment' in testo:
            print('  ! Forex Factory ha risposto con la verifica anti-bot: nessun dato')
            return pd.DataFrame()
        grezzi = d.execute_script(ESTRAI)
    finally:
        d.quit()

    if not grezzi:
        print('  ! stato interno non disponibile')
        return pd.DataFrame()

    righe = []
    for e in grezzi:
        istante = (datetime.fromtimestamp(e['dateline'], tz=timezone.utc)
                   if e.get('dateline') else None)
        righe.append({
            'Data_Rilascio': istante.strftime('%Y-%m-%d') if istante else '',
            'Orario': istante.strftime('%H:%M') if istante else (e.get('orarioEtichetta') or ''),
            'Istante_UTC': istante.isoformat() if istante else '',
            'Paese': VALUTA_PAESE.get(e.get('valuta'), ''),
            'Valuta': e.get('valuta') or '',
            'Importanza': e.get('impatto') or '',
            'Evento': e.get('nome') or '',
            'Periodo_Riferimento': 'N/D',       # Forex Factory non lo pubblica
            'Attuale': e.get('attuale') or '',
            'Previsto': e.get('previsto') or '',
            'Precedente': e.get('precedente') or '',
            'Revisione': e.get('revisione') or '',
            'Fonte': 'Forex Factory',
            'id_ff': e.get('id'),
        })
    return pd.DataFrame(righe)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--accumula', action='store_true',
                   help='unisce al file esistente deduplicando su id_ff')
    args = p.parse_args()

    df = scarica()
    if df.empty:
        raise SystemExit(1)

    print(f'eventi scaricati: {len(df)}')
    print('  giornate:', df.Data_Rilascio.nunique(),
          f'({df.Data_Rilascio.min()} -> {df.Data_Rilascio.max()})')
    print('  valute:', ', '.join(sorted(df.Valuta.unique())))
    print('  con impatto:', int((df.Importanza != '').sum()),
          '| con attuale:', int((df.Attuale != '').sum()),
          '| con previsto:', int((df.Previsto != '').sum()))
    print(df.Importanza.value_counts().to_string())

    percorso = 'ff_universo.csv'
    if args.accumula:
        try:
            vecchi = pd.read_csv(percorso)
            prima = len(vecchi)
            df = pd.concat([vecchi, df], ignore_index=True)
            df = df.drop_duplicates(subset='id_ff', keep='last')
            print(f'\naccumulo: {prima} righe gia presenti -> {len(df)} totali')
        except FileNotFoundError:
            print('\nnessun archivio precedente, ne creo uno nuovo')

    df.to_csv(percorso, index=False, encoding='utf-8-sig')
    print('salvato ->', percorso)
