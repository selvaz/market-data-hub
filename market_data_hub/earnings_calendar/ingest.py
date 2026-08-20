# -*- coding: utf-8 -*-
"""The earnings calendar write path: observations in, events out.

``earnings_observations`` keeps what each source said, stamped with the day we
saw it. ``earnings_events`` is derived from those and can always be rebuilt.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import duckdb

# Closed vocabulary. A country nobody mapped lands in 'unknown' rather than in
# a plausible-looking region: audit() counts those so the map can be grown.
REGIONS = ("us", "europe", "japan", "china_hk", "apac_ex_jp_cn",
           "americas_ex_us", "emea_other", "unknown")

# Later evidence outranks earlier: a release that happened cannot go back to
# being expected, whatever a stale forward run says about the same quarter.
STATUS_RANK = {"estimated": 0, "confirmed": 1, "occurred": 2}

# How far a release may move and still be the same release. Reporting is
# quarterly, so a few weeks tells a slipped date from the next quarter's print.
_TOLLERANZA_GIORNI = 25

_PAESI_PER_REGIONE = {
    "us": ["United States"],
    "japan": ["Japan"],
    "china_hk": ["China", "Hong Kong", "Macau"],
    "europe": [
        "Austria", "Belgium", "Bulgaria", "Croatia", "Cyprus", "Czech Republic",
        "Denmark", "Estonia", "Finland", "France", "Germany", "Greece", "Hungary",
        "Iceland", "Ireland", "Italy", "Latvia", "Lithuania", "Luxembourg", "Malta",
        "Netherlands", "Norway", "Poland", "Portugal", "Romania", "Slovakia",
        "Slovenia", "Spain", "Sweden", "Switzerland", "United Kingdom",
    ],
    "apac_ex_jp_cn": [
        "Australia", "Bangladesh", "India", "Indonesia", "Malaysia", "New Zealand",
        "Pakistan", "Philippines", "Singapore", "South Korea", "Sri Lanka",
        "Taiwan", "Thailand", "Vietnam",
    ],
    "americas_ex_us": [
        "Argentina", "Bermuda", "Brazil", "Canada", "Cayman Islands", "Chile",
        "Colombia", "Mexico", "Panama", "Peru", "Uruguay",
    ],
    "emea_other": [
        "Bahrain", "Egypt", "Israel", "Jordan", "Kenya", "Kuwait", "Morocco",
        "Nigeria", "Oman", "Qatar", "Russia", "Saudi Arabia", "South Africa",
        "Turkey", "United Arab Emirates",
    ],
}
_REGION_BY_COUNTRY = {paese: regione
                      for regione, paesi in _PAESI_PER_REGIONE.items()
                      for paese in paesi}

_THEMES_PATH = Path(__file__).resolve().parents[1] / "config" / "earnings_themes.yaml"
_themes_cache: Optional[dict] = None


def region_of(country: Optional[str]) -> str:
    """The region a country reports in, or 'unknown'."""
    return _REGION_BY_COUNTRY.get((country or "").strip(), "unknown")


def theme_of(industry: Optional[str]) -> Optional[str]:
    """The curated theme for an industry, or None when it has no mapping."""
    global _themes_cache
    if _themes_cache is None:
        mappa: dict = {}
        try:
            import yaml
            caricato = yaml.safe_load(_THEMES_PATH.read_text(encoding="utf-8")) or {}
            for tema, industrie in (caricato.get("themes") or {}).items():
                for i in industrie or ():
                    # An industry containing a colon parses as a mapping unless
                    # it is quoted, and would silently never match anything.
                    if not isinstance(i, str):
                        raise ValueError(
                            f"{_THEMES_PATH.name}: theme {tema!r} has a non-string "
                            f"entry {i!r} -- quote industries containing a colon")
                    mappa[i.strip().lower()] = tema
        except FileNotFoundError:
            pass
        # Published only once complete: a half-built map cached after an error
        # would answer None for everything, quietly, for the rest of the run.
        _themes_cache = mappa
    return _themes_cache.get((industry or "").strip().lower())


def make_event_id(exchange: str, symbol: str, release: datetime) -> str:
    """Identity for a release nothing has seen yet.

    Keyed on the day, so two releases a quarter apart can never share an id.
    A date that later moves is matched by resolve_event_id() instead.
    """
    impronta = f"{exchange}|{symbol}|{release.date().isoformat()}".encode("utf-8")
    return hashlib.sha1(impronta).hexdigest()[:16]


def resolve_event_id(con: duckdb.DuckDBPyConnection, exchange: str, symbol: str,
                     release: datetime, *,
                     tolerance_days: int = _TOLLERANZA_GIORNI) -> str:
    """Identity for a release, reusing a stored event when one is close enough.

    A date that slips from 30 September to 2 October is the same release, and
    keying on the day alone would report a no-show that never happened. But a
    company reporting on 1 July and again on 30 September has released twice,
    and merging those would erase the first.

    Reporting is quarterly, so a few weeks separates "the same release, moved"
    from "the next one". It is a heuristic, not a guarantee: a reschedule
    further out than the window splits into two events.
    """
    riga = con.execute(
        """
        SELECT event_id FROM earnings_events
        WHERE exchange = ? AND symbol = ?
          AND abs(date_diff('day', release_ts_utc, ?)) <= ?
        ORDER BY abs(date_diff('day', release_ts_utc, ?)), event_id
        LIMIT 1
        """,
        [exchange, symbol, release, tolerance_days, release],
    ).fetchone()
    if riga:
        return riga[0]
    return make_event_id(exchange, symbol, release)


@dataclass
class EarningsObservation:
    """One scheduled or published release, as ONE source reports it."""

    symbol: str
    exchange: str
    source: str
    status: str                          # 'estimated' | 'confirmed' | 'occurred'
    release_ts_utc: datetime
    tv_ticker: Optional[str] = None
    company_name: Optional[str] = None
    country: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    market_cap: Optional[float] = None
    release_precision: str = "minute"    # 'minute' | 'day'
    eps_estimate: Optional[float] = None
    eps_actual: Optional[float] = None
    revenue_estimate: Optional[float] = None
    revenue_actual: Optional[float] = None
    currency: Optional[str] = None
    vintage_date: date = field(default_factory=lambda: datetime.now(timezone.utc).date())

    def __post_init__(self) -> None:
        if self.status not in STATUS_RANK:
            raise ValueError(f"unknown status: {self.status!r}; "
                             f"expected one of {sorted(STATUS_RANK)}")
        if self.release_ts_utc.tzinfo is not None:
            self.release_ts_utc = (self.release_ts_utc.astimezone(timezone.utc)
                                   .replace(tzinfo=None))

    @property
    def region(self) -> str:
        return region_of(self.country)

    @property
    def event_id(self) -> str:
        return make_event_id(self.exchange, self.symbol, self.release_ts_utc)


def ingest_observations(
    con: duckdb.DuckDBPyConnection,
    osservazioni: Iterable[EarningsObservation],
    *,
    run_id: Optional[str] = None,
) -> dict:
    """Write the observations and rebuild the events they touch."""
    righe = list(osservazioni)
    if not righe:
        return {"observations": 0, "events": 0}

    # Rows of one batch must also match EACH OTHER, not only what is already
    # stored: nothing is written until the loop below, so two sources
    # disagreeing by a few hours would otherwise resolve independently and
    # split into two events -- the same call giving a different answer than
    # two successive ones.
    identita: dict[int, str] = {}
    ancore: dict[tuple, list] = {}
    for o in righe:
        chiave = (o.exchange, o.symbol)
        eid = next((e for istante, e in ancore.get(chiave, ())
                    if abs((o.release_ts_utc - istante).total_seconds())
                    <= _TOLLERANZA_GIORNI * 86400), None)
        if eid is None:
            eid = resolve_event_id(con, o.exchange, o.symbol, o.release_ts_utc)
            ancore.setdefault(chiave, []).append((o.release_ts_utc, eid))
        identita[id(o)] = eid

    for o in righe:
        con.execute(
            """
            INSERT OR REPLACE INTO earnings_observations
                (event_id, source, vintage_date, symbol, exchange, tv_ticker,
                 company_name, country, sector, industry, market_cap,
                 release_ts_utc, release_precision, status,
                 eps_estimate, eps_actual, revenue_estimate, revenue_actual,
                 currency, run_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [identita[id(o)], o.source, o.vintage_date, o.symbol, o.exchange, o.tv_ticker,
             o.company_name, o.country, o.sector, o.industry, o.market_cap,
             o.release_ts_utc, o.release_precision, o.status,
             o.eps_estimate, o.eps_actual, o.revenue_estimate, o.revenue_actual,
             o.currency, run_id],
        )

    toccati = sorted(set(identita.values()))
    consolidate_events(con, toccati)
    return {"observations": len(righe), "events": len(toccati)}


