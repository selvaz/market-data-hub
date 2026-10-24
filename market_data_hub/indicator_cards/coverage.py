"""Offline coverage of configured universes; no live database is opened."""
from __future__ import annotations

import json
from collections import defaultdict
from importlib.resources import files

from . import dataset, indicator_card, unmapped


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


def coverage_report() -> dict:
    from market_data_hub.config_loader import get_macro_series
    from market_data_hub.econ_calendar.catalog import load_catalog_rows
    from market_data_hub.macro_releases.catalogue import INDICATORS, US_MONTHLY

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
    }
    aliases = json.loads(files(__package__).joinpath("aliases_v2.1.json").read_text(encoding="utf-8"))
    return {
        "classification_version": dataset().classification_version,
        "cards": len(dataset().cards),
        "macro_releases": _coverage(catalogue),
        "tier_1_calendar": _coverage(calendar),
        "macro_series": _coverage(macro),
        "certified_families": {name: _coverage(ids) for name, ids in families.items()},
        "documented_certified_gaps": aliases["certified_gaps"],
        "ambiguities": mapping_ambiguities(),
    }


def main() -> None:
    print(json.dumps(coverage_report(), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
