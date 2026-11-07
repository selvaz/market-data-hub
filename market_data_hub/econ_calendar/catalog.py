# -*- coding: utf-8 -*-
"""The catalogue of tracked indicators: loading and upsert.

The catalogue is data, not code: it lives in ``config/econ_calendar.yaml``
next to ``macro_panel.yaml``, so the criticality classification can be
corrected without touching the module. Each entry also carries its matching
rules, because every source names the same indicator differently ('CPI YY,
NSA' on Yahoo, 'CPI y/y' on Tradays, 'CPI' on Nasdaq) and the rule is the
only place that knowledge lives.

Description and methodology sit on *archetypes*, not on individual
indicators: a y/y CPI is the same object in eleven countries, only the
issuing institute changes. Fixing one description propagates everywhere.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import duckdb
import yaml

# The hub uses ISO3; the calendar thinks in reporting areas. The aggregate
# euro area has no ISO3 of its own: 'EMU' is the ECB/Eurostat convention.
AREA_ISO3 = {
    "US": "USA", "CN": "CHN", "EZ": "EMU", "EU": "EMU", "UK": "GBR", "GB": "GBR",
    "JP": "JPN", "IN": "IND", "MX": "MEX", "BR": "BRA", "CA": "CAN", "AU": "AUS",
    "KR": "KOR", "TW": "TWN", "DE": "DEU", "FR": "FRA", "IT": "ITA", "ES": "ESP",
    "CH": "CHE", "NZ": "NZL", "ZA": "ZAF", "SE": "SWE", "NO": "NOR", "SG": "SGP",
    "HK": "HKG", "RU": "RUS", "TR": "TUR", "PL": "POL",
}

_DEFAULT_CATALOG = Path(__file__).resolve().parents[1] / "config" / "econ_calendar.yaml"


def to_iso3(codice: str) -> str:
    """ISO2 (or area code) -> the hub's ISO3. The euro area becomes 'EMU'."""
    c = (codice or "").strip().upper()
    if len(c) == 3 and c not in AREA_ISO3:
        return c
    try:
        return AREA_ISO3[c]
    except KeyError:
        raise ValueError(
            f"country code with no ISO3 mapping: {codice!r}. "
            "Add it to AREA_ISO3 rather than letting it through: an unmapped "
            "country would silently break the join with macro_panel."
        ) from None


def load_catalog_rows(percorso: Optional[Path] = None) -> list[dict]:
    """Read the catalogue, resolve country codes and flatten the archetypes.

    Flattening happens here and not in the DB because ``calendar_indicators``
    is the table agents query: it must answer without requiring the reader to
    know what an archetype is.
    """
    p = Path(percorso) if percorso else _DEFAULT_CATALOG
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    archetipi = {a["key"]: a for a in doc.get("archetypes", [])}

    righe = []
    for v in doc.get("indicators", []):
        a = archetipi.get(v.get("archetype"), {})
        righe.append({
            "indicator_key": v["key"],
            "country_iso2": v.get("country_iso2"),
            "country_iso3": to_iso3(v.get("country_iso2") or v.get("area", "")),
            "area": v["area"],
            "name": v["name"],
            "category": v.get("category"),
            "data_type": v.get("data_type"),
            "side": v.get("side"),
            "tags": v.get("tags"),
            "archetype": v.get("archetype"),
            "value_type": a.get("value_type"),
            "frequency": v.get("freq"),
            "criticality": v.get("criticality"),
            "nature": v.get("nature"),
            "rationale": v.get("rationale"),
            "agency": v.get("agency"),
            "description": a.get("description"),
            "methodology": a.get("methodology"),
            "macro_indicator_id": v.get("macro_indicator_id"),
            "macro_series_id": v.get("macro_series_id"),
            "match_rules": v.get("match_rules"),
            "match_excludes": v.get("match_excludes"),
            "active": v.get("active", True),
        })
    return righe


