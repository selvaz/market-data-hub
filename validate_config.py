# -*- coding: utf-8 -*-
"""
validate_config.py — cross-catalog consistency checks for the YAML configs.

Catches the classes of corruption that the Excel round-trip can introduce
(FRED IDs leaking into the Yahoo list, missing asset_class, duplicate ids,
missing Dalio thresholds, ...). Returns a non-zero exit code on any error, so
it can gate CI / a pre-run check.

    python validate_config.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

CFG = Path(__file__).parent / "market_data_hub" / "config"

# Dalio thresholds that dalio.classify_cycle_phase compares against directly.
# A missing key would become None and raise TypeError at runtime, so require them.
_REQUIRED_DALIO_THRESHOLDS = [
    "credit_gap_bubble", "credit_gap_late", "rate_near_zero", "weak_growth",
    "dsr_high", "dsr_peak_pct", "debt_high_level", "debt_crisis_level",
    "deficit_large", "debt_trend_high", "debt_trend_moderate",
]


def _y(name: str) -> dict:
    return yaml.safe_load((CFG / name).read_text(encoding="utf-8")) or {}


def validate() -> list[str]:
    """Return a list of human-readable errors (empty list == config is valid)."""
    errors: list[str] = []

    tickers = _y("tickers.yaml").get("yahoo", [])
    fred = _y("macro_series.yaml").get("fred", [])
    panel = _y("macro_panel.yaml").get("indicators", [])
    countries = _y("countries.yaml").get("countries", [])
    settings = _y("settings.yaml")

    fred_ids = {e["symbol"] for e in fred}
    ticker_syms = [e.get("symbol") for e in tickers]

    # 1. FRED IDs must not pollute the Yahoo ticker list.
    leaked = sorted(set(ticker_syms) & fred_ids)
    if leaked:
        errors.append(f"tickers.yaml: {len(leaked)} FRED series IDs in the Yahoo list: {leaked}")

    # 2. Every Yahoo ticker must have a symbol and a non-empty asset_class.
    for e in tickers:
        if not e.get("symbol"):
            errors.append("tickers.yaml: entry without 'symbol'")
        elif not e.get("asset_class"):
            errors.append(f"tickers.yaml: '{e['symbol']}' has no asset_class")

    # 3. No duplicate symbols / ids in any catalog.
    for label, ids in (("tickers.yaml", ticker_syms),
                       ("macro_series.yaml", [e.get("symbol") for e in fred]),
                       ("macro_panel.yaml", [i.get("id") for i in panel]),
                       ("countries.yaml", [c.get("iso3") for c in countries])):
        dups = sorted({x for x in ids if ids.count(x) > 1})
        if dups:
            errors.append(f"{label}: duplicate keys: {dups}")

    # 4. Macro_panel indicators need the fields the fetchers/upsert rely on.
    for i in panel:
        for field in ("id", "name", "pillar", "source", "freq"):
            if not i.get(field):
                errors.append(f"macro_panel.yaml: '{i.get('id', '?')}' missing '{field}'")

    # 5. Dalio thresholds referenced by the cycle classifier must all be present.
    dalio = settings.get("dalio", {})
    for key in _REQUIRED_DALIO_THRESHOLDS:
        if dalio.get(key) is None:
            errors.append(f"settings.yaml: dalio.{key} is missing")

    # 6. pillar_weights must cover every pillar used in the panel.
    weights = set((dalio.get("pillar_weights") or {}).keys())
    panel_pillars = {i.get("pillar") for i in panel if i.get("pillar")}
    missing_w = sorted(panel_pillars - weights)
    if missing_w:
        errors.append(f"settings.yaml: dalio.pillar_weights missing pillars: {missing_w}")

    return errors


def main() -> int:
    errors = validate()
    if errors:
        print(f"CONFIG INVALID — {len(errors)} error(s):")
        for e in errors:
            print("  -", e)
        return 1
    print("CONFIG OK — tickers={} fred={} panel={} countries={}".format(
        len(_y("tickers.yaml").get("yahoo", [])),
        len(_y("macro_series.yaml").get("fred", [])),
        len(_y("macro_panel.yaml").get("indicators", [])),
        len(_y("countries.yaml").get("countries", [])),
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
