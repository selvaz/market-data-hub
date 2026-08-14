# -*- coding: utf-8 -*-
"""Derive the reference period when no source publishes it.

``macro_panel`` knows which period a figure describes; the calendar was added
to supply the other axis, the moment of publication. Joining the two needs
``reference_date`` on both sides -- and the calendar has it on one event in
five, because Nasdaq and Tradays carry roughly two thirds of the observations
and neither publishes a reference period at all. Only MyFXBook and Yahoo do,
and their coverage is what it is.

The gap is not a reconciliation failure: over the whole load, every event where
some source supplied a period received it, 235 for 235. It is a coverage
ceiling, and no amount of better matching lifts it much -- an attempt to bind
more Yahoo and MyFXBook names moved the figure by six points and no further,
because the names still unbound were indicators nobody tracks.

So the period is derived instead. A release lands a fixed distance from the
period it describes: US CPI for July comes out in August, euro-area industrial
production for June comes out in August. Measured over the events where the
period IS known, 74 indicators out of 84 show a lag that never varies. The ten
that do vary are almost all euro-area aggregates, where national releases with
different lags are contaminating the series -- so the instability is a symptom
of a binding problem, not of the method.

Two rules this module keeps:

  - It learns per indicator, never per frequency. Both US CPI and euro-area
    industrial production are monthly, and their lags are 1 and 2. A frequency
    default would be wrong for one of them every single month.

  - What it writes is marked. ``reference_date_origin`` separates a period a
    provider published from one this module computed, because a backtest that
    cannot tell them apart is joining on dates nobody ever published and has no
    way to know.
"""
from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from datetime import date
from typing import Iterable, Optional

import duckdb

# Frequencies whose period is a calendar month, quarter or year, so a lag in
# months determines it. Weekly is excluded on purpose: US jobless claims refer
# to a specific week ending on a specific day, which a month lag cannot express.
# 'E' means the schedule is the calendar's own -- a policy meeting describes no
# period at all.
_DERIVABILI = {"M", "Q", "A"}
_MIN_EVENTI = 2         # one observation is an anecdote, not a lag


