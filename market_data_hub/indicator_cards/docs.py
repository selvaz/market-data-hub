"""Render Part B of ``docs/macro-indicators.md`` from the cards and the catalogues.

Everything between the generated markers is derived from package data and code:
the approved indicator cards (``dataset()``), the certified release families
(``macro_releases.catalogue.FAMILIES`` and ``read_api.FAMILY_INDICATORS``), the
FF consensus aliases, the reviewed edition calendars, the economic-calendar
catalogue and the hub macro-series configuration. Nothing is listed by hand, so
families and aliases added on other branches appear after a merge and a re-run.
Where a fact is not held anywhere in code or data, the cell says "—".
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from . import dataset, indicator_card
from .models import IndicatorCard

DOC_PATH = Path(__file__).resolve().parents[2] / "docs" / "macro-indicators.md"
BEGIN = "<!-- BEGIN GENERATED: macro-indicators -->"
END = "<!-- END GENERATED: macro-indicators -->"
CLASSES = ("LEAD", "COIN", "LAG", "CTX", "UNKNOWN")
DASH = "—"
# One of the store prefixes of the approved table, in display order.
STORES = ("ms", "cal", "es", "cat", "pan", "hub")


@dataclass(frozen=True)
class Row:
    @property
    def families(self) -> Tuple[str, ...]:
        return tuple(f for f, _ in self.by_family)

    @property
    def indicators(self) -> Tuple[str, ...]:
        return tuple(i for _, ids in self.by_family for i in ids)

    card: IndicatorCard
    by_family: Tuple[Tuple[str, Tuple[str, ...]], ...]  # certified family -> its catalogue indicator ids
    consensus: bool
    tier: Optional[str]
    release_time: str
    issuer_line: str


def _t(text: str) -> str:
    """Make a value safe inside a Markdown table cell."""
    return " ".join(str(text).split()).replace("|", "\\|")


# --------------------------------------------------------------------------- sources


def _families() -> Tuple[str, ...]:
    from market_data_hub.macro_releases.catalogue import FAMILIES
    return tuple(FAMILIES)


def _family_of_indicator() -> Dict[str, str]:
    """Catalogue indicator id -> certified family, from the read API's own map."""
    from market_data_hub.macro_releases.read_api import FAMILY_INDICATORS
    families = set(_families())
    result: Dict[str, str] = {}
    for family in sorted(FAMILY_INDICATORS):
        if family in families:
            for indicator in sorted(FAMILY_INDICATORS[family]):
                result.setdefault(indicator, family)
    return result


def _ff_alias_cards() -> set:
    """Keys of the cards that have at least one FF consensus alias."""
    from market_data_hub.macro_releases.consensus import ALIAS_FILE
    aliases = json.loads(Path(ALIAS_FILE).read_text(encoding="utf-8"))["aliases"]
    keys = set()
    for alias in aliases:
        card = indicator_card(f"cat:{alias['indicator_id']}")
        if card is not None:
            keys.add(card.key)
    return keys


def _release_time(family: str) -> str:
    """Distinct local release times of a family's reviewed editions, with the zone."""
    from market_data_hub.macro_releases.schedule import family_editions, issuer_zone
    editions = [meta for meta in family_editions(family).values() if len(meta) >= 3]
    times = sorted({meta[2] for meta in editions if meta[2]})
    if not times:
        # BOJ policy editions carry a date only: no issuer release time to show.
        return "date only" if editions else ""
    return f"{' / '.join(times)} {issuer_zone(family)}"


def _calendar_rows() -> Dict[str, dict]:
    from market_data_hub.econ_calendar.catalog import load_catalog_rows
    return {r["indicator_key"]: r for r in load_catalog_rows()}


def _macro_series_feeds() -> Dict[str, str]:
    """Hub macro-series symbol -> the feed the hub downloads it from."""
    from market_data_hub.config_loader import get_fred_series, get_nyfed_acm_series
    feeds = {r["symbol"]: "FRED (redistributor)" for r in get_fred_series()}
    feeds.update({r["symbol"]: "NY Fed ACM (issuer feed)" for r in get_nyfed_acm_series()})
    return feeds


_REDISTRIBUTORS = ("FRED", "ALFRED")
# Names that appear in a card's source string but are not publishers (a rule's author).
_NOT_AN_ISSUER = frozenset({"Sahm"})


