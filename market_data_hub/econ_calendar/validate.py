# -*- coding: utf-8 -*-
"""T1 validation: check -- and, when nothing else supplied one, fill -- the
actual of recent T1 releases against what actually published.

Single-sourcing drops the redundancy the old five-source pipeline had
almost by accident: when four other scrapers also saw a release, an outlier
reading stood out on its own just from disagreeing with the rest. Rather
than bolt on a second scraper -- which is how the calendar ended up with
five sources disagreeing with each other in the first place -- this runs a
narrow, targeted check instead: for the T1-criticality releases of the last
few days only, an LLM with live web search looks up what actually
published.

Two outcomes, decided by whether the event already has an actual:

* It has one (some source supplied it): the web figure is compared against
  it. A mismatch is written to ``calendar_event_notes``; the stored value is
  never corrected here, the note says a human should look.
* It has none (the current source, Forex Factory's feed, publishes no
  actuals at all): a confident, sourced web figure -- a number that parses
  and at least one URL, nothing less -- is fed to ``ingest_observations``
  as an observation with provenance ``'web'``, the lowest rank, so any
  aggregator's or agency's value overrides it the moment it arrives and it
  never displaces one. A ``web_fill`` note keeps the URLs. UNVERIFIED, or a
  reply without a parseable number or a source, writes nothing: a guessed
  print is worse than a missing one.

The pass runs over two disjoint stretches of time. The primary window is the
last few CALENDAR days (see ``window_start``) and covers every T1 release in
it. The catch-up reaches further back and looks only at events that still have
no value at all, a bounded number of times each: without it, a release the web
search missed while it was inside the window was never looked at again, and
stayed empty forever.

The engine is LazyBridge's ``ClaudeCodeEngine`` with ``web=True``, mirroring
investmentcommittee's weekly-earnings research step
(``src/investmentcommittee/weekly_earnings/pipeline.py``: ``_write`` /
``research_day``). That choice is deliberate, not incidental: the same
project measured LazyCrawler's DuckDuckGo backend returning quote pages and
calendar listings instead of the actual print, and moved its research step
onto Claude Code's own web search (no API key needed -- it speaks through
the local subscription) for exactly that reason. The same failure mode
almost certainly applies to a release calendar too, so this is not built on
top of LazyCrawler's search either.

Imports ``lazybridge`` lazily, inside the function that needs it, the same
way ``collect/myfxbook.py`` defers its selenium imports: nothing else in
this package needs an LLM engine, and importing it at module load time would
make ``import market_data_hub.econ_calendar.validate`` fail wherever
lazybridge is not installed, for every caller, even one that only wants
``run_validation``'s signature or never calls it.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

import duckdb

from market_data_hub.econ_calendar.ingest import (
    CalendarObservation,
    ingest_observations,
    parse_number,
)

# A compact figure at the START of a reply field: '2.5%', '-42.0K', '162K',
# '$-73.261 B', '54.6'. Anchored, so a sentence that merely contains a number
# ('rose for a third month (about 2.5%)') is not a figure.
_FIGURE = re.compile(r'^\s*(\$?[-+]?\$?\d[\d,]*(?:\.\d+)?\s?(?:%|[KMBT]\b)?)')


def _figure(testo: Optional[str]) -> Optional[str]:
    """The figure a reply field starts with, or None.

    The model is told to answer ACTUAL/PREVIOUS with the figure alone. On the
    first production pass it answered '2.5% m/m (July, revised prior month
    3.7%)': parse_number found the 2.5 inside, the gate passed, and the whole
    sentence went into calendar_events.actual. What is stored must be the
    figure; the raw reply stays in the note. A field that does not BEGIN
    with a figure is not one -- the number in the middle of a sentence may
    be the prior, the consensus, or a revision.
    """
    m = _FIGURE.match(testo or "")
    if not m:
        return None
    figura = m.group(1).strip()
    return figura if parse_number(figura) is not None else None

_SYSTEM = (
    "You verify one economic data release against what was actually "
    "published. You are given the indicator, the country, the release "
    "date, and what our calendar recorded for actual, previous "
    "and consensus. Search the web for the real published figures -- the "
    "issuing agency's own release, or a reputable financial news report of "
    "it -- and compare.\n"
    "Reply in EXACTLY this format, nothing before or after:\n"
    "STATUS: <MATCH|MISMATCH|FOUND|UNVERIFIED>\n"
    "ACTUAL: <the actual figure you found, or blank only for a recorded match>\n"
    "PREVIOUS: <the previous figure you found, or blank only for a recorded match>\n"
    "NOTE: <one sentence: what disagrees, or why it could not be verified>\n"
    "SOURCES:\n"
    "<one url per line, the pages you actually used>\n"
    "When our calendar has no actual (it says N/D), do not call the result "
    "MATCH or MISMATCH: find the published actual and previous and reply "
    "FOUND with the actual filled in. MATCH means our recorded actual AND "
    "previous both agree with what you found. "
    "MISMATCH means at least one of them disagrees -- report the correct "
    "value you found for the field(s) that disagree, leave the rest blank. "
    "UNVERIFIED means you could not find the real published figures at "
    "all. Never guess: if you are not confident, say UNVERIFIED."
)


_CAMPI = ("event_id", "indicator_key", "indicator_name", "area",
          "country_iso3", "release_utc", "actual", "previous", "consensus")

_SELECT_EVENTI = """
    SELECT e.event_id, e.indicator_key, i.name, i.area, i.country_iso3,
           e.release_utc, e.actual, e.previous, e.consensus
    FROM calendar_events e
    JOIN calendar_indicators i ON i.indicator_key = e.indicator_key
    WHERE i.criticality = 'T1'