def _fine_periodo(anno: int, mese: int, frequenza: str) -> date:
    """The last day of the period a release with this lag describes."""
    if frequenza == "A":
        return date(anno, 12, 31)
    if frequenza == "Q":
        mese = ((mese - 1) // 3) * 3 + 3        # end of the containing quarter
    return date(anno, mese, monthrange(anno, mese)[1])


def _sposta(giorno: date, mesi: int) -> tuple[int, int]:
    n = giorno.year * 12 + (giorno.month - 1) - mesi
    return n // 12, n % 12 + 1


def learn_lags(
    con: duckdb.DuckDBPyConnection,
    *,
    min_events: int = _MIN_EVENTI,
) -> dict[str, dict]:
    """The distance, in months, between a release and the period it describes.

    Learned only from events whose period a source actually published --
    inferred ones are excluded, or the module would be teaching itself.
    """
    righe = con.execute(
        """
        SELECT e.indicator_key, i.frequency,
               date_diff('month', e.reference_date, e.release_utc::date) AS scarto
        FROM calendar_events e
        JOIN calendar_indicators i ON i.indicator_key = e.indicator_key
        WHERE e.status = 'released'
          AND e.reference_date IS NOT NULL
          AND e.reference_date_origin = 'source'
          AND i.frequency IN ('M', 'Q', 'A')
        """
    ).fetchall()

    visti: dict[str, dict] = defaultdict(lambda: defaultdict(int))
    frequenze: dict[str, str] = {}
    for chiave, freq, scarto in righe:
        visti[chiave][scarto] += 1
        frequenze[chiave] = freq

    imparati = {}
    for chiave, conteggio in visti.items():
        n = sum(conteggio.values())
        if n < min_events:
            continue
        scarto, quante = max(conteggio.items(), key=lambda x: x[1])
        imparati[chiave] = {
            "lag_months": scarto,
            "frequency": frequenze[chiave],
            "events": n,
            "agreement": quante / n,
            "stable": len(conteggio) == 1,
            "observed": dict(conteggio),
        }
    return imparati


def infer_reference_dates(
    con: duckdb.DuckDBPyConnection,
    *,
    only_stable: bool = True,
    indicator_keys: Optional[Iterable[str]] = None,
    dry_run: bool = False,
) -> dict:
    """Fill the missing reference_date from the indicator's learned lag.

    ``only_stable`` keeps it to indicators whose lag never varied in the
    observed data. Dropping that admits the ones where a majority lag was
    picked over a minority, which on this data means mostly the euro-area
    aggregates -- exactly the series whose bindings are known to be mixed.
    Filling those would be writing a date derived from a contradiction.
    """
    imparati = learn_lags(con)
    if indicator_keys is not None:
        volute = set(indicator_keys)
        imparati = {k: v for k, v in imparati.items() if k in volute}
    usabili = {k: v for k, v in imparati.items()
               if v["stable"] or not only_stable}

    mancanti = con.execute(
        """
        SELECT e.event_id, e.indicator_key, e.release_utc::date
        FROM calendar_events e
        JOIN calendar_indicators i ON i.indicator_key = e.indicator_key
        WHERE e.status = 'released'
          AND e.reference_date IS NULL
          AND i.frequency IN ('M', 'Q', 'A')
        """
    ).fetchall()

    scritti, senza_regola = 0, 0
    for eid, chiave, giorno in mancanti:
        regola = usabili.get(chiave)
        if regola is None:
            senza_regola += 1
            continue
        anno, mese = _sposta(giorno, regola["lag_months"])
        fine = _fine_periodo(anno, mese, regola["frequency"])
        if not dry_run:
            con.execute(
                "UPDATE calendar_events SET reference_date = ?, "
                "reference_date_origin = 'inferred' WHERE event_id = ?",
                [fine, eid],
            )
        scritti += 1

    return {
        "indicators_learned": len(imparati),
        "indicators_usable": len(usabili),
        "events_missing": len(mancanti),
        "events_filled": scritti,
        "events_without_rule": senza_regola,
    }


def validate_lags(con: duckdb.DuckDBPyConnection) -> dict:
    """Hold-one-out: would the rule have predicted the periods we already know?

    Every event whose period a source published is hidden in turn, the lag
    relearned without it, and the prediction compared against the truth. It is
    the only honest way to quote an accuracy for something that will be applied
    where no truth exists.
    """
    righe = con.execute(
        """
        SELECT e.indicator_key, i.frequency, e.release_utc::date, e.reference_date
        FROM calendar_events e
        JOIN calendar_indicators i ON i.indicator_key = e.indicator_key
        WHERE e.status = 'released'
          AND e.reference_date IS NOT NULL
          AND e.reference_date_origin = 'source'
          AND i.frequency IN ('M', 'Q', 'A')
        """
    ).fetchall()

    per_chiave: dict[str, list] = defaultdict(list)
    for chiave, freq, giorno, vero in righe:
        per_chiave[chiave].append((freq, giorno, vero))

    giusti = sbagliati = saltati = 0
    errori: list[dict] = []
    for chiave, eventi in per_chiave.items():
        if len(eventi) < _MIN_EVENTI + 1:
            saltati += len(eventi)
            continue
        for i, (freq, giorno, vero) in enumerate(eventi):
            altri = eventi[:i] + eventi[i + 1:]
            conteggio: dict[int, int] = defaultdict(int)
            for _f, g, v in altri:
                conteggio[(g.year * 12 + g.month) - (v.year * 12 + v.month)] += 1
            scarto = max(conteggio.items(), key=lambda x: x[1])[0]
            anno, mese = _sposta(giorno, scarto)
            atteso = _fine_periodo(anno, mese, freq)
            if atteso == vero:
                giusti += 1
            else:
                sbagliati += 1
                errori.append({"indicator_key": chiave, "release": str(giorno),
                               "expected": str(atteso), "actual": str(vero)})

    totale = giusti + sbagliati
    return {
        "tested": totale,
        "correct": giusti,
        "wrong": sbagliati,
        "accuracy": giusti / totale if totale else None,
        "skipped_too_few": saltati,
        "errors": errori[:20],
    }
