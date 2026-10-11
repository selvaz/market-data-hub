# F08: official daily sovereign yields

Probe date: **11 October 2026**, from this Windows worktree. The collector is
implemented, but **fresh coverage of the whole requested universe is incomplete**.
Germany is blocked by an HTTP challenge and Italy's official benchmark export
ends in August. Consequently these probes do not establish a working daily
Italy-minus-Bund stress product.

The evidence ledger is [f08-probes.json](f08-probes.json): original request URL,
UTC probe time, HTTP status, content type, byte count and original-response SHA256
for 116 machine probes, plus parser audits of complete responses. Redirect tokens
and response excerpts are omitted. Small offline recordings and their provenance
are in [the fixture manifest](../tests/fixtures/sovereign_yields/manifest.json).
An HTTP 200 containing a portal shell or challenge is not counted as data.

## Machine probe table

The last date is an actual nonmissing observation in the fetched response, unless
explicitly labelled **catalogue** or **current-only**. A dash means no observation
date could be verified from this machine. **Not found** describes this investigation,
not proof that a country's central bank has no daily series. Where published
definitions establish a daily series but access fails, that distinction is explicit.

| Country | Official source | Endpoint / series probed | Frequency | Last observation | Status / limitation |
|---|---|---|---|---|---|
| Germany | Deutsche Bundesbank | [BBSIS daily REST](https://api.statistiken.bundesbank.de/rest/data/BBSIS/D.I.ZAR.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A?startPeriod=2026-10-01); ZAR/R10XX, R02XX for 2y | Daily, published definition | — | **Not found from machine:** REST, CSV and series page return HTTP 400 Enodia challenge. SDMX adapter configured; successful live parsing unverified. |
| France | Banque de France | [Webstat observations export](https://webstat.banque-france.fr/api/explore/v2.1/catalog/datasets/observations/exports/json?refine=series_key:FM.D.FR.EUR.FR2.BB.FRMOYTEC10.HSTA), TEC10 | Daily | — | **Needs key:** new API returns 404 dataset-not-found without key; old API returns 401. Keyed adapter implemented, positive response schema tested against documentation, not a live keyed recording. |
| Italy | Banca d'Italia | [BDS BMK0200 ZIP/CSV](https://a2a.bancaditalia.it/infostat/dataservices/export/IT/CSV/DATA/CUBE/BANKITALIA/DIFF/BMK0200), `MFN_BMK.D.020.922.0.EUR.210` | Daily observations, delayed release | **2026-08-31** | **Works, stale:** 10y BTP gross benchmark YTM; collector reports stalled/degraded. |
| Spain | Banco de Espana | [ti_1_3.csv](https://www.bde.es/webbe/es/estadisticas/compartido/datos/csv/ti_1_3.csv), `D_G0B1F0ZP` | Daily | **2026-10-07** | **Works:** secondary-market non-stripped government 10y yield. The 1–2y maturity bucket is not labelled constant 2y. |
| Portugal | Banco de Portugal | [BPstat table 484](https://bpstat.bportugal.pt/conteudos/quadros/484); [domain API](https://bpstat.bportugal.pt/data/v1/domains/) | Monthly 2/5/10y averages verified | — for daily | **Not found:** daily 10y endpoint unresolved; monthly data excluded. |
| Greece | Bank of Greece | [Government securities table](https://www.bankofgreece.gr/en/statistics/financial-markets-and-interest-rates/greek-government-securities) | Daily, published definition | — | **Not found from machine:** HTTP 403; historical endpoint unverified. |
| Netherlands | De Nederlandsche Bank | [API documentation](https://www.dnb.nl/en/statistics/access-to-statistics-through-api-services/); [metadata probe](https://api.dnb.nl/statistics/metadata/v1/dataflows) | Unverified | — | **Not found:** documentation 403, guessed discovery endpoint 404. |
| Belgium | National Bank of Belgium | [DF_IROLOBE2 / D.10Y.F](https://nsidisseminate-stat.nbb.be/rest/data/BE2,DF_IROLOBE2,1.0/D.10Y.F?startPeriod=2023-01-01); `D.2Y.F` | Daily | **2026-10-09** | **Works:** OLO reference rates at fixed residual maturities; 10y 4.31%, 2y 3.28%. Uses F reference rates, not A averages. |
| Austria | Oesterreichische Nationalbank | [Government bond yields](https://www.oenb.at/en/daten-und-forschung/datenangebot/zinssaetze-wechselkurse/renditen-oesterreichischer-bundesanleihen.html) | UDRB daily observations released weekly | — for fixed 10y | **Not found:** UDRB is a maturity basket, not a 10y yield. |
| Ireland | Central Bank of Ireland | [Interest/exchange-rate statistics](https://www.centralbank.ie/statistics/interest-rates-exchange-rates) | Unverified for daily 10y | — | **Not found:** portal reachable, daily 10y endpoint unresolved. |
| UK | Bank of England | [IADB CSV / IUDMNZC](https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp?csv.x=yes&Datefrom=01/Jan/2023&Dateto=now&SeriesCodes=IUDMNZC&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N) | Daily | **2026-10-07** | **Works:** fitted 10y nominal zero-coupon yield, 5.4412%. `IUDMNPY` is 10y par, not 2y. |
| Japan | Ministry of Finance | [Current JGB CSV](https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv) + [historical CSV](https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/historical/jgbcme_all.csv) | Daily | **2026-10-08** | **Works:** 10y 3.089%, 2y 1.936%; current English file is `jgbcme.csv`, not `jgbcm.csv` (404). |
| Switzerland | Swiss National Bank | [Current official chart JSON / r10](https://www.snb.ch/public/rates/interestRates) + [replacement history cube](https://data.snb.ch/api/cube/rendeiduebd/data/csv/en?fromDate=2023-01-01&toDate=2026-10-11), D0=CHF/D1=10J | Daily | **2026-10-09** (10y); **2026-09-30** (2y) | **Works for 10y:** 0.579%, current JSON complements the monthly release of daily history. 2y stale (0.287%). Old `rendoblid` stopped 2025-07-31; its `10J1` was Germany, not Switzerland. |
| Sweden | Sveriges Riksbank | [SWEA / SEGVB10YC](https://api.riksbank.se/swea/v1/Observations/SEGVB10YC/2023-01-01/2026-10-11); `SEGVB2YC` | Daily | **2026-10-09** | **Works, no key:** 10y 3.197%, 2y 2.714%. |
| Mexico | Banco de Mexico | [SIE API token-required probe](https://www.banxico.org.mx/SieAPIRest/service/v1/series/SF3006/datos/2026-10-01/2026-10-11); [CF114](https://www.banxico.org.mx/SieInternet/consultarDirectorioInternetAction.do?accion=consultarCuadro&idCuadro=CF114); [CF300](https://www.banxico.org.mx/SieInternet/consultarDirectorioInternetAction.do?accion=consultarCuadro&idCuadro=CF300) | Daily secondary 10y yield unverified | — | **Needs key and identifier:** API returns 400 missing token. SF3006 was a probe, not a verified 10y code. CF114/SF17808 is monthly; CF300 nominal bonds provide prices/coupons, not a verified 10y yield; SF63528 is FX. No functioning data adapter. |
| Brazil | Tesouro Nacional / BCB | [Treasury price/rate CSV](https://www.tesourotransparente.gov.br/ckan/dataset/df56aa42-484a-4a59-8184-7676580c81e3/resource/796d2059-14e9-44e3-80c9-2d9e30b405c1/download/precotaxatesourodireto.csv); [SGS 11 probe](https://api.bcb.gov.br/dados/serie/bcdata.sgs.11/dados?formato=json&dataInicial=01/10/2026&dataFinal=11/10/2026) | Daily fixed-maturity NTN-F quotes | **2026-10-09** | **Works for explicit nominal bonds:** morning purchase yield, NTN-F 2033 12.59%, NTN-F 2037 12.57% (near 10y now). Constant 10y SGS unresolved. Selic/real NTN-B not substituted. |
| Argentina | BCRA | [v4 Monetary catalogue](https://api.bcra.gob.ar/estadisticas/v4.0/Monetarias) plus offset=1000/limit=1000 | No sovereign-yield frequency identified | — | **Not found in all 1,610 catalogue entries:** no sovereign 10y or country-risk series found. This does not establish absence across every official publisher. v3 returned 410. |
| China | China Central Depository & Clearing / ChinaBond | [Government curve historyQuery](https://yield.chinabond.com.cn/cbweb-pbc-web/pbc/historyQuery?startDate=2026-01-01&endDate=2026-10-11&gjqx=10&qxId=hzsylqx&locale=en_US), 10Y | Daily | **2026-10-10** | **Works, no key:** RMB government curve 1.6849%. Original Saturday observation retained. Requests split into at most 365 inclusive days. |
| India | RBI / CCIL | [RBI DBIE](https://data.rbi.org.in/); [CCIL indicative yields](https://www.ccilindia.com/tenorwise-indicative-yields) | Current quote; daily historical API unverified | **2026-10-09**, current-only | **Not found for backfillable constant 10y:** CCIL 9Y–10Y central-government bond 6.94% GS2036 quoted 7.2858%; literal 10Y row is a state bond and excluded. CCIL page states commercial-use permission requirement. |
| Indonesia | Bank Indonesia / DJPPR | [BI market portal](https://www.bi.go.id/en/iru/economic-market-data/default.aspx); [DJPPR](https://www.djppr.kemenkeu.go.id/) | Unverified | — | **Not found:** DJPPR DNS failure persisted outside sandbox; BI page returned metadata/portal content, not daily 10y observations. |
| South Africa | SARB | [MMRD709A / R209 history](https://custom.resbank.co.za/SarbWebApi/WebIndicators/Shared/GetTimeseriesObservations/MMRD709A/2023-01-01/2026-10-11); `MMRD708A` / R2030 | Daily fixed bonds | **2026-10-08** | **Works for explicitly named bonds:** R209 (2036) 8.99%, R2030 (2030) 8.22%. Constant 10y unresolved; no misleading `CB_ZA_10Y` alias. |
| Turkey | TCMB | [EVDS2 API probe](https://evds2.tcmb.gov.tr/service/evds/series=TP.TAHVIL.Y10&startDate=01-10-2026&endDate=11-10-2026&type=json); [EVDS3](https://evds3.tcmb.gov.tr/) | Daily 10y unverified | — | **Needs key and current endpoint:** EVDS2 redirects to EVDS3 portal; guessed code not validated. No functioning data adapter. |
| Poland | NBP / Ministry of Finance | [NBP statistics](https://nbp.pl/en/statistic-and-financial-reporting/interest-rates/); [MF treasury securities](https://www.gov.pl/web/finance/treasury-securities) | Unverified for daily 10y | — | **Not found:** NBP 403; MF catalogue did not expose a daily yield. |
| Korea | Bank of Korea | [ECOS sample / 817Y002 / 010210000](https://ecos.bok.or.kr/api/StatisticSearch/sample/json/en/1/10/817Y002/D/20261001/20261011/010210000) | Daily | **2026-10-08** | **Works with sample; needs free key for production/history:** 10y Treasury bond 4.395%, unit Percent Per Annum. Sample capped at 10 rows; collector requires real key and paginates. |
| Colombia | Banco de la Republica | [Official catalogue](https://totoro.banrep.gov.co/estadisticas-economicas/faces/pages/Buscador.xhtml); SUAMECA series 640001 portal | Daily observations published weekly | **2026-10-02**, catalogue only | **Not found as working history endpoint:** daily 10y COP zero-coupon TES exists, but catalogue date is not a downloaded yield; main site challenge and new portal shell. |
| Chile | Banco Central de Chile | [Siete REST](https://si3.bcentral.cl/SieteRestWS/SieteRestWS.ashx?function=GetSeries&timeseries=F022.BTP.TIN.AN10.NO.Z.D&firstdate=2026-10-01&lastdate=2026-10-11); [official API guide](https://si3.bcentral.cl/estadisticas/Principal1/Web_Services/documentacion_en.html) | Secondary-market government 10y unverified | — | **Needs token and identifier:** tokenless HTTP 200 error HTML. BCP is central-bank paper; BTP series is auction yield. Neither is substituted for daily traded government 10y. No functioning data adapter. |
| Peru | Banco Central de Reserva del Peru | [PEN 10y API probe / PD31893DD](https://estadisticas.bcrp.gob.pe/estadisticas/series/api/PD31893DD/json/2023-01-01/2026-10-11); [official definition](https://estadisticas.bcrp.gob.pe/estadisticas/series/diarias/resultados/PD31893DD/html) | Daily, published definition | — from machine; catalogue **2026-10-07** | **Not found from machine:** JSON/CSV/catalogue return HTML challenge. Web-accessible official catalogue confirms PD31893DD is the local-currency series; PD31894DD is USD. Positive transport/format unverified, no data adapter. |

The Bundesbank key proposed in the task is a real daily series, as shown by its
[official series list](https://www.bundesbank.de/en/statistics/money-and-capital-markets/interest-rates-and-yields/daily-term-structure-on-listed-federal-securities-651570).
The failing response from this machine therefore does **not** prove the key is wrong.

## Implemented behavior

* New `sources/sovereign_yields.py` accepts inclusive start/end dates and emits
  canonical `macro_series` rows: `CB_<ISO2>_10Y` and optional `_2Y`, ISO3 country,
  `%` units, declared frequency `D`, actual official publisher in `source`.
* Config contains 36 requested/supplementary series across 27 countries: 20
  entries use 13 implemented provider formats; 16 unresolved entries fail
  explicitly as unavailable. Germany and France have adapters but no successful
  live responses; Korea's successful recording uses the public sample key.
* SARB R209/R2030 have explicit bond IDs. Their residual maturity declines with
  time, so neither is silently stored as a constant 10y yield.
* Brazil uses the expressly requested Tesouro Nacional source for nominal NTN-F
  2033/2037 morning purchase yields under `CB_BR_NTNF_2033/2037`. Retail offered
  quotes are labelled separately from closing market yields and constant 10y.
* Daily runner and backfill CLI include `sovereign_yields`. Default initial start
  is 2023-01-01; both CLIs support `--start`; daily CLI also supports `--end`. Incremental retrieval
  refreshes seven calendar days to capture revisions. ChinaBond one-year limits,
  ECOS pagination, Japan history/current overlap and SNB history/current overlap
  are handled. Current exports win overlapping official revisions.
* Complete response batches are fetched/parsed before a series is upserted.
  Existing primary-key upsert and vintage machinery are reused. Repeated runs
  do not duplicate observations. Transient transport, 429 and server failures
  retry; permanent 4xx fail directly; secret-bearing URLs are not logged.
* Every series has `max_lag_days: 3`, `lag_calendar: business`. A quote exactly
  three business days old remains fresh; the fourth is stalled. Business days
  mean Monday–Friday minus optional per-series `holidays: [YYYY-MM-DD, ...]`.
  National holiday lists are **not prepopulated**. Missing holiday/weekend quotes
  remain missing; there is no interpolation or forward fill. Genuine publisher
  weekend quotes (China) are retained.
* Coverage keeps the actual publisher and represents requested unavailable feeds
  once sovereign ingestion has been attempted. Legacy FRED lag settings remain
  calendar-based. Stored `lag_days` remains calendar days; the stalled decision
  for these daily series uses business days.
* Sovereign run codes: 0 successful/fresh, 1 internal/config/DB fault, 2 missing,
  blocked or stale feeds (including partial/all-unavailable), 3 no work, 9 a live
  writer owns the lock. Daily/backfill CLIs preserve these codes, including when
  the daily report is requested. Existing collectors retain their existing
  error-accounting behavior; this change does not retrofit their statuses.

No commercial, ECB monthly, OECD monthly, overnight-policy-rate or mismatched
currency/maturity replacement is implemented. Absence of an official daily
series has not been conclusively established for the unresolved countries, so
alternative publishers are not presented as approved fallbacks.

The collector supplies daily inputs; it does not synthesize a spread when either
leg is unavailable or stale. Use matching observation dates to compute
`100 * (CB_country_10Y - CB_DE_10Y)` in basis points. Series definitions differ:
the UK and Switzerland are zero-coupon curves, Germany is an annual-coupon
derived yield, and other countries include benchmark or constant-maturity yields.
Those definitions remain in series names. Cross-currency EM differences also
include domestic rates/currency effects, not just sovereign credit risk.

## Owner actions and remaining verification

| Environment variable | Free registration / source | What registration enables |
|---|---|---|
| `WEBSTAT_API_KEY` | Create a [Webstat account](https://webstat.banque-france.fr/), then generate a profile API key; see [migration guide](https://webstat.banque-france.fr/pages/guide-migration-api/) | Implemented TEC10 adapter uses `Authorization: Apikey ...`. Must verify a real keyed response and >=3 years of history. Missing key is unavailable. |
| `ECOS_API_KEY` | Register for an [ECOS Open API key](https://ecos.bok.or.kr/api/) | Implemented 817Y002/010210000 adapter; verify full pagination/backfill with real key. It deliberately does not use sample as a production substitute. |
| `BANXICO_API_TOKEN` | [Banxico SIE token registration](https://www.banxico.org.mx/SieAPIRest/service/v1/token) | Necessary for future API access, **not sufficient**: daily nominal M-bond 10y code still needs verification/implementation. |
| `EVDS_API_KEY` | Register at [TCMB EVDS3](https://evds3.tcmb.gov.tr/) | Necessary for future API access; EVDS3 endpoint and daily 10y identifier still need verification/implementation. |
| `BCCH_API_TOKEN` | Follow [BCCh API access instructions](https://si3.bcentral.cl/estadisticas/Principal1/Web_Services/acceso_api_en.html) | Necessary for future Siete access; secondary-market CLP government 10y series still needs verification/implementation. Current documentation specifies token, not legacy username/password. |

For Germany/Greece/Poland/Peru, publisher access from the runtime needs resolving
without treating a challenge as data. Italy needs a fresher official daily
release; Switzerland's 2y needs a current endpoint. The remaining catalogued
countries need verified series definitions and historical endpoints, not just
API credentials. Argentina specifically needs confirmation from BCRA/another
official publisher whether a daily sovereign yield or official country-risk
series is published; no such series was found in the full v4 monetary catalogue.

Three-year history was observed directly in the UK/Belgium/Sweden/SNB replacement
exports, Japan's archive, Italy/Spain long exports and Brazilian NTN-F 2033.
Brazilian NTN-F 2037 starts 2026-02-02; SARB R209 starts 2025-12-09 even when
requested from 2023. Those fixed bonds cannot supply three years before issuance.
ChinaBond's 2023
and 2026 year queries work; chunking covers intervening years in the collector.
Full keyed France/Korea history and blocked Germany history remain unverified.

`scripts/probe_sovereign_yields.py OUTPUT_DIR label=URL ...` reproduces read-only
probes and saves original responses. It never opens a hub database. Do not run
the ingestion CLI against a production database as part of validation.

Tests use recorded observations and temporary DuckDB databases exclusively.
Positive Webstat examples are explicitly documentation-derived, since no key was
provided. Tests exercise percent units, tenors, malformed/monthly/challenge
responses, negative yields, genuine weekend dates, explicit holiday closures,
staleness, backfill, pagination, retries/redaction, revision upserts and exit codes.

## Validation evidence

All commands used `C:/ProgramData/spyder-6/python.exe`, unpiped. Pytest basetemps
were isolated `f08-pytest-*` directories under `%TEMP%`, removed after completion.
No production ingestion, database connection, scheduled-task change or Telegram
send was performed for this work.

The first run before the collector existed ended with:

```text
10 failed, 32 errors in 4.08s
```

Additional regressions were demonstrated failing before their respective fixes:
single historical-month Japan backfill (1 failure), Webstat monthly/forecast
rejection (3 failures), SNB replacement cube (2 failures), SNB current/history
combination (1 failure), the complete CP932 MoF response (1 failure), and Brazil
fixed-maturity nominal yield parsing (2 failures).

Final targeted command:

```powershell
& C:/ProgramData/spyder-6/python.exe -m pytest tests/test_sovereign_yields.py --basetemp "$env:TEMP/f08-pytest-final-gate" --tb=short -q
```

```text
64 passed in 14.15s
```

The full suite was executed once, as requested:

```powershell
& C:/ProgramData/spyder-6/python.exe -m pytest --basetemp "$env:TEMP/f08-pytest-full" -q
```

```text
607 passed, 2 warnings in 497.83s (0:08:17)
```

That run collected before the final Japan encoding regression and late Treasury
addition; both were validated in the final 64-test targeted run. The full suite
was not repeated after those changes. The two warnings concern an existing
Telegram script docstring escape and an ALFRED fixture's deliberately truncated
vintage list, not sovereign data or a Telegram send.

Final static/config gates:

```powershell
& C:/ProgramData/spyder-6/python.exe -m ruff check .
& C:/ProgramData/spyder-6/python.exe validate_config.py
git diff --check
```

```text
All checks passed!
CONFIG OK — tickers=149 fred=100 panel=83 countries=64 sovereign_yields=36
```

`git diff --check` exited 0; Git emitted only LF-to-CRLF conversion notices.