def consolidate_events(con: duckdb.DuckDBPyConnection,
                       event_ids: Iterable[str]) -> int:
    """Rebuild ``earnings_events`` from the stored observations."""
    scritti = 0
    for eid in event_ids:
        osservazioni = con.execute(
            """
            SELECT source, symbol, exchange, tv_ticker, company_name, country,
                   sector, industry, market_cap,
                   release_ts_utc, release_precision, status, eps_estimate,
                   eps_actual, revenue_estimate, revenue_actual, currency
            FROM (
                SELECT *, row_number() OVER (
                           PARTITION BY source ORDER BY vintage_date DESC) AS rn
                FROM earnings_observations WHERE event_id = ?
            ) WHERE rn = 1
            """,
            [eid],
        ).fetchall()
        if not osservazioni:
            continue

        # The furthest-advanced source decides the event: one that saw the
        # release happen knows more than one still expecting it. Source name
        # breaks a tie, so repeated consolidations cannot disagree.
        ordinate = sorted(osservazioni, key=lambda r: (-STATUS_RANK[r[11]], r[0]))
        principale = ordinate[0]

        def primo(campo: int):
            """First non-null across sources, most advanced first."""
            for r in ordinate:
                if r[campo] is not None:
                    return r[campo]
            return None

        # Being the authority on WHETHER a release happened does not make a
        # source the authority on WHEN: a day-only row arrives as midnight, and
        # recording that would move an Asian release to the previous local day.
        # A known minute within a day of it wins -- a day, not the same UTC
        # date, because the local reporting day straddles midnight UTC.
        release, precisione = principale[9], principale[10]
        if precisione == "day" and release is not None:
            al_minuto = [r[9] for r in ordinate
                         if r[10] == "minute" and r[9] is not None
                         and abs((r[9] - release).total_seconds()) <= 86400]
            if al_minuto:
                release, precisione = al_minuto[0], "minute"

        # The region has to follow the country actually recorded, or an event
        # ends up filed under a country while counted as region 'unknown'.
        paese = primo(5)

        con.execute(
            """
            INSERT OR REPLACE INTO earnings_events
                (event_id, symbol, exchange, tv_ticker, company_name, country,
                 region, sector, industry, theme, market_cap,
                 release_ts_utc, release_precision, status, eps_estimate,
                 eps_actual, revenue_estimate, revenue_actual, currency,
                 n_sources, first_seen_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    COALESCE((SELECT first_seen_at FROM earnings_events WHERE event_id = ?),
                             ?), ?)
            """,
            [eid, principale[1], principale[2], primo(3), primo(4), paese,
             region_of(paese), primo(6), primo(7), theme_of(primo(7)), primo(8),
             release, precisione, principale[11],
             primo(12), primo(13), primo(14), primo(15), primo(16),
             len(osservazioni),
             eid, datetime.now(timezone.utc), datetime.now(timezone.utc)],
        )
        scritti += 1
    return scritti


