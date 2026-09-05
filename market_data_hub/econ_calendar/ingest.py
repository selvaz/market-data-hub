# -*- coding: utf-8 -*-
"""The calendar write path: raw observations and consolidation.

Two layers, deliberately kept apart:

``calendar_observations``
    what each source said, stamped with ``vintage_date``. Nothing is ever
    overwritten: a later revision is a new row, so every disagreement between
    sources stays inspectable instead of being smoothed away.

``calendar_events``
    one row per release, rebuilt from the observations under explicit
    precedence rules. It is derived, and can always be regenerated.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Iterable, Optional, Sequence

import duckdb

# A release is identified by indicator + UTC DAY, not by the minute: sources
# routinely disagree by minutes on the same event, and anchoring identity to
# the minute would produce duplicate events that no later reconciliation
# could merge back together.
_EVENT_GRAIN = "day"

# On the published value, whoever issues the figure wins; web validation
# fills are a last-resort placeholder. On the consensus
# there is no sensible precedence: different survey providers give different
# numbers, and mixing them manufactures surprises. Pick ONE source, say so.
# Forex Factory is that one source: the calendar is collected from its public
# feed, so there is no longer a genuine choice to make here, only a name to record.
_PROVENANCE_RANK = {"official": 0, "aggregator": 1, "web": 2}
DEFAULT_CONSENSUS_SOURCE = "forexfactory"


@dataclass
class CalendarObservation:
    """One calendar row, as published by ONE source."""

    indicator_key: str
    country_iso3: str
    source: str
    provenance: str                      # 'official' | 'aggregator' | 'web'
    source_event_name: str
    release_utc: datetime
    release_precision: str = "minute"    # 'minute' | 'day'
    reference_period: Optional[str] = None
    reference_date: Optional[date] = None
    actual: Optional[str] = None
    consensus: Optional[str] = None
    previous: Optional[str] = None
    revised_from: Optional[str] = None
    impact: Optional[str] = None
    vintage_date: date = field(default_factory=lambda: datetime.now(timezone.utc).date())

    def __post_init__(self) -> None:
        if self.provenance not in _PROVENANCE_RANK:
            raise ValueError(
                f"unknown provenance: {self.provenance!r}; "
                f"expected one of {sorted(_PROVENANCE_RANK)}"
            )
        if self.release_utc.tzinfo is not None:
            self.release_utc = self.release_utc.astimezone(timezone.utc).replace(tzinfo=None)

    @property
    def event_id(self) -> str:
        return make_event_id(self.indicator_key, self.release_utc)


def resolve_event_id(
    con: duckdb.DuckDBPyConnection,
    indicator_key: str,
    release_utc: datetime,
    *,
    tolerance_hours: int = 18,
) -> str:
    """Event id for a release, reusing an existing event when one is close enough.

    A deterministic hash of (indicator, UTC day) is not sufficient on its own:
    sources disagree about the timestamp of the same release, and when the
    disagreement straddles midnight the same print becomes two or three
    separate events. That happened with an RBA decision seen at 21:30, 04:30
    and 00:30 on three different days -- one release, three rows, each
    enriched separately.

    So identity is resolved against what is already stored: an observation
    lands on an existing event when it is within `tolerance_hours` of it. The
    window is wide because the disagreement is measured in hours, and safe
    because no indicator in the catalogue publishes twice within a day.
    """
    row = con.execute(
        """
        SELECT event_id FROM calendar_events
        WHERE indicator_key = ?
          AND abs(date_diff('minute', release_utc, ?)) <= ?
        ORDER BY abs(date_diff('minute', release_utc, ?))
        LIMIT 1
        """,
        [indicator_key, release_utc, tolerance_hours * 60, release_utc],
    ).fetchone()
    if row:
        return row[0]
    return make_event_id(indicator_key, release_utc)


def make_event_id(indicator_key: str, release_utc: datetime) -> str:
    """A stable, deterministic identifier for a release.

    Deterministic on purpose: two collectors seeing the same event must
    produce the same key without consulting each other.
    """
    grain = release_utc.date().isoformat() if _EVENT_GRAIN == "day" \
        else release_utc.replace(second=0, microsecond=0).isoformat()
    impronta = f"{indicator_key}|{grain}".encode("utf-8")
    return hashlib.sha1(impronta).hexdigest()[:16]


def parse_number(valore: Optional[str]) -> Optional[float]:
    """Extract the number from a published value ('-4.9%', '213 K', '$-73.2 B').

    Sources publish formatted strings, not numbers. Scale suffixes must be
    applied: without them '2.5 B' and '2.5 M' would come out identical.
    """
    if valore is None:
        return None
    # Sources embed non-breaking and zero-width spaces inside the numbers
    # ('$​-101.461\xa0B'): strip them before anything else.
    testo = str(valore).replace(",", "")
    testo = re.sub(r"[​‌‍﻿\xa0]", " ", testo).strip()
    if not testo or testo in {"-", "N/D", "nan", "None"}:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", testo)
    if not m:
        return None
    x = float(m.group(0))
    # The sign can precede the currency symbol ('-$101.5B'): searching for
    # the number alone drops it, and a trade balance with a flipped sign is
    # an error nothing downstream catches.
    if x >= 0 and re.match(r"^\s*-", testo):
        x = -x
    coda = testo[m.end():].strip().upper()
    if coda.startswith("K"):
        x *= 1e3
    elif coda.startswith("M"):
        x *= 1e6
    elif coda.startswith("B") or coda.startswith("BN"):
        x *= 1e9
    elif coda.startswith("T"):
        x *= 1e12
    return x


def _valori_concordi(valori: Sequence[Optional[float]], tolleranza: float = 0.02) -> Optional[bool]:
    """Sources agree on the value, allowing for the scale they write it in.

    Tradays writes '75.1 K' where Yahoo writes '75.1', and one source's
    billions are another's units. Comparing raw numbers would flag as a
    disagreement what is only a different unit convention, and bury the real
    disagreements under that noise. So a power-of-a-thousand factor is
    accepted: it is the only scale difference the sources actually use.
    """
    presenti = [v for v in valori if v is not None]
    if len(presenti) < 2:
        return None

    def vicini(a: float, b: float) -> bool:
        if abs(a - b) <= tolleranza * max(abs(a), abs(b), 1e-9):
            return True
        if a == 0 or b == 0:
            return False
        if (a > 0) != (b > 0):
            return False            # opposite signs are not a matter of scale
        grande, piccolo = (abs(a), abs(b)) if abs(a) > abs(b) else (abs(b), abs(a))
        for potenza in (1e3, 1e6, 1e9, 1e12):
            if abs(grande / potenza - piccolo) <= tolleranza * max(piccolo, 1e-9):
                return True
        return False

    riferimento = presenti[0]
    return all(vicini(riferimento, v) for v in presenti[1:])


def ingest_observations(
    con: duckdb.DuckDBPyConnection,
    osservazioni: Iterable[CalendarObservation],
    *,
    run_id: Optional[str] = None,
    consensus_source: str = DEFAULT_CONSENSUS_SOURCE,
) -> dict:
    """Write the observations and rebuild the events they touch.

    Returns a summary with the counts, so the caller can record it in
    ``ingestion_runs`` without reading the database back.
    """
    righe = list(osservazioni)
    if not righe:
        return {"observations": 0, "events": 0, "revised": 0}

    righe, respinte, ridiretti = _applica_alias(con, righe)
    if not righe:
        return {"observations": 0, "events": 0, "revised": 0,
                "rejected_by_alias": respinte, "redirected_by_alias": ridiretti}

    # Identity is resolved before writing, in chronological order: the first
    # observation of a release creates the event, later ones attach to it
    # even when their source places it hours away.
    righe.sort(key=lambda o: (o.indicator_key, o.release_utc))
    id_per_riga: dict[int, str] = {}
    ancore: dict[str, list[tuple[datetime, str]]] = {}
    for o in righe:
        eid = None
        for istante, candidato in ancore.get(o.indicator_key, []):
            if abs((o.release_utc - istante).total_seconds()) <= 18 * 3600:
                eid = candidato
                break
        if eid is None:
            eid = resolve_event_id(con, o.indicator_key, o.release_utc)
            ancore.setdefault(o.indicator_key, []).append((o.release_utc, eid))
        id_per_riga[id(o)] = eid

    revisioni = 0
    for o in righe:
        precedente = con.execute(
            """
            SELECT actual FROM calendar_observations
            WHERE event_id = ? AND source = ? AND vintage_date < ?
            ORDER BY vintage_date DESC LIMIT 1
            """,
            [id_per_riga[id(o)], o.source, o.vintage_date],
        ).fetchone()
        prior = precedente[0] if precedente else None
        if prior is None:
            tipo = "new"
        elif (prior or "") != (o.actual or ""):
            tipo = "revised"
            revisioni += 1
        else:
            tipo = "unchanged"

        # The forecast a provider carried before the print must survive the
        # print. Several calendars overwrite the consensus field with the
        # published value once a release lands, and vintage_date has day
        # granularity, so a pre-release capture and a post-release one on the
        # same day are the same row: INSERT OR REPLACE would erase the only
        # copy of what was expected, and a surprise computed afterwards would
        # be zero by construction.
        consenso = o.consensus
        if not consenso:
            stesso_giorno = con.execute(
                "SELECT consensus FROM calendar_observations "
                "WHERE event_id = ? AND source = ? AND vintage_date = ?",
                [id_per_riga[id(o)], o.source, o.vintage_date],
            ).fetchone()
            if stesso_giorno and stesso_giorno[0]:
                consenso = stesso_giorno[0]

        con.execute(
            """
            INSERT OR REPLACE INTO calendar_observations
                (event_id, source, provenance, vintage_date, source_event_name,
                 release_utc, release_precision, reference_period, reference_date,
                 actual, consensus,
                 previous, revised_from, impact, change_type, prior_actual, run_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [id_per_riga[id(o)], o.source, o.provenance, o.vintage_date, o.source_event_name,
             o.release_utc, o.release_precision, o.reference_period, o.reference_date,
             o.actual, consenso,
             o.previous, o.revised_from, o.impact, tipo, prior, run_id],
        )

    toccati = sorted(set(id_per_riga.values()))
    anagrafica = {}
    for o in righe:
        anagrafica.setdefault(id_per_riga[id(o)], o)
    consolidate_events(con, toccati, anagrafica, consensus_source=consensus_source)
    return {"observations": len(righe), "events": len(toccati), "revised": revisioni,
            "rejected_by_alias": respinte, "redirected_by_alias": ridiretti}