"""

# How many times one event may be looked up before it is left alone. A release
# whose figure three separate web searches could not find is not going to be
# found by the fourth: the print is behind a paywall, the indicator is named
# something the model cannot resolve, or our release_utc is wrong. Without a
# cap the catch-up pass turns into a permanent tax that grows with the
# calendar's history -- every unfillable event is re-searched, at cost, every
# single morning, forever.
DEFAULT_MAX_ATTEMPTS = 3

# How far back the catch-up looks. 30 calendar days, because the indicators
# this pass exists for are monthly: a print still missing a month after its
# release has had every daily run in between fail to find it, and by then the
# next period's release is already in the primary window. It is also wider than
# the whole history the calendar currently holds (12/08/2026 onwards, 26 days
# on 07/09/2026), so nothing stored today is out of reach of a re-try.
DEFAULT_CATCHUP_DAYS = 30


def _righe_a_eventi(righe) -> list[dict]:
    return [dict(zip(_CAMPI, r)) for r in righe]


def window_start(now_utc: datetime, lookback_days: int) -> datetime:
    """Midnight UTC of the first day the primary window covers.

    Calendar days, not hours from launch, and this is the whole point of the
    function existing. The old window was ``now - lookback_days``, and the job
    runs at 13:00 UTC (06:00 Pacific): with the default lookback of 3 that put
    the boundary at 13:00 UTC three days back, while US non-farm payrolls
    publish at 12:30 UTC. The single most important release in the calendar
    fell out of its own window by thirty minutes -- and, worse, whether any
    given release was in or out depended on the minute the scheduler happened
    to fire, so a run delayed by an hour saw a different set of events than one
    that started on time.

    Anchoring on midnight makes the window a statement about days: 'the last
    N calendar days, plus today'. It is reproducible, it does not move when the
    job is late, and the 12:30 release on the boundary day is inside it by
    twelve and a half hours instead of missing it by thirty minutes.
    """
    inizio = (now_utc - timedelta(days=lookback_days)).date()
    return datetime.combine(inizio, time.min)


def _t1_events_for_window(
    con: duckdb.DuckDBPyConnection, *, now_utc: datetime, lookback_days: int,
) -> list[dict]:
    """Recent, safely-past T1 releases, including ones awaiting an actual."""
    earliest = window_start(now_utc.replace(tzinfo=None), lookback_days)
    # Still measured from the actual instant, and deliberately so: this end of
    # the window is not about which day the release belongs to but about
    # whether it has already happened. Half an hour of grace lets the wires
    # catch up with the print.
    latest = now_utc.replace(tzinfo=None) - timedelta(minutes=30)
    return _righe_a_eventi(con.execute(
        _SELECT_EVENTI + "      AND e.release_utc BETWEEN ? AND ?\n"
        "    ORDER BY e.release_utc",
        [earliest, latest],
    ).fetchall())


def _t1_events_for_catchup(
    con: duckdb.DuckDBPyConnection, *, now_utc: datetime, lookback_days: int,
    catchup_days: int, max_attempts: int,
) -> list[dict]:
    """Older T1 releases that still have no value, and have attempts left.

    The primary window is a window in both directions: an event that was not
    filled while it was inside it is never looked at again, so a figure the
    web search missed once stays missing forever. Measured on the production
    database on 07/09/2026: 13 of 77 events carried no ``actual_num``, and
    nothing in the pipeline was ever going to revisit them.

    This is the second look. It covers the days between ``catchup_days`` back
    and the start of the primary window -- no overlap, so an event is never
    checked twice in one run -- and only events with no ``actual`` at all,
    because an event that has a value has nothing to catch up on. Each
    unsuccessful attempt leaves an ``actual_catchup`` note, and an event that
    has accumulated ``max_attempts`` of them drops out of the query.
    """
    if catchup_days <= lookback_days:
        return []
    adesso = now_utc.replace(tzinfo=None)
    earliest = window_start(adesso, catchup_days)
    latest = window_start(adesso, lookback_days)
    return _righe_a_eventi(con.execute(
        _SELECT_EVENTI + """
          AND e.actual IS NULL
          AND e.release_utc >= ? AND e.release_utc < ?
          AND (
              SELECT count(*) FROM calendar_event_notes n
              WHERE n.event_id = e.event_id
                AND json_extract_string(n.commentary_json, '$.check')
                    = 'actual_catchup'
          ) < ?
        ORDER BY e.release_utc DESC""",
        [earliest, latest, max_attempts],
    ).fetchall())


def _record_attempt(con: duckdb.DuckDBPyConnection, evento: dict, esito: str,
                    *, model: str, run_id: Optional[str]) -> None:
    """Mark that this event was looked for and not found, so the cap can bite.

    Written to ``calendar_event_notes``, which already carries a
    ``review_attempts`` column that nothing had ever filled -- the schema
    anticipated exactly this. The attempt counter is the number of these rows,
    not a mutable field, so a crash between the search and the write costs one
    attempt rather than corrupting the count.
    """
    n = con.execute(
        "SELECT count(*) FROM calendar_event_notes WHERE event_id = ? "
        "AND json_extract_string(commentary_json, '$.check') = 'actual_catchup'",
        [evento["event_id"]],
    ).fetchone()[0]
    con.execute(
        """
        INSERT OR REPLACE INTO calendar_event_notes
            (event_id, generated_at, model, review_attempts, commentary_json,
             not_found, run_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [evento["event_id"], datetime.now(timezone.utc), model, n + 1,
         json.dumps({"check": "actual_catchup", "attempt": n + 1,
                     "outcome": esito,
                     "release_utc": str(evento["release_utc"])},
                    ensure_ascii=False),
         f"published actual: {esito}", run_id],
    )


