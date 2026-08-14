# -*- coding: utf-8 -*-
"""What each source means by a name, recorded rather than recomputed.

``match_rules`` is a regex, and a regex generalises. That is the whole defect:
it does not decide about a name, it meets one and swallows it. ``hourly|earnings``
was never a judgement about BLS 'Real Earnings' -- a different series, out
alongside the CPI because it is CPI-deflated earnings -- yet it filed it as
Average Hourly Earnings, inherited the T1 tier, and put 0.0% for July in a
report when the real print was 3.2%, published five days earlier. Twelve more
bindings of the same shape turned up the same afternoon.

An alias cannot generalise. ``(source, country, name)`` is in the table or it
is not, and if it is, somebody put it there.

Country is what makes the key work. 'CPI y/y' on Tradays means eleven
different indicators, one per country; with the country in the key, the 411
bindings collected so far leave exactly zero ambiguity.

**The regex stays, one rung down.** When a name arrives that nobody has ruled
on, ``match_rules`` proposes an indicator and the row lands as 'proposed'.
Nothing enters the calendar on a proposal: proposals are a work queue.

**The failure mode changes shape, and that is the point.** An unknown name now
produces no match instead of a wrong one, so an event goes missing rather than
carrying a wrong number. That is the better failure, not a harmless one --
which is why ``unmapped()`` exists and why the daily job has to look at it.

**What this does not catch**, and it matters: on 12 August all three sources
called that release 'Real Earnings' and all three agreed on 0.0%. Cross-checking
sources against each other would have *confirmed* the wrong binding. Agreement
between sources catches timing and value errors, never a shared wrong name.
What catches it is ``cadence_violations()``: Average Hourly Earnings is monthly,
and two of them in one August is an arithmetic contradiction.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterable, Optional

import duckdb

# Names arrive with soft hyphens, non-breaking and zero-width spaces embedded
# by the scrapers, plus the asterisks and daggers sources use to mark revised
# or provisional figures. None of that distinguishes one indicator from another.
_INVISIBILI = dict.fromkeys(map(ord, "   ​‌‍﻿"), " ")
_ORNAMENTI = re.compile(r"[*†‡°]+")


def normalize_name(nome: str) -> str:
    """The join key: a source's name, stripped of what is merely typography.

    Deliberately gentler than the normalisation in ``audit``. There, 'm/m' and
    'y/y' are noise because they separate releases of one indicator; here they
    separate *different* indicators -- Mexico's core CPI m/m is not its core
    CPI y/y -- so nothing that could carry meaning is removed. Case, invisible
    characters, revision marks and spacing: that is all.
    """
    s = (nome or "").translate(_INVISIBILI)
    s = _ORNAMENTI.sub(" ", s).lower()
    s = re.sub(r"\s+", " ", s).strip(" -,.:;")
    return s


def upsert_alias(
    con: duckdb.DuckDBPyConnection,
    *,
    source: str,
    country_iso3: str,
    source_name: str,
    indicator_key: Optional[str],
    status: str = "confirmed",
    decided_by: Optional[str] = None,
    note: Optional[str] = None,
) -> str:
    """Record a decision about one name. Returns the normalised key."""
    if status not in {"confirmed", "proposed", "rejected"}:
        raise ValueError(
            f"unknown alias status: {status!r}. Use 'confirmed' (a decision was "
            "made), 'proposed' (the regex suggested it, nobody has ruled) or "
            "'rejected' (seen and deliberately not tracked)."
        )
    norm = normalize_name(source_name)
    con.execute(
        """
        INSERT OR REPLACE INTO calendar_indicator_aliases
            (source, country_iso3, source_name_norm, source_name_raw,
             indicator_key, status, decided_by, decided_at, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [source, country_iso3, norm, source_name, indicator_key, status,
         decided_by, datetime.now(timezone.utc), note],
    )
    return norm


def resolve(
    con: duckdb.DuckDBPyConnection,
    source: str,
    country_iso3: str,
    source_name: str,
) -> Optional[str]:
    """The indicator this source means by this name, or None.

    None covers three different situations on purpose -- never seen, seen and
    not tracked, seen and only proposed -- because the caller's action is the
    same in all three: do not put it in the calendar. Telling them apart is
    ``unmapped()``'s job, and that is a reporting question, not an ingestion one.
    """
    r = con.execute(
        """
        SELECT indicator_key FROM calendar_indicator_aliases
        WHERE source = ? AND country_iso3 = ? AND source_name_norm = ?
          AND status = 'confirmed'
        """,
        [source, country_iso3, normalize_name(source_name)],
    ).fetchone()
    return r[0] if r else None


def load_aliases(con: duckdb.DuckDBPyConnection) -> dict[tuple[str, str, str], str]:
    """Every confirmed binding as a dict, for ingesting a day in one pass."""
    return {
        (s, c, n): k
        for s, c, n, k in con.execute(
            "SELECT source, country_iso3, source_name_norm, indicator_key "
            "FROM calendar_indicator_aliases "
            "WHERE status = 'confirmed' AND indicator_key IS NOT NULL"
        ).fetchall()
    }


