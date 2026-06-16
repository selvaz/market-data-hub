# -*- coding: utf-8 -*-
"""
_generate_config.py — genera tickers.yaml e macro_series.yaml dall'Excel 4-layer
di quant_timeseries_suite, aggiungendo gli indici VIX term-structure e i ticker
extra da zero_noise_pipeline. Eseguito una sola volta (rigenerabile).
"""
from pathlib import Path
import pandas as pd
import yaml

EXCEL = r"D:\SALVATI_CLEAN\quant_timeseries_suite\ts1_downloader\database_4layer_FINAL.xlsx"
OUT = Path(__file__).parent

# Indici VIX term-structure usati da quant_vix_calibrator / zero_noise (Yahoo ^ index)
VIX_INDICES = ["^VIX", "^VIX9D", "^VIX3M", "^VIX6M", "^VVIX", "^VXN"]
# Extra da zero_noise non presenti nell'Excel
ZERO_NOISE_EXTRA = {
    "FEZ":  ("EQUITY", "Europe"),
    "UUP":  ("FX", "USD"),
    "VWO":  ("EQUITY", "Emerging Markets"),
}
# Priority tier per coverage_score (1=alta importanza .. 4=bassa)
PRIORITY_BY_CLASS = {
    "EQUITY": 1, "FIXED_INCOME": 1, "ALTERNATIVES": 1,
    "MACRO": 2, "FX": 2, "COMMODITIES": 2, "REAL_ESTATE": 3,
}


def src_of(fonte: str) -> str:
    f = str(fonte).lower()
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
            # paese: EA se la serie e' area euro, altrimenti US
            area = entry["area"]
            entry["country"] = "EA" if area in ("EA", "ECB") else "US"
            fred.append(entry)

    have = {e["symbol"] for e in yahoo}
    for sym in VIX_INDICES:
        if sym not in have:
            yahoo.append({"symbol": sym, "asset_class": "ALTERNATIVES",
                          "area": "US", "name": f"VIX term structure {sym}",
                          "priority": 1})
    for sym, (cls, area) in ZERO_NOISE_EXTRA.items():
        if sym not in have:
            yahoo.append({"symbol": sym, "asset_class": cls, "area": area,
                          "name": sym, "priority": PRIORITY_BY_CLASS.get(cls, 3)})

    (OUT / "tickers.yaml").write_text(
        yaml.safe_dump({"yahoo": yahoo}, sort_keys=False, allow_unicode=True),
        encoding="utf-8")
    (OUT / "macro_series.yaml").write_text(
        yaml.safe_dump({"fred": fred}, sort_keys=False, allow_unicode=True),
        encoding="utf-8")

    print(f"tickers.yaml: {len(yahoo)} simboli Yahoo")
    print(f"macro_series.yaml: {len(fred)} serie FRED")


if __name__ == "__main__":
    main()