def _prompt(evento: dict) -> str:
    return (
        f"Indicator: {evento['indicator_name']} ({evento['area']}, "
        f"{evento['country_iso3']})\n"
        f"Release date: {evento['release_utc']}\n"
        f"Our calendar recorded -- actual: {evento['actual'] or 'N/D'}, "
        f"previous: {evento['previous'] or 'N/D'}, "
        f"consensus: {evento['consensus'] or 'N/D'}\n"
        "Find the published actual and previous. "
        + ("Our actual is missing: return FOUND with the published actual; "
           "MATCH/MISMATCH cannot apply until one is recorded."
           if not evento["actual"] else
           "Check our calendar's actual and previous against it.")
    )


def _ask(prompt: str, *, model: str, effort: str, max_turns: int,
          timeout_s: float) -> str:
    """One web-search call through Claude Code -- no API key, the local
    subscription speaks for it, the same as investmentcommittee's research step.

    ``asyncio.wait_for`` is a soft timeout only: it does not reach into the
    child session ``ClaudeCodeEngine`` spawns, so a truly hung call can run
    past it. investmentcommittee works around exactly that for its own
    (heavier, higher-volume) research step by running the engine in a
    separate, killable process; this is one short check per T1 release
    rather than a whole day's research, so the simpler soft timeout is used
    here instead -- a known gap, worth revisiting if this is ever run at
    higher volume or the timeout starts being hit in practice.

    Raises on ``envelope.ok is False`` rather than returning its (empty)
    text. A failed call -- caught live: claude-agent-sdk missing from an
    environment that only had plain ``lazybridge`` installed -- comes back
    as an ok=False envelope with empty text, not an exception; letting that
    fall through to ``_parse("")`` reads as a confident UNVERIFIED for every
    single release, indistinguishable from the model genuinely finding
    nothing. The caller's ``except Exception`` already exists to count and
    print exactly this shape of failure once, instead of it hiding as 100%
    of the day's checks quietly landing UNVERIFIED.
    """
    from lazybridge import Agent, ClaudeCodeEngine

    async def _chiedi() -> str:
        agente = Agent(
            name="econ_calendar_validate",
            engine=ClaudeCodeEngine(model, system=_SYSTEM, web=True,
                                    reasoning_effort=effort, max_turns=max_turns),
        )
        busta = await agente.run(prompt)
        if not busta.ok:
            errore = busta.model_dump().get("error") or {}
            raise RuntimeError(
                f"{errore.get('type', 'Error')}: {errore.get('message', 'call failed')}")
        return busta.text()

    return asyncio.run(asyncio.wait_for(_chiedi(), timeout=timeout_s))


