"""Offline coverage of configured universes; no live database is opened."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from importlib.resources import files
from pathlib import Path

from . import dataset, indicator_card, unmapped
from .build import DATA_DIR, PSV_PATH, parse_ids
from .models import Evidence, IndicatorCard, Timing

DRAFT_PSV_PATH = DATA_DIR / "draft_rows_v2.2.psv"
DRAFT_TIMING_PATH = DATA_DIR / "timing_v2.2-draft.json"


def tier1_ids() -> list[str]:
    """Portable snapshot of the external tier-1 catalogue, not the code catalogue."""
    snapshot = json.loads(files(__package__).joinpath("tier1_v1.json").read_text(encoding="utf-8"))
    ids = snapshot["ids"]
    if len(ids) != 86 or len(set(ids)) != len(ids):
        raise ValueError("Tier-1 snapshot must contain 86 distinct ids")
    return ids


def load_draft_cards(
    psv_path: Path = DRAFT_PSV_PATH, timing_path: Path = DRAFT_TIMING_PATH,
) -> tuple[IndicatorCard, ...]:
    """Validate proposed cards for offline review only; never alter the shipped index."""
    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    if timing["classification_version"] != "v2.2-draft":
        raise ValueError("Expected v2.2-draft timing evidence")
    if timing["source_table_sha256"] != hashlib.sha256(psv_path.read_bytes()).hexdigest():
        raise ValueError("Draft timing belongs to a different PSV")
    with PSV_PATH.open(encoding="utf-8-sig", newline="") as f:
        header = next(csv.reader(f, delimiter="|"))
    with psv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter="|")
        if reader.fieldnames != header:
            raise ValueError("Draft rows must have the same 19 columns as the approved table")
        rows = list(reader)
    keys = [row["key"] for row in rows]
    if len(set(keys)) != len(keys) or set(timing["rows"]) != set(keys):
        raise ValueError("Draft timing must cover exactly the distinct draft keys")
    cards = []
    seen = set()
    approved_themes = {card.theme for card in dataset().cards}
    for row in rows:
        if None in row or any(value is None for value in row.values()):
            raise ValueError("Draft row must contain exactly 19 values")
        if row["flag"] != "draft v2.2, pending owner approval":
            raise ValueError("Draft row must retain its pending-approval flag")
        if (row["timing_class"], row["timing_basis"], row["vkeys"], row["class_pair"]) != (
            "UNKNOWN", "—", "", "",
        ) or timing["rows"][row["key"]] != {
            "descriptive_peak": "not estimated", "predictive_screen": "not tested",
        }:
            raise ValueError("Unvalidated draft timing must remain unknown and untested")
        if row["theme"] not in approved_themes or row["nature"] not in {"HARD", "SOFT"}:
            raise ValueError("Draft theme and nature must use the approved vocabulary")
        ids = parse_ids(row["ids"])
        # Proposed ids are context ids only; do not invent store ingestion ids.
        if not ids.cat or any(getattr(ids, store) for store in (
            "ms", "es", "cal", "pan", "hub", "derived_ms", "notes",
        )):
            raise ValueError("Draft ids must be explicit catalogue context ids")
        identities = [row["key"], *ids.cat, *(f"cat:{value}" for value in ids.cat)]
        if any("*" in identity or identity in seen or indicator_card(identity) is not None
               for identity in identities):
            raise ValueError("Draft identity duplicates or overlaps an approved card")
        seen.update(identities)
        cards.append(IndicatorCard(
            key=row["key"], concept=row["concept"], country=row["country"], ids=ids,
            raw_ids=row["ids"], theme=row["theme"],
            secondary=tuple(s.strip() for s in row["secondary"].split(";") if s.strip()),
            nature=row["nature"],
            timing=Timing(
                timing_class=row["timing_class"], basis=None, reference=row["reference"],
                class_pair=None, relationship_keys=(), predictive_screen=(),
                screen_note="not tested", descriptive_peak=None,
            ),
            evidence=Evidence(measurement=row["ev_meas"], mechanism=row["ev_mech"], local_timing=row["ev_timing"]),
            revision_behaviour="Not specified in draft row.",
            known_failure_modes=(row["flag"],), flag=row["flag"], rationale=row["rationale"],
            source=row["source"], frequency=row["freq"],
        ))
    return tuple(cards)


def tier1_coverage(with_drafts: bool = False) -> dict:
    """Resolve actual cards, keeping approved and proposed coverage explicit."""
    drafts = {}
    if with_drafts:
        for card in load_draft_cards():
            for identity in (card.key, *card.ids.cat, *(f"cat:{value}" for value in card.ids.cat)):
                drafts[identity] = card
    resolutions = {}
    missing = []
    for identity in tier1_ids():
        card = indicator_card(identity)
        status = "approved"
        if card is None:
            card = drafts.get(identity)
            status = "draft"
        if card is None:
            missing.append(identity)
        else:
            resolutions[identity] = {"status": status, "key": card.key}
    return {
        "mapped": len(resolutions), "total": len(tier1_ids()), "unmapped": missing,
        "approved": sum(r["status"] == "approved" for r in resolutions.values()),
        "draft": sum(r["status"] == "draft" for r in resolutions.values()),
        "with_drafts": with_drafts, "resolutions": resolutions,
    }


def _coverage(ids: list[str]) -> dict:
    missing = unmapped(ids)
    return {"mapped": len(ids) - len(missing), "total": len(ids), "unmapped": missing}


def mapping_ambiguities() -> dict[str, dict]:
    """Report exact and bare-id collisions, including the chosen card."""
    candidates = defaultdict(set)
    for card in dataset().cards:
        candidates[card.key].add(card.key)
        for store in ("ms", "es", "cal", "cat", "pan", "hub"):
            for value in getattr(card.ids, store):
                if "*" not in value:
                    candidates[f"{store}:{value}"].add(card.key)
                    candidates[value].add(card.key)
    return {
        identity: {"candidates": sorted(keys), "selected": indicator_card(identity).key}
        for identity, keys in sorted(candidates.items()) if len(keys) > 1
    }


def coverage_report(with_drafts: bool = False) -> dict:
    from market_data_hub.config_loader import get_macro_series
    from market_data_hub.econ_calendar.catalog import load_catalog_rows
    from market_data_hub.macro_releases.catalogue import INDICATORS, UK_FAMILIES, US_MONTHLY

    catalogue = sorted(f"cat:{key}" for key in INDICATORS)
    calendar = sorted({f"cal:{r['indicator_key']}" for r in load_catalog_rows()
                       if r["criticality"] == "T1" and r["active"]})
    macro = sorted({f"ms:{r['symbol']}" for r in get_macro_series() if r.get("enabled", True)})
    families = {
        "us_cpi": [i for i in catalogue if i.startswith("cat:us_cpi_")],
        "us_payrolls": ["cat:us_payrolls_change"],
        "us_gdp": ["cat:us_gdp_qq_saar"],
        "ea_hicp": [i for i in catalogue if "hicp" in i],
        "uk_gdp": [i for i in catalogue if i.startswith("cat:uk_gdp_")],
        # M9a-1 (24/10): the US monthly families list their own measures.
        **{family: sorted(f"cat:{m.id}" for m in measures) for family, measures in US_MONTHLY.items()},
        # M9a-2 (24/10): UK families, euro-area GDP and weekly claims.
        **{family: sorted(f"cat:{m.id}" for m in measures) for family, measures in UK_FAMILIES.items()},
        "ea_gdp": [i for i in catalogue if i.startswith("cat:ea21_gdp_")],
        "us_jobless_claims": ["cat:us_initial_jobless_claims"],
    }
    aliases = json.loads(files(__package__).joinpath("aliases_v2.1.json").read_text(encoding="utf-8"))
    return {
        "classification_version": dataset().classification_version,
        "cards": len(dataset().cards),
        "tier_1_measures": tier1_coverage(with_drafts=with_drafts),
        "macro_releases": _coverage(catalogue),
        "tier_1_calendar": _coverage(calendar),
        "macro_series": _coverage(macro),
        "certified_families": {name: _coverage(ids) for name, ids in families.items()},
        "documented_certified_gaps": aliases["certified_gaps"],
        "ambiguities": mapping_ambiguities(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-drafts", action="store_true", help="Include pending v2.2 cards in tier-1 coverage only")
    args = parser.parse_args()
    print(json.dumps(coverage_report(with_drafts=args.with_drafts), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