def _applica_alias(con, righe):
    """Let the recorded decision outrank whatever bound the row upstream.

    Matching happens in the collector -- that is the design, and it is why
    `CalendarObservation` arrives with an `indicator_key` already chosen. The
    consequence is that a wrong choice sails straight through here: a
    'Real Earnings' row pre-bound to us_earnings was written and consolidated
    exactly as before, and the alias table sat beside the pipeline documenting
    a rule nothing enforced.

    So the check moves to the one place every writer passes through. A
    rejected triple is dropped; a triple bound to a different indicator is
    redirected to it. Both are counted and returned, because a row silently
    discarded during ingestion is the kind of loss nobody notices until the
    number is missing from a report.

    Rows whose triple has no ruling are left exactly as the collector bound
    them: the table records decisions, it is not a whitelist, and treating an
    absent row as a refusal would empty the calendar.
    """
    try:
        decisioni = con.execute(
            "SELECT source, country_iso3, source_name_norm, indicator_key, status "
            "FROM calendar_indicator_aliases WHERE status IN ('confirmed', 'rejected')"
        ).fetchall()
    except Exception:
        return righe, 0, 0          # table absent: a DB below v11
    if not decisioni:
        return righe, 0, 0

    from market_data_hub.econ_calendar.aliases import normalize_name
    mappa = {(f, p, n): (k, s) for f, p, n, k, s in decisioni}

    tenute, respinte, ridiretti = [], 0, 0
    for o in righe:
        voce = mappa.get((o.source, o.country_iso3, normalize_name(o.source_event_name)))
        if voce is None:
            tenute.append(o)
            continue
        chiave, stato = voce
        if stato == "rejected":
            respinte += 1
            continue
        if chiave and chiave != o.indicator_key:
            o.indicator_key = chiave
            ridiretti += 1
        tenute.append(o)
    return tenute, respinte, ridiretti