def _parse(testo: str) -> dict:
    """The model's structured reply -> a dict.

    Tolerant of extra whitespace and a missing SOURCES: block, strict about
    nothing else -- an LLM's output is read as data, never executed, and an
    unparseable STATUS line degrades to UNVERIFIED rather than crashing the
    run over one bad reply.
    """
    stato, campi = "UNVERIFIED", {"actual": "", "previous": "", "note": ""}
    corpo, _, coda = testo.partition("SOURCES:")
    for riga in corpo.splitlines():
        riga = riga.strip()
        if not riga or ":" not in riga:
            continue
        chiave, _, valore = riga.partition(":")
        chiave, valore = chiave.strip().upper(), valore.strip()
        if chiave == "STATUS":
            stato = valore.upper() if valore.upper() in (
                "MATCH", "MISMATCH", "FOUND", "UNVERIFIED") else "UNVERIFIED"
        elif chiave == "ACTUAL":
            campi["actual"] = valore
        elif chiave == "PREVIOUS":
            campi["previous"] = valore
        elif chiave == "NOTE":
            campi["note"] = valore
    urls = [r.strip().lstrip("-* ").strip() for r in coda.splitlines()
            if r.strip().startswith(("http", "- http", "* http"))]
    return {"status": stato, **campi,
            "sources": [u for u in urls if u.startswith("http")][:5]}


