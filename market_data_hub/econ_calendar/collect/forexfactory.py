# -*- coding: utf-8 -*-
"""Collect Forex Factory's public current-week JSON calendar feed."""
import hashlib
import json
import os
import re
import sys
import tempfile
import time
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


MANIFEST_NAME = 'manifest.jsonl'
MAX_ATTEMPTS = 2          # one try plus exactly one retry: never a loop
RETRY_BACKOFF_S = 60      # the feed 429s after ~4 calls in 30 minutes
_SNAPSHOT_NAME = re.compile(r'^\d{4}-\d{2}-\d{2}(\.\d{6})?\.json$')
_URL = re.compile(r'https?://\S+')
_parse_iso = datetime.fromisoformat   # bound at import: tests patch `datetime`


def _sleep(seconds):
    time.sleep(seconds)


def _redact(text, limit=200):
    """Short, single-line error text with URLs (which may carry tokens) removed."""
    text = _URL.sub('<url>', ' '.join(str(text).split()))
    return text[:limit]


def _utc_z(moment):
    """ISO 8601 UTC with a Z suffix; a naive moment is read as local time."""
    return moment.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _feed_week(feed):
    """(min, max) event dates as written in the feed, or (None, None)."""
    days = []
    for event in feed:
        try:
            days.append(_parse_iso(event['date']).date())
        except (KeyError, TypeError, ValueError):
            continue
    if not days:
        return None, None
    return min(days).isoformat(), max(days).isoformat()


def _append_manifest(directory, record):
    """Append one JSON line; earlier lines are never rewritten."""
    directory.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=True) + '\n'
    with open(directory / MANIFEST_NAME, 'a', encoding='utf-8', newline='\n') as stream:
        stream.write(line)
        stream.flush()
        os.fsync(stream.fileno())


