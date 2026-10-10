# FRED daily FX rates (WS-02a)

## Configuration and ingestion

The daily job in `run_daily.py` selects the FRED source in
`market_data_hub/runner.py::run_fred`. It reads
`market_data_hub/config/macro_series.yaml` through `get_fred_series()`, passes
its per-series metadata to `sources/fred.py::fetch_fred`, and upserts into
`macro_series` (key: date, series_id). Observed revisions are recorded in
`macro_series_vintage`, and fetch outcomes in `download_log`. An API key uses
the JSON API; without one, the existing public CSV endpoint is used.

WS-02a adds 23 H.10 daily bilateral series to the existing 77 FRED entries
(100 total). No collector or schema migration is required. This change only
configures the next operator-deployed ingestion; production ingestion was not
run as part of implementation.

**Daily observations are published weekly.** The Federal Reserve normally
publishes the previous business week's daily observations on Monday at
4:15 p.m. Eastern, or the next business day after a Monday federal holiday.
A daily hub run cannot make these observations available sooner. Country-risk
consumers should use observation dates and this release cadence when judging
freshness, rather than assuming a daily FRED publication.
Sources: [Federal Reserve H.10](https://www.federalreserve.gov/releases/h10/)
and [FRED daily-rates explanation](https://fred.stlouisfed.org/categories/94?t=usa).

All 23 H.10 pairs configure `max_lag_days: 10`: coverage flags them stalled
only when the last observation is more than 10 calendar days old. Their
detected observation frequency remains D for gap detection and scoring.
The optional non-negative integer override is passed to `is_stalled()` by
the coverage builder, which stores the verdict in `coverage_report.stalled`
and `status`. Both `v_stalled` and `reader.get_stalled()` read that stored
verdict. Existing flags are refreshed by the next coverage rebuild; no
schema migration is needed. Series without an override retain their usual
frequency-based limits, including three days for ordinary daily series.

## Verified series and quote conventions

Verified on 2026-10-10: every series page returned HTTP 200 with the exact
series ID in the title, published units matching the convention below, and
frequency Daily. The extracted facts and source URLs are recorded in
`tests/fixtures/fred_h10_series_metadata.json`. No added IDs remain unverified.
The repo previously had response-shape mocks, but no recorded ID-validation
catalog. The new recording permits offline checks; it is a dated snapshot,
not a claim that future availability is guaranteed.

Each YAML entry includes `currency`, `unit`, `quote_convention`, and
`currency_strength_orientation`. The existing metadata path persists
`unit` and `series_name` in `macro_series`; the full metadata is available
from `get_fred_series()`. The `country` field uses the existing ISO2 / EA
convention for these series. EUR is stored once with country EA and applies
to DE, FR, IT, ES, PT, GR and NL in the requested monitor. USD is the benchmark
and needs no USD/USD series. `DTWEXBGS` remains a broad USD index, with a
different meaning from these bilateral currency rates.

| Currency | Country / area | FRED series | Stored unit | Stronger-currency orientation |
| --- | --- | --- | --- | --- |
| MXN | Mexico (MX) | [DEXMXUS](https://fred.stlouisfed.org/series/DEXMXUS) | MXN per USD | -1 |
| BRL | Brazil (BR) | [DEXBZUS](https://fred.stlouisfed.org/series/DEXBZUS) | BRL per USD | -1 |
| INR | India (IN) | [DEXINUS](https://fred.stlouisfed.org/series/DEXINUS) | INR per USD | -1 |
| CNY | China (CN) | [DEXCHUS](https://fred.stlouisfed.org/series/DEXCHUS) | CNY per USD | -1 |
| KRW | South Korea (KR) | [DEXKOUS](https://fred.stlouisfed.org/series/DEXKOUS) | KRW per USD | -1 |
| JPY | Japan (JP) | [DEXJPUS](https://fred.stlouisfed.org/series/DEXJPUS) | JPY per USD | -1 |
| CHF | Switzerland (CH) | [DEXSZUS](https://fred.stlouisfed.org/series/DEXSZUS) | CHF per USD | -1 |
| CAD | Canada (CA) | [DEXCAUS](https://fred.stlouisfed.org/series/DEXCAUS) | CAD per USD | -1 |
| GBP | United Kingdom (GB) | [DEXUSUK](https://fred.stlouisfed.org/series/DEXUSUK) | USD per GBP | +1 |
| EUR | Euro Area (EA) | [DEXUSEU](https://fred.stlouisfed.org/series/DEXUSEU) | USD per EUR | +1 |
| AUD | Australia (AU) | [DEXUSAL](https://fred.stlouisfed.org/series/DEXUSAL) | USD per AUD | +1 |
| ZAR | South Africa (ZA) | [DEXSFUS](https://fred.stlouisfed.org/series/DEXSFUS) | ZAR per USD | -1 |
| TWD | Taiwan (TW) | [DEXTAUS](https://fred.stlouisfed.org/series/DEXTAUS) | TWD per USD | -1 |
| HKD | Hong Kong (HK) | [DEXHKUS](https://fred.stlouisfed.org/series/DEXHKUS) | HKD per USD | -1 |
| MYR | Malaysia (MY) | [DEXMAUS](https://fred.stlouisfed.org/series/DEXMAUS) | MYR per USD | -1 |
| THB | Thailand (TH) | [DEXTHUS](https://fred.stlouisfed.org/series/DEXTHUS) | THB per USD | -1 |
| SGD | Singapore (SG) | [DEXSIUS](https://fred.stlouisfed.org/series/DEXSIUS) | SGD per USD | -1 |
| NOK | Norway (NO) | [DEXNOUS](https://fred.stlouisfed.org/series/DEXNOUS) | NOK per USD | -1 |
| SEK | Sweden (SE) | [DEXSDUS](https://fred.stlouisfed.org/series/DEXSDUS) | SEK per USD | -1 |
| DKK | Denmark (DK) | [DEXDNUS](https://fred.stlouisfed.org/series/DEXDNUS) | DKK per USD | -1 |
| NZD | New Zealand (NZ) | [DEXUSNZ](https://fred.stlouisfed.org/series/DEXUSNZ) | USD per NZD | +1 |
| VES | Venezuela (VE) | [DEXVZUS](https://fred.stlouisfed.org/series/DEXVZUS) | VES per USD | -1 |
| LKR | Sri Lanka (LK) | [DEXSLUS](https://fred.stlouisfed.org/series/DEXSLUS) | LKR per USD | -1 |

The full current H.10 bilateral universe includes the examples in the contract
plus LKR (`DEXSLUS`). CHF, SGD, NOK, SEK, DKK, NZD, VES and LKR are supplemental
to the requested country list. ZAR is covered by `DEXSFUS`; it is not a gap.

For a positive rate x:

- `usd_per_currency_unit`: USD value of one currency unit is x; orientation +1.
- `currency_units_per_usd`: USD value of one currency unit is 1/x; orientation -1.

For a currency-strength log change use
`orientation * log(x_new / x_old)`. Positive means the currency strengthened
against USD. For an exact simple percentage return use
`100 * (x_new / x_old - 1)` for USD-per-unit quotes and
`100 * (x_old / x_new - 1)` for units-per-USD quotes. Simply negating the raw
percentage change of an inverse quote is not the exact currency return.
Stored observations retain FRED's raw values; conversion belongs downstream.
Missing holiday observations are dropped by the existing fetcher, not filled.

For VES, FRED explicitly records a redenomination on 2021-10-04 that divides
the preceding denomination by 1,000,000. Do not interpret a return across that
break as currency appreciation without accounting for the denomination.
See the [DEXVZUS series notes](https://fred.stlouisfed.org/series/DEXVZUS).

## Missing currencies and free daily sources to evaluate

These 18 currencies from the requested monitor have no current daily bilateral
H.10 series. No guessed DEX IDs or constant peg values are configured. Suggested
sources below are public reference / official-rate data to evaluate in a later
task, including automated access, history, units and market representativeness.
They are not implemented or approved as interchangeable market prices here.

| Currency | Monitor country | Suggested source |
| --- | --- | --- |
| TRY | TR | [ECB daily reference rates](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html) |
| PLN | PL | ECB daily reference rates |
| HUF | HU | ECB daily reference rates |
| CZK | CZ | ECB daily reference rates |
| ILS | IL | ECB daily reference rates |
| IDR | ID | ECB daily reference rates |
| PHP | PH | ECB daily reference rates |
| EGP | EG | [Central Bank of Egypt exchange-rate history](https://www.cbe.org.eg/ar/economic-research/statistics/exchange-rates/historical-data) |
| NGN | NG | [Central Bank of Nigeria daily official NFEM rates](https://www.cbn.gov.ng/rates/ExchRateByCurrency.html) |
| ARS | AR | [BCRA daily wholesale reference rate (A3500)](https://www.bcra.gob.ar/catalogo_de_datos/tipo-de-cambio-de-referencia-mayorista-y-promedio-mensual/) |
| CLP | CL | [Banco Central de Chile daily indicators / observed dollar](https://si3.bcentral.cl/indicadoressiete/secure/IndicadoresDiarios.aspx?Idioma=es-CL) |
| COP | CO | [Banco de la Republica TRM history](https://www.banrep.gov.co/es/como-consultar-datos-historicos-tasa-representativa-mercado-trm-nuevo-portal-estadisticas) |
| PEN | PE | [BCRP daily exchange-rate series](https://estadisticas.bcrp.gob.pe/estadisticas/series/diarias/tipo-de-cambio-nominal) |
| PKR | PK | [State Bank of Pakistan daily exchange-rate datasets](https://www.sbp.org.pk/economic-data) |
| SAR | SA | [Saudi Central Bank exchange rates](https://www.sama.gov.sa/en-us/currency/finexc/pages/currency.aspx); evaluate dated daily history rather than synthesizing the peg |
| AED | AE | [Central Bank of UAE daily FX rates](https://centralbank.ae/en/forex-eibor/exchange-rates/); these are published for VAT obligations |
| VND | VN | [State Bank of Vietnam](https://www.sbv.gov.vn/) central exchange rate (Ty gia trung tam); dated export/API still needs evaluation |
| RUB | RU | [Bank of Russia official daily rates](https://www.cbr.ru/eng/currency_base/daily/) |

ECB rates are currency units per EUR. Obtain units per USD by dividing a
currency's EUR reference quote by the same date's USD-per-EUR quote; align
observation dates before calculating returns. ECB also covers ZAR as a
possible alternate source. Its RUB reference rate has been suspended since
March 2022, so it is not a current RUB fallback.
Source: [ECB reference-rate coverage and RUB notice](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html).

The repo already has `market_data_hub/sources/ecb.py`, a generic ECB SDMX
macro-panel collector. The configured ECB entries in `macro_panel.yaml`
use MIR cost-of-borrowing datasets. There is no configured EXR daily FX
reference-rate collector or eurofxref XML/CSV feed. WS-02a does not add one.

Source verification limitations for later evaluation: the CBE English page
rejected this browsing request, although the indexed Arabic history page
exposes date controls. The older CBN ASP link failed; the current HTML page
was found instead. SBV search results exposed the central-rate navigation,
but a current dated machine-readable daily export was not verified. These
limitations concern suggested follow-up sources, not the 23 FRED IDs.

## Offline tests

`tests/test_fred_fx.py` checks ID coverage, quote conventions (including the
four USD-per-unit exceptions, JPY, CHF and ZAR), and all configured H.10
metadata. It replays the public CSV recording
`tests/fixtures/fred_dexjpus_2024-01-12_2024-01-16.csv` through the existing
FRED runner into a scratch DuckDB. The CSV was recorded from
[FRED's public DEXJPUS endpoint](https://fred.stlouisfed.org/graph/fredgraph.csv?id=DEXJPUS&cosd=2024-01-12&coed=2024-01-16)
on 2026-10-10. Its two actual rates are 144.86 and 147.01 JPY per USD;
2024-01-15 has an empty holiday value. The test blocks requests at the
transport layer, mocks the HTTP response, and checks stored raw values,
unit, country, source, series name and a successful download-log entry.
