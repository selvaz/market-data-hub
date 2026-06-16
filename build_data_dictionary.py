# -*- coding: utf-8 -*-
"""
build_data_dictionary.py — generates an Excel data dictionary with ALL the
system's series (Yahoo, FRED, Macro_Panel): code, name, economic meaning,
provider, category, frequency, unit, area/country, last date, priority.

Output: data_dictionary.xlsx  (sheet 'Dictionary' + 'Summary')
"""
from __future__ import annotations
import sys
from pathlib import Path
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent))
BASE = Path(__file__).parent
CFG = BASE / "market_data_hub" / "config"


def _y(name):
    return yaml.safe_load(open(CFG / name, encoding="utf-8"))


# ---- real frequency/last date from the DB (if accessible) ------------------
def db_freshness():
    out = {}
    try:
        import duckdb
        con = duckdb.connect(str(BASE / "market_data.duckdb"), read_only=True)
        for r in con.execute("SELECT symbol, source, freq_detected, last_date "
                             "FROM coverage_report").fetchall():
            out[r[0]] = (r[2], str(r[1]) if r[1] is not None else "")
            out[(r[1], r[0])] = (r[2], str(r[3]) if r[3] is not None else "")
        con.close()
    except Exception as e:
        print(f"(DB not accessible for freq/last_date: {str(e)[:50]})")
    return out


FREQ_LABEL = {"D": "Daily", "W": "Weekly", "M": "Monthly",
              "Q": "Quarterly", "A": "Annual", "UNKNOWN": "-"}

# ---- economic meaning: FRED (by symbol) ------------------------------------
FRED_MEANING = {
 "DCOILBRENTEU": "Brent crude oil spot price: global energy benchmark, driver of inflation and terms of trade.",
 "DCOILWTICO": "WTI spot price (Cushing): North American oil benchmark.",
 "VIXCLS": "VIX index: 30-day implied volatility of the S&P 500, the 'fear gauge' and a thermometer of risk sentiment.",
 "DGS10": "10Y Treasury yield: the reference risk-free rate, the anchor of global rates.",
 "DGS2": "2Y Treasury: very sensitive to expectations of Fed monetary policy.",
 "DGS30": "30Y Treasury: long-term growth and inflation expectations.",
 "DGS3MO": "3M Treasury: close to the policy rate, the short-term cost of money.",
 "T10Y2Y": "10Y-2Y spread: the slope of the curve; if negative, a classic leading signal of recession.",
 "EFFR": "Effective Federal Funds Rate: the Fed's operational policy rate.",
 "ECBDFR": "ECB deposit rate: floor of the rate corridor in the euro area.",
 "ECBMRRFR": "ECB main refinancing rate (refi): the central policy rate.",
 "ECBMLFR": "ECB marginal lending rate: cap of the rate corridor.",
 "T10YIE": "10Y breakeven inflation: market inflation expectations over 10 years.",
 "T5YIE": "5Y breakeven inflation: market inflation expectations over 5 years.",
 "CPIAUCSL": "US headline CPI: consumer inflation, a reference for the Fed.",
 "CPILFESL": "US Core CPI (ex food & energy): underlying inflation, more persistent.",
 "PCEPI": "US PCE: consumption deflator, the Fed's preferred inflation measure.",
 "PCEPILFE": "US Core PCE: key inflation measure for Fed decisions.",
 "CP0000EZ19M086NEST": "Euro-area HICP: harmonized inflation, the ECB's target.",
 "GDP": "US nominal GDP (current prices).",
 "GDPC1": "US real GDP (chained 2017$): economic growth net of inflation.",
 "INDPRO": "US industrial production: manufacturing and energy cycle.",
 "CLVMEURSCAB1GQEA19": "Euro-area real GDP (chained 2010 EUR).",
 "EUNNGDP": "Euro-area nominal GDP.",
 "UNRATE": "US unemployment rate: health of the labor market, part of the Fed's mandate.",
 "PAYEMS": "US nonfarm payrolls: a key labor-market indicator.",
 "LRHUTTTTEZM156S": "Euro-area harmonized unemployment.",
 "M2SL": "US M2 money supply: liquidity in the system, a medium-term inflationary signal.",
 "WALCL": "Fed total assets: size of the balance sheet (QE/QT), base liquidity.",
 "BAMLC0A0CM": "US Investment Grade corporate OAS: the IG credit risk premium.",
 "BAMLH0A0HYM2": "US High Yield OAS: speculative credit risk, a proxy for risk appetite.",
 "AAA": "Aaa corporate yield (Moody's): cost of the highest-quality debt.",
 "BAA": "Baa corporate yield (Moody's): cost of medium-to-low quality debt.",
 "NFCI": "Chicago Fed National Financial Conditions Index: aggregate US financial conditions.",
 "STLFSI4": "St. Louis Fed Financial Stress Index: systemic market stress.",
 "HOUST": "US housing starts: real estate sector, a leading cyclical indicator.",
 "RSAFS": "US retail sales: consumption and domestic demand.",
 "DTWEXBGS": "Trade-Weighted US Dollar Index (Broad): trade-weighted strength of the dollar.",
}

