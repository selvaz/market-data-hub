# -*- coding: utf-8 -*-
"""T1 validation: cross-check MyFXBook's own reading against what published.

Single-sourcing on MyFXBook drops the redundancy the old five-source
pipeline had almost by accident: when four other scrapers also saw a
release, an outlier reading stood out on its own just from disagreeing with
the rest. Nothing catches MyFXBook misreading its own page any more.

Rather than bolt on a second scraper -- which is how the calendar ended up
with five sources disagreeing with each other in the first place -- this
runs a narrow, targeted check instead: for the day's T1-criticality releases
only, an LLM with live web search looks up what actually published and
compares it against MyFXBook's actual/previous/consensus. A mismatch is
written to ``calendar_event_notes``; nothing here ever corrects MyFXBook's
own value in ``calendar_events``, it only flags where the two disagree, for
a human to look at.

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
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import duckdb

from market_data_hub.econ_calendar.ingest import (
    CalendarObservation,
    ingest_observations,
    parse_number,
)

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


def _t1_events_for_window(
    con: duckdb.DuckDBPyConnection, *, now_utc: datetime, lookback_days: int,
) -> list[dict]:
    """Recent, safely-past T1 releases, including ones awaiting an actual."""
    earliest = now_utc - timedelta(days=lookback_days)
    latest = now_utc - timedelta(minutes=30)
    righe = con.execute(
        """
        SELECT e.event_id, e.indicator_key, i.name, i.area, i.country_iso3,
               e.release_utc, e.actual, e.previous, e.consensus
        FROM calendar_events e
        JOIN calendar_indicators i ON i.indicator_key = e.indicator_key
        WHERE i.criticality = 'T1'
          AND e.release_utc BETWEEN ? AND ?
        ORDER BY e.release_utc
        """,
        [earliest.replace(tzinfo=None), latest.replace(tzinfo=None)],
    ).fetchall()
    return [
        {"event_id": r[0], "indicator_key": r[1], "indicator_name": r[2],
         "area": r[3], "country_iso3": r[4], "release_utc": r[5],
         "actual": r[6], "previous": r[7], "consensus": r[8]}
        for r in righe
    ]


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
) -> dict:
    """Fill or cross-check recent, safely-past T1 releases.

    Returns a summary -- how many were checked, matched, mismatched or could
    not be verified -- so the caller can print it without reading the notes
    table back. A mismatch is written to calendar_event_notes; a match or an
    unverified check writes nothing, because neither is actionable.
    """
    if lookback_days < 0:
        raise ValueError("lookback_days must be non-negative")
    now_utc = datetime.now(timezone.utc)
    if day is not None:
        now_utc = datetime.combine(day, now_utc.timetz())
    eventi = _t1_events_for_window(
        con, now_utc=now_utc, lookback_days=lookback_days)
    esito = {"checked": len(eventi), "match": 0, "mismatch": 0,
             "unverified": 0, "errors": 0, "filled": 0,
             "found_unusable": 0}
    fills: list[tuple[dict, dict]] = []
    for evento in eventi:
        try:
            testo = _ask(_prompt(evento), model=model, effort=effort,
                        max_turns=max_turns, timeout_s=timeout_s)
            risultato = _parse(testo)
        except Exception as e:
            # One release's check is the only thing lost: everything already
            # ingested and audited stands regardless of whether this ran.
            esito["errors"] += 1
            print(f'    {evento["indicator_name"]}: could not run '
                  f'({type(e).__name__}: {str(e)[:120]})', flush=True)
            continue

        if not evento["actual"]:
            if risultato["status"] == "UNVERIFIED":
                esito["unverified"] += 1
            elif parse_number(risultato["actual"]) is None or not risultato["sources"]:
                esito["found_unusable"] += 1
            else:
                fills.append((evento, risultato))
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
                actual=risultato["actual"],
                previous=risultato["previous"] or None,
                consensus=None,
                impact=None,
                vintage_date=oggi,
            )
            for evento, risultato in fills
        ]
        ingest_result = ingest_observations(con, osservazioni, run_id=run_id)
        esito["filled"] = ingest_result["observations"]
        for evento, risultato in fills:
            _write_note(con, evento, risultato, model=model, run_id=run_id,
                        check="web_fill")
    return esito
