# -*- coding: utf-8 -*-
"""
_generate_macro_panel.py — generates countries.yaml and macro_panel.yaml.

countries.yaml : extended country map (~65 countries) with iso3/iso2/name/wb/imf.
macro_panel.yaml : cross-country indicator catalog (WDI/WEO/WGI/IDS) ported
                   from macro_dashboard_v2_bundle, with metadata (pillar, orientation,
                   priority, dataset, code, freq, source, api_source_id, fallback).

Run once (regenerable). The codes are then VALIDATED live by
validate_macro_panel.py before use in production.
"""
from pathlib import Path
import yaml

OUT = Path(__file__).parent

# (iso3, iso2, name) — wb and imf coincide with iso3 for all these countries.
COUNTRIES = [
    # G7
    ("USA","US","United States"),("JPN","JP","Japan"),("DEU","DE","Germany"),
    ("GBR","GB","United Kingdom"),("FRA","FR","France"),("ITA","IT","Italy"),
    ("CAN","CA","Canada"),
    # Other G20
    ("CHN","CN","China"),("IND","IN","India"),("BRA","BR","Brazil"),
    ("RUS","RU","Russia"),("KOR","KR","Korea, Rep."),("AUS","AU","Australia"),
    ("MEX","MX","Mexico"),("IDN","ID","Indonesia"),("SAU","SA","Saudi Arabia"),
    ("TUR","TR","Turkiye"),("ARG","AR","Argentina"),("ZAF","ZA","South Africa"),
    # Other developed
    ("CHE","CH","Switzerland"),("NLD","NL","Netherlands"),("SWE","SE","Sweden"),
    ("NOR","NO","Norway"),("DNK","DK","Denmark"),("FIN","FI","Finland"),
    ("BEL","BE","Belgium"),("AUT","AT","Austria"),("IRL","IE","Ireland"),
    ("PRT","PT","Portugal"),("ESP","ES","Spain"),("GRC","GR","Greece"),
    ("NZL","NZ","New Zealand"),("ISR","IL","Israel"),("SGP","SG","Singapore"),
    ("HKG","HK","Hong Kong SAR"),("LUX","LU","Luxembourg"),
    # EU rest
    ("POL","PL","Poland"),("CZE","CZ","Czechia"),("HUN","HU","Hungary"),
    ("ROU","RO","Romania"),("BGR","BG","Bulgaria"),("HRV","HR","Croatia"),
    ("SVK","SK","Slovak Republic"),("SVN","SI","Slovenia"),("LTU","LT","Lithuania"),
    ("LVA","LV","Latvia"),("EST","EE","Estonia"),("CYP","CY","Cyprus"),
    ("MLT","MT","Malta"),
    # Major EM Asia
    ("THA","TH","Thailand"),("MYS","MY","Malaysia"),("PHL","PH","Philippines"),
    ("VNM","VN","Vietnam"),("PAK","PK","Pakistan"),("BGD","BD","Bangladesh"),
    # Major EM other
    ("EGY","EG","Egypt"),("NGA","NG","Nigeria"),("COL","CO","Colombia"),
    ("CHL","CL","Chile"),("PER","PE","Peru"),("UKR","UA","Ukraine"),
    ("ARE","AE","United Arab Emirates"),("QAT","QA","Qatar"),("KWT","KW","Kuwait"),
]