# ---- economic meaning: Macro_Panel (by id) ---------------------------------
MP_MEANING = {
 "real_gdp_growth": "Real GDP growth: pace of economic expansion, the basis for debt sustainability.",
 "gdp_current_usd": "Absolute size of the economy in USD: the country's weight and absorption capacity.",
 "gdp_per_capita_growth": "GDP per capita growth: improvement in the average standard of living.",
 "unemployment_rate": "Unemployment (ILO): underutilization of labor, social tensions and domestic demand.",
 "investment_gdp": "Gross fixed capital formation %GDP: investment, the engine of future growth.",
 "gross_savings_gdp": "Gross savings %GDP: capacity to self-finance investment.",
 "labor_productivity_level": "GDP per worker (PPP): labor productivity, structural competitiveness.",
 "high_tech_exports_share": "Hi-tech export share: sophistication and value added of the production base.",
 "rnd_expenditure_gdp": "R&D expenditure %GDP: innovative capacity and potential growth.",
 "population_growth": "Population growth: demographic dynamics, potential demand.",
 "dependency_ratio": "Dependency ratio: demographic pressure on welfare and public finances.",
 "gdp_growth_weo": "Real GDP growth (WEO, with projections): the IMF's forward-looking outlook.",
 "gdp_usd_weo": "Nominal GDP USD (WEO, with projections).",
 "gdp_per_capita_usd": "GDP per capita USD: absolute income level.",
 "gdp_ppp": "GDP at purchasing power parity: comparable real economic size.",
 "gdp_per_capita_ppp": "GDP per capita PPP: standard of living comparable across countries.",
 "gdp_ppp_world_share": "Share of world GDP (PPP): global geopolitical and economic weight.",
 "population": "Population (millions): size of the market and the labor force.",
 "current_account_usd": "Current account balance in USD: external position in absolute terms.",
 "unemployment_weo": "Unemployment (WEO, with projections): labor-market outlook.",
 "inflation_cpi": "CPI inflation: erosion of purchasing power, an anchor for expectations.",
 "real_interest_rate": "Real interest rate: the real cost of capital, monetary restrictiveness.",
 "lending_interest_rate": "Lending rate: cost of credit for firms and households.",
 "broad_money_gdp": "Broad money %GDP: financial depth and liquidity of the economy.",
 "inflation_avg_weo": "Average CPI inflation (WEO, with projections): the expected inflation regime.",
 "inflation_eop_weo": "End-of-period inflation (WEO): price pressure at year-end.",
 "current_account_gdp": "Current account balance %GDP: external financing needs; large deficits = vulnerability.",
 "fx_reserves_usd": "FX reserves (incl. gold): a buffer against external shocks and defense of the exchange rate.",
 "fx_reserves_months_imports": "Reserves in months of imports: external coverage; <3 months = IMF alert threshold.",
 "official_fx_rate": "Official exchange rate (LCU/USD): exchange-rate regime and level.",
 "exports_gdp": "Exports %GDP: openness and capacity to generate hard currency.",
 "imports_gdp": "Imports %GDP: dependence on foreign supply, foreign-currency needs.",
 "fdi_inflows_gdp": "Net FDI inflows %GDP: foreign confidence and stable non-debt financing.",
 "ppp_conversion_rate": "Implicit PPP conversion rate: divergence of domestic prices vs the US.",
 "remittances_gdp": "Remittances %GDP: stable currency flows from emigrant workers, support to external accounts.",
 "private_credit_gdp": "Private-sector credit %GDP: the credit cycle; rapid increases = bubble risk.",
 "public_debt_gdp": "Gross public debt %GDP: sovereign solvency; >90% = stress threshold.",
 "fiscal_balance_gdp": "Government budget balance %GDP: deficit = financing needs.",
 "primary_balance_gdp": "Primary balance %GDP (ex interest): fiscal effort to stabilize the debt.",
 "government_revenue_gdp": "Government revenue %GDP: the state's capacity to raise taxes.",
 "government_expenditure_gdp": "Government expenditure %GDP: size and rigidity of the state budget.",
 "total_external_debt_usd": "Total external debt (USD): overall exposure to foreign creditors.",
 "external_debt_gni": "External debt %GNI: weight of foreign debt relative to national income.",
 "ppg_external_debt_usd": "PPG external debt (USD): the share owed to official creditors (multilateral/bilateral).",
 "short_term_debt_usd": "Short-term external debt (USD): obligations maturing within the year.",
 "short_term_debt_reserves": "Short-term debt %reserves: rollover risk; >100% = vulnerability to a sudden stop.",
 "debt_service_exports": "Debt service %exports: capacity to repay debt with hard currency.",
 "interest_revenue": "Interest %revenue: the interest burden on the budget; debt affordability.",
 "tourism_receipts_usd": "Tourism receipts (USD): a source of hard currency, relevant for tourism-based economies.",
 "tourism_exports_share": "Tourism %exports: structural dependence on tourism.",
 "food_imports_share": "Food imports %merchandise imports: vulnerability to food prices.",
 "fuel_imports_share": "Fuel imports %imports: exposure to rising energy prices (importers).",
 "npl_ratio": "NPL %gross loans: quality of bank assets, systemic risk.",
 "bank_capital_ratio": "Bank capital/assets: capital strength of the banking system.",
 "wgi_voice_accountability": "Voice & accountability (WGI): civil liberties and political accountability.",
 "wgi_political_stability": "Political stability (WGI): risk of instability/violence.",
 "wgi_government_effectiveness": "Government effectiveness (WGI): quality of public administration.",
 "wgi_regulatory_quality": "Regulatory quality (WGI): the regulatory climate for the private sector.",
 "wgi_rule_of_law": "Rule of law (WGI): legal certainty and protection of contracts.",
 "wgi_control_corruption": "Control of corruption (WGI): institutional integrity.",
 "trade_openness": "Trade openness (exports+imports %GDP): integration into global trade.",
 "natural_resource_rents_gdp": "Natural resource rents %GDP: dependence on commodities.",
 "military_expenditure_gdp": "Military expenditure %GDP: budget priorities, geopolitical risk.",
 "food_exports_share": "Food exports %merchandise exports: exposure to agricultural prices (exporters).",
 "fuel_exports_share": "Fuel exports %merchandise exports: dependence on hydrocarbon revenue.",
 "metals_exports_share": "Metals/minerals exports %merchandise exports: exposure to the metals cycle.",
 "bis_dsr_private": "Private debt service ratio (BIS): share of income absorbed by debt service; peak = top/depression of the cycle (Dalio).",
 "bis_credit_gap": "Private credit-to-GDP gap (BIS, HP filter): deviation of credit from trend; >+10pp = bubble/leveraging (Dalio archetypal threshold).",
 "bis_policy_rate": "Central bank policy rate (BIS): nominal rate; ~0 = 'pushing on a string'; used for nom_growth vs nom_rate.",
}

