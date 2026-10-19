"""Explicit legacy period spellings, without release-date or year inference."""
from datetime import date
import re
import unicodedata


def fold(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', value.casefold())
                   if not unicodedata.combining(c))


MONTHS = {}
for n, words in enumerate((
    'january jan enero gennaio janvier janeiro januar',
    'february feb febrero febbraio fevrier fevereiro februar',
    'march mar marzo mars marco marz',
    'april apr abril aprile avril', 'may mayo maggio mai maio',
    'june jun junio giugno juin junho juni', 'july jul julio luglio juillet julho juli',
    'august aug agosto aout', 'september sep sept septiembre settembre septembre setembro set',
    'october oct octubre ottobre octobre outubro oktober',
    'november nov noviembre novembre novembro', 'december dec diciembre dicembre decembre dezembro dezember'), 1):
    MONTHS.update({word: n for word in words.split()})


def frequency(value):
    value = value.strip().casefold()
    return {'monthly': 'M', 'quarterly': 'Q', 'annual': 'A', 'yearly': 'A', 'y': 'A',
            'daily': 'D', 'weekly': 'W', 'biweekly': 'W', 'bi-weekly': 'W', 'fortnightly': 'W',
            'e': 'IRREGULAR', 'event': 'IRREGULAR', 'aperiodic': 'IRREGULAR',
            'per meeting': 'IRREGULAR', 'every 6 weeks': 'IRREGULAR',
            '8 times per year': 'IRREGULAR', 'bi-monthly': 'IRREGULAR'}.get(value, value.upper())


def parsed_period(raw, geography=None):
    if not isinstance(raw, str):
        return None, None
    text = fold(raw.strip())
    text = re.sub(r'^week\s+ending\s+', '', text)
    if re.fullmatch(r'[0-9]{4}', text) and int(text) > 0:
        return text, 'A'
    if re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', text):
        try:
            return date.fromisoformat(text).isoformat(), 'D'
        except ValueError:
            return None, None
    month = re.fullmatch(r'([0-9]{4})[-.]([0-9]{1,2})', text)
    if month:
        year, number = map(int, month.groups())
    else:
        month = re.fullmatch(r'([0-9]{1,2})/([0-9]{4})', text)
        if month:
            number, year = map(int, month.groups())
        else:
            month = re.fullmatch(r'([a-z]+)\.?[\s-]+(?:de\s+)?([0-9]{4})', text)
            year, number = (int(month[2]), MONTHS.get(month[1], 0)) if month else (0, 0)
    if year and number:
        try:
            date(year, number, 1)
            return f'{year:04}-{number:02}', 'M'
        except ValueError:
            return None, None
    quarter = re.fullmatch(r'([0-9]{4})[-\s]?q([1-4])', text)
    if quarter:
        return f'{quarter[1]}-Q{quarter[2]}', 'Q'
    quarter = re.fullmatch(r'(?:q|quarter\s+)([1-4])[/\s]+([0-9]{4})', text)
    if not quarter:
        quarter = re.fullmatch(r'([1-4])(?:st|nd|rd|th)?\s+quarter\s+(?:of\s+)?([0-9]{4})', text)
    if quarter:
        return f'{quarter[2]}-Q{quarter[1]}', 'Q'
    quarter = re.fullmatch(r'(first|second|third|fourth|i|ii|iii|iv|primero|segundo|tercero|cuarto)\s+'
                           r'(?:quarter|trimestre)\s+(?:(?:of|de)\s+)?([0-9]{4})', text)
    if quarter:
        words = [('first', 'i', 'primero'), ('second', 'ii', 'segundo'),
                 ('third', 'iii', 'tercero'), ('fourth', 'iv', 'cuarto')]
        n = next(n for n, names in enumerate(words, 1) if quarter[1] in names)
        return f'{quarter[2]}-Q{n}', 'Q'
    roc = re.fullmatch(r'(?:roc\s*|民國|民国)?([0-9]{2,4})(?:年|/)([0-9]{1,2})月?', text)
    if not roc and geography == 'TWN':
        roc = re.fullmatch(r'([0-9]{3})([0-9]{2})', text)
    if roc:
        year, month = map(int, roc.groups())
        if year < 1000:
            if geography != 'TWN' and not text.startswith(('roc', '民國', '民国')):
                return None, None
            year += 1911
        try:
            date(year, month, 1)
            return f'{year:04}-{month:02}', 'M'
        except ValueError:
            return None, None
    day = re.fullmatch(r'([a-z]+)\.?\s+([0-9]{1,2}),?\s+([0-9]{4})', text)
    if day:
        month, number, year = MONTHS.get(day[1], 0), int(day[2]), int(day[3])
    else:
        day = re.fullmatch(r'([0-9]{1,2})\s+(?:de\s+)?([a-z]+)\.?\s+(?:de\s+)?([0-9]{4})', text)
        number, month, year = (int(day[1]), MONTHS.get(day[2], 0), int(day[3])) if day else (0, 0, 0)
    try:
        return date(year, month, number).isoformat(), 'D'
    except ValueError:
        return None, None


def explicit_period(raw, freq, geography=None):
    period, kind = parsed_period(raw, geography)
    freq = frequency(freq)
    if period and not period.startswith('0000') and (freq in {'UNKNOWN', 'IRREGULAR'} or
            freq == kind or (kind == 'D' and freq == 'W')):
        return period
    return None