# Catalogo indicatori. WB = World Bank REST, IMF = IMF DataMapper.
# api_source_id: WB database id (2=WDI, 3=WGI). orientation: +1 healthier / -1 worse / 0.
SPECS = [
    # GROWTH / PRODUCTIVITY (WDI)
    dict(id="real_gdp_growth", name="Real GDP growth", pillar="growth", priority=1,
         freq="A", unit="percent", source="WB", dataset="WDI", code="NY.GDP.MKTP.KD.ZG", orientation=1, api_source_id=2),
    dict(id="gdp_current_usd", name="GDP current USD", pillar="growth", priority=1,
         freq="A", unit="usd", source="WB", dataset="WDI", code="NY.GDP.MKTP.CD", orientation=0, api_source_id=2),
    dict(id="gdp_per_capita_growth", name="GDP per capita growth", pillar="growth", priority=1,
         freq="A", unit="percent", source="WB", dataset="WDI", code="NY.GDP.PCAP.KD.ZG", orientation=1, api_source_id=2),
    dict(id="unemployment_rate", name="Unemployment (ILO modeled)", pillar="growth", priority=1,
         freq="A", unit="percent", source="WB", dataset="WDI", code="SL.UEM.TOTL.ZS", orientation=-1, api_source_id=2),
    dict(id="investment_gdp", name="Gross capital formation %GDP", pillar="growth", priority=1,
         freq="A", unit="percent", source="WB", dataset="WDI", code="NE.GDI.TOTL.ZS", orientation=1, api_source_id=2),
    dict(id="gross_savings_gdp", name="Gross savings %GDP", pillar="growth", priority=1,
         freq="A", unit="percent", source="WB", dataset="WDI", code="NY.GNS.ICTR.ZS", orientation=1, api_source_id=2),
    dict(id="labor_productivity_level", name="GDP per person employed (const 2021 PPP $)", pillar="growth", priority=2,
         freq="A", unit="usd", source="WB", dataset="WDI", code="SL.GDP.PCAP.EM.KD", orientation=1, api_source_id=2),
    dict(id="high_tech_exports_share", name="High-tech exports %manufactured", pillar="growth", priority=2,
         freq="A", unit="percent", source="WB", dataset="WDI", code="TX.VAL.TECH.MF.ZS", orientation=1, api_source_id=2),
    dict(id="rnd_expenditure_gdp", name="R&D expenditure %GDP", pillar="growth", priority=2,
         freq="A", unit="percent", source="WB", dataset="WDI", code="GB.XPD.RSDV.GD.ZS", orientation=1, api_source_id=2),
    dict(id="population_growth", name="Population growth", pillar="growth", priority=3,
         freq="A", unit="percent", source="WB", dataset="WDI", code="SP.POP.GROW", orientation=0, api_source_id=2),
    dict(id="dependency_ratio", name="Age dependency ratio", pillar="growth", priority=3,
         freq="A", unit="percent", source="WB", dataset="WDI", code="SP.POP.DPND", orientation=-1, api_source_id=2),
    # LIQUIDITY / MONETARY (WDI)
    dict(id="inflation_cpi", name="CPI inflation", pillar="liquidity", priority=1,
         freq="A", unit="percent", source="WB", dataset="WDI", code="FP.CPI.TOTL.ZG", orientation=-1, api_source_id=2),
    dict(id="real_interest_rate", name="Real interest rate", pillar="liquidity", priority=1,
         freq="A", unit="percent", source="WB", dataset="WDI", code="FR.INR.RINR", orientation=0, api_source_id=2),
    dict(id="lending_interest_rate", name="Lending interest rate", pillar="liquidity", priority=2,
         freq="A", unit="percent", source="WB", dataset="WDI", code="FR.INR.LEND", orientation=-1, api_source_id=2),
    dict(id="broad_money_gdp", name="Broad money %GDP", pillar="liquidity", priority=2,
         freq="A", unit="percent", source="WB", dataset="WDI", code="FM.LBL.BMNY.GD.ZS", orientation=0, api_source_id=2),
    # EXTERNAL (IMF WEO + WDI)
    dict(id="current_account_gdp", name="Current account balance %GDP", pillar="external", priority=1,
         freq="A", unit="percent", source="IMF", dataset="WEO", code="BCA_NGDPD", orientation=1,
         fallback=dict(source="WB", dataset="WDI", code="BN.CAB.XOKA.GD.ZS", api_source_id=2)),
    dict(id="fx_reserves_usd", name="Total reserves incl. gold (USD)", pillar="external", priority=1,
         freq="A", unit="usd", source="WB", dataset="WDI", code="FI.RES.TOTL.CD", orientation=1, api_source_id=2),
    dict(id="fx_reserves_months_imports", name="Reserves in months of imports", pillar="external", priority=1,
         freq="A", unit="months", source="WB", dataset="WDI", code="FI.RES.TOTL.MO", orientation=1, api_source_id=2),
    dict(id="official_fx_rate", name="Official exchange rate (LCU/USD)", pillar="external", priority=1,
         freq="A", unit="lcu_per_usd", source="WB", dataset="WDI", code="PA.NUS.FCRF", orientation=0, api_source_id=2),
    dict(id="exports_gdp", name="Exports %GDP", pillar="external", priority=1,
         freq="A", unit="percent", source="WB", dataset="WDI", code="NE.EXP.GNFS.ZS", orientation=1, api_source_id=2),
    dict(id="imports_gdp", name="Imports %GDP", pillar="external", priority=1,
         freq="A", unit="percent", source="WB", dataset="WDI", code="NE.IMP.GNFS.ZS", orientation=0, api_source_id=2),
    dict(id="fdi_inflows_gdp", name="FDI net inflows %GDP", pillar="external", priority=2,
         freq="A", unit="percent", source="WB", dataset="WDI", code="BX.KLT.DINV.WD.GD.ZS", orientation=1, api_source_id=2),
    # DEBT CYCLE (WDI)
    dict(id="private_credit_gdp", name="Domestic credit to private sector %GDP", pillar="debt_cycle", priority=1,
         freq="A", unit="percent", source="WB", dataset="WDI", code="FS.AST.PRVT.GD.ZS", orientation=0, api_source_id=2),
    # SOVEREIGN / FISCAL (IMF WEO primary, World Bank fallback)
    # IMF = "general government" (comparative standard); WB = "central government"
    # (thinner coverage) used only if IMF is unreachable/blocked.
    dict(id="public_debt_gdp", name="Govt gross debt %GDP", pillar="sovereign", priority=1,
         freq="A", unit="percent", source="IMF", dataset="WEO", code="GGXWDG_NGDP", orientation=-1,
         fallback=dict(source="WB", dataset="WDI", code="GC.DOD.TOTL.GD.ZS", api_source_id=2)),
    dict(id="fiscal_balance_gdp", name="Govt net lending/borrowing %GDP", pillar="sovereign", priority=1,
         freq="A", unit="percent", source="IMF", dataset="WEO", code="GGXCNL_NGDP", orientation=1,
         fallback=dict(source="WB", dataset="WDI", code="GC.NLD.TOTL.GD.ZS", api_source_id=2)),
    dict(id="primary_balance_gdp", name="Govt primary balance %GDP", pillar="sovereign", priority=2,
         freq="A", unit="percent", source="IMF", dataset="WEO", code="GGXONLB_NGDP", orientation=1),
    dict(id="government_revenue_gdp", name="Govt revenue %GDP", pillar="sovereign", priority=2,
         freq="A", unit="percent", source="IMF", dataset="WEO", code="GGR_NGDP", orientation=0,
         fallback=dict(source="WB", dataset="WDI", code="GC.REV.XGRT.GD.ZS", api_source_id=2)),
    dict(id="government_expenditure_gdp", name="Govt expenditure %GDP", pillar="sovereign", priority=2,
         freq="A", unit="percent", source="IMF", dataset="WEO", code="GGX_NGDP", orientation=0,
         fallback=dict(source="WB", dataset="WDI", code="GC.XPN.TOTL.GD.ZS", api_source_id=2)),
    # BANKING (WDI)
    dict(id="npl_ratio", name="Bank NPLs %gross loans", pillar="banking", priority=1,
         freq="A", unit="percent", source="WB", dataset="WDI", code="FB.AST.NPER.ZS", orientation=-1, api_source_id=2),
    dict(id="bank_capital_ratio", name="Bank capital to assets ratio", pillar="banking", priority=2,
         freq="A", unit="percent", source="WB", dataset="WDI", code="FB.BNK.CAPA.ZS", orientation=1, api_source_id=2),
    # GOVERNANCE (WGI, source=3) — codes migrated to GOV_WGI_*.EST (verified live)
    dict(id="wgi_voice_accountability", name="Voice & accountability", pillar="governance", priority=1,
         freq="A", unit="index", source="WB", dataset="WGI", code="GOV_WGI_VA.EST", orientation=1, api_source_id=3),
    dict(id="wgi_political_stability", name="Political stability", pillar="governance", priority=1,
         freq="A", unit="index", source="WB", dataset="WGI", code="GOV_WGI_PV.EST", orientation=1, api_source_id=3),
    dict(id="wgi_government_effectiveness", name="Government effectiveness", pillar="governance", priority=1,
         freq="A", unit="index", source="WB", dataset="WGI", code="GOV_WGI_GE.EST", orientation=1, api_source_id=3),
    dict(id="wgi_regulatory_quality", name="Regulatory quality", pillar="governance", priority=1,
         freq="A", unit="index", source="WB", dataset="WGI", code="GOV_WGI_RQ.EST", orientation=1, api_source_id=3),
    dict(id="wgi_rule_of_law", name="Rule of law", pillar="governance", priority=1,
         freq="A", unit="index", source="WB", dataset="WGI", code="GOV_WGI_RL.EST", orientation=1, api_source_id=3),
    dict(id="wgi_control_corruption", name="Control of corruption", pillar="governance", priority=1,
         freq="A", unit="index", source="WB", dataset="WGI", code="GOV_WGI_CC.EST", orientation=1, api_source_id=3),
    # GEOPOLITICAL / RESOURCES (WDI)
    dict(id="trade_openness", name="Trade %GDP", pillar="geopolitical", priority=1,
         freq="A", unit="percent", source="WB", dataset="WDI", code="NE.TRD.GNFS.ZS", orientation=0, api_source_id=2),
    dict(id="natural_resource_rents_gdp", name="Natural resource rents %GDP", pillar="geopolitical", priority=2,
         freq="A", unit="percent", source="WB", dataset="WDI", code="NY.GDP.TOTL.RT.ZS", orientation=0, api_source_id=2),
    dict(id="military_expenditure_gdp", name="Military expenditure %GDP", pillar="geopolitical", priority=3,
         freq="A", unit="percent", source="WB", dataset="WDI", code="MS.MIL.XPND.GD.ZS", orientation=0, api_source_id=2),
]


def main():
    countries = [{"iso3": a, "iso2": b, "name": c, "wb": a, "imf": a}
                 for a, b, c in COUNTRIES]
    (OUT / "countries.yaml").write_text(
        yaml.safe_dump({"countries": countries}, sort_keys=False, allow_unicode=True),
        encoding="utf-8")
    (OUT / "macro_panel.yaml").write_text(
        yaml.safe_dump({"indicators": SPECS}, sort_keys=False, allow_unicode=True),
        encoding="utf-8")
    print(f"countries.yaml: {len(countries)} countries")
    print(f"macro_panel.yaml: {len(SPECS)} indicators")
    print("  per pillar:", end=" ")
    from collections import Counter
    print(dict(Counter(s["pillar"] for s in SPECS)))


if __name__ == "__main__":
    main()
