# -*- coding: utf-8 -*-
"""
validate_config.py — cross-catalog consistency checks for the YAML configs.

Catches the classes of corruption that the Excel round-trip can introduce
(FRED IDs leaking into the Yahoo list, missing asset_class, duplicate ids,
...). Returns a non-zero exit code on any error, so it can gate CI / a
pre-run check.

The Dalio-specific checks (dalio/dalio_v2 threshold and weight-key
validation) moved to the LazyRay repo's own validate_config.py along with
the settings.yaml blocks they validate.

    python validate_config.py
"""
from __future__ import annotations

import sys
import re
from datetime import date
from pathlib import Path

import yaml

CFG = Path(__file__).parent / "market_data_hub" / "config"


def _y(name: str) -> dict:
    return yaml.safe_load((CFG / name).read_text(encoding="utf-8")) or {}


def validate() -> list[str]:
    """Return a list of human-readable errors (empty list == config is valid)."""
    errors: list[str] = []

    tickers = _y("tickers.yaml").get("yahoo", [])
    fred = _y("macro_series.yaml").get("fred", [])
    acm = _y("macro_series.yaml").get("nyfed_acm", [])
    panel = _y("macro_panel.yaml").get("indicators", [])
    countries = _y("countries.yaml").get("countries", [])
    sovereign = _y("sovereign_yields.yaml").get("series", [])

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
                       ("macro_series.yaml", [e.get("symbol") for e in fred + acm]),
                       ("macro_panel.yaml", [i.get("id") for i in panel]),
                       ("sovereign_yields.yaml", [e.get("symbol") for e in sovereign]),
                       ("countries.yaml", [c.get("iso3") for c in countries])):
        dups = sorted({x for x in ids if ids.count(x) > 1})
        if dups:
            errors.append(f"{label}: duplicate keys: {dups}")

    # 4. Macro_panel indicators need the fields the fetchers/upsert rely on.
    for i in panel:
        for field in ("id", "name", "pillar", "source", "freq"):
            if not i.get(field):
                errors.append(f"macro_panel.yaml: '{i.get('id', '?')}' missing '{field}'")

    # 5. Optional per-series publication lag is measured in calendar days.
    for label, entries in (("tickers.yaml", tickers), ("macro_series.yaml", fred + acm)):
        for entry in entries:
            if "max_lag_days" in entry:
                value = entry["max_lag_days"]
                if type(value) is not int or value < 0:
                    errors.append(
                        f"{label}: '{entry.get('symbol', '?')}' max_lag_days "
                        "must be a non-negative integer")

    from market_data_hub.sources.sovereign_yields import PROVIDERS
    for entry in sovereign:
        prefix = f"sovereign_yields.yaml: '{entry.get('symbol', '?')}'"
        for field, expected in (("unit", "%"), ("frequency", "D"),
                                ("max_lag_days", 3), ("lag_calendar", "business")):
            if entry.get(field) != expected or (field == "max_lag_days" and type(entry.get(field)) is not int):
                errors.append(f"{prefix} {field} must be {expected!r}")
        if not re.fullmatch(r"[A-Z]{3}", str(entry.get("country", ""))):
            errors.append(f"{prefix} country must be ISO3")
        if not re.fullmatch(r"(?:CB_(?:[A-Z]{2}_(?:\d+Y|R209|R2030|NTNF_\d{4})|EA_(?:AAA|ALL)_(?:2|10)Y)|ITA_BTP10_MOT|DEU_BUND10_FA)", str(entry.get("symbol", ""))):
            errors.append(f"{prefix} invalid symbol")
        if not entry.get("source") or not entry.get("name"):
            errors.append(f"{prefix} source and name are required")
        provider = entry.get("provider")
        if provider not in PROVIDERS:
            errors.append(f"{prefix} unknown provider {provider!r}")
        elif provider == "unavailable":
            if not entry.get("unavailable_reason"):
                errors.append(f"{prefix} unavailable_reason is required")
        else:
            if not str(entry.get("endpoint", "")).startswith("https://") or not entry.get("provider_code"):
                errors.append(f"{prefix} HTTPS endpoint and provider_code required")
        if provider == "ecb" or str(entry.get("symbol", "")).startswith("CB_EA_"):
            match = re.fullmatch(r"CB_EA_(AAA|ALL)_(2|10)Y", str(entry.get("symbol", "")))
            code = (f"B.U2.EUR.4F.G_N_{'A' if match[1] == 'AAA' else 'C'}.SV_C_YM.SR_{match[2]}Y"
                    if match else None)
            if (provider != "ecb" or entry.get("country") != "EMU" or entry.get("source") != "ECB"
                    or not code or entry.get("provider_code") != code
                    or entry.get("endpoint") != f"https://data-api.ecb.europa.eu/service/data/YC/{code}"
                    or entry.get("key_env")):
                errors.append(f"{prefix} ECB curve requires its matching YC key, endpoint, ECB publisher and EMU region")
        if provider == "tesouro":
            try:
                date.fromisoformat(entry.get("maturity", ""))
            except (TypeError, ValueError):
                errors.append(f"{prefix} maturity must be an ISO date")
            if entry.get("provider_code") != "Tesouro Prefixado com Juros Semestrais":
                errors.append(f"{prefix} provider_code must identify nominal NTN-F")
        bond_contracts = {
            "mot": ("ITA_BTP10_MOT", "ITA", "Borsa Italiana", "nearest_10y_nominal_btp",
                    "https://www.borsaitaliana.it/borsa/obbligazioni/mot/btp/lista.html"),
            "finanzagentur": ("DEU_BUND10_FA", "DEU", "Bundesrepublik Deutschland Finanzagentur",
                            "nearest_10y_nominal_bund",
                            "https://www.deutsche-finanzagentur.de/fileadmin/user_upload/Institutionelle-investoren/bundesbank/Rendite_engl.pdf"),
        }
        bond_provider = next((p for p, c in bond_contracts.items() if c[0] == entry.get("symbol")), None)
        if provider in bond_contracts or bond_provider:
            contract = bond_contracts.get(provider)
            actual = tuple(entry.get(k) for k in ("symbol", "country", "source", "provider_code", "endpoint"))
            if (actual != contract or entry.get("key_env")
                    or entry.get("selection_rule") != "nearest_10y_residual_then_earlier_maturity_then_isin"
                    or entry.get("history_mode") != "current_only_daily_accumulation"):
                errors.append(f"{prefix} bond snapshot requires its venue/issuer identity, endpoint and roll/history rules")
        if entry.get("key_env") and not re.fullmatch(r"[A-Z][A-Z0-9_]+", entry["key_env"]):
            errors.append(f"{prefix} key_env must name an environment variable")
        try:
            for holiday in entry.get("holidays", ()):
                date.fromisoformat(str(holiday))
        except (TypeError, ValueError):
            errors.append(f"{prefix} holidays must be ISO dates")
    for entry in acm:
        for field in ("symbol", "name", "unit", "country", "asset_class"):
            if not entry.get(field):
                errors.append(f"macro_series.yaml: NY Fed ACM missing '{field}'")
        if entry.get("frequency") != "M" or entry.get("source") != "nyfed_acm":
            errors.append("macro_series.yaml: NY Fed ACM chart CSV must have frequency M and source nyfed_acm")

    return errors


def main() -> int:
    errors = validate()
    if errors:
        print(f"CONFIG INVALID — {len(errors)} error(s):")
        for e in errors:
            print("  -", e)
        return 1
    print("CONFIG OK — tickers={} fred={} panel={} countries={} nyfed_acm={} sovereign_yields={}".format(
        len(_y("tickers.yaml").get("yahoo", [])),
        len(_y("macro_series.yaml").get("fred", [])),
        len(_y("macro_panel.yaml").get("indicators", [])),
        len(_y("countries.yaml").get("countries", [])),
        len(_y("macro_series.yaml").get("nyfed_acm", [])),
        len(_y("sovereign_yields.yaml").get("series", [])),
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