def seed_from_observations(
    con: duckdb.DuckDBPyConnection,
    *,
    decided_by: str = "seed",
    status: str = "confirmed",
) -> int:
    """Bootstrap the table from the bindings already in the calendar.

    Starting from what the regex accepted is the only sensible beginning --
    the alternative is 411 rows typed by hand -- but the seed is not verified
    by virtue of being the seed. It carries whatever errors the regex made and
    nobody has caught yet. ``audit.suspect_matches`` is the review queue for
    exactly this, and it is why this is a function somebody calls rather than
    a step in the migration.
    """
    righe = con.execute(
        """
        SELECT o.source, i.country_iso3, o.source_event_name, e.indicator_key,
               count(*) AS n
        FROM calendar_observations o
        JOIN calendar_events e USING (event_id)
        JOIN calendar_indicators i ON i.indicator_key = e.indicator_key
        GROUP BY ALL
        """
    ).fetchall()

    n = 0
    for source, paese, nome, chiave, quante in righe:
        upsert_alias(
            con, source=source, country_iso3=paese, source_name=nome,
            indicator_key=chiave, status=status, decided_by=decided_by,
            note=f"seeded from {quante} observation(s) matched by match_rules",
        )
        n += 1
    return n


def propose(
    con: duckdb.DuckDBPyConnection,
    *,
    source: str,
    country_iso3: str,
    source_name: str,
    indicator_key: Optional[str] = None,
    note: Optional[str] = None,
) -> bool:
    """Record a name nobody has ruled on. Returns True if it was new.

    Never overwrites an existing row: a decision already taken outranks a
    fresh suggestion, and a rejection that quietly turned back into a proposal
    would be the same silent drift the alias table exists to stop.
    """
    norm = normalize_name(source_name)
    esiste = con.execute(
        "SELECT 1 FROM calendar_indicator_aliases "
        "WHERE source = ? AND country_iso3 = ? AND source_name_norm = ?",
        [source, country_iso3, norm],
    ).fetchone()
    if esiste:
        return False
    upsert_alias(con, source=source, country_iso3=country_iso3,
                 source_name=source_name, indicator_key=indicator_key,
                 status="proposed", decided_by="match_rules", note=note)
    return True


def unmapped(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Names awaiting a decision, the ones the regex would have swallowed.

    This is the cost of the alias table and it has to be paid in the open: an
    unknown name no longer produces a wrong event, it produces no event, and
    nothing else in the pipeline will mention it.
    """
    return [
        {"source": s, "country_iso3": c, "source_name": raw or n,
         "suggested": k, "note": note}
        for s, c, n, raw, k, note in con.execute(
            "SELECT source, country_iso3, source_name_norm, source_name_raw, "
            "       indicator_key, note "
            "FROM calendar_indicator_aliases WHERE status = 'proposed' "
            "ORDER BY source, country_iso3, source_name_norm"
        ).fetchall()
    ]


# Releases per calendar period implied by the catalogue's frequency. 'E' means
# the schedule is the calendar's own (central bank meetings), so there is
# nothing to check against.
_ATTESI = {"W": ("week", 1), "M": ("month", 1), "Q": ("quarter", 1), "A": ("year", 1)}


def cadence_violations(
    con: duckdb.DuckDBPyConnection,
    *,
    indicator_keys: Optional[Iterable[str]] = None,
) -> list[dict]:
    """Indicators that released more often than their frequency allows.

    The check the alias table cannot perform on its own. A monthly indicator
    with two released events in one August is a contradiction no amount of
    agreement between sources can excuse -- and that is exactly the shape the
    Real Earnings binding took, sitting next to the genuine payroll-day print.

    Two honest limits. Flash and final estimates of the same indicator land in
    one period by design and will show up here; so will the first month a
    revision is filed as its own release. This reviews, it does not reject.
    """
    dove, parametri = "", []
    if indicator_keys is not None:
        chiavi = list(indicator_keys)
        if not chiavi:
            return []
        dove = f"AND e.indicator_key IN ({','.join('?' * len(chiavi))})"
        parametri = chiavi

    fuori = []
    for freq, (unita, atteso) in _ATTESI.items():
        righe = con.execute(
            f"""
            SELECT e.indicator_key, i.name, i.area, i.criticality,
                   date_trunc('{unita}', e.release_utc) AS periodo,
                   count(*) AS n,
                   string_agg(strftime(e.release_utc, '%Y-%m-%d'), ', '
                              ORDER BY e.release_utc) AS giorni
            FROM calendar_events e
            JOIN calendar_indicators i ON i.indicator_key = e.indicator_key
            WHERE i.frequency = ? AND e.status = 'released' {dove}
            GROUP BY ALL
            HAVING count(*) > ?
            """,
            [freq, *parametri, atteso],
        ).fetchall()
        for chiave, nome, area, crit, periodo, n, giorni in righe:
            fuori.append({
                "indicator_key": chiave, "indicator_name": nome, "area": area,
                "criticality": crit, "frequency": freq,
                "period": str(periodo)[:10], "releases": n, "expected": atteso,
                "dates": giorni,
            })

    fuori.sort(key=lambda x: (-x["releases"], x["indicator_key"]))
    return fuori


def format_unmapped(righe: list[dict]) -> str:
    if not righe:
        return "No unmapped name: every name seen has been ruled on."
    out = [f"{len(righe)} names awaiting a decision "
           f"(they produce no event until one is made):", ""]
    for r in righe:
        suggerito = r["suggested"] or "-- no suggestion"
        out.append(f"  {r['source']:10s} {r['country_iso3']}  {r['source_name']}")
        out.append(f"      regex suggests: {suggerito}")
    return "\n".join(out)


def format_cadence(righe: list[dict]) -> str:
    if not righe:
        return "No cadence violation: every indicator released as often as declared."
    out = [f"{len(righe)} indicators released more often than declared:", ""]
    for r in righe:
        out.append(f"[{r['criticality']}] {r['area']} {r['indicator_name']} "
                   f"({r['indicator_key']}, {r['frequency']})")
        out.append(f"      {r['period']}: {r['releases']} releases where "
                   f"{r['expected']} was expected -- {r['dates']}")
    return "\n".join(out)
