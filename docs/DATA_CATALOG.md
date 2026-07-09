# market_data_hub — Data Catalogue

> The complete map of every series in the database: provider, group, native
> frequency, typical update lag, history depth, and which table/column holds it.
> For the engine and DB internals see [ARCHITECTURE.md](ARCHITECTURE.md); to
> *query* this catalogue programmatically (by asset class, area, sector, pillar)
> and pull analysis-ready series, see [EXTRACTION.md](EXTRACTION.md).

Totals: **111 Yahoo symbols** + **77 FRED series** + **6 crypto × 3 timeframes**.

Legend — *Stalled-after* is the freshness threshold from the coverage engine:
beyond it a series is flagged `stalled`. *Typical lag* is how old the newest
point normally is on a healthy day.

---

## 1. prices_daily — Yahoo Finance (daily OHLCV + adj_close)

- **Table:** `prices_daily` · **Frequency:** daily (trading days)
- **Fields:** open, high, low, close, **adj_close**, volume
- **Backfill start:** 2010-01-01 · **Typical lag:** 0–1 trading day ·
  **Stalled-after:** 3 days
- **Live injection:** intraday for liquid classes (EQUITY, ALTERNATIVES,
  FIXED_INCOME, COMMODITIES, REAL_ESTATE) → today's row with `is_live=TRUE`

### EQUITY (49) — `asset_class = EQUITY`
| Sub-group | Symbol | Series name |
|-----------|--------|-------------|
| Global / broad | ACWI | MSCI ACWI |
| | VEA | Vanguard FTSE Developed Markets |
| | IEMG | iShares Core MSCI Emerging Markets |
| | EMXC | iShares MSCI EM ex China |
| | VWO | Vanguard FTSE Emerging Markets |
| US core / size / style | SPY | S&P 500 (SPDR S&P 500 ETF Trust) |
| | QQQ | Nasdaq 100 (Invesco QQQ Trust) |
| | IWM | Russell 2000 (iShares Russell 2000) |
| | VUG | Vanguard Growth |
| | VTV | Vanguard Value |
| | VGT | Vanguard Information Technology |
| US sectors (SPDR) | XLV | Health Care Select Sector SPDR |
| | XLF | Financial Select Sector SPDR |
| | XLY | Consumer Discretionary Select Sector SPDR |
| | XLC | Communication Services Select Sector SPDR |
| | XLI | Industrial Select Sector SPDR |
| | XLP | Consumer Staples Select Sector SPDR |
| | XLE | Energy Select Sector SPDR |
| | XLU | Utilities Select Sector SPDR |
| | XLB | Materials Select Sector SPDR |
| Europe broad / sectors | EXSA.DE | iShares STOXX Europe 600 |
| | VGK | Vanguard FTSE Europe |
| | FEZ | SPDR EURO STOXX 50 |
| | EXV1.DE | iShares STOXX Europe 600 Banks |
| | EXV3.DE | iShares STOXX Europe 600 Technology |
| | EXV4.DE | iShares STOXX Europe 600 Health Care |
| | EXH4.DE | iShares STOXX Europe 600 Industrial Goods & Services |
| | EXH1.DE | iShares STOXX Europe 600 Oil & Gas |
| | EXH9.DE | iShares STOXX Europe 600 Utilities |
| Single-country DM | EWU | iShares MSCI United Kingdom |
| | EWG | iShares MSCI Germany |
| | EWQ | iShares MSCI France |
| | EWI | iShares MSCI Italy |
| | EWJ | iShares MSCI Japan |
| | EWC | iShares MSCI Canada |
| | EWA | iShares MSCI Australia |
| Asia / China | AAXJ | iShares MSCI All Country Asia ex Japan |
| | FXI | iShares China Large-Cap |
| | MCHI | iShares MSCI China |
| | KWEB | KraneShares CSI China Internet |
| | KBUY | KraneShares China Consumer Leaders |
| | KURE | KraneShares MSCI China Health Care |
| | CQQQ | Invesco China Technology |
| | INDA | iShares MSCI India |
| | EWY | iShares MSCI South Korea |
| | EWT | iShares MSCI Taiwan |
| LatAm | EWZ | iShares MSCI Brazil |
| | EWW | iShares MSCI Mexico |
| | ILF | iShares Latin America 40 |