def split_source(source: str) -> Tuple[List[str], List[str]]:
    """Split a card's source string into (issuers, redistributors).

    The source string mixes the original publisher with the redistributor that
    serves the series: ``ICE via FRED``, ``FRED / Fed Board``. FRED and ALFRED
    are only ever redistributors, never the issuer. Parts are separated by ``;``
    or ``/``; ``X via FRED`` names X as the issuer. An empty issuer list means the
    issuer cannot be determined from the string.
    """
    issuers: List[str] = []
    via: List[str] = []
    for part in (p.strip() for chunk in (source or "").split(";") for p in chunk.split("/")):
        if not part:
            continue
        name, _, tail = part.partition(" via ")
        for token in (name.strip(), tail.strip()):
            if token.upper() in _REDISTRIBUTORS:
                if token.upper() not in via:
                    via.append(token.upper())
            elif token and token == name.strip() and token not in _NOT_AN_ISSUER and token not in issuers:
                issuers.append(token)
    return issuers, via


def _fred_redistributed() -> set:
    from market_data_hub.macro_releases.fred import MAPPINGS
    return set(MAPPINGS)


def _geography_names() -> Dict[str, str]:
    from market_data_hub.config_loader import get_countries
    return {c["iso3"]: c["name"] for c in get_countries()}


# --------------------------------------------------------------------------- rows


def _expand(pattern: str, universe: Iterable[str]) -> List[str]:
    return sorted(u for u in universe if fnmatchcase(u, pattern)) if "*" in pattern else (
        [pattern] if pattern in set(universe) else [])


def build_rows() -> List[Row]:
    from market_data_hub.macro_releases.catalogue import INDICATORS
    family_of = _family_of_indicator()
    consensus = _ff_alias_cards()
    calendar = _calendar_rows()
    feeds = _macro_series_feeds()
    fred_indicators = _fred_redistributed()
    times: Dict[str, str] = {}
    rows = []
    for card in dataset().cards:
        indicators = tuple(sorted({i for value in card.ids.cat for i in _expand(value, INDICATORS)
                                   if i in family_of}))
        families = tuple(sorted({family_of[i] for i in indicators}))
        by_family = tuple((f, tuple(i for i in indicators if family_of[i] == f)) for f in families)
        for family in families:
            times.setdefault(family, _release_time(family))
        release = "; ".join(f"{times[f]} ({f})" if len(families) > 1 else times[f]
                            for f in families if times[f]) or DASH
        tiers = [INDICATORS[i].tier for i in indicators if INDICATORS[i].tier]
        cal_keys = sorted({k for value in card.ids.cal for k in _expand(value, calendar)})
        tiers += [calendar[k]["criticality"] for k in cal_keys if calendar[k].get("criticality")]
        source_issuers, source_via = split_source(card.source)
        issuer = ["issuer: " + (_t("; ".join(source_issuers)) if source_issuers else DASH)]
        certified = sorted({INDICATORS[i].issuer_id for i in indicators})
        if certified:
            issuer.append("certified issuer: " + ", ".join(certified))
        agencies = sorted({calendar[k]["agency"] for k in cal_keys if calendar[k].get("agency")})
        if agencies:
            issuer.append("calendar agency: " + ", ".join(agencies))
        via = sorted({feeds[v] for v in card.ids.ms if v in feeds})
        if set(indicators) & fred_indicators:
            via.append("FRED (redistributor)")
        via += [f"{name} (redistributor)" for name in source_via]
        if via:
            issuer.append("feed: " + ", ".join(sorted(set(via))))
        rows.append(Row(card, by_family, card.key in consensus,
                        min(tiers) if tiers else None, release, "<br>".join(issuer)))
    return rows


def _geography_key(code: str, counts: Counter) -> Tuple[int, str]:
    return (-counts[code], code)


# --------------------------------------------------------------------------- rendering


def _timing(card: IndicatorCard) -> str:
    t = card.timing
    if t.timing_class == "UNKNOWN":
        text = "not classified"
    else:
        text = t.timing_class
        if t.reference and t.reference != "none named":
            text += f" vs {t.reference}"
        if t.basis:
            text += f" [{t.basis}]"
    return f"{_t(text)}<br>{card.nature}"


