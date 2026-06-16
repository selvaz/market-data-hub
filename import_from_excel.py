# -*- coding: utf-8 -*-
"""
import_from_excel.py — round-trip Excel->YAML for all market_data_hub catalogs.

Usage:
    # Import all 3 sheets from data_master.xlsx (main mode)
    python import_from_excel.py --file data_master.xlsx --type all

    # Single sheet
    python import_from_excel.py --file data_master.xlsx --type tickers
    python import_from_excel.py --file data_master.xlsx --type fred
    python import_from_excel.py --file data_master.xlsx --type macro_panel

    # Dry run (no writing)
    python import_from_excel.py --file data_master.xlsx --type all --validate-only

    # Custom sheet
    python import_from_excel.py --file data_master.xlsx --type tickers --sheet MySheet

The logic is MERGE: Excel fields overwrite the corresponding YAML fields,
all other YAML fields (fallback, api_source_id, etc.) are preserved.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

BASE_DIR = Path(__file__).parent
CONFIG_DIR = BASE_DIR / "market_data_hub" / "config"

TICKERS_YAML = CONFIG_DIR / "tickers.yaml"
FRED_YAML = CONFIG_DIR / "macro_series.yaml"
MACRO_YAML = CONFIG_DIR / "macro_panel.yaml"

# Mapping: Excel column -> YAML field for each sheet
TICKER_COL_MAP = {
    "Ticker":       "symbol",
    "Name":         "name",
    "Asset_Class":  "asset_class",   # canonical column (Layer1-4 = taxonomy only)
    "Area":         "area",
    "Priority":     "priority",
}
FRED_COL_MAP = {
    "SeriesID":    "symbol",
    "Name":        "name",
    "Asset_Class": "asset_class",
    "Area":        "area",
    "Country":     "country",
    "Priority":    "priority",
}
MACRO_COL_MAP = {
    "Indicator_ID":   "id",
    "Name":           "name",
    "Pillar":         "pillar",
    "Priority":       "priority",
    "Freq":           "freq",
    "Unit":           "unit",
    "Source":         "source",
    "Dataset":        "dataset",
    "Provider_Code":  "code",
    "Orientation":    "orientation",
    "Api_Source_Id":  "api_source_id",
    "Dalio_Role":      "dalio_role",
    "Bis_Country_Dim": "bis_country_dim",
    "Euro_Aggregate":  "euro_aggregate",
}
# integer fields of macro_panel (handled with _safe_int)
MACRO_INT_FIELDS = {"priority", "orientation", "api_source_id"}
# read-only info columns (populated from the DB, NOT re-imported into the YAML)
MACRO_READONLY = {"Countries", "Last_Date"}


# ------------------------------------------------------------------ helpers

def _load_file(filepath: str, sheet: Optional[str] = None) -> pd.DataFrame:
    p = filepath
    if p.endswith(".xlsx"):
        return pd.read_excel(p, sheet_name=sheet or 0)
    return pd.read_csv(p)


def _load_all_sheets(filepath: str) -> dict[str, pd.DataFrame]:
    """Read all sheets from Excel, return dict name->DataFrame."""
    sheets = pd.read_excel(filepath, sheet_name=None)
    return {k: v for k, v in sheets.items()}


def _read_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _write_yaml(path: Path, data: dict, dry_run: bool = False) -> None:
    if dry_run:
        return
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False,
                  sort_keys=False)


def _safe_int(val, default: int = 2) -> int:
    try:
        v = pd.to_numeric(val, errors="coerce")
        return int(v) if not pd.isna(v) else default
    except Exception:
        return default


def _clean(val) -> str:
    if pd.isna(val):
        return ""
    return str(val).strip()


def _print_section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ------------------------------------------------------------------ importers

def import_tickers(df: pd.DataFrame, validate_only: bool = False,
                   col_map: Optional[dict] = None) -> int:
    """
    Merge the Tickers sheet into tickers.yaml.
    Updated fields: symbol, name, asset_class, area, priority.
    """
    cmap = col_map or TICKER_COL_MAP

    # Key column: Ticker or symbol
    key_col = next((c for c in ["Ticker", "symbol"] if c in df.columns), None)
    if key_col is None:
        raise ValueError("Column 'Ticker' missing in the Tickers sheet")

    existing = _read_yaml(TICKERS_YAML)
    entries: list[dict] = existing.get("yahoo", [])
    by_symbol = {e["symbol"]: e for e in entries}

    # Cross-catalog guard: FRED series IDs (DGS10, CPIAUCSL, ...) are not Yahoo
    # tickers. If they leak into the Tickers sheet they would pollute the Yahoo
    # universe and be requested from Yahoo (which 404s). Skip such collisions.
    fred_ids = {e["symbol"] for e in _read_yaml(FRED_YAML).get("fred", [])}

    added, updated, skipped, errors = 0, 0, 0, []

    for idx, row in df.iterrows():
        symbol = _clean(row.get(key_col, ""))
        if not symbol:
            errors.append(f"Row {idx}: empty symbol, skipped")
            skipped += 1
            continue

        if symbol in fred_ids:
            errors.append(f"{symbol}: FRED series ID, not a Yahoo ticker — skipped")
            skipped += 1
            continue

        existing_entry = by_symbol.get(symbol, {})
        entry = dict(existing_entry)  # copy to preserve extra fields
        entry["symbol"] = symbol

        for excel_col, yaml_field in cmap.items():
            if yaml_field == "symbol":
                continue
            if excel_col in df.columns:
                raw = row.get(excel_col)
                if pd.notna(raw) and str(raw).strip():
                    if yaml_field == "priority":
                        entry[yaml_field] = _safe_int(raw)
                    else:
                        entry[yaml_field] = _clean(raw)

        is_new = symbol not in by_symbol
        by_symbol[symbol] = entry
        if is_new:
            added += 1
        else:
            updated += 1

    if not validate_only:
        ordered = list(by_symbol.values())
        _write_yaml(TICKERS_YAML, {"yahoo": ordered})

    print(f"  Ticker: {added} new, {updated} updated, {skipped} skipped")
    if errors:
        for e in errors[:10]:
            print(f"    WARN: {e}")
    return added + updated


def import_fred(df: pd.DataFrame, validate_only: bool = False,
                col_map: Optional[dict] = None) -> int:
    """
    Merge the FRED sheet into macro_series.yaml.
    Updated fields: symbol, name, asset_class, area, country, priority.
    """
    cmap = col_map or FRED_COL_MAP

    key_col = next((c for c in ["SeriesID", "symbol"] if c in df.columns), None)
    if key_col is None:
        raise ValueError("Column 'SeriesID' missing in the FRED sheet")

    existing = _read_yaml(FRED_YAML)
    entries: list[dict] = existing.get("fred", [])
    by_symbol = {e["symbol"]: e for e in entries}

    added, updated, skipped, errors = 0, 0, 0, []

    for idx, row in df.iterrows():
        symbol = _clean(row.get(key_col, ""))
        if not symbol:
            errors.append(f"Row {idx}: empty SeriesID, skipped")
            skipped += 1
            continue

        existing_entry = by_symbol.get(symbol, {})
        entry = dict(existing_entry)
        entry["symbol"] = symbol

        for excel_col, yaml_field in cmap.items():
            if yaml_field == "symbol":
                continue
            if excel_col in df.columns:
                raw = row.get(excel_col)
                if pd.notna(raw) and str(raw).strip():
                    if yaml_field == "priority":
                        entry[yaml_field] = _safe_int(raw)
                    else:
                        entry[yaml_field] = _clean(raw)

        is_new = symbol not in by_symbol
        by_symbol[symbol] = entry
        if is_new:
            added += 1
        else:
            updated += 1

    if not validate_only:
        ordered = list(by_symbol.values())
        _write_yaml(FRED_YAML, {"fred": ordered})

    print(f"  FRED: {added} new, {updated} updated, {skipped} skipped")
    if errors:
        for e in errors[:10]:
            print(f"    WARN: {e}")
    return added + updated


def import_macro_panel(df: pd.DataFrame, validate_only: bool = False,
                       col_map: Optional[dict] = None) -> int:
    """
    Merge the Macro_Panel sheet into macro_panel.yaml.
    Updated fields: id, name, pillar, priority, source, dataset, code.
    Preserves: freq, unit, orientation, fallback, api_source_id.
    """
    cmap = col_map or MACRO_COL_MAP

    key_col = next((c for c in ["Indicator_ID", "id"] if c in df.columns), None)
    if key_col is None:
        raise ValueError("Column 'Indicator_ID' missing in the Macro_Panel sheet")

    existing = _read_yaml(MACRO_YAML)
    entries: list[dict] = existing.get("indicators", [])
    by_id = {e["id"]: e for e in entries}

    added, updated, skipped, errors = 0, 0, 0, []

    for idx, row in df.iterrows():
        ind_id = _clean(row.get(key_col, ""))
        if not ind_id:
            errors.append(f"Row {idx}: empty Indicator_ID, skipped")
            skipped += 1
            continue

        existing_entry = by_id.get(ind_id, {})
        entry = dict(existing_entry)  # preserve any fields not in Excel
        entry["id"] = ind_id

        # scalar fields (column->field map)
        for excel_col, yaml_field in cmap.items():
            if yaml_field == "id":
                continue
            if excel_col in df.columns:
                raw = row.get(excel_col)
                if pd.notna(raw) and str(raw).strip():
                    if yaml_field in MACRO_INT_FIELDS:
                        entry[yaml_field] = _safe_int(raw)
                    else:
                        entry[yaml_field] = _clean(raw)

        # nested fallback from the Fallback_* columns (if present and filled)
        fb_src = _clean(row.get("Fallback_Source", ""))
        if fb_src:
            fb = {"source": fb_src,
                  "dataset": _clean(row.get("Fallback_Dataset", "")),
                  "code": _clean(row.get("Fallback_Code", ""))}
            fb_api = row.get("Fallback_Api_Source_Id")
            if pd.notna(fb_api) and str(fb_api).strip():
                fb["api_source_id"] = _safe_int(fb_api)
            entry["fallback"] = fb
        elif "Fallback_Source" in df.columns and not fb_src:
            entry.pop("fallback", None)  # emptied in Excel -> remove

        # bis_dimensions from JSON string (if present)
        bd = _clean(row.get("Bis_Dimensions", ""))
        if bd:
            try:
                import json
                entry["bis_dimensions"] = json.loads(bd)
            except Exception:
                errors.append(f"{ind_id}: invalid Bis_Dimensions JSON, ignored")

        is_new = ind_id not in by_id
        by_id[ind_id] = entry
        if is_new:
            added += 1
        else:
            updated += 1

    if not validate_only:
        ordered = list(by_id.values())
        _write_yaml(MACRO_YAML, {"indicators": ordered})

    print(f"  Macro_Panel: {added} new, {updated} updated, {skipped} skipped")
    if errors:
        for e in errors[:10]:
            print(f"    WARN: {e}")
    return added + updated


# ------------------------------------------------------------------ main

def main() -> int:
    p = argparse.ArgumentParser(
        description="Round-trip Excel->YAML for all market_data_hub catalogs"
    )
    p.add_argument("--file", required=True,
                   help="Excel file (.xlsx) — typically data_master.xlsx")
    p.add_argument("--type", required=True,
                   choices=["tickers", "fred", "macro_panel", "all"],
                   help="Sheet to import: tickers / fred / macro_panel / all")
    p.add_argument("--sheet",
                   help="Custom sheet name (only if --type != all)")
    p.add_argument("--validate-only", action="store_true",
                   help="Dry run: read and validate without writing")
    args = p.parse_args()

    if not Path(args.file).exists():
        print(f"ERROR: file not found: {args.file}")
        return 1

    if args.validate_only:
        print("[DRY RUN — no writing]")

    total = 0

    if args.type == "all":
        # Read all sheets from the master Excel
        try:
            sheets = _load_all_sheets(args.file)
        except Exception as e:
            print(f"ERROR reading Excel: {e}")
            return 1

        sheet_names = list(sheets.keys())
        print(f"Sheets found in {args.file}: {sheet_names}")

        # Tickers
        ticker_sheet = next((k for k in sheet_names
                             if k.lower() in ("tickers", "ticker")), None)
        if ticker_sheet:
            _print_section(f"Sheet: {ticker_sheet} -> tickers.yaml")
            print(f"  {len(sheets[ticker_sheet])} rows")
            try:
                total += import_tickers(sheets[ticker_sheet], args.validate_only)
            except Exception as e:
                print(f"  ERROR: {e}")
        else:
            print("WARN: no 'Tickers' sheet found")

        # FRED
        fred_sheet = next((k for k in sheet_names
                           if k.lower() in ("fred", "macro_series")), None)
        if fred_sheet:
            _print_section(f"Sheet: {fred_sheet} -> macro_series.yaml")
            print(f"  {len(sheets[fred_sheet])} rows")
            try:
                total += import_fred(sheets[fred_sheet], args.validate_only)
            except Exception as e:
                print(f"  ERROR: {e}")
        else:
            print("WARN: no 'FRED' sheet found")

        # Macro_Panel
        macro_sheet = next((k for k in sheet_names
                            if k.lower() in ("macro_panel", "macro panel",
                                             "macropanel", "wdi_weo")), None)
        if macro_sheet:
            _print_section(f"Sheet: {macro_sheet} -> macro_panel.yaml")
            print(f"  {len(sheets[macro_sheet])} rows")
            try:
                total += import_macro_panel(sheets[macro_sheet], args.validate_only)
            except Exception as e:
                print(f"  ERROR: {e}")
        else:
            print("WARN: no 'Macro_Panel' sheet found")

    else:
        # Single sheet
        try:
            df = _load_file(args.file, args.sheet)
        except Exception as e:
            print(f"ERROR reading file: {e}")
            return 1

        print(f"Loaded {len(df)} rows from {args.file}"
              + (f" [sheet: {args.sheet}]" if args.sheet else ""))

        try:
            if args.type == "tickers":
                _print_section("Tickers -> tickers.yaml")
                total = import_tickers(df, args.validate_only)
            elif args.type == "fred":
                _print_section("FRED -> macro_series.yaml")
                total = import_fred(df, args.validate_only)
            elif args.type == "macro_panel":
                _print_section("Macro_Panel -> macro_panel.yaml")
                total = import_macro_panel(df, args.validate_only)
        except Exception as e:
            print(f"ERROR: {e}")
            return 1

    action = "validated" if args.validate_only else "imported/updated"
    print(f"\nTotal: {total} records {action}")
    if not args.validate_only:
        print("YAML updated — re-run runner.py to download any new tickers/series")
    return 0


if __name__ == "__main__":
    sys.exit(main())