### FIXED_INCOME (21) — `asset_class = FIXED_INCOME`
| Sub-group | Symbol | Series name |
|-----------|--------|-------------|
| US Treasuries (curve) | SHY | iShares 1-3 Year Treasury Bond |
| | IEI | iShares 3-7 Year Treasury Bond |
| | IEF | iShares 7-10 Year Treasury Bond |
| | TLT | iShares 20+ Year Treasury Bond |
| | BIL | SPDR Bloomberg 1-3 Month T-Bill |
| | SHV | iShares Short Treasury Bond |
| Aggregate / TIPS / global | AGG | iShares Core US Aggregate Bond |
| | TIP | iShares TIPS Bond |
| | BNDX | Vanguard Total International Bond (hedged) |
| | BWX | SPDR Bloomberg International Treasury |
| IG credit | LQD | iShares iBoxx $ Investment Grade Corporate |
| | VCSH | Vanguard Short-Term Corporate |
| High yield | HYG | iShares iBoxx $ High Yield Corporate |
| | EUHY | iShares € High Yield Corporate |
| | HYXU | iShares Global ex USD High Yield Corporate |
| | HYEM | VanEck Emerging Markets High Yield Bond |
| | HYD | VanEck High Yield Muni |
| EM debt | EMB | iShares JP Morgan USD Emerging Markets Bond |
| | EMLC | VanEck Emerging Markets Local Currency Bond |
| | CEMB | iShares Emerging Markets Corporate Bond |
| Municipals | MUB | iShares National Muni Bond |

### COMMODITIES (15) — `asset_class = COMMODITIES`
| Sub-group | Symbol | Series name |
|-----------|--------|-------------|
| Broad baskets | DBC | Invesco DB Commodity Index Tracking |
| | PDBC | Invesco Optimum Yield Diversified Commodity |
| Energy | USO | United States Oil Fund |
| | UNG | United States Natural Gas Fund |
| | UGA | United States Gasoline Fund |
| Precious metals | GLD | SPDR Gold Trust |
| | SLV | iShares Silver Trust |
| | PPLT | abrdn Physical Platinum Shares |
| | PALL | abrdn Physical Palladium Shares |
| Base metals | DBB | Invesco DB Base Metals Fund |
| | CPER | United States Copper Index Fund |
| Agriculture | DBA | Invesco DB Agriculture Fund |
| | CORN | Teucrium Corn Fund |
| | WEAT | Teucrium Wheat Fund |
| | SOYB | Teucrium Soybean Fund |

### ALTERNATIVES (14) — `asset_class = ALTERNATIVES`
| Sub-group | Symbol | Series name |
|-----------|--------|-------------|
| Crypto (Yahoo daily) | BTC-USD | Bitcoin (Yahoo USD spot) |
| | ETH-USD | Ethereum (Yahoo USD spot) |
| | SOL-USD | Solana (Yahoo USD spot) |
| | IBIT | iShares Bitcoin Trust |
| | ETHA | iShares Ethereum Trust |
| **VIX term structure** | **^VIX** | CBOE Volatility Index (30-day) |
| | **^VIX9D** | CBOE S&P 500 9-Day Volatility Index |
| | **^VIX3M** | CBOE 3-Month Volatility Index |
| | **^VIX6M** | CBOE S&P 500 6-Month Volatility Index |
| | **^VVIX** | CBOE VVIX (VIX vol-of-vol) |
| | **^VXN** | CBOE Nasdaq-100 Volatility Index |
| Vol / infra | VIXY | ProShares VIX Short-Term Futures |
| | IGF | iShares Global Infrastructure |
| | IFRA | iShares US Infrastructure |

> The 6 `^VIX*` indices feed the `v_vix_term_structure` view — the backbone for
> `quant_vix_calibrator` / `quant_vix_dashboard`.