def _stores(row: Row) -> str:
    ids = row.card.ids
    lines = []
    if row.families:
        lines.append("**macro_releases** " + ", ".join(
            f"`{f}` ({', '.join(f'`{i}`' for i in ids)})" for f, ids in row.by_family))
    for store in STORES:
        values = getattr(ids, store)
        if values:
            lines.append(f"{store}: " + ", ".join(f"`{_t(v)}`" for v in values))
    if ids.derived_ms:
        lines.append("derived from ms: " + ", ".join(f"`{_t(v)}`" for v in ids.derived_ms))
    lines += [f"note: {_t(n)}" for n in ids.notes]
    return "<br>".join(lines) or DASH


def _table(rows: List[Row]) -> List[str]:
    out = ["| Concept (card key) | Theme | Timing class vs reference · nature | Evidence (`ev_timing`) | "
           "Stores | Issuer / source | Freq | Release time | Official card | Consensus | Tier |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for row in sorted(rows, key=lambda r: (r.card.theme, r.card.concept, r.card.key)):
        card = row.card
        out.append("| " + " | ".join([
            f"{_t(card.concept)} (`{card.key}`)", _t(card.theme), _timing(card),
            _t(card.evidence.local_timing), _stores(row), row.issuer_line, _t(card.frequency),
            _t(row.release_time), "yes" if row.families else "no",
            "yes" if row.consensus else "no", row.tier or DASH]) + " |")
    return out


def _summary(by_geo: Dict[str, List[Row]], order: List[str], names: Dict[str, str]) -> List[str]:
    head = ["Geography", "Concepts", "Official cards", "Consensus",
            *[c if c != "UNKNOWN" else "UNKNOWN (not classified)" for c in CLASSES]]
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]

    def line(label: str, rows: List[Row]) -> str:
        counts = Counter(r.card.timing.timing_class for r in rows)
        cells = [label, len(rows), sum(1 for r in rows if r.families),
                 sum(1 for r in rows if r.consensus), *[counts[c] for c in CLASSES]]
        return "| " + " | ".join(str(c) for c in cells) + " |"

    for code in order:
        out.append(line(f"{code} ({names[code]})" if code in names else code, by_geo[code]))
    out.append(line("**All**", [r for rows in by_geo.values() for r in rows]))
    return out


def _coverage_notes(rows: List[Row]) -> List[str]:
    from market_data_hub.macro_releases.catalogue import INDICATORS
    from market_data_hub.macro_releases.read_api import FAMILY_INDICATORS
    carded = {i for r in rows for i in r.indicators}
    families = _families()
    no_map = [f for f in families if f not in FAMILY_INDICATORS]
    no_card = [f for f in families if f in FAMILY_INDICATORS and not any(f in r.families for r in rows)]
    loose = sorted((set(_family_of_indicator()) & set(INDICATORS)) - carded)
    calendar = _calendar_rows()
    class_of: Dict[str, str] = {}
    for r in rows:
        for value in r.card.ids.cal:
            for key in _expand(value, calendar):
                class_of.setdefault(key, r.card.timing.timing_class)
    tiers = Counter(c.get("criticality") or DASH for c in calendar.values())
    low = [k for k, c in calendar.items() if c.get("criticality") in ("T2", "T3")]
    low_mapped = Counter(class_of[k] for k in low if k in class_of)
    return [
        f"- Economic-calendar catalogue (`config/econ_calendar.yaml`): {len(calendar)} entries "
        f"({', '.join(f'{t} {n}' for t, n in sorted(tiers.items()))}). "
        f"{len(low)} are tier 2 or 3: {sum(low_mapped.values())} of them map to an approved card and carry "
        f"that card's class ({', '.join(f'{low_mapped[c]} {c}' for c in CLASSES if low_mapped[c]) or 'none'}); "
        f"{len(low) - sum(low_mapped.values())} map to no card and are not classified.",
        f"- Certified families in `macro_releases.catalogue.FAMILIES`: {len(families)} "
        f"({', '.join(f'`{f}`' for f in families)}).",
        "- Families with no indicator card: " + (", ".join(f"`{f}`" for f in no_card) or "none") + ".",
        "- Families the read API has no indicator map for: " + (", ".join(f"`{f}`" for f in no_map) or "none") + ".",
        "- Catalogue indicators with no card alias: " + (", ".join(f"`{i}`" for i in loose) or "none") + ".",
    ]


