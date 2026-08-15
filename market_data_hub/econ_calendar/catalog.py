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
                 archetype, value_type, frequency, criticality, nature, rationale,
                 agency, description, methodology, macro_indicator_id,
                 macro_series_id, match_rules, match_excludes, active, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [r["indicator_key"], r["country_iso3"], r.get("country_iso2"),
             r["area"], r["name"], r.get("category"), r.get("archetype"),
             r.get("value_type"), r.get("frequency"), r.get("criticality"),
             r.get("nature"), r.get("rationale"), r.get("agency"),
             r.get("description"), r.get("methodology"),
             r.get("macro_indicator_id") or None, r.get("macro_series_id") or None,
             r.get("match_rules"), r.get("match_excludes"),
             str(r.get("active", "true")).lower() not in {"false", "0", "no"}, ora],
        )
        n += 1
    return n