### FX (10) — `asset_class = FX`
| Symbol | Series name |
|--------|-------------|
| EURUSD=X | EUR/USD spot |
| GBPUSD=X | GBP/USD spot |
| USDJPY=X | USD/JPY spot |
| AUDUSD=X | AUD/USD spot |
| USDCAD=X | USD/CAD spot |
| USDCHF=X | USD/CHF spot |
| EURGBP=X | EUR/GBP spot |
| EURJPY=X | EUR/JPY spot |
| EURCHF=X | EUR/CHF spot |
| UUP | Invesco DB US Dollar Index Bullish Fund |

### REAL_ESTATE (2) — `asset_class = REAL_ESTATE`
| Symbol | Series name |
|--------|-------------|
| VNQ | Vanguard Real Estate |
| VNQI | Vanguard Global ex-US Real Estate |

---

## 2. crypto_ohlcv — Binance (intraday/daily OHLCV)

- **Table:** `crypto_ohlcv` · **Backfill start:** 2018-01-01
- **Symbols (6):** BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, ADAUSDT, XRPUSDT
- **Timeframes:** `1h`, `4h`, `1d`
- **Extra fields:** volume_quote, n_trades, taker_buy_base (order-flow), is_closed
- **Typical lag:** < 1 candle · refresh re-pulls the last few candles to fix
  still-forming ones (`is_closed=FALSE`)

| timeframe | rows / year / symbol | freq_detected |
|-----------|----------------------|---------------|
| 1h | ~8,760 | (sub-daily → UNKNOWN in day-based detector) |
| 4h | ~2,190 | sub-daily |
| 1d | ~365 | D |

---

## 3. macro_series — FRED (single-value macro)

- **Table:** `macro_series` · **Backfill start:** 2000-01-01
- **Access:** official API with key (public CSV blocked by proxy — see notes)

### US rates & curve — daily (lag 1–2 d, stalled-after 3 d)
| Symbol | Series name |
|--------|-------------|
| DGS3MO | 3-Month Treasury Constant Maturity |
| DGS2 | 2-Year Treasury Constant Maturity |
| DGS10 | 10-Year Treasury Constant Maturity |
| DGS30 | 30-Year Treasury Constant Maturity |
| T10Y2Y | 10Y minus 2Y Treasury Spread |
| T10YIE | 10-Year Breakeven Inflation Rate |
| T5YIE | 5-Year Breakeven Inflation Rate |
| T5YIFR | 5Y5Y Forward Inflation Expectation |
| DFII5 | 5-Year TIPS Real Yield |
| DFII10 | 10-Year TIPS Real Yield |
| EFFR | Effective Federal Funds Rate |
| VIXCLS | CBOE Volatility Index (VIX) Close |

### US credit — daily/weekly
| Symbol | Series name | Freq |
|--------|-------------|------|
| BAMLC0A0CM | ICE BofA US Corporate Master OAS (IG) | daily |
| BAMLC0A4CBBB | ICE BofA BBB Corporate OAS | daily |
| BAMLH0A0HYM2 | ICE BofA US High Yield OAS | daily |
| BAMLH0A3HYC | ICE BofA CCC & Lower HY OAS | daily |
| AAA | Moody's Seasoned Aaa Corporate Bond Yield | daily |
| BAA | Moody's Seasoned Baa Corporate Bond Yield | daily |
| NFCI | Chicago Fed National Financial Conditions Index | weekly |
| STLFSI4 | St. Louis Fed Financial Stress Index | weekly |

### US activity & prices — monthly (lag 15–45 d, stalled-after 45 d)
| Symbol | Series name |
|--------|-------------|
| CPIAUCSL | CPI-U All items (SA) |
| CPILFESL | Core CPI (CPI ex Food & Energy, SA) |
| PCEPI | PCE Price Index |
| PCEPILFE | Core PCE Price Index |
| INDPRO | Industrial Production Index |
| UNRATE | Unemployment Rate |
| PAYEMS | Nonfarm Payrolls (Total) |
| HOUST | Housing Starts |
| RSAFS | Retail Sales (Total) |

