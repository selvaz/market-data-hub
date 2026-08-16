# -*- coding: utf-8 -*-
"""Each source's timezone offset, measured from the batch being ingested.

Three of the five sources do not hand over UTC, and none of them says so. The
offsets were once constants measured in August, with a note to re-measure them
after every clock change -- a number that has to be right, cannot be checked,
and fails silently by exactly one hour. This module replaces the note with a
check that runs on every batch.

What each source needs, and why they differ:

``tradays``   renders its times in JavaScript from the *viewer's* clock. Forcing
              the browser through three zones moved 197 of 203 events, so the
              old constant of 7 was this server's Pacific offset, not Tradays'.
              `setup_browser()` pins the browser to UTC, so the expectation here
              is zero -- and a non-zero reading means the pin stopped working.
``yahoo``     labels its times UTC and means it. Expectation: zero.
``myfxbook``  renders server-side and ignores what the browser declares (0 of
              235 events moved), so its offset is real. It is DERIVED here
              rather than written down, because nothing about it is guaranteed
              to hold next month.
``nasdaq``    publishes New York time; `consolidate.istante()` converts it with
              ZoneInfo, which already follows US daylight saving. Checking a
              fixed number here would fail every November for being right.
``forexfactory`` reads a Unix epoch, which has no timezone to get wrong.

Method: releases whose *local* time is publicly fixed. The US 08:30 ET prints
(CPI, jobless claims, retail sales) and the ECB decision at 14:15 CET/CEST are
the anchors. Their local time never moves; their UTC time moves twice a year
with daylight saving, so it is computed per release date rather than written
down -- a fixed UTC hour is right for half the year and one hour wrong for the
other half, which is the exact error this module exists to catch.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

# source -> how its offset is established
DERIVED = ('myfxbook',)          # measured from the batch, every run
EXPECTED_ZERO = ('tradays', 'yahoo')
NOT_APPLICABLE = ('nasdaq', 'forexfactory')

# source -> (country code, event name pattern, LOCAL hour as float, IANA zone)
#
# The hour is the release's *local* time, which is what the issuing agency
# fixes and never changes: the BLS prints at 08:30 New York, the ECB decides at
# 14:15 Frankfurt. The UTC hour those correspond to is computed per release
# date, because it differs by one hour between summer and winter.
#
# The codes are ISO, not currencies, because that is what the sources write by
# the time they reach here: myfxbook labels by currency but its collector now
# maps to ISO before writing, so an anchor on 'USD' would match nothing and the
# batch would be refused for having no anchors rather than for being undatable.
ANCORE = {
    'myfxbook': [
        ('US', r'Inflation Rate|Initial Jobless|Retail Sales', 8.5, 'America/New_York'),
        ('EU', r'Interest Rate', 14.25, 'Europe/Berlin'),
    ],
    'tradays': [
        ('US', r'CPI|Initial Jobless Claims|Retail Sales', 8.5, 'America/New_York'),
    ],
    'yahoo': [
        ('US', r'CPI YY|Initial Jobless', 8.5, 'America/New_York'),
    ],
}

TOLLERANZA = 0.01               # hours; the anchors are whole or half hours


class TimezoneUnknown(RuntimeError):
    """The batch does not say what timezone it is in, so it cannot be ingested.

    Raised rather than defaulted. A default here is a guess about when a
    release became public, and the point-in-time bridge is built on that
    answer being true.
    """


def ore(orario) -> Optional[float]:
    """Hours since midnight, from either '13:45' or '1:45 PM'."""
    o = str(orario).strip()
    if not o:
        return None
    try:
        if 'AM' in o.upper() or 'PM' in o.upper():
            from datetime import datetime
            d = datetime.strptime(o.replace(' UTC', '').strip(), '%I:%M %p')
            return d.hour + d.minute / 60
        h, m = o.split(':')
        return int(h) + int(m) / 60
    except (ValueError, AttributeError):
        return None


def giorno_di(valore, oggi=None):
    """The release date of a row, from whatever the source writes.

    Sources disagree: ISO for most, '13 August' -- with no year -- for Tradays.
    The missing year is taken as the one that puts the date nearest to today
    without landing far in the future, because a batch is collected backwards
    from the present and a January batch legitimately spans two years.
    Returns None when nothing parses: such a row simply cannot serve as an
    anchor, which is safer than dating it by assumption.
    """
    s = str(valore).strip()
    if not s:
        return None
    oggi = oggi or datetime.utcnow().date()
    try:
        return datetime.strptime(s, '%Y-%m-%d').date()
    except ValueError:
        pass
    testa = s.split(',')[0].strip()
    for formato in ('%d %B', '%d %b'):
        for anno in (oggi.year, oggi.year - 1, oggi.year + 1):
            try:
                g = datetime.strptime(f'{testa} {anno}', f'{formato} %Y').date()
            except ValueError:
                continue
            # A batch runs backwards from today; a date more than a couple of
            # months ahead belongs to the previous year, not this one.
            if g - oggi <= timedelta(days=60):
                return g
    return None


def ora_utc_ancora(locale: float, zona: str, giorno) -> Optional[float]:
    """The UTC hour a fixed local release time falls on, on that date.

    08:30 New York is 12:30 UTC in summer and 13:30 in winter. Which one it is
    depends on the date, so it is asked of the date rather than assumed.
    """
    if giorno is None:
        return None
    h, m = divmod(round(locale * 60), 60)
    istante = datetime(giorno.year, giorno.month, giorno.day, h, m,
                       tzinfo=ZoneInfo(zona))
    utc = istante.astimezone(ZoneInfo('UTC'))
    return utc.hour + utc.minute / 60


def measure(d, fonte: str, oggi=None) -> float:
    """The offset to ADD to this source's times to reach UTC.

    `d` is the source's own frame, with `Paese`, `Evento`, `Orario` and
    `Data_Rilascio`. Raises TimezoneUnknown when the anchors are absent or
    disagree.

    Each anchor row is measured against the UTC hour its own release date
    implies, so a batch spanning a daylight-saving change measures the same
    offset on both sides of it instead of splitting into two populations.
    """
    if fonte in NOT_APPLICABLE:
        return 0.0

    ancore = ANCORE.get(fonte)
    if not ancore:
        raise TimezoneUnknown(f"{fonte}: no anchors defined, so no offset can be "
                              f"established. Add one to ANCORE before ingesting it.")

    misure = []
    for paese, pattern, ora_locale, zona in ancore:
        sub = d[(d.Paese.astype(str) == paese)
                & d.Evento.astype(str).str.contains(pattern, case=False,
                                                    regex=True, na=False)]
        scarti = []
        for _, r in sub.iterrows():
            pubblicata = ore(r.Orario)
            if pubblicata is None:
                continue
            vera = ora_utc_ancora(ora_locale, zona,
                                  giorno_di(r.get('Data_Rilascio', ''), oggi))
            if vera is None:
                continue
            scarti.append(round(vera - pubblicata, 2))
        if not scarti:
            continue
        misure.append(Counter(scarti).most_common(1)[0][0])

    if not misure:
        raise TimezoneUnknown(
            f"{fonte}: none of the {len(ancore)} anchors appear in this batch, so "
            f"its timezone cannot be established. A batch too narrow to contain a "
            f"US 08:30 print cannot be dated; widen it or ingest it separately.")

    if max(misure) - min(misure) > TOLLERANZA:
        raise TimezoneUnknown(
            f"{fonte}: the anchors disagree ({misure}). One of them is not the "
            f"release it is taken for -- do not ingest until it is known which.")

    scarto = misure[0]

    if fonte in EXPECTED_ZERO and abs(scarto) > TOLLERANZA:
        raise TimezoneUnknown(
            f"{fonte}: expected UTC and measured {scarto:+.2f} h. For tradays this "
            f"means the browser is no longer pinned to UTC (see "
            f"setup_browser), and every time in this batch is off by that much.")

    return scarto


def report(frames: dict) -> int:
    """Print what each source's batch says. Returns the number of failures."""
    print(f"{'source':14} {'how':14} {'offset to UTC':>14}   note")
    print('-' * 72)
    guasti = 0
    for fonte, d in frames.items():
        come = ('derived' if fonte in DERIVED
                else 'expected 0' if fonte in EXPECTED_ZERO else 'n/a')
        try:
            scarto = measure(d, fonte)
            print(f'{fonte:14} {come:14} {scarto:>+13.2f} h')
        except TimezoneUnknown as e:
            guasti += 1
            print(f'{fonte:14} {come:14} {"REFUSED":>14}   {e}')
    return guasti


if __name__ == '__main__':
    import argparse

    import pandas as pd

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--csv', action='append', default=[], metavar='SOURCE=PATH',
                    help='es. --csv myfxbook=myfxbook_universo.csv')
    args = ap.parse_args()

    frames = {}
    for voce in args.csv:
        fonte, _, path = voce.partition('=')
        frames[fonte] = pd.read_csv(path).fillna('')
    raise SystemExit(1 if report(frames) else 0)
