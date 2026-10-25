"""Reviewed exact measures for the pilot and US monthly families; stages stay separate."""
from .models import Indicator


def measure(id, issuer, geo, transform, adjustment, unit="%", scale=1, definition=None, frequency=None):
    return Indicator(id=id, measure=id, issuer_id=issuer, geography=geo,
                     seasonal_adjustment=adjustment, transform=transform, unit=unit,
                     scale=scale, frequency=frequency or ("Q" if transform == "qq_saar" else "M"),
                     tier="T1", definition=definition or id, catalogue_status="supported", active=True)


INDICATORS = {
    i.id: i for i in [
        *[measure(f"us_cpi_{part}_{basis}", "BLS", "USA", basis, adjustment)
          for part in ("headline", "core") for basis, adjustment in (("mm", "SA"), ("yy", "NSA"))],
        measure("us_payrolls_change", "BLS", "USA", "change", "SA", "persons", 1000,
                "CES total nonfarm employment, monthly net change, thousands of persons"),
        measure("us_gdp_qq_saar", "BEA", "USA", "qq_saar", "SA",
                definition="NIPA T10101, line 1 A191RL, published percent change at annual rate"),
        measure("ea_hicp_yy", "Eurostat", "EA20", "yy", "NSA"),
        measure("ea_hicp_mm", "Eurostat", "EA20", "mm", "NSA"),
        measure("ea21_hicp_yy", "Eurostat", "EA21", "yy", "NSA"),
        measure("ea21_hicp_mm", "Eurostat", "EA21", "mm", "NSA"),
        measure("ea_future_hicp_yy", "Eurostat", "EA_PENDING", "yy", "NSA"),
        measure("ea_future_hicp_mm", "Eurostat", "EA_PENDING", "mm", "NSA"),
        measure("uk_gdp_mm", "ONS", "GBR", "mm", "SA"),
        measure("uk_gdp_index", "ONS", "GBR", "level", "SA", "index"),
    ]
}

BLS_SERIES = {
    "CUSR0000SA0": ("us_cpi_headline_mm", "pct_changes", "1"),
    "CUUR0000SA0": ("us_cpi_headline_yy", "pct_changes", "12"),
    "CUSR0000SA0L1E": ("us_cpi_core_mm", "pct_changes", "1"),
    "CUUR0000SA0L1E": ("us_cpi_core_yy", "pct_changes", "12"),
    "CES0000000001": ("us_payrolls_change", "net_changes", "1"),
}

# Definitions copied from catalogo-tier1.toml; additions absent there are the
# M9a-1 brief's explicit measures. The brief corrects PPI y/y to NSA (WPUFD4).
US_MONTHLY = {
    "us_ppi": [
        measure("us_ppi_mm", "BLS", "USA", "mm", "SA", definition="Producer Price Index, final demand, month-over-month percent change"),
        measure("us_ppi_yy", "BLS", "USA", "yy", "NSA", definition="Producer Price Index, final demand, 12-month percent change")],
    "us_jolts": [measure("us_jolts_openings", "BLS", "USA", "level", "SA", "persons", 1000,
                         "Job openings, total nonfarm, level, seasonally adjusted, thousands of jobs")],
    "us_pce": [
        measure("us_pce_price_index_mm", "BEA", "USA", "mm", "SA", definition="Personal Consumption Expenditures price index, month-over-month percent change"),
        measure("us_pce_price_index_yy", "BEA", "USA", "yy", "SA", definition="Personal Consumption Expenditures price index, 12-month percent change -- the Fed's preferred headline inflation gauge"),
        measure("us_core_pce_mm", "BEA", "USA", "mm", "SA", definition="PCE price index excluding food and energy, month-over-month percent change"),
        measure("us_core_pce_yy", "BEA", "USA", "yy", "SA", definition="PCE price index excluding food and energy, 12-month percent change -- the FOMC's formal 2% target gauge")],
    "us_retail_sales": [
        measure("us_retail_sales_mm", "Census", "USA", "mm", "SA", definition="Advance estimate of retail and food services sales, month-over-month percent change"),
        measure("us_retail_sales_ex_auto_mm", "Census", "USA", "mm", "SA", definition="Retail sales excluding motor vehicle and parts dealers, m/m percent change")],
    "us_housing_starts": [
        measure("us_housing_starts", "Census", "USA", "level", "SA", "units", 1000, "New privately-owned housing units started, total, SAAR"),
        measure("us_building_permits", "Census", "USA", "level", "SA", "units", 1000, "New privately-owned housing units authorized by building permits, total, SAAR")],
    "us_durable_goods": [
        measure("us_durable_goods_orders_mm", "Census", "USA", "mm", "SA", definition="Manufacturers' new orders for durable goods, m/m percent change"),
        measure("us_durable_goods_ex_transport_mm", "Census", "USA", "mm", "SA", definition="Manufacturers' new orders for durable goods excluding transportation, m/m percent change")],
    "us_industrial_production": [
        measure("us_industrial_production_mm", "FederalReserve", "USA", "mm", "SA", definition="Index of real output for manufacturing, mining, and utilities, m/m percent change"),
        measure("us_capacity_utilization", "FederalReserve", "USA", "level", "SA", definition="Output index divided by capacity index, total industry")],
    "us_trade_balance": [measure("us_trade_balance", "Census/BEA", "USA", "level", "SA", "USD", 1e9,
                                 "Balance of payments basis trade balance in goods and services")],
}
INDICATORS.update((i.id, i) for measures in US_MONTHLY.values() for i in measures)
NEW_BLS_SERIES = {
    "WPSFD4": ("us_ppi_mm", "pct_changes", "1"),
    "WPUFD4": ("us_ppi_yy", "pct_changes", "12"),
    "JTS000000000000000JOL": ("us_jolts_openings", "level", "0"),
}