PROVIDER = {"IMF": "IMF WEO", "WB": "World Bank", "fred": "FRED (St. Louis Fed)",
            "yahoo": "Yahoo Finance"}
ASSET_LABEL = {"EQUITY": "Equity", "FIXED_INCOME": "Fixed Income",
               "COMMODITIES": "Commodities", "FX": "FX/Currencies",
               "ALTERNATIVES": "Alternatives/Volatility", "REAL_ESTATE": "Real Estate",
               "MACRO": "Macro", "CRYPTO": "Crypto"}


def _s(v):
    """Clean string: NaN/None -> ''."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip()


def yahoo_meaning(e, layers):
    ac = e.get("asset_class", "")
    geo = _s(layers.get("Layer3_Geographic")) or _s(e.get("area"))
    sub = _s(layers.get("Layer2_SubAssetClass"))
    base = {
     "EQUITY": f"Equity exposure ({geo}{'/'+sub if sub else ''}); equity risk factor.",
     "FIXED_INCOME": f"Fixed-income exposure {sub or geo}; rate and/or credit risk.",
     "COMMODITIES": f"Commodity exposure ({sub or geo}); terms of trade and inflation.",
     "FX": f"Currency pair/index; exchange-rate risk ({geo}).",
     "ALTERNATIVES": f"Alternative/volatility instrument ({sub or geo}); hedge and risk sentiment.",
     "REAL_ESTATE": f"Real estate/REIT exposure ({geo}); sensitive to rates.",
    }
    return base.get(ac, e.get("name", ""))


def main():
    fresh = db_freshness()
    rows = []

    # --- Tickers (Yahoo) with Layer taxonomy from data_master.xlsx if present
    layers_map = {}
    dm = BASE / "data_master.xlsx"
    if dm.exists():
        try:
            tk = pd.read_excel(dm, "Tickers")
            for _, r in tk.iterrows():
                layers_map[str(r["Ticker"])] = r.to_dict()
        except Exception:
            pass

    for e in _y("tickers.yaml").get("yahoo", []):
        sym = e["symbol"]
        if sym in {x["symbol"] for x in _y("macro_series.yaml").get("fred", [])}:
            continue  # FRED codes stay in the FRED downloader
        f, ld = fresh.get(("yahoo", sym), fresh.get(sym, ("D", "")))
        rows.append({
            "System": "Yahoo", "Code": sym, "Name": e.get("name", ""),
            "Economic_Meaning": yahoo_meaning(e, layers_map.get(sym, {})),
            "Provider": "Yahoo Finance", "Category": ASSET_LABEL.get(e.get("asset_class"), e.get("asset_class", "")),
            "Frequency": FREQ_LABEL.get(f, f or "Daily"), "Unit": "price",
            "Area_Country": e.get("area", ""), "Last_Date": ld, "Priority": e.get("priority", ""),
        })

    # --- FRED
    for e in _y("macro_series.yaml").get("fred", []):
        sym = e["symbol"]
        f, ld = fresh.get(("fred", sym), fresh.get(sym, ("", "")))
        rows.append({
            "System": "FRED", "Code": sym, "Name": e.get("name", ""),
            "Economic_Meaning": FRED_MEANING.get(sym, e.get("name", "")),
            "Provider": "FRED (St. Louis Fed)", "Category": ASSET_LABEL.get(e.get("asset_class"), e.get("asset_class", "")),
            "Frequency": FREQ_LABEL.get(f, f or "-"), "Unit": "index/value",
            "Area_Country": e.get("area", ""), "Last_Date": ld, "Priority": e.get("priority", ""),
        })

    # --- Macro_Panel
    for e in _y("macro_panel.yaml").get("indicators", []):
        iid = e["id"]
        f, ld = fresh.get(("macro_panel", iid), ("A", ""))
        rows.append({
            "System": "Macro_Panel", "Code": e.get("code", ""), "Name": e.get("name", ""),
            "Economic_Meaning": MP_MEANING.get(iid, e.get("name", "")),
            "Provider": PROVIDER.get(e.get("source"), e.get("source", "")) +
                        (f" / {e.get('dataset')}" if e.get("dataset") else ""),
            "Category": f"Pillar: {e.get('pillar','')}",
            "Frequency": FREQ_LABEL.get(e.get("freq", "A"), "Annual"),
            "Unit": e.get("unit", ""), "Area_Country": "64 countries (cross-country)",
            "Last_Date": ld, "Priority": e.get("priority", ""),
        })

    df = pd.DataFrame(rows, columns=["System", "Code", "Name", "Economic_Meaning",
        "Provider", "Category", "Frequency", "Unit", "Area_Country", "Last_Date", "Priority"])

    # summary
    summ = (df.groupby("System").agg(Series=("Code", "count")).reset_index())
    summ.loc[len(summ)] = ["TOTAL", len(df)]

    out = BASE / "data_dictionary.xlsx"
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="Dictionary", index=False)
        summ.to_excel(w, sheet_name="Summary", index=False)
        # readable column widths
        ws = w.sheets["Dictionary"]
        widths = {"A": 12, "B": 22, "C": 42, "D": 70, "E": 20, "F": 22,
                  "G": 13, "H": 14, "I": 22, "J": 12, "K": 9}
        for col, wd in widths.items():
            ws.column_dimensions[col].width = wd

    print(f"OK -> {out}")
    print(summ.to_string(index=False))
    miss = df[df["Economic_Meaning"] == df["Name"]]
    if len(miss):
        print(f"\nWithout a dedicated meaning (using the name): {len(miss)}")
        print("  ", miss["Code"].tolist()[:20])


if __name__ == "__main__":
    main()
