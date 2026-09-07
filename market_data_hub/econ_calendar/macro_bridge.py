# -*- coding: utf-8 -*-
"""Fill a missing published value from the macro series already in this database.

Forex Factory's feed publishes no actuals at all, so every figure in
``calendar_events.actual`` arrives from somewhere else. Until now that
somewhere else was a web search (``validate.py``): an LLM looks the print up
and, if it comes back with a figure and a URL, the value is ingested with
provenance ``'web'``. That works, costs money, and is not reproducible -- two
runs can disagree.

For the handful of indicators that have an exact counterpart among the hub's
own ``macro_series`` rows, none of that is necessary: the number is already in
the same file, collected from FRED by a different job. This module reads it
from there.

Three things make it safe rather than convenient:

*The bridge is declared, not guessed.* ``calendar_indicators.macro_series_id``
comes from ``config/econ_calendar.yaml`` and is filled only where the series
measures exactly the same quantity, in the same unit, with the same
transformation. Today that is two indicators out of 141 (see the comments in
the YAML); everything else stays NULL on purpose, because most calendar
indicators are a *change* (m/m, y/y) while almost every FRED series here is a
*level*, and pairing the two produces a credible, wrong number.

*The refusal is enforced in code, not only in the comment.* Even a mapping
somebody adds carelessly to the YAML is rejected here unless the indicator's
archetype is a pure level in the same unit -- ``_IDENTITY_VALUE_TYPES``. A
level-to-change mapping cannot reach the write path by being written down.

*The value is the first vintage, and only when that vintage can BE the
publication.* ``macro_series`` holds the latest revision;
``macro_series_vintage`` holds what this hub saw the first time it collected an
observation -- which is the published figure only if the hub was already
collecting that series when it came out. Usually it was not: UNRATE's 319
observations back to 2000 all carry one vintage date, the day of the historical
backfill, by which time every one of them had already been revised. So the
earliest vintage is accepted only when the hub saw it within a few days of the
release; otherwise nothing is written. An observation with no vintage row is
skipped too, rather than read from the revised table: a value with no
publication date attached has no business in a column that says what came out
that day.

Precedence: the observation is written with provenance ``'derived'``, which
ranks below an agency and below an aggregator (the print itself always wins)
but above ``'web'`` (an agency series beats an LLM's reading of a news page).
And the fill only ever runs against events whose ``actual`` is NULL, so a value
that came from a real source is never touched.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional

import duckdb

from market_data_hub.econ_calendar.ingest import (
    CalendarObservation,
    ingest_observations,
)

# The value types for which a FRED level is literally the number the calendar
# prints. A rate in percentage points is one: 'Unemployment Rate 4.1%' is the
# same object in both tables, same unit, no transformation.
#
# Everything else is deliberately absent, and each absence has a reason:
# 'Variazione % a/a' / 'm/m' / 't/t' would need a transformation of the series
# (a ratio of two observations) that the calendar's own convention -- which
# month is the base, seasonally adjusted or not, revised or first print --
# does not pin down; 'Migliaia annualizzate' and the other counting units are
# the same quantity but not the same SCALE (the hub stores HOUST in thousands,
# 1239.0, while calendar_events.actual_num for us_housing is in units,
# 1239000.0), and inventing the factor here is exactly the kind of quiet
# arithmetic this module exists not to do.
#
# Widening this set is a code change on purpose: it is the point at which
# somebody has to say, in writing, what transformation they are claiming.
_IDENTITY_VALUE_TYPES = {"%"}

# How the event's reference period is matched against the series observation.
# FRED stamps a monthly observation on the FIRST day of the month it measures
# ('2026-08-01' is August); the calendar stamps reference_date on the LAST day
# of the period it measures ('2026-08-31' is August). They are the same period
# written two ways, so the comparison is on the period, never on the day.
_PERIOD_GRAIN = {"M": "month", "Q": "quarter"}


def _formatta(valore: float, value_type: Optional[str]) -> str:
    """The number written the way the rest of the column writes it.

    ``calendar_events.actual`` is a string and ``actual_num`` is derived from
    it by ``parse_number``, so the string carries the unit. A rate has to come
    out as '4.1%', matching what the aggregator and the web fill already store
    for the same indicators; writing a bare '4.1' would parse to the same
    number but read differently to a human comparing rows.
    """
    testo = f"{valore:g}"
    return testo + "%" if value_type == "%" else testo


def bridged_indicators(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """The indicators whose bridge is declared AND usable, with the refusals.

    Returns one dict per indicator carrying ``usable`` and, when it is False,
    the ``reason``. The refusals are returned rather than dropped: a mapping
    that exists in the YAML and never fires is precisely the thing a reader
    needs told, instead of discovering an empty result and guessing.
    """
    righe = con.execute(
        """
        SELECT indicator_key, country_iso3, name, macro_series_id,
               frequency, value_type
        FROM calendar_indicators
        WHERE active AND macro_series_id IS NOT NULL AND macro_series_id <> ''
        ORDER BY indicator_key
        """
    ).fetchall()
    esito = []
    for chiave, paese, nome, serie, freq, tipo in righe:
        motivo = None
        if tipo not in _IDENTITY_VALUE_TYPES:
            motivo = (f"value_type {tipo!r} is not a level in the series' own "
                      f"unit; only {sorted(_IDENTITY_VALUE_TYPES)} bridge without "
                      f"a transformation")
        elif freq not in _PERIOD_GRAIN:
            motivo = (f"frequency {freq!r} has no period grain to match a series "
                      f"observation against; supported: {sorted(_PERIOD_GRAIN)}")
        esito.append({"indicator_key": chiave, "country_iso3": paese,
                      "name": nome, "macro_series_id": serie,
                      "frequency": freq, "value_type": tipo,
                      "usable": motivo is None, "reason": motivo})
    return esito


#: How long after a release the hub may first have seen the value and still be
#: holding the ORIGINAL print rather than a revision.
#:
#: Seven days, and the number is not arbitrary. The vintage table records when
#: THIS HUB first collected an observation, which is only the publication value
#: if the hub was already collecting that series when it came out. It usually
#: was not: UNRATE holds 319 observations reaching back to 2000, and every one
#: before July 2026 carries the same vintage date, 2026-07-10 -- the day of the
#: historical backfill, by which time each of those figures had already been
#: revised, some of them many times. Treating that as "what was published"
#: would write a 2026 revision into a column that says what came out in 2003,
#: and `derived` outranks `web`, so the later cross-check could not correct it.
#:
#: From July 2026 on the same series shows what an honest first print looks
#: like: the August observation was first seen on 2026-09-04, the day the
#: figure was released. The window separates the two cases, and it is generous
#: on purpose -- a collection that fell a few days behind is still the first
#: print, while a backfill is years late and never passes.
_VINTAGE_WINDOW_DAYS = 7


def _first_vintage(
    con: duckdb.DuckDBPyConnection, series_id: str, grain: str,
    reference_date: date, release_date: date,
) -> Optional[tuple[date, float, date]]:
    """(observation date, value, vintage date) for the event's period, or None.

    Reads ``macro_series_vintage`` and takes the EARLIEST vintage, then checks
    that the earliest vintage can actually BE the publication: the hub must
    have collected it no earlier than the release and no later than
    ``_VINTAGE_WINDOW_DAYS`` after it. Without that check the earliest vintage
    is merely the first value this hub ever saw, which for anything predating
    the historical backfill is a revision wearing the date of the backfill.

    ``macro_series`` (the revised table) is deliberately not consulted as a
    fallback -- a value with no vintage row has no publication date attached to
    it, and filling from it would put a figure of unknown age into a column
    that is supposed to say what came out that day.

    Refuses when the period holds more than one distinct observation date: that
    means the grain is wrong for this series, and picking one of them would be
    a guess.
    """
    righe = con.execute(
        f"""
        SELECT date, value, vintage_date
        FROM macro_series_vintage
        WHERE series_id = ?
          AND value IS NOT NULL
          AND date_trunc('{grain}', date) = date_trunc('{grain}', ?::DATE)
        ORDER BY date, vintage_date
        """,
        [series_id, reference_date],
    ).fetchall()
    if not righe:
        return None
    if len({r[0] for r in righe}) != 1:
        return None
    data_oss, valore, vintage = righe[0]
    if not (release_date <= vintage <= release_date + timedelta(days=_VINTAGE_WINDOW_DAYS)):
        return None
    return data_oss, valore, vintage


def fill_from_macro_series(
    con: duckdb.DuckDBPyConnection,
    *,
    run_id: Optional[str] = None,
    now_utc: Optional[datetime] = None,
    indicator_keys: Optional[Iterable[str]] = None,
) -> dict:
    """Fill missing actuals from the bridged series. Returns a summary.

    Only events that satisfy every one of these are touched:

    * the indicator declares a ``macro_series_id`` that survives
      :func:`bridged_indicators`;
    * the event has no ``actual`` at all -- a value from a real source is never
      overwritten, whatever its provenance;
    * the event has a ``reference_date``, so the period is known rather than
      inferred from the release day;
    * the release is already in the past, so the run cannot claim a print that
      has not happened;
    * exactly one series observation falls in that period, and it has a first
      vintage.

    Every fill leaves a ``macro_series_fill`` note beside the event naming the
    series, the observation date and the vintage it was read at, so no number
    in this table is anonymous.
    """
    adesso = (now_utc or datetime.now(timezone.utc)).replace(tzinfo=None)
    indicatori = [i for i in bridged_indicators(con) if i["usable"]]
    if indicator_keys is not None:
        volute = set(indicator_keys)
        indicatori = [i for i in indicatori if i["indicator_key"] in volute]

    esito = {"bridged_indicators": len(indicatori), "candidates": 0,
             "filled": 0, "no_series_observation": 0, "skipped_future": 0}
    da_scrivere: list[tuple[dict, CalendarObservation, dict]] = []
    for ind in indicatori:
        grana = _PERIOD_GRAIN[ind["frequency"]]
        eventi = con.execute(
            """
            SELECT event_id, release_utc, reference_period, reference_date
            FROM calendar_events
            WHERE indicator_key = ?
              AND actual IS NULL AND actual_num IS NULL
              AND reference_date IS NOT NULL
            ORDER BY release_utc
            """,
            [ind["indicator_key"]],
        ).fetchall()
        for event_id, release_utc, periodo, reference_date in eventi:
            esito["candidates"] += 1
            if release_utc > adesso:
                esito["skipped_future"] += 1
                continue
            trovato = _first_vintage(con, ind["macro_series_id"], grana,
                                     reference_date, release_utc.date())
            if trovato is None:
                # One counter for both refusals on purpose: neither is an
                # error, and the summary already says how many candidates
                # there were. What matters is that a refusal is never a fill.
                esito["no_series_observation"] += 1
                continue
            data_oss, valore, vintage = trovato
            da_scrivere.append((
                ind,
                CalendarObservation(
                    indicator_key=ind["indicator_key"],
                    country_iso3=ind["country_iso3"],
                    source=f"macro_series:{ind['macro_series_id']}",
                    provenance="derived",
                    source_event_name=ind["name"],
                    release_utc=release_utc,
                    reference_period=periodo,
                    reference_date=reference_date,
                    actual=_formatta(valore, ind["value_type"]),
                ),
                {"event_id": event_id, "observation_date": data_oss,
                 "vintage_date": vintage, "value": valore,
                 "reference_date": reference_date},
            ))

    if not da_scrivere:
        return esito

    risultato = ingest_observations(
        con, [o for _, o, _ in da_scrivere], run_id=run_id)
    esito["filled"] = risultato["observations"]
    ora = datetime.now(timezone.utc)
    for ind, _, dettaglio in da_scrivere:
        con.execute(
            """
            INSERT OR REPLACE INTO calendar_event_notes
                (event_id, generated_at, model, commentary_json,
                 technical_source, run_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [dettaglio["event_id"], ora, f"macro_series:{ind['macro_series_id']}",
             json.dumps({
                 "check": "macro_series_fill",
                 "macro_series_id": ind["macro_series_id"],
                 "series_observation_date": str(dettaglio["observation_date"]),
                 "series_first_vintage": str(dettaglio["vintage_date"]),
                 "value": dettaglio["value"],
                 "event_reference_date": str(dettaglio["reference_date"]),
                 "note": ("filled from the hub's own macro series, first "
                          "vintage; no source published the value"),
             }, ensure_ascii=False),
             f"macro_series:{ind['macro_series_id']}", run_id],
        )
    return esito