def upsert_indicators(
    con: duckdb.DuckDBPyConnection,
    righe: Iterable[dict],
) -> int:
    """Insert or update the catalogue. Idempotent on the key."""
    ora = datetime.now(timezone.utc)
    n = 0
    for r in righe:
        con.execute(
            """
            INSERT OR REPLACE INTO calendar_indicators
                (indicator_key, country_iso3, country_iso2, area, name, category,
                 data_type, side, tags,
                 archetype, value_type, frequency, criticality, nature, rationale,
                 agency, description, methodology, macro_indicator_id,
                 macro_series_id, match_rules, match_excludes, active, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [r["indicator_key"], r["country_iso3"], r.get("country_iso2"),
             r["area"], r["name"], r.get("category"),
             r.get("data_type"), r.get("side"), r.get("tags"),
             r.get("archetype"),
             r.get("value_type"), r.get("frequency"), r.get("criticality"),
             r.get("nature"), r.get("rationale"), r.get("agency"),
             r.get("description"), r.get("methodology"),
             r.get("macro_indicator_id") or None, r.get("macro_series_id") or None,
             r.get("match_rules"), r.get("match_excludes"),
             str(r.get("active", "true")).lower() not in {"false", "0", "no"}, ora],
        )
        n += 1
    return n


def available_series(
    con: duckdb.DuckDBPyConnection,
    *,
    day: Optional[str] = None,
    from_day: Optional[str] = None,
    to_day: Optional[str] = None,
    country: Optional[str] = None,
    area: Optional[str] = None,
    category: Optional[str] = None,
    tags: Optional[Iterable[str]] = None,
    data_type: Optional[str] = None,
    criticality: Optional[str] = None,
    released_only: bool = False,
) -> list[dict]:
    """What the calendar can answer about, along the axes people ask in.

    With no arguments it says what exists. With a window it says what came out
    in it. With ``country='IND', category='Inflation'`` it answers the question
    an agent actually has, without that agent needing to know an indicator_key.

    Two columns exist for the caller's benefit rather than the catalogue's.
    ``events`` is how many releases fall in the window, so an empty answer is
    distinguishable from an untracked one. ``with_reference_date`` is how many
    of those carry a period: a caller planning to join against ``macro_panel``
    can see whether that join has anything to stand on *before* attempting it,
    which today would be 43% of releases and nothing like uniform across
    indicators.
    """
    dove, parametri = ["i.active"], []
    if day:
        from_day = to_day = day
    if from_day:
        dove.append("e.release_utc >= ?::date")
        parametri.append(from_day)
    if to_day:
        dove.append("e.release_utc < ?::date + INTERVAL 1 DAY")
        parametri.append(to_day)
    # Compared case-insensitively. These are a closed vocabulary a caller is
    # expected to have read from catalogue_vocabulary(), and an agent that
    # writes 'inflation' for 'Inflation' should get the answer rather than an
    # empty list indistinguishable from 'nothing matches'.
    for colonna, valore in (("i.country_iso3", country), ("i.area", area),
                            ("i.category", category), ("i.data_type", data_type),
                            ("i.criticality", criticality)):
        if valore:
            dove.append(f"lower({colonna}) = lower(?)")
            parametri.append(valore)
    for t in (tags or []):
        # pipe-delimited on both sides so 'hard' cannot match 'hardship'
        dove.append("'|' || coalesce(i.tags, '') || '|' LIKE ?")
        parametri.append(f"%|{t}|%")
    if released_only:
        dove.append("e.status = 'released'")

    # LEFT JOIN, deliberately: an indicator the calendar tracks but that has no
    # event in the window is an answer, not a row to hide. 'we watch Indian CPI
    # and nothing came out' and 'we do not watch it' are different statements.
    giunzione = "LEFT JOIN" if not (from_day or released_only) else "JOIN"
    return [
        dict(zip(("indicator_key", "name", "area", "country_iso3", "category",
                  "data_type", "side", "tags", "frequency", "criticality",
                  "nature", "agency", "events", "first_release", "last_release",
                  "with_reference_date"), r))
        for r in con.execute(f"""
            SELECT i.indicator_key, i.name, i.area, i.country_iso3, i.category,
                   i.data_type, i.side, i.tags, i.frequency, i.criticality,
                   i.nature, i.agency,
                   count(e.event_id) AS events,
                   min(e.release_utc) AS first_release,
                   max(e.release_utc) AS last_release,
                   count(e.reference_date) AS with_reference_date
            FROM calendar_indicators i
            {giunzione} calendar_events e ON e.indicator_key = i.indicator_key
            WHERE {' AND '.join(dove)}
            GROUP BY ALL
            ORDER BY i.criticality, i.area, i.name
        """, parametri).fetchall()
    ]


def catalogue_vocabulary(con: duckdb.DuckDBPyConnection) -> dict:
    """The values available_series() will actually match, with their counts.

    A filter over a closed vocabulary is only usable by someone who knows the
    vocabulary. Without this a caller guesses between 'Inflation', 'inflation'
    and 'Prices', and two of those return an empty list that looks exactly like
    a legitimate 'nothing matched'. Matching is case-insensitive for the same
    reason; this is how a caller learns the axes exist at all.
    """
    def conta(colonna: str, dividi: bool = False) -> dict:
        righe = con.execute(
            f"SELECT {colonna}, count(*) FROM calendar_indicators "
            f"WHERE active AND {colonna} IS NOT NULL AND {colonna} <> '' "
            f"GROUP BY 1 ORDER BY 2 DESC"
        ).fetchall()
        if not dividi:
            return {v: n for v, n in righe}
        singoli: dict[str, int] = {}
        for valore, n in righe:
            for pezzo in str(valore).split("|"):
                if pezzo:
                    singoli[pezzo] = singoli.get(pezzo, 0) + n
        return dict(sorted(singoli.items(), key=lambda x: -x[1]))

    return {
        "category": conta("category"),
        "data_type": conta("data_type"),
        "side": conta("side"),
        "nature": conta("nature"),
        "criticality": conta("criticality"),
        "frequency": conta("frequency"),
        "area": conta("area"),
        "country_iso3": conta("country_iso3"),
        "tags": conta("tags", dividi=True),
    }