def _write_note(con: duckdb.DuckDBPyConnection, evento: dict, esito: dict,
                *, model: str, run_id: Optional[str], check: str = "calendar_vs_published") -> None:
    """Record what the web check found, next to what the calendar had.

    Two kinds of note share this function. ``check="calendar_vs_published"``
    is a MISMATCH on an event that already had an actual: the stored value
    stands, the note says a human should look. ``check="web_fill"`` is the
    audit trail of an actual this module itself supplied for an event that
    had none: the value went in through ingest_observations with
    provenance 'web' (lowest precedence), and the note keeps the sources it
    came from so the number is never anonymous.

    calendar_event_notes is shared with the press-commentary enrichment this
    package already writes (drivers/components/technical_source/etc.): this
    reuses the same table rather than adding a new one, filling only the
    columns a validation check actually has content for.
    """
    contenuto = json.dumps({
        "check": check,
        "calendar": {"actual": evento["actual"], "previous": evento["previous"],
                     "consensus": evento["consensus"]},
        "published": {"actual": esito["actual"] or None,
                       "previous": esito["previous"] or None},
        "note": esito["note"],
    }, ensure_ascii=False)
    con.execute(
        """
        INSERT INTO calendar_event_notes
            (event_id, generated_at, model, commentary_json, technical_source, run_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [evento["event_id"], datetime.now(timezone.utc), model, contenuto,
         "; ".join(esito["sources"]) or None, run_id],
    )


def run_validation(
    con: duckdb.DuckDBPyConnection,
    day: Optional[date] = None,
    *,
    run_id: Optional[str] = None,
    model: str = "sonnet",
    effort: str = "low",
    max_turns: int = 8,
    timeout_s: float = 240.0,
    lookback_days: int = 3,
    catchup_days: int = DEFAULT_CATCHUP_DAYS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> dict:
    """Fill or cross-check recent, safely-past T1 releases.

    Two passes over two disjoint stretches of time. The primary window is the
    last ``lookback_days`` calendar days plus today, and covers every T1 event
    in it whether or not it already has a value. The catch-up reaches from
    ``catchup_days`` back to the edge of that window and looks only at events
    that still have no value at all, at most ``max_attempts`` times each; set
    ``catchup_days=0`` to switch it off.

    Returns a summary -- how many were checked, matched, mismatched or could
    not be verified -- so the caller can print it without reading the notes
    table back. A mismatch is written to calendar_event_notes; a match or an
    unverified check writes nothing, because neither is actionable.
    """
    if lookback_days < 0:
        raise ValueError("lookback_days must be non-negative")
    if catchup_days < 0:
        raise ValueError("catchup_days must be non-negative")
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    now_utc = datetime.now(timezone.utc)
    if day is not None:
        now_utc = datetime.combine(day, now_utc.timetz())
    eventi = _t1_events_for_window(
        con, now_utc=now_utc, lookback_days=lookback_days)
    ripasso = _t1_events_for_catchup(
        con, now_utc=now_utc, lookback_days=lookback_days,
        catchup_days=catchup_days, max_attempts=max_attempts)
    # Counted apart from `checked`: 'we looked at 4 releases today' and 'we
    # also went back for 2 older ones nobody had ever filled' are different
    # statements, and folding them together hides whether the backlog is
    # shrinking.
    esito = {"checked": len(eventi), "match": 0, "mismatch": 0,
             "unverified": 0, "errors": 0, "filled": 0,
             "found_unusable": 0, "rechecked": len(ripasso),
             "refilled": 0, "attempts_exhausted": 0}
    in_ripasso = {e["event_id"] for e in ripasso}
    fills: list[tuple[dict, dict]] = []
    for evento in eventi + ripasso:
        try:
            testo = _ask(_prompt(evento), model=model, effort=effort,
                        max_turns=max_turns, timeout_s=timeout_s)
            risultato = _parse(testo)
        except Exception as e:
            # One release's check is the only thing lost: everything already
            # ingested and audited stands regardless of whether this ran.
            esito["errors"] += 1
            # Deliberately NOT counted as an attempt when the event came from
            # the catch-up: an engine failure is not the event's fault, and a
            # week of a missing claude-agent-sdk would otherwise exhaust every
            # event in the backlog without a single search ever having run.
            print(f'    {evento["indicator_name"]}: could not run '
                  f'({type(e).__name__}: {str(e)[:120]})', flush=True)
            continue

        if not evento["actual"]:
            # Fill only on a figure the field STARTS with, plus a source.
            # parse_number alone let a whole sentence through (see _figure).
            if risultato["status"] == "UNVERIFIED":
                esito["unverified"] += 1
                usabile = False
            elif _figure(risultato["actual"]) is None or not risultato["sources"]:
                esito["found_unusable"] += 1
                usabile = False
            else:
                fills.append((evento, risultato))
                usabile = True
            if evento["event_id"] in in_ripasso and not usabile:
                _record_attempt(
                    con, evento,
                    "unverified" if risultato["status"] == "UNVERIFIED"
                    else "found but unusable",
                    model=model, run_id=run_id)
            continue

        if risultato["status"] == "MISMATCH":
            esito["mismatch"] += 1
            _write_note(con, evento, risultato, model=model, run_id=run_id)
            print(f'    MISMATCH  {evento["indicator_name"]} '
                  f'({evento["country_iso3"]}): {risultato["note"][:100]}', flush=True)
        elif risultato["status"] == "MATCH":
            esito["match"] += 1
        else:
            esito["unverified"] += 1

    if fills:
        oggi = now_utc.date()
        osservazioni = [
            CalendarObservation(
                indicator_key=evento["indicator_key"],
                country_iso3=evento["country_iso3"],
                source=f"web:{model}",
                provenance="web",
                source_event_name=evento["indicator_name"],
                release_utc=evento["release_utc"],
                # The figures, not the fields: the note below keeps the raw
                # reply, calendar_events gets '2.5%', never the sentence.
                actual=_figure(risultato["actual"]),
                previous=_figure(risultato["previous"]),
                consensus=None,
                impact=None,
                vintage_date=oggi,
            )
            for evento, risultato in fills
        ]
        ingest_result = ingest_observations(con, osservazioni, run_id=run_id)
        esito["filled"] = ingest_result["observations"]
        esito["refilled"] = sum(1 for evento, _ in fills
                                if evento["event_id"] in in_ripasso)
        for evento, risultato in fills:
            _write_note(con, evento, risultato, model=model, run_id=run_id,
                        check="web_fill")

    # Reported after the writes, so it counts events that are out of attempts
    # *now* rather than events that were out of attempts before this run added
    # one. A caller printing this sees the size of the give-up pile.
    if ripasso:
        esito["attempts_exhausted"] = con.execute(
            """
            SELECT count(*) FROM (
                SELECT event_id, count(*) AS n FROM calendar_event_notes
                WHERE json_extract_string(commentary_json, '$.check')
                      = 'actual_catchup'
                GROUP BY event_id
            ) WHERE n >= ?
            """, [max_attempts]).fetchone()[0]
    return esito
