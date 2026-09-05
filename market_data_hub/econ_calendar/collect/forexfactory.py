# -*- coding: utf-8 -*-
"""Collect Forex Factory's public current-week JSON calendar feed."""
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from .myfxbook import COLONNE

URL = 'https://nfs.faireconomy.media/ff_calendar_thisweek.json'
HEADERS = {'User-Agent': 'market-data-hub economic-calendar collector'}
VALUTA_PAESE = {
    'USD': 'US', 'EUR': 'EU', 'GBP': 'GB', 'JPY': 'JP', 'AUD': 'AU',
    'NZD': 'NZ', 'CAD': 'CA', 'CHF': 'CH', 'CNY': 'CN',
}
EUR_ISSUER_PREFIXES = {
    'German ': 'DE', 'French ': 'FR', 'Italian ': 'IT', 'Spanish ': 'ES',
}


def paese_iso(country, title):
    """Forex Factory uses EUR for both EMU and member-state releases."""
    paese = VALUTA_PAESE.get(country)
    if country == 'EUR':
        for prefisso, iso2 in EUR_ISSUER_PREFIXES.items():
            if str(title).startswith(prefisso):
                return iso2
    return paese


def _righe(eventi, da, a):
    inizio = datetime.strptime(da, '%Y-%m-%d').date()
    fine = datetime.strptime(a, '%Y-%m-%d').date()
    righe = []
    for evento in eventi:
        if not isinstance(evento, dict):
            raise ValueError('Forex Factory feed contains a non-object event')
        mancanti = set(('title', 'country', 'date', 'impact', 'forecast', 'previous')) - set(evento)
        if mancanti:
            raise ValueError(f"Forex Factory feed event lacks keys: {sorted(mancanti)}")
        if evento['country'] == 'All' or evento['impact'] == 'Holiday':
            continue
        try:
            istante = datetime.fromisoformat(evento['date']).astimezone(timezone.utc)
        except (TypeError, ValueError) as e:
            raise ValueError(f"invalid Forex Factory event date: {evento['date']!r}") from e
        if not inizio <= istante.date() <= fine:
            continue
        paese = paese_iso(evento['country'], evento['title'])
        if paese is None:
            continue
        righe.append({
            'Data_Rilascio': istante.strftime('%Y-%m-%d'),
            'Orario': istante.strftime('%H:%M'),
            'Paese': paese,
            'Importanza': str(evento['impact']).lower(),
            'Evento': evento['title'],
            'Periodo_Riferimento': 'N/D',
            'Attuale': '',
            'Previsto': evento['forecast'],
            'Precedente': evento['previous'],
            'Revisione': '',
            'Fonte': 'ForexFactory',
        })
    return righe


def scarica(da, a, out) -> pd.DataFrame:
    """Fetch once, append filtered releases, then retain the latest revision."""
    try:
        risposta = requests.get(URL, headers=HEADERS, timeout=30)
    except requests.RequestException as e:
        raise RuntimeError(f'Forex Factory feed request failed: {e}') from e
    if risposta.status_code != 200:
        raise RuntimeError(f'Forex Factory feed returned HTTP {risposta.status_code}')
    try:
        eventi = risposta.json()
    except ValueError as e:
        raise RuntimeError('Forex Factory feed returned invalid JSON') from e
    if not isinstance(eventi, list):
        raise RuntimeError('Forex Factory feed JSON must be an event list')

    out = Path(out)
    nuovi = pd.DataFrame(_righe(eventi, da, a), columns=COLONNE)
    nuovo_file = not out.exists()
    nuovi.to_csv(out, mode='a', index=False, header=nuovo_file, encoding='utf-8-sig')
    raccolti = pd.read_csv(out).fillna('')
    raccolti = raccolti.drop_duplicates(
        subset=['Data_Rilascio', 'Paese', 'Evento', 'Periodo_Riferimento'],
        keep='last',
    ).reset_index(drop=True)
    raccolti.to_csv(out, index=False, encoding='utf-8-sig')
    return raccolti
