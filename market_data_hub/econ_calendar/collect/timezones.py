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

Method: releases whose UTC time is publicly fixed. The US 08:30 ET prints (CPI,
jobless claims, retail sales) and the ECB decision are the anchors -- they move
only with US and euro-area daylight saving, which is the drift being looked for.
"""
from __future__ import annotations

from collections import Counter
from typing import Optional

# source -> how its offset is established
DERIVED = ('myfxbook',)          # measured from the batch, every run
EXPECTED_ZERO = ('tradays', 'yahoo')
NOT_APPLICABLE = ('nasdaq', 'forexfactory')

# source -> (country code, event name pattern, true UTC hour as float)
#
# The codes are ISO, not currencies, because that is what the sources write by
# the time they reach here: myfxbook labels by currency but its collector now
# maps to ISO before writing, so an anchor on 'USD' would match nothing and the
# batch would be refused for having no anchors rather than for being undatable.
ANCORE = {
    'myfxbook': [
        ('US', r'Inflation Rate|Initial Jobless|Retail Sales', 12.5),
        ('EU', r'Interest Rate', 12.25),
    ],
    'tradays': [
        ('US', r'CPI|Initial Jobless Claims|Retail Sales', 12.5),
    ],
    'yahoo': [
        ('US', r'CPI YY|Initial Jobless', 12.5),
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


def measure(d, fonte: str) -> float:
    """The offset to ADD to this source's times to reach UTC.

    `d` is the source's own frame, with `Paese`, `Evento` and `Orario`.
    Raises TimezoneUnknown when the anchors are absent or disagree.
    """
    if fonte in NOT_APPLICABLE:
        return 0.0

    ancore = ANCORE.get(fonte)
    if not ancore:
        raise TimezoneUnknown(f"{fonte}: no anchors defined, so no offset can be "
                              f"established. Add one to ANCORE before ingesting it.")

    misure = []
    for paese, pattern, utc_vero in ancore:
        s = d[(d.Paese.astype(str) == paese)
              & d.Evento.astype(str).str.contains(pattern, case=False, regex=True,
                                                  na=False)]
        valori = [v for v in (ore(x) for x in s.Orario) if v is not None]
        if not valori:
            continue
        comune = Counter(valori).most_common(1)[0][0]
        misure.append(round(utc_vero - comune, 2))

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
