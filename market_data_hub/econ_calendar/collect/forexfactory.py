# -*- coding: utf-8 -*-
"""Collect Forex Factory's public current-week JSON calendar feed."""
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from .myfxbook import COLONNE

URL = 'https://nfs.faireconomy.media/ff_calendar_thisweek.json'
HEADERS = {'User-Agent': 'market-data-hub economic-calendar collector'}
DEFAULT_RAW_DIR = Path(r'C:\ProgramData\InvestmentCommittee\econ_calendar\raw')
VALUTA_PAESE = {
    'USD': 'US', 'EUR': 'EU', 'GBP': 'GB', 'JPY': 'JP', 'AUD': 'AU',
    'NZD': 'NZ', 'CAD': 'CA', 'CHF': 'CH', 'CNY': 'CN',
}
EUR_ISSUER_PREFIXES = {
    'German ': 'DE', 'French ': 'FR', 'Italian ': 'IT', 'Spanish ': 'ES',
}


def archive_raw(directory=DEFAULT_RAW_DIR) -> int:
    """Fetch once and preserve response bytes; 0 written, 3 unchanged.

    No calendar parsing, DB connection or market_data lock is involved.
    Compare with the latest snapshot for the local fetch date. On Windows,
    rename refuses an existing destination, including a concurrent writer's.
    """
    try:
        response = requests.get(URL, headers=HEADERS, timeout=30)
    except requests.RequestException as e:
        raise RuntimeError(f'Forex Factory feed request failed: {e}') from e
    if response.status_code != 200:
        raise RuntimeError(f'Forex Factory feed returned HTTP {response.status_code}')
    content = response.content
    # Bytes are stored unchanged; this only refuses a block page or empty body
    # that would otherwise become the day's snapshot.
    try:
        is_feed = isinstance(json.loads(content), list)
    except ValueError:
        is_feed = False
    if not is_feed:
        raise RuntimeError('Forex Factory feed is not a JSON list; nothing archived')
    fetched = datetime.now()
    day = fetched.strftime('%Y-%m-%d')
    directory = Path(directory)
    base = directory / f'{day}.json'
    revisions = sorted(directory.glob(f'{day}.[0-9][0-9][0-9][0-9][0-9][0-9].json'))
    latest = revisions[-1] if revisions else base
    if latest.exists() and latest.read_bytes() == content:
        return 3
    target = directory / f'{day}.{fetched:%H%M%S}.json' if base.exists() else base
    # A second different feed in the same second must never erase a snapshot.
    if target.exists():
        raise FileExistsError(f'archive snapshot already exists: {target}')
    directory.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=f'.{day}.',
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.rename(target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return 0


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
