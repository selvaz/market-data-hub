# -*- coding: utf-8 -*-
"""Tradays (MQL5): il calendario, col paese vero e non solo la valuta.

Il paese viene letto dall'href dell'evento, non dalla colonna valuta. Su MQL5
quella colonna e' EUR per tutta l'eurozona, quindi CPI tedesco e CPI francese
sono indistinguibili -- ed e' esattamente il modo in cui i rilasci nazionali
dei membri finiscono contati come rilasci dell'area euro. L'href invece lo
dice: /en/economic-calendar/germany/consumer-price-index-mm

Gli orari sono resi dal JavaScript della pagina nell'ora di chi guarda, quindi
il browser va fissato a UTC: se ne occupa `setup_browser()`, e `timezones.py`
verifica su ogni lotto che il fissaggio abbia tenuto.
"""
import re
import time

import pandas as pd
from bs4 import BeautifulSoup

from .browser import setup_browser

SETTIMANE_INDIETRO = 13

PAESE_ISO = {
    'united-states': 'US', 'china': 'CN', 'european-union': 'EU', 'germany': 'DE',
    'france': 'FR', 'italy': 'IT', 'spain': 'ES', 'netherlands': 'NL',
    'united-kingdom': 'GB', 'japan': 'JP', 'india': 'IN', 'mexico': 'MX',
    'brazil': 'BR', 'canada': 'CA', 'australia': 'AU', 'south-korea': 'KR',
    'korea': 'KR', 'taiwan': 'TW', 'switzerland': 'CH', 'new-zealand': 'NZ',
    'south-africa': 'ZA', 'norway': 'NO', 'sweden': 'SE', 'singapore': 'SG',
    'hong-kong': 'HK', 'austria': 'AT', 'belgium': 'BE', 'finland': 'FI',
    'greece': 'GR', 'ireland': 'IE', 'portugal': 'PT', 'slovakia': 'SK',
}


def _testo(nodo):
    return nodo.get_text(' ', strip=True) if nodo else ''


def paese_da_link(nodo):
    a = nodo.select_one('.ec-table__col_event a')
    if not a or not a.get('href'):
        return ''
    m = re.search(r'/economic-calendar/([^/]+)/', a['href'])
    if not m:
        return ''
    return PAESE_ISO.get(m.group(1), m.group(1)[:2].upper())


def _raccogli(soup, righe):
    corpo = soup.select_one('.ec-table__body')
    if corpo is None:
        return 0
    giorno, n = 'N/D', 0
    for nodo in corpo.find_all('div', recursive=False):
        classi = nodo.get('class') or []
        if 'ec-table__title' in classi:
            giorno = _testo(nodo)
            continue
        if 'ec-table__item' not in classi:
            continue
        imp = nodo.select_one('.ec-table__importance')
        ci = ' '.join(imp.get('class') or []) if imp else ''
        righe.append({
            'Data_Rilascio': giorno,
            'Orario': _testo(nodo.select_one('.ec-table__col_time > div')),
            'Valuta': _testo(nodo.select_one('.ec-table__curency-name')),
            'Paese': paese_da_link(nodo),
            'Importanza': next((liv for liv in ('high', 'medium', 'low')
                                if f'importance_{liv}' in ci), 'none'),
            'Evento': _testo(nodo.select_one('.ec-table__col_event')),
            'Periodo_Riferimento': 'N/D',     # non esposto in lista
            'Attuale': _testo(nodo.select_one('.ec-table__col_actual')),
            'Previsto': _testo(nodo.select_one('.ec-table__col_forecast')),
            'Precedente': _testo(nodo.select_one('.ec-table__col_previous')),
            'Revisione': 'N/D',
            'Fonte': 'Tradays'})
        n += 1
    return n


def scarica(settimane=SETTIMANE_INDIETRO, verboso=True):
    """Settimana corrente piu' `settimane` indietro, navigate col JS della pagina."""
    d = setup_browser()
    righe = []
    try:
        d.get('https://www.mql5.com/en/economic-calendar')
        time.sleep(9)
        n = _raccogli(BeautifulSoup(d.page_source, 'html.parser'), righe)
        if verboso:
            print(f'  settimana 0: {n}', flush=True)
        for passo in range(1, settimane + 1):
            try:
                d.execute_script('Calendar.getCalendarPrevWeek();')
            except Exception as e:
                print('  navigazione JS interrotta:', str(e)[:80], flush=True)
                break
            time.sleep(6)
            n = _raccogli(BeautifulSoup(d.page_source, 'html.parser'), righe)
            if verboso:
                print(f'  settimana -{passo}: {n}', flush=True)
            if n == 0:
                break
    finally:
        d.quit()

    df = pd.DataFrame(righe).replace(['', '-'], pd.NA).fillna('N/D')
    if verboso and len(df):
        risolti = int((df.Paese != 'N/D').sum())
        print(f'  paese risolto dall href: {risolti}/{len(df)}', flush=True)
    return df