def consolidate_events(
    con: duckdb.DuckDBPyConnection,
    event_ids: Sequence[str],
    contesto: "Iterable[CalendarObservation] | dict[str, CalendarObservation]" = (),
    *,
    consensus_source: str = DEFAULT_CONSENSUS_SOURCE,
) -> int:
    """Rebuild ``calendar_events`` for the given events.

    The context is only used for identity (indicator, country, period): the
    values are read back from the observations, so the function stays usable
    for a later re-consolidation without the original rows at hand.
    """
    # Identity must be keyed on the RESOLVED id, not on the observation's
    # nominal hash: after resolution the two can differ.
    if isinstance(contesto, dict):
        per_id = dict(contesto)
    else:
        per_id = {}
        for o in contesto:
            per_id.setdefault(o.event_id, o)

    scritti = 0
    for eid in event_ids:
        # latest version per source
        osservazioni = con.execute(
            """
            SELECT source, provenance, source_event_name, release_utc,
                   reference_period, actual, consensus, previous, revised_from,
                   impact, reference_date, release_precision
            FROM (
                SELECT *, row_number() OVER (
                           PARTITION BY source ORDER BY vintage_date DESC) AS rn
                FROM calendar_observations WHERE event_id = ?
            ) WHERE rn = 1
            """,
            [eid],
        ).fetchall()
        if not osservazioni:
            continue

        # The consensus is read from the OLDEST version each source carried,
        # not the newest. A forecast only exists before the print, and
        # providers routinely replace it with the published number afterwards;
        # reading the latest row would take that replacement for an
        # expectation and report a surprise of zero.
        primo_consenso = {
            fonte: valore
            for fonte, valore in con.execute(
                """
                SELECT source, consensus FROM (
                    SELECT source, consensus, row_number() OVER (
                               PARTITION BY source ORDER BY vintage_date ASC) AS rn
                    FROM calendar_observations
                    WHERE event_id = ? AND consensus IS NOT NULL AND consensus <> ''
                ) WHERE rn = 1
                """,
                [eid],
            ).fetchall()
        }
        osservazioni = [
            r[:6] + (primo_consenso.get(r[0], r[6]),) + r[7:] for r in osservazioni
        ]

        anagrafica = per_id.get(eid)
        if anagrafica is None:
            precedente = con.execute(
                "SELECT indicator_key, country_iso3, release_precision "
                "FROM calendar_events WHERE event_id = ?", [eid]).fetchone()
            if precedente is None:
                # without identity the event cannot be written; the
                # observations remain available for a later re-consolidation
                continue
            indicator_key, country_iso3, precisione = precedente
        else:
            indicator_key = anagrafica.indicator_key
            country_iso3 = anagrafica.country_iso3
            precisione = anagrafica.release_precision

        # The reference date comes from the first source that has one, like
        # the period: taking it from identity would inherit it from an
        # arbitrary source, often the very one that does not publish it.
        reference_date = next((r[10] for r in osservazioni if r[10] is not None), None)

        def scegli(campo: int, fonti_ordinate) -> tuple:
            for fonte in fonti_ordinate:
                for r in osservazioni:
                    if r[0] == fonte and r[campo] not in (None, "", "N/D", "-"):
                        return r[campo], r[0], r[1]
            return None, None, None

        # published value: issuing agencies first, then aggregators, then
        # web validation fills. The latter must never displace a sourced
        # calendar value when a better observation arrives.
        per_provenienza = sorted(osservazioni, key=lambda r: _PROVENANCE_RANK[r[1]])
        ordine_valore = [r[0] for r in per_provenienza]
        actual, actual_src, actual_prov = scegli(5, ordine_valore)
        previous, _, _ = scegli(7, ordine_valore)
        revised, _, _ = scegli(8, ordine_valore)

        # consensus: one source, named. If it is missing, leave it empty
        # rather than falling back to another provider.
        consensus, consensus_src, _ = scegli(6, [consensus_source])
        # The consensus comes from one source, but the others say whether
        # that number holds up. On a US CPI print one source gave 2.7%
        # against a 3.5% previous and a 3.4% actual -- a forecast nobody
        # ever made -- while another copied the released value into the
        # expected column. Without this check the report would have
        # announced a record surprise.
        attese = [parse_number(r[6]) for r in osservazioni]
        attese = [a for a in attese if a is not None]
        consensus_contestato = _valori_concordi(attese)
        if consensus_contestato is not None:
            consensus_contestato = not consensus_contestato
        # When sources diverge the honest answer is the range: printing
        # '2.7%' when another source says '3.4%' manufactures a surprise
        # nobody actually expected.
        cons_min = min(attese) if attese else None
        cons_max = max(attese) if attese else None

        impact, impact_src, _ = scegli(9, ordine_valore)
        periodo = next((r[4] for r in osservazioni if r[4] not in (None, "", "N/D")), None)

        # A source that publishes only a date arrives as midnight, and taking
        # the earliest timestamp across sources would make that midnight the
        # release instant of an event another collector timed to the minute.
        # v_macro_panel_asof exposes this as `known_from`, so the cost is a
        # backtest treating a figure as public hours before it was: the one
        # error the point-in-time bridge exists to prevent. A known minute
        # always outranks a placeholder, however early the placeholder looks.
        al_minuto = [r[3] for r in osservazioni
                     if r[3] is not None and r[11] == "minute"]
        if al_minuto:
            release, precisione = min(al_minuto), "minute"
        else:
            release = min(r[3] for r in osservazioni if r[3] is not None)
            precisione = "day"

        numerici = [parse_number(r[5]) for r in osservazioni]

        con.execute(
            """
            INSERT OR REPLACE INTO calendar_events
                (event_id, indicator_key, country_iso3, release_utc, release_precision,
                 reference_period, reference_date, reference_date_origin,
                 status, actual, actual_num,
                 actual_source, actual_provenance, consensus, consensus_num,
                 consensus_source, consensus_disputed, consensus_low,
                 consensus_high, consensus_n, previous, previous_num,
                 revised_from, impact, impact_source, n_sources, values_agree,
                 first_seen_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    COALESCE((SELECT first_seen_at FROM calendar_events WHERE event_id = ?),
                             ?), ?)
            """,
            [eid, indicator_key, country_iso3, release, precisione,
             periodo, reference_date,
             # a re-consolidation must not relabel an inferred date as published
             "source" if reference_date is not None else None,
             "released" if actual else "scheduled",
             actual, parse_number(actual), actual_src, actual_prov,
             consensus, parse_number(consensus), consensus_src, consensus_contestato,
             cons_min, cons_max, len(attese),
             previous, parse_number(previous), revised, impact, impact_src,
             len(osservazioni), _valori_concordi(numerici),
             eid, datetime.now(timezone.utc), datetime.now(timezone.utc)],
        )
        scritti += 1
    return scritti
