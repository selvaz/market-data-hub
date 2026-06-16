# -*- coding: utf-8 -*-
"""
export_to_excel.py — builds data_master.xlsx (EDITABLE master) from the YAMLs.

It is the inverse of import_from_excel.py. It generates an Excel with ALL the
fields needed to rebuild the YAMLs without loss: the user can add rows in
Excel and then re-run the import.

Full flow:
    1. python export_to_excel.py                     # YAML -> data_master.xlsx
    2. <the user edits/adds rows in Excel>
    3. python import_from_excel.py --file data_master.xlsx --type all   # Excel -> YAML
    4. python run_daily.py --report --open           # download + report

Generated sheets:
    Tickers      (Yahoo)   — Layer taxonomy + asset_class/area/priority
    FRED                   — FRED series
    Macro_Panel  (cross-country) — ALL fields: freq, unit, orientation,
                 api_source_id, dalio_role, fallback_*, bis_dimensions, ...
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
import pandas as pd
import yaml

BASE = Path(__file__).parent
CFG = BASE / "market_data_hub" / "config"
OUT = BASE / "data_master.xlsx"
TICKERS_CSV = BASE / "tickers_master.csv"   # Layer taxonomy (read-only)


def _y(name):
    return yaml.safe_load(open(CFG / name, encoding="utf-8")) or {}


def _db_panel_info():
    """Countries covered + last date per indicator (read-only, from the DB)."""
    info = {}
    try:
        import duckdb
        con = duckdb.connect(str(BASE / "market_data.duckdb"), read_only=True)
        for r in con.execute("SELECT indicator_id, count(DISTINCT country_iso3), "
                             "max(date) FROM macro_panel GROUP BY indicator_id").fetchall():
            info[r[0]] = (r[1], str(r[2]))
        con.close()
    except Exception as e:
        print(f"(DB not accessible: {str(e)[:50]})")
    return info


def build_tickers() -> pd.DataFrame:
    tick = _y("tickers.yaml").get("yahoo", [])
    tmap = {e["symbol"]: e for e in tick}
    # Layer taxonomy from tickers_master.csv (if present)
    layers = {}
    if TICKERS_CSV.exists():
        t = pd.read_csv(TICKERS_CSV)
        for _, r in t.iterrows():
            layers[str(r["Ticker"])] = r.to_dict()
    rows = []
    for sym, e in tmap.items():
        L = layers.get(sym, {})
        rows.append({
            "Ticker": sym, "Area": e.get("area", ""),
            "Layer1_AssetClass": L.get("Layer1_AssetClass", e.get("asset_class", "")),
            "Layer1_Benchmark": L.get("Layer1_Benchmark", ""),
            "Layer2_SubAssetClass": L.get("Layer2_SubAssetClass", ""),
            "Layer3_Geographic": L.get("Layer3_Geographic", ""),
            "Layer4_Granular": L.get("Layer4_Granular", ""),
            "Priority": e.get("priority", ""),   # empty if absent (faithful round-trip)
            "Asset_Class": e.get("asset_class", ""),
            "Name": e.get("name", ""),
        })
    return pd.DataFrame(rows)


def build_fred() -> pd.DataFrame:
    fred = _y("macro_series.yaml").get("fred", [])
    return pd.DataFrame([{
        "SeriesID": e["symbol"], "Name": e.get("name", ""),
        "Asset_Class": e.get("asset_class", ""), "Area": e.get("area", ""),
        "Country": e.get("country", ""), "Priority": e.get("priority", 2),
    } for e in fred])


def build_macro_panel() -> pd.DataFrame:
    mp = _y("macro_panel.yaml").get("indicators", [])
    info = _db_panel_info()
    rows = []
    for e in mp:
        fb = e.get("fallback") or {}
        bd = e.get("bis_dimensions")
        nc, ld = info.get(e["id"], ("", ""))
        rows.append({
            "Indicator_ID": e["id"], "Name": e.get("name", ""),
            "Pillar": e.get("pillar", ""), "Priority": e.get("priority", 2),
            "Freq": e.get("freq", ""), "Unit": e.get("unit", ""),
            "Source": e.get("source", ""), "Dataset": e.get("dataset", ""),
            "Provider_Code": e.get("code", ""),
            "Orientation": e.get("orientation", 0),
            "Api_Source_Id": e.get("api_source_id", ""),
            "Dalio_Role": e.get("dalio_role", ""),
            "Fallback_Source": fb.get("source", ""),
            "Fallback_Dataset": fb.get("dataset", ""),
            "Fallback_Code": fb.get("code", ""),
            "Fallback_Api_Source_Id": fb.get("api_source_id", ""),
            "Bis_Dimensions": json.dumps(bd) if bd else "",
            "Bis_Country_Dim": e.get("bis_country_dim", ""),
            "Euro_Aggregate": e.get("euro_aggregate", ""),
            "Countries": nc, "Last_Date": ld,   # read-only (info from the DB)
        })
    return pd.DataFrame(rows)


# column widths for readability
_WIDTHS = {
    "Tickers": {"A": 12, "C": 18, "D": 14, "E": 20, "F": 18, "G": 14, "I": 16, "J": 42},
    "FRED": {"A": 20, "B": 46, "C": 14, "D": 12, "E": 10, "F": 9},
    "Macro_Panel": {"A": 26, "B": 40, "C": 14, "E": 7, "F": 14, "G": 8, "H": 9,
                    "I": 20, "L": 48, "Q": 40, "R": 16},
}


def main() -> int:
    tk, fr, mp = build_tickers(), build_fred(), build_macro_panel()
    with pd.ExcelWriter(OUT, engine="openpyxl") as w:
        tk.to_excel(w, sheet_name="Tickers", index=False)
        fr.to_excel(w, sheet_name="FRED", index=False)
        mp.to_excel(w, sheet_name="Macro_Panel", index=False)
        for sheet, widths in _WIDTHS.items():
            ws = w.sheets[sheet]
            for col, wd in widths.items():
                ws.column_dimensions[col].width = wd
    print(f"OK -> {OUT}")
    print(f"  Tickers     {len(tk)} rows ({len(tk.columns)} columns)")
    print(f"  FRED        {len(fr)} rows ({len(fr.columns)} columns)")
    print(f"  Macro_Panel {len(mp)} rows ({len(mp.columns)} columns)")
    print("\nMacro_Panel columns:", list(mp.columns))
    return 0


if __name__ == "__main__":
    sys.exit(main())