def bls_family(indicator):
    if indicator in {i.id for i in US_MONTHLY["us_ppi"]}:
        return "us_ppi"
    if indicator == "us_jolts_openings":
        return "us_jolts"
    return "us_payrolls" if indicator.startswith("us_payrolls") else "us_cpi"


UK_FAMILIES = {
    "uk_cpi": [
        measure("uk_cpi_yy", "ONS", "GBR", "yy", "NSA", definition="CPI all items, published 12-month rate, D7G7"),
        measure("uk_core_cpi_yy", "ONS", "GBR", "yy", "NSA", definition="CPI excluding energy, food, alcohol and tobacco, published 12-month rate, DKO8")],
    "uk_labour_market": [
        measure("uk_unemployment_rate", "ONS", "GBR", "level", "SA", definition="ILO unemployment rate, aged 16+, three months ending reference month, MGSX"),
        measure("uk_regular_pay_yy", "ONS", "GBR", "yy", "SA", definition="Great Britain whole economy regular pay excluding arrears, nominal three-month average y/y growth, KAI9; period is ending month")],
}
INDICATORS.update((i.id, i) for measures in UK_FAMILIES.values() for i in measures)
INDICATORS.update((i.id, i) for i in [measure(f"ea21_gdp_{basis}", "Eurostat", "EA21", basis, "SCA",
    definition="EA21 real GDP, published chain-linked volume percent change, seasonal and calendar adjustment",
    frequency="Q") for basis in ("qq", "yy")])
INDICATORS["us_initial_jobless_claims"] = measure("us_initial_jobless_claims", "DOL", "USA", "level", "SA",
    "claims", 1000, "DOL ETA initial unemployment insurance claims, SA level, thousands; reference period is Saturday week ending", frequency="W")
