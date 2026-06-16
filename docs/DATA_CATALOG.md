# market_data_hub — Data Catalogue

> The complete map of every series in the database: provider, group, native
> frequency, typical update lag, history depth, and which table/column holds it.
> For the engine and DB internals see [ARCHITECTURE.md](ARCHITECTURE.md).

Totals: **111 Yahoo symbols** + **38 FRED series** + **6 crypto × 3 timeframes**.

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
| Sub-group | Symbols |
|-----------|---------|
| Global / broad | ACWI, VEA, IEMG, EMXC, VWO |
| US core / size / style | SPY, QQQ, IWM, VUG, VTV, VGT |
| US sectors (SPDR) | XLV, XLF, XLY, XLC, XLI, XLP, XLE, XLU, XLB |
| Europe broad / sectors | EXSA.DE, VGK, FEZ, EXV1.DE, EXV3.DE, EXV4.DE, EXH4.DE, EXH1.DE, EXH9.DE |
| Single-country DM | EWU, EWG, EWQ, EWI, EWJ, EWC, EWA |
| Asia / China | AAXJ, FXI, MCHI, KWEB, KBUY, KURE, CQQQ, INDA, EWY, EWT |
| LatAm | EWZ, EWW, ILF |

### FIXED_INCOME (21) — `asset_class = FIXED_INCOME`
| Sub-group | Symbols |
|-----------|---------|
| US Treasuries (curve) | SHY, IEI, IEF, TLT, BIL, SHV |
| Aggregate / TIPS / global | AGG, TIP, BNDX, BWX |
| IG credit | LQD, VCSH |
| High yield | HYG, EUHY, HYXU, HYEM, HYD |
| EM debt | EMB, EMLC, CEMB |
| Municipals | MUB |

### COMMODITIES (15) — `asset_class = COMMODITIES`
| Sub-group | Symbols |
|-----------|---------|
| Broad baskets | DBC, PDBC |
| Energy | USO, UNG, UGA |
| Precious metals | GLD, SLV, PPLT, PALL |
| Base metals | DBB, CPER |
| Agriculture | DBA, CORN, WEAT, SOYB |

### ALTERNATIVES (14) — `asset_class = ALTERNATIVES`
| Sub-group | Symbols |
|-----------|---------|
| Crypto (Yahoo daily) | BTC-USD, ETH-USD, SOL-USD, IBIT, ETHA |
| **VIX term structure** | **^VIX, ^VIX9D, ^VIX3M, ^VIX6M, ^VVIX, ^VXN** |
| Vol / infra | VIXY, IGF, IFRA |

> The 6 `^VIX*` indices feed the `v_vix_term_structure` view — the backbone for
> `quant_vix_calibrator` / `quant_vix_dashboard`.

### FX (10) — `asset_class = FX`
EURUSD=X, GBPUSD=X, USDJPY=X, AUDUSD=X, USDCAD=X, USDCHF=X, EURGBP=X, EURJPY=X, EURCHF=X, UUP

### REAL_ESTATE (2) — `asset_class = REAL_ESTATE`
VNQ, VNQI

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
DGS3MO, DGS2, DGS10, DGS30, T10Y2Y, T10YIE, T5YIE, EFFR, VIXCLS

### US credit — daily/weekly
BAMLC0A0CM (IG OAS), BAMLH0A0HYM2 (HY OAS), AAA, BAA, NFCI (weekly), STLFSI4 (weekly)

### US activity & prices — monthly (lag 15–45 d, stalled-after 45 d)
CPIAUCSL, CPILFESL, PCEPI, PCEPILFE, INDPRO, UNRATE, PAYEMS, HOUST, RSAFS

### US money / Fed — monthly / weekly
M2SL (monthly), WALCL (weekly)

### US GDP — quarterly (lag 30–90 d, stalled-after 120 d)
GDP, GDPC1

### Energy spot — daily
DCOILBRENTEU (Brent), DCOILWTICO (WTI)

### USD index — daily
DTWEXBGS (broad trade-weighted USD)

### Euro-area policy rates (via FRED) — daily
ECBDFR (deposit facility), ECBMRRFR (main refi), ECBMLFR (marginal lending)

### Euro-area macro (via FRED) — monthly/quarterly
CP0000EZ19M086NEST (HICP), CLVMEURSCAB1GQEA19 (real GDP, Q),
EUNNGDP (nominal GDP), LRHUTTTTEZM156S (unemployment)

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
- **Sources:** World Bank REST (WDI db, WGI db) + IMF DataMapper (WEO)
- **Read API:** `reader.read_macro_panel(indicators, countries, wide=…)`
- **Extra metadata per row:** `pillar`, `orientation` (+1 healthier / −1 worse /
  0), `provider_dataset`, `provider_code`, `unit`

