# F08: official daily sovereign yields

Probe date: **11 October 2026**, from this Windows worktree, including F08b/F08c.
**Italy and Germany now have fresh daily 10y bond yields through plain HTTP**:
MOT publishes the BTP gross yield and Finanzagentur publishes the Bund reference
yield, both dated October 9. Code computes their aligned **113 bp Italy/Bund
spread**. Banca d'Italia BMK0200 stays as official history, last August 31.
Bundesbank REST remains blocked by Enodia; the **ECB AAA 10y spot curve remains
the explicitly labelled official stand-in** when a fresh aligned Bund leg is
unavailable. Four ECB curves and France's no-key TEC10 CSV also work.

The evidence ledger is [f08-probes.json](f08-probes.json): original request URL,
UTC probe time, HTTP status, content type, byte count and original-response SHA256
for 170 machine probes, plus parser audits of complete responses and code-computed
spread results. Redirect tokens
and response excerpts are omitted. Small offline recordings and their provenance
are in [the fixture manifest](../tests/fixtures/sovereign_yields/manifest.json).
F08c's recorded tables/PDF have a separate
[bond fixture manifest](../tests/fixtures/sovereign_bonds/manifest.json).
An HTTP 200 containing a portal shell or challenge is not counted as data.

## Machine probe table

The last date is an actual nonmissing observation in the fetched response, unless
explicitly labelled **catalogue** or **current-only**. A dash means no observation
date could be verified from this machine. **Not found** describes this investigation,
not proof that a country's central bank has no daily series. Where published
definitions establish a daily series but access fails, that distinction is explicit.