### US money / Fed / liquidity — monthly / weekly / daily
| Symbol | Series name | Freq |
|--------|-------------|------|
| M2SL | M2 Money Stock | monthly |
| WALCL | Fed Total Assets | weekly |
| WTREGEN | Treasury General Account (TGA) | weekly |
| RRPONTSYD | Overnight Reverse Repo (RRP) | daily |

— net liquidity ≈ WALCL − WTREGEN − RRPONTSYD

### US GDP — quarterly (lag 30–90 d, stalled-after 120 d)
| Symbol | Series name |
|--------|-------------|
| GDP | GDP (current $) |
| GDPC1 | Real GDP (SAAR, chained 2017$) |

### Energy spot — daily
| Symbol | Series name |
|--------|-------------|
| DCOILBRENTEU | Brent Spot Price |
| DCOILWTICO | WTI Spot Price (Cushing, OK) |

### USD index — daily
| Symbol | Series name |
|--------|-------------|
| DTWEXBGS | Trade Weighted US Dollar Index: Broad |

### Euro-area policy rates (via FRED) — daily
| Symbol | Series name |
|--------|-------------|
| ECBDFR | ECB Deposit Facility Rate |
| ECBMRRFR | ECB Main Refinancing Operations Rate (fixed rate tenders) |
| ECBMLFR | ECB Marginal Lending Facility Rate |

### Euro-area macro (via FRED) — monthly/quarterly
| Symbol | Series name | Freq |
|--------|-------------|------|
| CP0000EZ19M086NEST | HICP All items (Index 2015=100) | monthly |
| CLVMEURSCAB1GQEA19 | Real GDP (chained 2010 EUR) | quarterly |
| EUNNGDP | GDP (EUR/ECU series, nominal) | quarterly |
| LRHUTTTTEZM156S | Harmonised Unemployment Rate (Total, monthly) | monthly |

### Cross-country 10Y government bond yields (OECD long-term rates via FRED) — monthly

32 single FRED series (one per country, like `DGS10`), id pattern
`IRLTLT01{ISO2}M156N`. Stored in `macro_series` with `country` = **ISO3** so the
`v_macro_panel_ext` view can remap them into panel shape (`indicator_id =
bond_yield_10y`) for the Dalio layer. Countries (32, fresh through 2026):
USA, JPN, DEU, GBR, FRA, ITA, CAN, KOR, AUS, MEX, ZAF, CHE, NLD, SWE, NOR, DNK,
FIN, BEL, AUT, IRL, PRT, ESP, GRC, NZL, ISR, LUX, POL, CZE, HUN, SVK, SVN, CHL.
*(RU exists on FRED but stalls in 2018 under sanctions → excluded; the ~30 other
panel countries have no OECD long-rate series and stay uncovered.)*

---

## 4. Update-lag summary by group

| Group | Frequency | Typical lag | Stalled-after |
|-------|-----------|-------------|---------------|
| Equity / ETF / FX / REIT / commodity ETFs | daily | 0–1 d | 3 d |
| VIX indices | daily | 0–1 d | 3 d |
| Crypto (Binance) | 1h/4h/1d | < 1 candle | 3 d (1d) |
| FRED rates / credit / energy / USD | daily | 1–2 d | 3 d |
| FRED financial-stress (NFCI, STLFSI4, WALCL) | weekly | 3–7 d | 10 d |
| FRED CPI / PCE / employment / production | monthly | 15–45 d | 45 d |
| FRED GDP | quarterly | 30–90 d | 120 d |

---

## 5. macro_panel — cross-country macro panel (World Bank WDI/WGI + IMF WEO)

Ported from `macro_dashboard_v2_bundle` and **integrated** into the database as
the `macro_panel` table, keyed `(date, country_iso3, indicator_id)`. A different
data shape from `macro_series` (single-country FRED): an **annual panel across
64 countries**. Every indicator code was **validated live** against the
provider APIs (see `validate_macro_panel.py` / `macro_panel_validation.csv`).