**Countries (64):** G7 + rest of G20 + EU27 + other developed (CHE, NOR, NZL,
ISR, SGP, HKG, …) + major EM (THA, MYS, PHL, VNM, PAK, BGD, EGY, NGA, COL, CHL,
PER, UKR, ARE, QAT, KWT, …).

**Indicator catalogue (~50, by pillar):**

| Pillar | Provider/dataset | Indicators (provider code) |
|--------|------------------|----------------------------|
| growth | WB / WDI | real_gdp_growth (NY.GDP.MKTP.KD.ZG), gdp_current_usd (NY.GDP.MKTP.CD), gdp_per_capita_growth (NY.GDP.PCAP.KD.ZG), unemployment_rate (SL.UEM.TOTL.ZS), investment_gdp (NE.GDI.TOTL.ZS), gross_savings_gdp (NY.GNS.ICTR.ZS), labor_productivity_growth (SL.GDP.PCAP.EM.KD.ZG), high_tech_exports_share (TX.VAL.TECH.MF.ZS), rnd_expenditure_gdp (GB.XPD.RSDV.GD.ZS), population_growth (SP.POP.GROW), dependency_ratio (SP.POP.DPND) |
| liquidity | WB / WDI | inflation_cpi (FP.CPI.TOTL.ZG), real_interest_rate (FR.INR.RINR), lending_interest_rate (FR.INR.LEND), broad_money_gdp (FM.LBL.BMNY.GD.ZS) |
| external | IMF / WEO + WB / WDI·IDS | current_account_gdp (WEO BCA_NGDPD; fallback WDI BN.CAB.XOKA.GD.ZS), fx_reserves_usd (FI.RES.TOTL.CD), fx_reserves_months_imports (FI.RES.TOTL.MO), official_fx_rate (PA.NUS.FCRF), exports_gdp (NE.EXP.GNFS.ZS), imports_gdp (NE.IMP.GNFS.ZS), fdi_inflows_gdp (BX.KLT.DINV.WD.GD.ZS), short_term_external_debt_reserves (IDS DT.DOD.DSTC.IR.ZS), debt_service_exports (IDS DT.TDS.DECT.EX.ZS) |
| debt_cycle | WB + BIS | private_credit_gdp (FS.AST.PRVT.GD.ZS), external_debt_gni (IDS DT.DOD.DECT.GN.ZS), **BIS** credit_to_gdp_gap, household_dsr, corporate_dsr, private_nonfinancial_dsr (quarterly) |
| sovereign | IMF / WEO | public_debt_gdp (GGXWDG_NGDP), fiscal_balance_gdp (GGXCNL_NGDP), primary_balance_gdp (GGXONLB_NGDP), government_revenue_gdp (GGR_NGDP), government_expenditure_gdp (GGX_NGDP), interest_revenue (WDI GC.XPN.INTP.RV.ZS) |
| banking | WB / WDI | npl_ratio (FB.AST.NPER.ZS), bank_capital_ratio (FB.BNK.CAPA.ZS), bank_liquid_reserves_assets (FD.RES.LIQU.AS.ZS) |
| governance | WB / WGI | wgi_voice_accountability (VA.EST), wgi_political_stability (PV.EST), wgi_government_effectiveness (GE.EST), wgi_regulatory_quality (RQ.EST), wgi_rule_of_law (RL.EST), wgi_control_corruption (CC.EST) |
| geopolitical | WB / WDI | trade_openness (NE.TRD.GNFS.ZS), natural_resource_rents_gdp (NY.GDP.TOTL.RT.ZS), military_expenditure_gdp (MS.MIL.XPND.GD.ZS) |

**Frequency / lag of this panel:** mostly **annual** (WDI/WEO/WGI — lag 3–18
months, stalled-after 400 d) and **quarterly** (BIS DSR / credit gap —
stalled-after 120 d). The existing `coverage_score` already handles these
freq-aware lags, so the engine works unchanged.

**Integration note.** Each indicator carries metadata the current schema does
not model: `pillar`, `orientation` (+1 healthier / −1 worse / 0 neutral),
`priority`, multi-provider `fallback_sources`, and per-country provider codes.
A future `macro_panel(date, country_iso3, indicator_id, value, pillar,
orientation, source, provider_dataset, provider_code, unit, status)` would carry
them and let the same coverage engine score cross-country availability. The
"select best source" logic (highest coverage_score, IMF↔WB fallback) is already
implemented in `macro_dashboard.py::select_best_observations` and can be ported.

> **Status:** documented as a proposed extension. The data providers (World
> Bank, IMF DataMapper, BIS/DBnomics) are all keyless REST APIs reachable from
> this network; only the FRED CSV endpoint is proxy-blocked.
