# -*- coding: utf-8 -*-
"""Calendario economico Nasdaq: una GET per giornata, niente browser ne' chiave.

Perche' questa fonte al posto di Forex Factory: storico profondo (risponde
almeno dal 2015), venti paesi, e una descrizione testuale per ogni evento.
Non pubblica il periodo di riferimento ne' un livello di impatto.
"""
import argparse
import html
import json
import re
import subprocess
import time
from datetime import datetime, timedelta

import pandas as pd

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')

PAESE_ISO = {
    'United States': 'US', 'United Kingdom': 'GB', 'Euro Zone': 'EU', 'Germany': 'DE',
    'France': 'FR', 'Italy': 'IT', 'Spain': 'ES', 'Netherlands': 'NL', 'Belgium': 'BE',
    'Austria': 'AT', 'Ireland': 'IE', 'Portugal': 'PT', 'Greece': 'GR', 'Finland': 'FI',
    'Japan': 'JP', 'China': 'CN', 'India': 'IN', 'South Korea': 'KR', 'Taiwan': 'TW',
    'Hong Kong': 'HK', 'Singapore': 'SG', 'Australia': 'AU', 'New Zealand': 'NZ',
    'Canada': 'CA', 'Mexico': 'MX', 'Brazil': 'BR', 'Argentina': 'AR', 'Chile': 'CL',
    'Colombia': 'CO', 'Switzerland': 'CH', 'Norway': 'NO', 'Sweden': 'SE',
    'Denmark': 'DK', 'Poland': 'PL', 'Russia': 'RU', 'Turkey': 'TR', 'Israel': 'IL',
    'South Africa': 'ZA', 'Indonesia': 'ID', 'Malaysia': 'MY', 'Thailand': 'TH',
    'Czech Republic': 'CZ', 'Hungary': 'HU', 'Romania': 'RO',
}


def pulisci(valore):
    """I campi Nasdaq arrivano con entita' HTML e spazi unificatori."""
    if valore is None:
        return ''
    testo = html.unescape(html.unescape(str(valore)))
    testo = re.sub(r'<[^>]+>', ' ', testo)
    testo = testo.replace('\xa0', ' ')
    return re.sub(r'\s+', ' ', testo).strip()


def giornata(data_iso, tentativi=3, pausa=1.5):
    url = f'https://api.nasdaq.com/api/calendar/economicevents?date={data_iso}'
    for n in range(tentativi):
        r = subprocess.run(['curl', '-sS', '-m', '30', '-A', UA, url],
                           capture_output=True, text=True, encoding='utf-8', errors='replace')
        try:
            d = json.loads(r.stdout)
            return ((d.get('data') or {}).get('rows') or [])
        except Exception:
            time.sleep(pausa * (n + 1))
    print(f'  ! {data_iso}: nessuna risposta valida dopo {tentativi} tentativi', flush=True)
    return []


def scarica(da, a, pausa=0.7):
    giorno = datetime.strptime(da, '%Y-%m-%d')
    fine = datetime.strptime(a, '%Y-%m-%d')
    righe = []
    while giorno <= fine:
        iso = giorno.strftime('%Y-%m-%d')
        eventi = giornata(iso)
        for e in eventi:
            paese = pulisci(e.get('country'))
            righe.append({
                'Data_Rilascio': iso,
                'Orario': pulisci(e.get('gmt')),
                'Paese': PAESE_ISO.get(paese, ''),
                'Paese_esteso': paese,
                'Importanza': '',                 # Nasdaq non pubblica l'impatto
                'Evento': pulisci(e.get('eventName')),
                'Periodo_Riferimento': 'N/D',     # ne' il periodo di riferimento
                'Attuale': pulisci(e.get('actual')),
                'Previsto': pulisci(e.get('consensus')),
                'Precedente': pulisci(e.get('previous')),
                'Revisione': '',
                'Descrizione_fonte': pulisci(e.get('description'))[:600],
                'Fonte': 'Nasdaq',
            })
        if eventi:
            print(f'  {iso}: {len(eventi)}', flush=True)
        giorno += timedelta(days=1)
        time.sleep(pausa)
    return pd.DataFrame(righe)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--da', default='2026-05-11')
    p.add_argument('--a', default='2026-08-16')
    p.add_argument('--out', default='nasdaq_universo.csv')
    args = p.parse_args()

    df = scarica(args.da, args.a)
    if df.empty:
        print('nessun dato')
        raise SystemExit(1)

    df.to_csv(args.out, index=False, encoding='utf-8-sig')
    print(f'\neventi: {len(df)} su {df.Data_Rilascio.nunique()} giornate')
    print('  con attuale :', int((df.Attuale != '').sum()))
    print('  con previsto:', int((df.Previsto != '').sum()))
    print('  con descrizione:', int((df.Descrizione_fonte != '').sum()))
    print('  paesi non mappati:', sorted({r for r in df[df.Paese == ''].Paese_esteso})[:12])
    print('\npaesi piu presenti:')
    print(df.Paese_esteso.value_counts().head(18).to_string())
    print('\nsalvato ->', args.out)