- **Table:** `macro_panel` · **Frequency:** annual (A) · **Backfill start:** 2000
- **Typical lag:** 3–18 months (WDI/WEO/WGI) · **Stalled-after:** 400 days
- **Sources:** World Bank REST (WDI db, WGI db) + IMF DataMapper (WEO + Fiscal Monitor `ie`/`rltir` + Global Debt Database `NFC_LS`) + BIS SDMX (DSR, credit-gap, policy rate, REER) + ECB Data Portal SDMX (MIR cost-of-borrowing)
- **Read API:** `reader.read_macro_panel(indicators, countries, wide=…)`
- **Dalio read layer:** the `v_macro_panel_ext` view = `macro_panel` **+** single-country
  FRED series remapped into panel shape (currently the 10Y bond yield). `dalio.py`
  reads this view so cross-country FRED inputs are visible without moving storage.
- **Extra metadata per row:** `pillar`, `orientation` (+1 healthier / −1 worse /
  0), `provider_dataset`, `provider_code`, `unit`

**Countries (64):** G7 + rest of G20 + EU27 + other developed (CHE, NOR, NZL,
ISR, SGP, HKG, …) + major EM (THA, MYS, PHL, VNM, PAK, BGD, EGY, NGA, COL, CHL,
PER, UKR, ARE, QAT, KWT, …).

**Indicator catalogue (80, by pillar):**