def _fetch(record):
    """GET with at most one retry (connection error, timeout, 429, 5xx).

    Fills the record's http_status and clock as it goes and returns the 200
    response, or raises RuntimeError. Never more than MAX_ATTEMPTS calls.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.get(URL, headers=HEADERS, timeout=30)
        except requests.RequestException as e:
            record['http_status'] = None
            failure = RuntimeError(f'Forex Factory feed request failed: {e}')
            failure.__cause__ = e
            retryable = True
        else:
            status = response.status_code
            record['http_status'] = status
            if status == 200:
                return response
            body = getattr(response, 'content', None)
            if isinstance(body, bytes):
                record['sha256'] = hashlib.sha256(body).hexdigest()
                record['bytes'] = len(body)
            failure = RuntimeError(f'Forex Factory feed returned HTTP {status}')
            retryable = status == 429 or status >= 500
        record['_clock'] = datetime.now()
        if not retryable or attempt == MAX_ATTEMPTS:
            raise failure
        _sleep(RETRY_BACKOFF_S)


def archive_raw(directory=DEFAULT_RAW_DIR) -> int:
    """Fetch and preserve response bytes; 0 written, 3 unchanged, raises on failure.

    Every call appends exactly one line to ``manifest.jsonl`` in ``directory``
    (written, unchanged or failed, with the reason) before returning or
    raising. One retry after RETRY_BACKOFF_S for connection errors, timeouts,
    429 and 5xx; a body that is not a JSON list is never retried.

    No calendar parsing, DB connection or market_data lock is involved.
    Compare with the latest snapshot for the local fetch date. On Windows,
    rename refuses an existing destination, including a concurrent writer's.
    """
    directory = Path(directory)
    record = {'outcome': 'failed', 'file': None, 'sha256': None, 'bytes': None,
              'http_status': None, 'error': None,
              'feed_week_start': None, 'feed_week_end': None,
              '_clock': datetime.now()}
    try:
        code = _archive(directory, record)
    except Exception as e:
        record['outcome'] = 'failed'
        record['file'] = None
        record['error'] = _redact(f'{type(e).__name__}: {e}')
        try:
            _append_manifest(directory, _manifest_line(record))
        except OSError as manifest_error:
            # The original failure is the one the caller must see.
            print(f'manifest append FAILED: {_redact(manifest_error)}',
                  file=sys.stderr, flush=True)
        raise
    _append_manifest(directory, _manifest_line(record))
    return code


def _manifest_line(record):
    clock = record['_clock']
    return {
        'captured_at_utc': _utc_z(clock),
        'local_date': clock.strftime('%Y-%m-%d'),
        'outcome': record['outcome'],
        'file': record['file'],
        'sha256': record['sha256'],
        'bytes': record['bytes'],
        'http_status': record['http_status'],
        'error': record['error'],
        'feed_week_start': record['feed_week_start'],
        'feed_week_end': record['feed_week_end'],
    }


def _archive(directory, record) -> int:
    response = _fetch(record)
    # Captured at the moment the successful response arrived.
    fetched = record['_clock'] = datetime.now()
    content = response.content
    record['sha256'] = hashlib.sha256(content).hexdigest()
    record['bytes'] = len(content)
    # Bytes are stored unchanged; this only refuses a block page or empty body
    # that would otherwise become the day's snapshot.
    try:
        feed = json.loads(content)
    except ValueError:
        feed = None
    if not isinstance(feed, list):
        raise RuntimeError('Forex Factory feed is not a JSON list; nothing archived')
    record['feed_week_start'], record['feed_week_end'] = _feed_week(feed)
    day = fetched.strftime('%Y-%m-%d')
    base = directory / f'{day}.json'
    revisions = sorted(directory.glob(f'{day}.[0-9][0-9][0-9][0-9][0-9][0-9].json'))
    latest = revisions[-1] if revisions else base
    if latest.exists() and latest.read_bytes() == content:
        record['outcome'] = 'unchanged'
        record['file'] = str(latest)
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
    record['outcome'] = 'written'
    record['file'] = str(target)
    return 0


class Snapshot(tuple):
    """``(captured_at_utc, path)`` that also carries ``captured_at_source``.

    ``captured_at_source`` is ``"manifest"`` (exact capture time recorded by
    archive_raw) or ``"mtime"`` (fallback: file modification time, which a
    copy or restore can change -- treat as approximate).
    """

    def __new__(cls, captured_at_utc, path, captured_at_source):
        self = super().__new__(cls, (captured_at_utc, path))
        self.captured_at_source = captured_at_source
        return self


def load_snapshots(directory=DEFAULT_RAW_DIR):
    """Snapshots in ``directory`` as ``[(captured_at_utc, path)]``, oldest first.

    ``captured_at_utc`` is a timezone-aware UTC datetime. Times come from the
    manifest's ``written`` lines (the file's content was first seen then);
    snapshot files the manifest does not mention fall back to their mtime.
    To pick the snapshot for a release, take the last entry with
    ``captured_at_utc < release_utc`` (strictly before). Malformed manifest
    lines and lines whose file has gone are skipped.
    """
    directory = Path(directory)
    found = {}
    manifest = directory / MANIFEST_NAME
    if manifest.exists():
        for raw_line in manifest.read_text(encoding='utf-8').splitlines():
            try:
                entry = json.loads(raw_line)
                if entry['outcome'] != 'written' or not entry['file']:
                    continue
                moment = datetime.strptime(entry['captured_at_utc'], '%Y-%m-%dT%H:%M:%SZ')
                path = directory / Path(entry['file']).name
            except (ValueError, KeyError, TypeError):
                continue
            if path.exists():
                found[path.name] = Snapshot(moment.replace(tzinfo=timezone.utc), path,
                                            'manifest')
    if directory.is_dir():
        for path in directory.iterdir():
            if _SNAPSHOT_NAME.match(path.name) and path.name not in found:
                moment = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                found[path.name] = Snapshot(moment, path, 'mtime')
    return sorted(found.values(), key=lambda s: (s[0], s[1].name))


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