LEGEND = """\
How to read the tables:

- **Timing class vs reference** is the owner-approved class (`LEAD`, `COIN`, `LAG`, `CTX`) relative to the
  named reference, with its basis in brackets (`DEFINITION`, `HYPOTHESIS`, `DESCRIPTIVE`) when the card has
  one; `not classified` is `UNKNOWN`. Nature is `HARD`, `SOFT`, `POLICY`, `MARKET` or `MARKET-DERIVED`.
- **Evidence** is the card's local-timing label (`ev_timing`); `none` means no timing evidence is recorded.
- **Stores**: `macro_releases` is the certified official family, built from issuer editions, listing the
  catalogue indicator ids that map to the card. `ms:` is the hub `macro_series` table, `es:` the
  `econ_state.db` store named by the approved table (it is not part of this repository), `cal:` an economic-
  calendar indicator key (`calendar_indicators.indicator_key`; the tables `calendar_indicators`,
  `calendar_events` and `calendar_observations` live in the configured hub DuckDB database and are written by
  `run_econ_calendar.py` from the catalogue `config/econ_calendar.yaml`), `pan:` the cross-country
  `macro_panel`, `hub:` another hub table and `cat:` the planned catalogue (not downloaded). Ids are shown as
  recorded on the card; this page does not verify that each id is present in its store (see the coverage
  report). Some `cal:` ids come from the approved table and are not keys of the catalogue above. Notes copy
  the card's own gap annotations verbatim.
- **Issuer / source**: `issuer` is the original publisher named in the card's source column, with any
  redistributor removed; `—` when the source column does not name one. `certified issuer` is the issuer of the
  official family. `calendar agency` is the agency named by the calendar catalogue. `feed` is where the hub
  downloads from, or a redistributor named in the card's source column: FRED and ALFRED are redistributors,
  never the issuer.
- **Freq**: D daily, W weekly, 2W fortnightly, M monthly, Q quarterly, A annual, E scheduled events (policy
  meetings), as recorded on the card.
- **Release time**: local issuer time and zone from the reviewed edition calendars of the official family;
  `—` where no edition calendar holds it.
- **Official card**: the card maps to a family in `FAMILIES`. **Consensus**: an FF alias exists for it.
- **Tier**: the best (T1 highest) tier among the card's catalogue and calendar entries found in the repository
  catalogues; `—` when none is found.
"""


def render() -> str:
    rows = build_rows()
    by_geo: Dict[str, List[Row]] = defaultdict(list)
    for row in rows:
        by_geo[row.card.country].append(row)
    counts = Counter({code: len(items) for code, items in by_geo.items()})
    order = sorted(by_geo, key=lambda c: _geography_key(c, counts))
    names = _geography_names()
    out = [BEGIN, "",
           "<!-- Generated by `python -m market_data_hub.indicator_cards.docs`; do not edit by hand. -->", "",
           "### Summary by geography", "", *_summary(by_geo, order, names), "",
           "### Coverage notes", "", *_coverage_notes(rows), "", LEGEND]
    for code in order:
        title = f"{code} ({names[code]})" if code in names else code
        out += [f"### {title}", "", *_table(by_geo[code]), ""]
    out.append(END)
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- file handling


def split_document(text: str) -> Tuple[str, str, str]:
    start, end = text.find(BEGIN), text.find(END)
    if start < 0 or end < start:
        raise SystemExit(f"Generated markers missing in {DOC_PATH}")
    end += len(END)
    if text[end:end + 1] == "\n":
        end += 1
    return text[:start], text[start:end], text[end:]


def updated_document(text: str) -> str:
    before, _, after = split_document(text)
    return before + render() + after


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check freshness without writing")
    parser.add_argument("--path", type=Path, default=DOC_PATH)
    args = parser.parse_args()
    current = args.path.read_text(encoding="utf-8")
    result = updated_document(current)
    if args.check:
        if current != result:
            raise SystemExit("docs/macro-indicators.md Part B is stale; run python -m market_data_hub.indicator_cards.docs")
        print("docs/macro-indicators.md Part B is up to date")
    elif current != result:
        with args.path.open("w", encoding="utf-8", newline="\n") as f:
            f.write(result)


if __name__ == "__main__":
    main()