| Pillar | Count | Indicators (source/dataset · provider code; fb = fallback) |
|--------|-------|------------------------------------------------------------|
| growth | 18 | real_gdp_growth (WB/WDI NY.GDP.MKTP.KD.ZG), gdp_current_usd (WB/WDI NY.GDP.MKTP.CD), gdp_per_capita_growth (WB/WDI NY.GDP.PCAP.KD.ZG), unemployment_rate (WB/WDI SL.UEM.TOTL.ZS), investment_gdp (WB/WDI NE.GDI.TOTL.ZS), gross_savings_gdp (WB/WDI NY.GNS.ICTR.ZS), labor_productivity_level (WB/WDI SL.GDP.PCAP.EM.KD), high_tech_exports_share (WB/WDI TX.VAL.TECH.MF.ZS), rnd_expenditure_gdp (WB/WDI GB.XPD.RSDV.GD.ZS), population_growth (WB/WDI SP.POP.GROW), dependency_ratio (WB/WDI SP.POP.DPND), gdp_growth_weo (IMF/WEO NGDP_RPCH), gdp_usd_weo (IMF/WEO NGDPD), gdp_per_capita_usd (IMF/WEO NGDPDPC), gdp_ppp (IMF/WEO PPPGDP), gdp_per_capita_ppp (IMF/WEO PPPPC), population (IMF/WEO LP), unemployment_weo (IMF/WEO LUR) |
| liquidity | 7 | inflation_cpi (WB/WDI FP.CPI.TOTL.ZG), real_interest_rate (WB/WDI FR.INR.RINR), lending_interest_rate (WB/WDI FR.INR.LEND), broad_money_gdp (WB/WDI FM.LBL.BMNY.GD.ZS), inflation_avg_weo (IMF/WEO PCPIPCH), inflation_eop_weo (IMF/WEO PCPIEPCH), bis_policy_rate (BIS/WS_CBPOL M.{iso2}) |
| external | 16 | current_account_gdp (IMF/WEO BCA_NGDPD; fb WB/WDI BN.CAB.XOKA.GD.ZS), fx_reserves_usd (WB/WDI FI.RES.TOTL.CD), fx_reserves_months_imports (WB/WDI FI.RES.TOTL.MO), official_fx_rate (WB/WDI PA.NUS.FCRF), exports_gdp (WB/WDI NE.EXP.GNFS.ZS), imports_gdp (WB/WDI NE.IMP.GNFS.ZS), fdi_inflows_gdp (WB/WDI BX.KLT.DINV.WD.GD.ZS), ppp_conversion_rate (IMF/WEO PPPEX), current_account_usd (IMF/WEO BCA), remittances_gdp (WB/WDI BX.TRF.PWKR.DT.GD.ZS), short_term_debt_usd (WB/WDI DT.DOD.DSTC.CD), short_term_debt_reserves (WB/WDI DT.DOD.DSTC.IR.ZS), tourism_receipts_usd (WB/WDI ST.INT.RCPT.CD), tourism_exports_share (WB/WDI ST.INT.RCPT.XP.ZS), food_imports_share (WB/WDI TM.VAL.FOOD.ZS.UN), fuel_imports_share (WB/WDI TM.VAL.FUEL.ZS.UN) |
| debt_cycle | 3 | private_credit_gdp (WB/WDI FS.AST.PRVT.GD.ZS), bis_dsr_private (BIS/WS_DSR Q.{iso2}.P), bis_credit_gap (BIS/WS_CREDIT_GAP Q.{iso2}.P.A.C) |
| sovereign | 10 | public_debt_gdp (IMF/WEO GGXWDG_NGDP; fb WB/WDI GC.DOD.TOTL.GD.ZS), fiscal_balance_gdp (IMF/WEO GGXCNL_NGDP; fb WB/WDI GC.NLD.TOTL.GD.ZS), primary_balance_gdp (IMF/WEO GGXONLB_NGDP), government_revenue_gdp (IMF/WEO GGR_NGDP; fb WB/WDI GC.REV.XGRT.GD.ZS), government_expenditure_gdp (IMF/WEO GGX_NGDP; fb WB/WDI GC.XPN.TOTL.GD.ZS), total_external_debt_usd (WB/WDI DT.DOD.DECT.CD), external_debt_gni (WB/WDI DT.DOD.DECT.GN.ZS), ppg_external_debt_usd (WB/WDI DT.DOD.DPPG.CD), debt_service_exports (WB/WDI DT.TDS.DECT.EX.ZS), interest_revenue (WB/WDI GC.XPN.INTP.RV.ZS) |
| banking | 2 | npl_ratio (WB/WDI FB.AST.NPER.ZS), bank_capital_ratio (WB/WDI FB.BNK.CAPA.ZS) |
| governance | 6 | wgi_voice_accountability (WB/WGI GOV_WGI_VA.EST), wgi_political_stability (WB/WGI GOV_WGI_PV.EST), wgi_government_effectiveness (WB/WGI GOV_WGI_GE.EST), wgi_regulatory_quality (WB/WGI GOV_WGI_RQ.EST), wgi_rule_of_law (WB/WGI GOV_WGI_RL.EST), wgi_control_corruption (WB/WGI GOV_WGI_CC.EST) |
| geopolitical | 7 | trade_openness (WB/WDI NE.TRD.GNFS.ZS), natural_resource_rents_gdp (WB/WDI NY.GDP.TOTL.RT.ZS), military_expenditure_gdp (WB/WDI MS.MIL.XPND.GD.ZS), gdp_ppp_world_share (IMF/WEO PPPSH), food_exports_share (WB/WDI TX.VAL.FOOD.ZS.UN), fuel_exports_share (WB/WDI TX.VAL.FUEL.ZS.UN), metals_exports_share (WB/WDI TX.VAL.MMTL.ZS.UN) |
| social *(weight 5)* | 1 | gini (WB `SI.POV.GINI`, ~59) — income inequality, Dalio's internal-conflict / changing-world-order dimension. **WIRED into the composite** (the one genuinely new dimension; not double-counted by any existing pillar). |
| markets *(unweighted)* | 10 | reer_broad (BIS/WS_EER, ~56), interest_on_debt_gdp (IMF/FM `ie`, ~60), real_long_rate (IMF/FM `rltir`, ~49), corporate_debt_gdp (IMF/GDD `NFC_LS`, ~58), ecb_cost_borrow_nfc + ecb_cost_borrow_house (ECB/MIR, ~21 EU), private_debt_gdp (IMF/GDD `PVD_LS`, 64 — raw material for a computed credit gap), household_debt_gdp (IMF/GDD `HH_LS`, ~58), govt_net_debt_gdp (IMF/WEO `GGXWDN`, ~47), imf_policy_rate (IMF SDMX `MFS166`, EM the BIS misses). Plus view-only `bond_yield_10y` (FRED) and `implied_interest_rate`. `markets` is unweighted (`markets: 0`) — redundant with existing pillars, so kept out of the composite to avoid double-counting. **NOTE:** `implied_interest_rate` IS consumed by `dalio.py` as the cost-of-debt (`nom_rate`), replacing the policy rate in the beautiful-vs-ugly / r-vs-g test. Two more are exposed **only through the `v_macro_panel_ext` view**, not stored as panel rows: `bond_yield_10y` (bridged from FRED IRLTLT01*) and `implied_interest_rate` (derived: `interest_on_debt_gdp / gross_debt × 100`, ~60). The `markets` pillar is intentionally **not** in the composite weights (`markets: 0`), so these staged inputs do not change any composite/phase until explicitly wired into the methodology. |