REMAINING_FAMILIES = {
    "us_umich_sentiment": [measure("us_umich_consumer_sentiment", "UMich", "USA", "level", "NSA", "index",
        definition="Index of Consumer Sentiment, 1966 Q1=100, issuer preliminary and final readings")],
    "ea_ec_surveys": [
        measure("ea_economic_sentiment_indicator", "ECFIN", "EA21", "level", "SA", "index", definition="Economic Sentiment Indicator, issuer standardised business and consumer composite, long-term mean 100"),
        measure("ea_consumer_confidence", "ECFIN", "EA21", "level", "SA", "%", definition="Consumer confidence indicator, seasonally adjusted balance of positive and negative responses; flash and final")],
    "uk_total_pay": [measure("uk_average_weekly_earnings_yy", "ONS", "GBR", "yy", "SA",
        definition="Average weekly earnings, total pay including bonuses, three-month average y/y growth; reference month is window ending")],
    "ea_core_hicp": [measure("ea21_core_hicp_yy", "Eurostat", "EA21", "yy", "NSA",
        definition="HICP excluding energy, food, alcohol and tobacco, issuer 12-month rate, EA21")],
    "us_new_home_sales": [measure("us_new_home_sales", "Census", "USA", "level", "SA", "units", 1000,
                                  "New single-family houses sold, thousands of units, SAAR")],
    "uk_retail_sales": [measure("uk_retail_sales_mm", "ONS", "GBR", "mm", "SA",
        definition="Retail Sales Index, volume, all retailers including fuel, published monthly percent change, Great Britain")],
    "ea_trade_balance": [measure("ea_trade_balance", "Eurostat", "EA21", "level", "SA", "EUR", 1000000,
        "Balance of extra-euro area exports and imports of goods, seasonally adjusted, millions of EUR")],
    "ea_industrial_production": [measure("ea_industrial_production_mm", "Eurostat", "EA21", "mm", "SA",
        definition="Production in total industry excluding construction, published monthly percent change, EA21")],
    "ea_retail_sales": [measure("ea_retail_sales_mm", "Eurostat", "EA21", "mm", "SA",
                                definition="Deflated turnover (volume) of total retail trade, published monthly percent change, EA21")],
    "ea_unemployment": [measure("ea_unemployment_rate", "Eurostat", "EA21", "level", "SA",
                                definition="Unemployed as percent of labour force, age 15-74, EA21")],
}
for _measures in REMAINING_FAMILIES.values():
    INDICATORS.update((m.id, m) for m in _measures)
