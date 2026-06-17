# -*- coding: utf-8 -*-
"""
_generate_config.py — generates tickers.yaml and macro_series.yaml from the
4-layer Excel of quant_timeseries_suite, adding the VIX term-structure indices
and the extra tickers from zero_noise_pipeline. Run once (regenerable).
"""
from pathlib import Path
import pandas as pd
import yaml

EXCEL = r"D:\SALVATI_CLEAN\quant_timeseries_suite\ts1_downloader\database_4layer_FINAL.xlsx"
OUT = Path(__file__).parent

# VIX term-structure indices used by quant_vix_calibrator / zero_noise (Yahoo ^ index)
VIX_INDICES = ["^VIX", "^VIX9D", "^VIX3M", "^VIX6M", "^VVIX", "^VXN"]
# Extras from zero_noise not present in the Excel
ZERO_NOISE_EXTRA = {
    "FEZ":  ("EQUITY", "Europe", "EQUITY | Europe | SPDR EURO STOXX 50"),
    "UUP":  ("FX", "USD", "FX | USD | Invesco DB US Dollar Index Bullish Fund"),
    "VWO":  ("EQUITY", "Emerging Markets", "EQUITY | EM | Vanguard FTSE Emerging Markets"),
}
# Priority tier for coverage_score (1=high importance .. 4=low)
PRIORITY_BY_CLASS = {
    "EQUITY": 1, "FIXED_INCOME": 1, "ALTERNATIVES": 1,
    "MACRO": 2, "FX": 2, "COMMODITIES": 2, "REAL_ESTATE": 3,
}


def src_of(source: str) -> str:
    f = str(source).lower()
    if "fred" in f:
        return "FRED"
    if "ecb.europa" in f:
        return "ECB"
    if "yahoo" in f or "yfinance" in f:
        return "YAHOO"
    return "OTHER"


def main():
    df = pd.read_excel(EXCEL)
    df["SRC"] = df["Fonte"].apply(src_of)

    yahoo, fred = [], []

    for _, r in df.iterrows():
        cls = str(r["Layer1_AssetClass"]).strip()
        entry = {
            "symbol": str(r["Ticker"]).strip(),
            "asset_class": cls,
            "area": str(r["Area"]).strip(),
            "name": str(r["Serie"]).strip(),
            "priority": PRIORITY_BY_CLASS.get(cls, 3),
        }
        if r["SRC"] == "YAHOO":
            yahoo.append(entry)
        elif r["SRC"] == "FRED":
            # country: EA if the series is euro area, otherwise US
            area = entry["area"]
            entry["country"] = "EA" if area in ("EA", "ECB") else "US"
            fred.append(entry)

    have = {e["symbol"] for e in yahoo}
    for sym in VIX_INDICES:
        if sym not in have:
            yahoo.append({"symbol": sym, "asset_class": "ALTERNATIVES",
                          "area": "US", "name": f"VIX term structure {sym}",
                          "priority": 1})
    for sym, (cls, area, name) in ZERO_NOISE_EXTRA.items():
        if sym not in have:
            yahoo.append({"symbol": sym, "asset_class": cls, "area": area,
                          "name": name, "priority": PRIORITY_BY_CLASS.get(cls, 3)})

    (OUT / "tickers.yaml").write_text(
        yaml.safe_dump({"yahoo": yahoo}, sort_keys=False, allow_unicode=True),
        encoding="utf-8")
    (OUT / "macro_series.yaml").write_text(
        yaml.safe_dump({"fred": fred}, sort_keys=False, allow_unicode=True),
        encoding="utf-8")

    print(f"tickers.yaml: {len(yahoo)} Yahoo symbols")
    print(f"macro_series.yaml: {len(fred)} FRED series")


if __name__ == "__main__":
    main()