**Frequency / lag of this panel:** mostly **annual** (WDI/WEO/WGI — lag 3–18
months, stalled-after 400 d) and **quarterly** (BIS DSR / credit gap —
stalled-after 120 d). The existing `coverage_score` already handles these
freq-aware lags, so the engine works unchanged.

**Point-in-time (revisions).** FRED/WEO/WDI values are revised after first
release, so `macro_series` / `macro_panel` keep only the latest figure. To make
macro-signal backtests revision-safe, every ingest also appends changed values
to `macro_series_vintage` / `macro_panel_vintage` (stamped with the ingest
`vintage_date`). Read as-known-then with the `asof=<date>` argument of
`read_macro` / `read_macro_panel`. Note: history only exists from when ingestion
began — there is no pre-existing vintage for periods before first ingest.

**Integration note (implemented).** Each row already carries `pillar`,
`orientation` (+1 healthier / −1 worse / 0 neutral), `source`,
`provider_dataset`, `provider_code` and `unit` (see the table above), so the
metadata travels with the data.

- **Cross-country coverage scoring.** `macro_panel_coverage` (one row per
  indicator) scores how many of the expected countries carry each indicator,
  the freshest date, declared vs detected frequency, and a freq-aware stalled
  flag — computed by `coverage.report.rebuild_macro_panel_coverage()` using the
  same coverage engine as `coverage_report`. Read via
  `reader.get_macro_panel_coverage()`. (The panel is a `(date, country,
  indicator)` table, so it is scored here rather than mixed into the per-symbol
  `coverage_report`.)
- **Select best source.** `sources.macro_panel.fetch_indicator(..., select_best=True)`
  fetches both the primary and the fallback and keeps whichever covers more
  countries (the IMF↔WB "best source" idea). Enabled with
  `macro_panel.select_best_source: true` in `settings.yaml` (default off — it
  costs one extra API call per indicator that has a fallback). The default still
  uses the cheap primary→fallback-on-empty path.

> **Note on providers:** World Bank, IMF DataMapper and BIS/DBnomics are keyless
> REST APIs reachable from this network; only the FRED CSV endpoint is
> proxy-blocked here, so the live `select_best` path is exercised on the user's
> machine (the selection logic itself is unit-tested with synthetic frames).

---

## 6. factor_returns — Fama-French / momentum factors (Ken French Data Library)

Daily/monthly factor returns used for factor-based allocation, risk
decomposition and quant backtests. Long format keyed `(date, factor_set,
factor)`; values are **decimal returns** (Ken French publishes percent — the
source converts).

- **Table:** `factor_returns` · **Backfill start:** 1990 (configurable)
- **Source:** Ken French Data Library (keyless CSV zips)
- **Read API:** `reader.read_factors(factors=…, factor_set=…, wide=…)`

| factor_set | Freq | Factors |
|------------|------|---------|
| `FF5_daily` | D | Mkt-RF, SMB, HML, RMW, CMA, RF |
| `MOM_daily` | D | Mom |
| `FF5_monthly` | M | Mkt-RF, SMB, HML, RMW, CMA, RF |

Datasets to download are listed under `factors.datasets` in `settings.yaml`; the
catalog of available sets lives in `sources/factors.py::CATALOG`.