CENTRAL_BANKS = {
    "boj_policy": [measure("boj_policy_rate_decision", "BOJ", "JPN", "level", "n/a",
        definition="Short-term policy interest rate (uncollateralized overnight call rate) set by the BOJ Policy Board", frequency="E")],
    "pboc_lpr": [measure("pboc_lpr_decision", "PBOC/NIFC", "CHN", "level", "n/a",
        definition="1-year and over-5-year Loan Prime Rates published by NIFC under PBOC authorization; scalar is 1Y", frequency="E")],
    "boc_policy": [measure("boc_overnight_rate_decision", "BoC", "CAN", "level", "n/a",
        definition="Target for the overnight rate set by the Bank of Canada", frequency="E")],
    "boe_policy": [measure("boe_bank_rate_decision", "BoE", "GBR", "level", "n/a",
        definition="Bank Rate set by the Monetary Policy Committee", frequency="E")],
    "ecb_policy": [measure("ecb_deposit_facility_rate_decision", "ECB", "EMU", "level", "n/a", "% per annum",
        definition="Deposit facility rate set by the ECB Governing Council", frequency="E")],
    "fomc": [measure("fed_funds_rate_decision", "FederalReserve", "USA", "level", "n/a",
        definition="FOMC federal funds target range; scalar value is upper bound, complete range in policy_decision", frequency="E")],
}
INDICATORS.update((m.id, m) for measures in CENTRAL_BANKS.values() for m in measures)
HOUSING_FAMILIES = {
    "jp_housing_starts": [measure("jp_housing_starts_yy", "MLIT", "JPN", "yy", "NSA",
        definition="MLIT national new residential housing starts, published unadjusted annual percent change; excludes nonresidential starts and the separate SAAR monthly change")],
    "ca_housing_starts": [measure("ca_housing_starts", "CMHC", "CAN", "level", "SA", "units",
        definition="CMHC total monthly housing starts for all areas in Canada, seasonally adjusted annual rate (SAAR); dated public release original print, not the six-month trend or urban actual starts")],
    "de_building_permits": [
        measure("de_building_permits", "Destatis", "DEU", "level", "NSA", "dwellings",
            definition="Germany: residential dwellings permitted in new and existing buildings, published monthly total rounded to nearest 100; not a count of buildings or YTD"),
        measure("de_building_permits_yy", "Destatis", "DEU", "yy", "NSA",
            definition="Germany: published annual percent change in total residential dwellings permitted, including new and existing buildings; not recomputed from rounded counts")],
    "uk_mortgage_approvals": [measure("uk_mortgage_approvals", "BoE", "GBR", "level", "SA", "approvals",
        definition="BoE Money and Credit: net mortgage approvals for house purchase to individuals, seasonally adjusted count; cancellations netted, excludes remortgaging; public release headline rounded to nearest 100, distinct from unrounded LPMVTVX history")],
    "uk_hpi": [measure("uk_hpi_yy", "HMLR/ONS", "GBR", "yy", "NSA",
        definition="UK House Price Index, all residential properties purchased for market value, cash and mortgage transactions; published national annual change; provisional and subject to registration revisions")],
    "ea_house_purchase_loans": [measure("ea_loans_house_purchase_yy", "ECB", "EA_CHANGING", "yy", "SCA",
        definition="ECB BSI.M.U2.Y.U.A22T.A.I.U2.2250.Z01.A: adjusted MFI loans to euro area households and NPISH for house purchase; annual growth rate, working day and seasonally adjusted; transfers and notional cash pooling adjusted; changing euro area composition")],
    "ea_house_purchase_cost": [measure("ea_cost_borrow_house", "ECB", "EA_CHANGING", "level", "not-stated", "% per annum",
        definition="ECB MIR.M.U2.B.A2C.AM.R.A.2250.EUR.N: composite cost of borrowing for households and NPISH for house purchase, new business, AAR/NDER; volumes weighted with a moving average; euro denominated; changing euro area composition")],
    "ea_hpi": [measure("ea21_hpi_" + basis, "Eurostat", "EA21", basis, "NSA",
        definition="Eurostat EA21 House Price Index, all new and existing residential properties purchased by households; published nominal " + basis + " percent change, not seasonally adjusted",
        frequency="Q") for basis in ("qq", "yy")],
    "us_case_shiller": [measure("us_case_shiller_" + area + "_yy", "SPDJI", "USA", "yy", "NSA",
        definition="Private S&P Cotality (formerly CoreLogic) Case-Shiller " + label + " public press-release annual headline; NSA repeat-sales single-family house prices, monthly three-month moving average")
        for area, label in (("national", "U.S. National"), ("20", "20-City Composite"))],
    "us_pending_home_sales": [measure("us_pending_home_sales_mm", "NAR", "USA", "mm", "not-stated",
        definition="Private NAR public press-release national headline: pending home sales monthly percent change; contracts, not closed sales. Adjustment is not stated in the public headline; no index level or licensed table is collected")],
    "us_existing_home_sales": [
        measure("us_existing_home_sales", "NAR", "USA", "level", "SA", "units", 1000000,
                "Private NAR public press-release national headline: total existing-home sales, SA annual rate in millions"),
        measure("us_existing_home_sales_mm", "NAR", "USA", "mm", "SA",
                definition="Private NAR public press-release national headline: total existing-home sales monthly percent change")],
    "us_pmms": [measure("us_pmms_30y", "FreddieMac", "USA", "level", "NSA",
        definition="Freddie Mac PMMS U.S. weekly average 30-year fixed-rate mortgage, conventional conforming purchase loans, percent; reference period is survey week ending from the issuer calendar",
        frequency="W")],
    "us_construction_spending": [measure("us_construction_spending_mm", "Census", "USA", "mm", "SA",
        definition="Total value of construction put in place, public and private, SA annual rate; published monthly percent change")],
    "us_fhfa_hpi": [measure("us_fhfa_hpi_" + basis, "FHFA", "USA", basis, "SA",
        definition="FHFA monthly purchase-only national house price index, published " + basis + " percent change")
        for basis in ("mm", "yy")],
}
INDICATORS.update((m.id, m) for measures in HOUSING_FAMILIES.values() for m in measures)
FAMILIES = ("us_cpi", "us_payrolls", "us_gdp", "ea_hicp", "uk_gdp", *US_MONTHLY, *UK_FAMILIES, "ea_gdp", "us_jobless_claims", *REMAINING_FAMILIES, *CENTRAL_BANKS, *HOUSING_FAMILIES)


def hicp_indicator(period, basis, geography="EA"):
    """Resolve headline membership by period; preserve fixed EA20 golden identity."""
    if geography == "EA":
        if "2023-01" <= period <= "2025-12":
            geography = "EA20"
        elif period >= "2026-01":
            geography = "EA21"
        else:
            geography = "EA_PENDING"
    if geography not in ("EA20", "EA21"):
        return INDICATORS[f"ea_future_hicp_{basis}"]
    prefix = "ea" if geography == "EA20" else "ea21"
    return INDICATORS[f"{prefix}_hicp_{basis}"]