| Country | Official source | Endpoint / series probed | Frequency | Last observation | Status / limitation |
|---|---|---|---|---|---|
| Euro area, AAA (Bund stand-in) | ECB | [YC / G_N_A / SR_10Y](https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y?format=csvdata&startPeriod=2023-01-01); [SR_2Y](https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_2Y?format=csvdata&startPeriod=2023-01-01) | Business-daily (`B`), stored `D` | **2026-10-08**, both | **Works, no key:** 962 observations each from 2023-01-02; 10y 3.51918971%, 2y 3.0331429854%. Svensson spot curve, continuous compounding; not a German national yield. |
| Euro area, all ratings | ECB | [YC / G_N_C / SR_10Y](https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_C.SV_C_YM.SR_10Y?format=csvdata&startPeriod=2023-01-01); [SR_2Y](https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_C.SV_C_YM.SR_2Y?format=csvdata&startPeriod=2023-01-01) | Business-daily (`B`), stored `D` | **2026-10-08**, both | **Works, no key:** 962 observations each from 2023-01-02; 10y 4.1258286142%, 2y 3.2485820015%. |
| Germany, current | Bundesrepublik Deutschland Finanzagentur (issuer) | [Daily prices/yields PDF](https://www.deutsche-finanzagentur.de/fileadmin/user_upload/Institutionelle-investoren/bundesbank/Rendite_engl.pdf), linked by [download centre](https://www.deutsche-finanzagentur.de/en/download-center) | Daily current snapshot | **2026-10-09** | **Works, HTTP 200, no key/challenge:** `DEU_BUND10_FA`, 3.47%, clean reference price 96.15, ISIN DE000BU2Z072. Conventional nominal Bund nearest ten-year residual maturity; daily accumulation, historical ISIN backfill unverified. |
| Germany | Deutsche Bundesbank | [BBSIS daily REST](https://api.statistiken.bundesbank.de/rest/data/BBSIS/D.I.ZAR.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A?startPeriod=2026-10-01); ZAR/R10XX, R02XX for 2y | Daily, published definition | — | **Not found from machine:** REST, CSV and series page return HTTP 400 Enodia challenge. SDMX adapter configured; successful live parsing unverified. |
| France | Banque de France | [Public Webstat custom CSV / TEC10](https://webstat.banque-france.fr/export/csv/fr/catalog/FM/FM.D.FR.EUR.FR2.BB.FRMOYTEC10.HSTA) | Daily | **2026-10-09** | **Works, no key:** 5,615 nonmissing observations since 2004-11-03, latest 4.795%. Active catalog switched to recorded CSV. AFT home/daily/definition/history paths return HTTP 403 challenges; tokenless JSON export remains 404. |
| Italy, current | Borsa Italiana MOT (regulated venue, explicitly authorized in F08c) | [BTP universe](https://www.borsaitaliana.it/borsa/obbligazioni/mot/btp/lista.html); [selected bond/yield](https://www.borsaitaliana.it/borsa/obbligazioni/mot/btp/scheda/IT0005729931-MOTX.html?lang=it) | Daily current snapshot | **2026-10-09** | **Works, HTTP 200, no key/challenge:** `ITA_BTP10_MOT`, published gross yield 4.60%, yield reference price 95.64, ISIN IT0005729931. Fixed nominal BTP nearest ten-year residual maturity; daily accumulation, no plain-HTTP historical download found in probed pages. |
| Italy | Banca d'Italia | [BDS BMK0200 ZIP/CSV](https://a2a.bancaditalia.it/infostat/dataservices/export/IT/CSV/DATA/CUBE/BANKITALIA/DIFF/BMK0200), `MFN_BMK.D.020.922.0.EUR.210` | Daily observations, monthly batch | **2026-08-31** | **Works, stale:** active table in September 2026 report; no fresh daily alternative found in BDI/MEF/ECB probes below. Collector reports stalled/degraded. |
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
The F08b repeat again returned HTTP 400 Enodia. No proof-of-work script was
executed and no challenge was solved or bypassed.

## Italy and France investigation (F08b)

Italy's BMK0200 is **active, with a publication lag consistent with monthly
batches**. The [15 September 2026 report](https://www.bancaditalia.it/pubblicazioni/mercato-finanziario/2026-mercato-finanziario/statistiche_MFN_20260915.pdf)
explicitly lists BMK0200 as the daily benchmark table in BDS and covers July–August.
The downloaded ZIP member is stamped `20260910_155110`; its last nonmissing 10y
observation is August 31. Banca d'Italia's market-statistics page describes monthly
publication, and its [2026 calendar](https://www.bancaditalia.it/statistiche/calendario-pubblicazioni/Cal_mese_ita_2026.pdf)
schedules the next market report on October 15. The calendar's reference period
and the report's supplementary yields can differ; that schedule does not guarantee
a particular new daily yield date. These findings support publication lag rather
than discontinuation, but do not make the feed usable as a current stress input.

BMK0100 and QMOT0100 exports both returned data with monthly series keys. MEF's
[principal interest-rates page](https://www.dt.mef.gov.it/it/debito_pubblico/dati_statistici/principali_tassi_di_interesse/)
links annual/issuance statistics (the 2025 PDF), not a fresh daily secondary-market
10y quote. ECB's country IRS endpoint returns monthly data; the daily counterpart
and daily Italy FM queries returned 404. These probes did **not** identify a fresh,
backfillable official daily Italian 10y alternative; they do not establish absence
across all official datasets. The all-rating euro-area curve is not substituted
for Italy. All URLs, outcomes and evidence links are in `f08b_investigation` in the
probe ledger.

France's documented [Webstat custom CSV export](https://webstat.banque-france.fr/export/csv/fr/catalog/FM/FM.D.FR.EUR.FR2.BB.FRMOYTEC10.HSTA)
works without `WEBSTAT_API_KEY` and supplies more than three years of history.
Its UTF-8 BOM, semicolon delimiters, decimal commas, percent unit and metadata
are checked. Blank weekend/holiday records marked `OBS_STATUS=M` are skipped.
AFT's daily TEC10 page, home, French/English definition pages and historical
archive path all returned HTTP 403 challenges from this machine; no AFT adapter
was added. The optional keyed JSON adapter remains tested for callers explicitly
choosing it, but the active French series no longer requires registration.

## Current Italy and Germany bonds (F08c)

`sources/sovereign_bonds.py` is dispatched by the existing sovereign collector;
the daily job already reads this catalog. Both new entries have `%`, `D`, ISO3
country and three-business-day freshness limits. **No API registration is needed.**
`CB_IT_10Y` / BMK0200 remains the Banca d'Italia monthly-batch official history;
`ITA_BTP10_MOT` is the current Italian stress leg. The Bundesbank curve IDs remain
separate from `DEU_BUND10_FA`, the current German bond/reference leg.

Selection runs against the complete published universe on the **observation
date**, minimizing the absolute calendar-day distance between maturity and that
date plus ten years. February 29 targets clamp to February 28 where necessary;
ties choose the earlier maturity, then lexical ISIN. The series rolls when
another eligible maturity becomes closer; it does not splice old ISIN prices
into the newly selected bond or select a further bond to hide missing data.

* MOT: 12 plain-HTTP pages, 441 listed rows and 105 eligible fixed nominal BTPs
  in the complete live recording. Conventional fixed coupons and nominal Green
  BTPs qualify; strips, inflation-linked BTP/BTP Italia and step-up
  Valore/Futura/Piu bonds do not. The selected instrument is checked again on its
  own page for ISIN, issuer, maturity, EUR currency, plain-vanilla structure,
  semiannual fixed coupon and no indexation.
* Finanzagentur: conventional nominal `DBR` securities, including conventional
  twins and zero coupons; inflation-linked `DBRI`, green `G`, Schatz `BKO` and
  Bobl `OBL` are excluded. Residual maturity determines selection even if a bond
  originally had a longer tenor. The selected August 2036 Bund matches the
  issuer home page's 10-year 3.47% headline on October 9.

MOT's yield table publishes **gross effective YTM 4.60%**, **clean yield-reference
price 95.64**, and **reference date 2026-10-09**. Its calculations are credited to
Skipper Informatica Srl on the venue page. We store that published yield directly;
no price-to-yield calculation is necessary. The separate `Prezzo ufficiale`
94.89651 is dated October 8, and the official-list close 95.42 is not the yield
table's reference price: neither is paired with October 9's yield.

Finanzagentur's accessible [current daily PDF](https://www.deutsche-finanzagentur.de/fileadmin/user_upload/Institutionelle-investoren/bundesbank/Rendite_engl.pdf)
publishes yield **3.47%**, clean price **96.15**, ISIN **DE000BU2Z072**, coupon
**3%** and maturity **2036-08-15** on **2026-10-09**. It republishes the Bundesbank
reference price at Frankfurt Stock Exchange (noon); it is not a closing price.
The old `/en/federal-securities/prices-and-yields` URL is 404. No challenge is
present in the PDF or download-centre response. `pypdf>=4,<7` is the added runtime
dependency for text extraction; no browser or JavaScript is used.

The yield stays in `macro_series`; additive schema **v25** adds
`sovereign_bond_metadata`, keyed by `(date, series_id)`, with ISIN, maturity,
annual coupon, reference price, price type, roll rule and source URL. Yield,
metadata and vintage rows commit in one transaction. Re-observation replaces
the same daily keys without duplicates; a metadata-write failure rolls everything
back and returns fault `1`. Only temporary test DBs were migrated in this work.
Consumers can retrieve traceable spread inputs with:

```sql
SELECT m.*, b.isin
FROM macro_series m
LEFT JOIN sovereign_bond_metadata b USING (date, series_id)
```

**Backfill limitation:** no plain-HTTP historical yield/ISIN download was found
on the probed MOT bond, details, yield, official-list or chart pages. The chart
iframe returns a JS web-component shell with an Incapsula script; no scripts,
auth tokens, API calls or challenge path were followed. Finanzagentur's PDF is
current-only. Its home-page chart includes historical 10y values, but historical
ISINs and roll semantics were not established, so they are not assigned to this
distinct bond series. Both collectors accept the existing inclusive start/end
arguments and return only the dated current snapshot if it falls in the range.
They accumulate from the first authorized production run. Three-year history
continues to come from the existing official history feeds; it is not invented
for these new series.

The original Bundesbank Enodia and AFT challenged endpoints were left alone.
No anti-bot/proof-of-work/JS challenge was solved, emulated or bypassed; no
headless browser, UA/IP rotation or challenge-cookie replay was used.

## Implemented behavior

* New `sources/sovereign_yields.py` accepts inclusive start/end dates and emits
  canonical `macro_series` rows: `CB_<ISO2>_10Y` and optional `_2Y`, ISO3 country,
  `%` units, declared frequency `D`, actual official publisher in `source`.
* Config contains 42 requested/supplementary series across 27 countries plus the
  euro-area aggregate: 26 entries use 16 active provider formats; 16 unresolved
  entries fail explicitly as unavailable. Bundesbank REST has no successful live response;
  Korea's successful recording uses the public sample key.
* `CB_EA_AAA_10Y`, `CB_EA_AAA_2Y`, `CB_EA_ALL_10Y`, `CB_EA_ALL_2Y`
  use publisher `ECB` and region marker `EMU` (an aggregate, not national ISO3).
  Original business-daily frequency `B` is normalized to the hub's daily `D`.
  Exact YC dimensions, percent-per-annum units and multiplier zero are required.
  The existing daily/backfill runner automatically includes these catalog entries.
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

F08c explicitly authorizes Borsa Italiana's public regulated MOT venue pages for
the distinct Italian current series. No commercial aggregator, ECB monthly,
OECD monthly, overnight-policy-rate or mismatched
currency/maturity replacement is implemented. Absence of an official daily
series has not been conclusively established for the unresolved countries, so
unresolved alternative publishers are not presented as approved fallbacks. F08b
explicitly authorizes the daily ECB AAA curve as the Bund stand-in described above.

## Spreads computed in code (F08b/F08c, G10)

[market_data_hub/sovereign_spreads.py](../market_data_hub/sovereign_spreads.py)
provides `derive_sovereign_spreads(observations, catalog, as_of=...)`. Pass long-form
`macro_series` rows with date, series_id, value, unit, frequency and country, and
`get_sovereign_yields()`, in the consumer's analysis step **before** presenting
results to an LLM or dashboard. There is no internal derived-series ingestion
pipeline in this hub: `custom_series` is the public write surface for downstream
apps. The new function is pure, opens no DB and does not persist derived rows.

It derives every configured, enabled euro-country `CB_<ISO2>_10Y` with available
observations, including Italy/France/Portugal/Greece/etc when their feeds become
available, plus the current `ITA_BTP10_MOT` entry. Italy prefers a fresh MOT
leg; if it is absent or stale and older, the official history leg remains visible
with its own freshness. **When Italy and `DEU_BUND10_FA` are fresh and have a fresh
common date, Italy uses `SPREAD_IT_10Y_VS_BUND_FA`. Otherwise the reference is the
labelled ECB AAA stand-in**, with `SPREAD_IT_10Y_VS_ECB_AAA`. Other euro countries
retain their ECB reference. Both input IDs are in observations and status, and
optional joined ISINs are retained as `country_isin` / `reference_isin`.
EM and other currencies are excluded, including local-currency history
before a country's euro adoption date. Series IDs are
`SPREAD_<ISO2>_10Y_VS_ECB_AAA`, unit `bp`, with both input IDs retained. Arithmetic
is `100 * (country_percent_yield - CB_EA_AAA_10Y)` on an inner join of exact dates,
without interpolation or forward fill. These are **spreads against an official
AAA spot proxy** whenever the reference is ECB, and are not pure measures of credit risk:
the proxy is a continuously compounded spot curve while country feeds include
benchmark YTMs and constant-maturity reference rates.

`SpreadResult.observations` contains historical derived rows;
`SpreadResult.status` contains an explicit row for every configured euro 10y.
`freshness_date` is the older leg's latest observation; `last_spread_date` is the
latest common date. A current result is stalled if either leg **or** the latest
common date is more than three business days old on either leg's configured
holiday calendar. This also catches recent but disjoint histories. Missing legs
and missing common dates have explicit reasons. Historical stalled results remain
available, so consumers **must join status and require `stalled == False` before
labelling a value current**. Conflicting unresolved revisions, nonfinite values,
wrong units/frequency/country and intraday dates raise errors; future observations
are excluded for the requested as-of date.

F08c code-computed current result from the complete live MOT/PDF recordings,
as of **2026-10-11**:

| Country | Latest common date | Reference | Spread (bp) | Status |
|---|---|---|---:|---|
| Italy | 2026-10-09 | Finanzagentur Bund, DE000BU2Z072 | **113.00** | Fresh; country ISIN IT0005729931 |

`f08c_parser_audit` and `f08c_spread_audit` in the ledger record the full-precision
code result (112.99999999999994 bp) and freshness date October 9. This compares
MOT's yield reference with the German noon reference, not simultaneous intraday
prices. If the Bund is unavailable and Italy has only October 9 while ECB ends
October 8, there is **no common date**: the fallback is labelled and stalled,
without inventing an October 9 ECB quote. Consumers must still require a fresh
status. Published yield rounding limits spread precision.

Earlier F08b code-computed results from complete recordings, as of **2026-10-11**;
the Italy row below is BMK0200 history, superseded as a current leg by F08c:

| Country | Latest common date | Spread vs ECB AAA 10y (bp) | Current status |
|---|---|---:|---|
| Spain | 2026-10-07 | 55.764878 | Fresh |
| Belgium | 2026-10-08 | 87.081029 | Fresh |
| France | 2026-10-08 | 137.881029 | Fresh; French leg itself extends to October 9 |
| Italy | 2026-08-31 | 76.052645 | **Stale history only; unavailable as current stress** |

Full-precision arithmetic and both curve/country parser audits are recorded in
`f08b_spread_audit` and `f08b_parser_audit` in the ledger. Other requested countries'
machine observations in the table above are retained from F08; F08b re-probed
ECB, Germany, France, Italy, Spain and Belgium.

## Owner actions and remaining verification

| Environment variable | Free registration / source | What registration enables |
|---|---|---|
| `ECOS_API_KEY` | Register for an [ECOS Open API key](https://ecos.bok.or.kr/api/) | Implemented 817Y002/010210000 adapter; verify full pagination/backfill with real key. It deliberately does not use sample as a production substitute. |
| `BANXICO_API_TOKEN` | [Banxico SIE token registration](https://www.banxico.org.mx/SieAPIRest/service/v1/token) | Necessary for future API access, **not sufficient**: daily nominal M-bond 10y code still needs verification/implementation. |
| `EVDS_API_KEY` | Register at [TCMB EVDS3](https://evds3.tcmb.gov.tr/) | Necessary for future API access; EVDS3 endpoint and daily 10y identifier still need verification/implementation. |
| `BCCH_API_TOKEN` | Follow [BCCh API access instructions](https://si3.bcentral.cl/estadisticas/Principal1/Web_Services/acceso_api_en.html) | Necessary for future Siete access; secondary-market CLP government 10y series still needs verification/implementation. Current documentation specifies token, not legacy username/password. |

For Bundesbank REST/Greece/Poland/Peru, publisher access from the runtime needs
resolving without treating a challenge as data. Germany's issuer and Italy's MOT
current series work without registration; historical bond/ISIN backfill remains
unverified. Switzerland's 2y needs a current endpoint. The remaining catalogued
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
ECB and France history now work without keys. Full keyed Korea history and blocked
Germany history remain unverified. `WEBSTAT_API_KEY` is optional for the retained
JSON adapter and is not needed for the active France catalog.

`scripts/probe_sovereign_yields.py OUTPUT_DIR label=URL ...` reproduces read-only
probes and saves original responses. It never opens a hub database. Do not run
the ingestion CLI against a production database as part of validation.

Tests use recorded observations and temporary DuckDB databases exclusively.
Positive Webstat examples are explicitly documentation-derived, since no key was
provided. Tests exercise percent units, tenors, malformed/monthly/challenge
responses, negative yields, genuine weekend dates, explicit holiday closures,
staleness, backfill, pagination, retries/redaction, revision upserts and exit codes.

## Validation evidence

The results in this subsection record the original **F08 / `b39f487`** work.
Final F08b evidence after all code changes is recorded in the following subsection.

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
was not repeated in F08 after those changes; F08b reruns the full suite after all
changes. The two warnings concern an existing
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

## F08b final validation

Tests were written before implementation, using small recordings from the new
machine probes. The first ECB/spread run ended with:

```text
6 failed, 11 passed, 14 errors in 4.43s
```

The newly discovered public French CSV was also demonstrated failing before its
adapter/catalog change:

```text
2 failed, 8 passed, 31 deselected in 3.45s
```

An additional euro-adoption regression failed before correcting eligibility and
excluding pre-adoption local-currency dates (`1 failed, 42 deselected in 3.65s`).
The final targeted run covers exact keys, tenors, units, multipliers, missing
weekends, forecast rejection, inclusive backfill, common-date arithmetic, both
legs' freshness, holiday boundaries, disjoint histories, revisions, future dates
and temporary-DB runner upserts:

```powershell
& C:/ProgramData/spyder-6/python.exe -m pytest tests/test_sovereign_spreads.py tests/test_sovereign_yields.py --basetemp "$env:TEMP/f08b-pytest-final-target2" --tb=short -q
```

```text
107 passed in 17.64s
```

The full suite ran **once in F08b, after every code change**, including all changes
in the original F08 commit. No code or tests changed after this run:

```powershell
& C:/ProgramData/spyder-6/python.exe -m pytest --basetemp "$env:TEMP/f08b-pytest-full" -q
```

```text
655 passed, 1 warning in 458.60s (0:07:38)
```

The warning is the existing ALFRED test's deliberately truncated vintage list.
All pytest basetemps used for F08b were under `%TEMP%` and removed afterward.
Final static/config commands, unpiped:

```powershell
& C:/ProgramData/spyder-6/python.exe -m ruff check market_data_hub/sources/sovereign_yields.py market_data_hub/sovereign_spreads.py tests/test_sovereign_spreads.py tests/test_sovereign_yields.py validate_config.py
& C:/ProgramData/spyder-6/python.exe -X utf8 validate_config.py
git diff --check
```

```text
All checks passed!
CONFIG OK — tickers=149 fred=100 panel=83 countries=64 sovereign_yields=40
```

All gates exited 0. Production databases and ingestion, scheduled tasks and
Telegram were untouched. Changes are committed locally on the existing F08
branch, on top of `b39f487`; no push or PR is performed.

## F08c final validation

Tests were written before implementation using the new plain-HTTP recordings.
The initial run, before catalog entries, collectors or metadata storage existed:

```powershell
& C:/ProgramData/spyder-6/python.exe -m pytest tests/test_sovereign_bonds.py -q --basetemp "$env:TEMP/f08c-red"
```

```text
16 failed in 5.15s
```

The subsequent audit demonstrated empty-page acceptance and a MOT-only Italy
catalog failing before fixes. It also identified two older CFTC migration tests
hard-coded to schema v24; their version assertion now requires `SCHEMA_VERSION`
while retaining all variant/default/data checks (`4 failed, 50 passed in 16.62s`).
A malformed MOT maturity was demonstrated failing before converting the external
format error to explicit unavailable/degraded status
(`1 failed, 41 deselected in 3.78s`).

Final targeted tests include all 42 new cases, existing collectors/spreads,
metadata migration and existing CFTC schema preservation:

```powershell
& C:/ProgramData/spyder-6/python.exe -m pytest tests/test_sovereign_bonds.py tests/test_sovereign_yields.py tests/test_sovereign_spreads.py tests/test_cftc_history.py tests/test_schema_version.py -q --basetemp "$env:TEMP/f08c-targeted-final" --tb=short
```

```text
168 passed in 35.54s
```

Coverage includes published gross yield versus net yield, reference versus
official price dates, complete pagination/caching, missing/duplicate/malformed
pages, nominal eligibility, roll/tie/leap-day rules, challenge rejection,
inclusive range filtering without false backfill, ISIN preservation, idempotent
upserts and atomic rollback, stale exit code 2/fault code 1, Bund preference,
labelled ECB fallback and disjoint dates without forward fill.

Static/config gates used the required interpreter and ran unpiped:

```powershell
& C:/ProgramData/spyder-6/python.exe -m ruff check market_data_hub/sources/sovereign_bonds.py market_data_hub/sources/sovereign_yields.py market_data_hub/sovereign_spreads.py market_data_hub/runner.py market_data_hub/db/upsert.py market_data_hub/db/connection.py tests/test_sovereign_bonds.py tests/test_cftc_history.py validate_config.py
& C:/ProgramData/spyder-6/python.exe -X utf8 validate_config.py
```

```text
All checks passed!
CONFIG OK — tickers=149 fred=100 panel=83 countries=64 sovereign_yields=42
```

Both exited 0. The probe ledger was checked for 170 unique labels and original
response hashes; its F08c parser/spread audit uses complete live responses and
opens no database. The recorded HTML fixtures retain original table whitespace.

The full suite ran **once at the end of F08c, after all code and test changes**:

```powershell
& C:/ProgramData/spyder-6/python.exe -m pytest -q --basetemp "$env:TEMP/f08c-full" --tb=short
```

Exact final line (exit 0):

```text
697 passed, 1 warning in 471.56s (0:07:51)
```

The warning is the existing ALFRED test's deliberately truncated vintage list.
No code or tests changed after this full run began; only validation documentation
was completed afterward. All F08c pytest basetemps and original probe scratch
responses were under `%TEMP%` and removed afterward; the committed recordings
and original-response hashes preserve the evidence. No production DB was opened,
no production ingestion or scheduled-task/Telegram action was performed, and
there was no push or PR change. The local F08c commit sits on top of `917ffda`.