def audit(con: duckdb.DuckDBPyConnection) -> dict:
    """Counts of invariant violations, not of rows. Never fatal."""
    def conta(sql: str) -> int:
        try:
            return con.execute(sql).fetchone()[0]
        except duckdb.Error:
            return -1

    return {
        # An event with no surviving observation cannot be regenerated.
        "orphan_events": conta(
            "SELECT count(*) FROM earnings_events e WHERE NOT EXISTS ("
            "SELECT 1 FROM earnings_observations o WHERE o.event_id = e.event_id)"),
        "unknown_region": conta(
            "SELECT count(*) FROM earnings_events WHERE region = 'unknown'"),
        "unmapped_industry": conta(
            "SELECT count(DISTINCT industry) FROM earnings_events "
            "WHERE theme IS NULL AND industry IS NOT NULL"),
        "missing_market_cap": conta(
            "SELECT count(*) FROM earnings_events WHERE market_cap IS NULL"),
        # A release that happened without a figure, or one that has not yet
        # happened but already carries one: both mean a source contradicted itself.
        "occurred_without_actual": conta(
            "SELECT count(*) FROM earnings_events WHERE status = 'occurred' "
            "AND eps_actual IS NULL AND revenue_actual IS NULL"),
        "pending_with_actual": conta(
            "SELECT count(*) FROM earnings_events WHERE status <> 'occurred' "
            "AND (eps_actual IS NOT NULL OR revenue_actual IS NOT NULL)"),
    }
