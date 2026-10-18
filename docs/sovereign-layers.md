# Sovereign yields: market snapshots and issuer histories

Design approved 2026-10-18. These two layers have separate series IDs and never
replace, splice, interpolate or forward-fill each other. Issuers retain authority.
Existing `CB_*`, `DEU_BUND10_FA` and `ITA_BTP10_MOT` remain unchanged; their
source catalogue and earlier live limitations are in [the original source notes](f08-sovereign-yields.md).

## Daily refresh and backfill

`run_daily.py` -> `runner.run` -> `sovereign_refresh.refresh` runs both layers
under the existing database write lock. Both the default source list and explicit
`--sources sovereign_yields` include this step; `--sources sovereign_layers`
selects just the new layers. Existing jobs that explicitly omit both source names
will continue to omit sovereign data. No scheduled task or production database
was changed. Production receives this code when its pinned hub version is deployed.

```powershell
# Use an explicit non-production destination when validating.
& C:\ProgramData\spyder-6\python.exe run_daily.py --sources sovereign_layers --db .tmp/example.duckdb --report
& C:\ProgramData\spyder-6\python.exe run_backfill.py --sources sovereign_layers --db .tmp/example.duckdb
```

First load and backfill mode request all recorded source history. Incremental
runs refresh seven days before each ID's watermark to retain source revisions.
`--start` overrides the history start; `--end` is inclusive. A historical end
cannot create TradingView history. Annual Treasury responses and complete MoF
curves are cached across tenors within a run; failed chunks never yield partial
rows for a series. Official observations and their vintages commit together.
All HTTP uses `requests`; the existing LazyTools scanner client uses `httpx`,
so the hub keeps its transport independent, with the same batching/rate-budget
principle and no new sibling dependency.

`macro_series` stores values and publisher names. New series' complete reviewed
definitions (including `source_class`, percent unit, convention, tenor, publication
calendar and history start) are persisted in `sovereign_series_metadata.metadata_json`.
Schema v27 adds that catalogue, the snapshot attempt ledger and cross-check table;
it does not change or rewrite existing macro observations.
All new IDs also appear in the hub's catalog/search/describe APIs, including their
source class, yield convention and publication lag. Coverage uses the daily
business-day freshness policy and retains missing layer feeds after an attempt.

## TradingView date, close gate and request budget

The undocumented global scanner returned 106 non-null yields across 29 markets.
Only live-returned tenors are enabled. The four attempted tenors were 02Y, 05Y,
10Y and 30Y for every market listed below, plus Argentina (no result).

The scanner's `time` is the daily bar **open**: the US Friday bar began Thursday
evening. `last_update_time`, `last_bar_time`, `time_close`, `session` and
`last_price_time` returned null. `time|1` returned a latest minute-bar timestamp,
and `timezone` identifies its session calendar. Snapshots use `close` together
with `time|1`, converted in that reviewed provider timezone. For example, the
provider uses America/New_York for Japanese yields, whose latest global quote
timestamp falls on Saturday in Japan but Friday in its provider session. The
separate national timezone gates capture after the configured local close.
Missing/invalid timestamps, unexpected timezone changes and weekend source dates
fail explicitly: no guessed weekday or holiday adjustment.

A Sunday capture stores Friday's provider date. The Indonesian 10y fixture was
already dated Thursday 2026-10-15; it retains Thursday. Quotes are not promoted
to a later date simply because another series is newer. A matching series/date
snapshot is immutable. History begins at the first successful capture, including
that quote's dated prior close, and cannot reconstruct missed past sessions.

The existing daily invocations capture markets whose local close cutoff has
passed (configured `close_after`, national IANA timezone). Before-close weekdays
are deferred; a historical `--end` also skips a current snapshot. The configuration
uses conservative local cutoffs (US 16:00, Canada/UK 17:00, most continental Europe
18:00; full values in YAML), rather than claiming an official trading calendar.
A national holiday whose last published bar is older keeps that older date.

One batch covers all eligible symbols. Each symbol has at most one attempt per
local capture date, persisted even on failure; reruns and process restarts do not
consume another daily attempt. A shared process lock spaces requests by at least
one second (configurable upward). No automatic scanner retry. A failure or changed
schema is logged as unavailable and yields degraded status 2 while official and
other source refreshes continue. A crash leaves an attempted claim, intentionally
spending that day's budget. Official/market observations older than three business
days are logged stalled.

## Comparable pairs and tolerance

The allowlist contains seven comparisons, joined on the **same observation date**
only: `MOF_JGB_{2,5,10,30}Y` vs `TV_JP_{2,5,10,30}Y`, and
`BOC_{2,5,10}Y` vs `TV_CA_{2,5,10}Y`. Japanese yields are nominal
constant-maturity YTMs (semiannual compounding) interpolated by the MoF versus a
vendor benchmark; Canadian pairs compare nominal benchmark bond YTMs. Different
benchmark selection, interpolation and fixing times can leave a basis even on
the same date. The vendor does not publish a complete calculation convention.

`abs(official_percent - market_percent) * 100` is the absolute difference in bp.
The default is **10 bp** for every enabled tenor, including 10y; change
`tolerance_bp` or a pair's `tolerance_bp` in
`market_data_hub/config/sovereign_layers.yaml`. The threshold is strictly greater
than the tolerance. No flags are computed for absent same-date intersections.

Treasury/BoE par curves, BoE/ECB/SNB zero-coupon curves, euro-area aggregate
stand-ins, and Canada's LONG series are excluded from benchmark-YTM comparisons.
No fitted par/spot/YTM conversion or convention splice is performed. Previously
configured national sources remain separate; this change only adds the reviewed
new-adapter pair allowlist. The cross-check refreshes the last 40 calendar days
to catch lagged official arrivals; `--start` permits a longer recomputation.
`sovereign_cross_checks` retains exact differences and convention notes.
Divergences are printed, recorded in `download_log`, and shown in both HTML and
Markdown job reports. They are informational flags, not value corrections.

## Source evidence and gaps

- [US Treasury daily par curve](https://home.treasury.gov/resource-center-data-chart-center/interest-rates)
  exports one CSV per year from 1990. Includes 1.5-month and 4-month columns where
  supplied, in addition to the usual 1-month through 30-year tenors. Missing
  observations and the historical 30-year publication gap remain missing.
- [Japan MoF archive](https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/historical/)
  supplies `jgbcme_all.csv`, combined with `jgbcme.csv` for the current month.
  [MoF methodology](https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/qa.htm)
  specifies constant-maturity, semiannual-compounded market yields.
- [BoE source catalogue](https://www.bankofengland.co.uk/boeapps/database/FromShowColumns.asp?searchText=yield)
  lists IUDSNPY (5y), IUDMNPY (10y), IUDLNPY (**20y**, not 30y).
  No verified daily IADB par codes for 2y or 30y were found; they are not invented
  or substituted with zero-coupon codes. Direct requests on 2026-10-18 returned
  HTTP 403. These three adapters are enabled, with 1-2 business-day publication lag,
  but positive live BoE history/start/counts could not be verified. Their request
  floor is 1900 to avoid truncating history. The positive CSV fixture is explicitly
  synthetic; `boe_10Y.csv` preserves the actual live denial.
- [BoC Valet](https://www.bankofcanada.ca/valet/docs) series are
  BD.CDN.2YR.DQ.YLD, BD.CDN.5YR.DQ.YLD, BD.CDN.10YR.DQ.YLD and
  BD.CDN.LONG.DQ.YLD. LONG means the designated long-term benchmark; it is not
  an exact constant-maturity 30y series and is named `BOC_LONG`.
- Bundesbank and RBA were not requested or bypassed. Argentina remains an explicit
  market coverage gap rather than receiving a fabricated yield.

## Fixture backfill inventory

Counts below are distinct (date, new series ID) rows in the full recorded
responses, for an **empty destination** as of 2026-10-18. They are not a delta
against production; no production database was read. Synthetic BoE values are
excluded. Missing source values are excluded. An incremental rerun was checked
against a temporary warehouse for unchanged counts.

| Source | Series | Rows available |
|---|---:|---:|
| US Treasury | 14 | 99,824 |
| Japan MoF | 15 | 164,002 |
| Bank of Canada | 4 | 25,789 |
| Bank of England | 3 configured | Unknown; live HTTP 403 (0 replayed) |
| TradingView | 106 | 106 initial dated snapshots; no historical backfill |

## Series inventory

All live dates below come from the recorded fixtures captured on 2026-10-18.
“First capture” means accumulation only. BoE dates are explicitly unverified.
This table lists every new ID; the original official catalogue is linked above.

| ID | Source / class | Tenor | Convention | Unit | Publication lag | History start | Last date seen live |
|---|---|---|---|---|---|---|---|
| `UST_PAR_1M` | US Treasury / official | 1M | par yield | percent | same business day | 2001-07-31 | 2026-10-16 |
| `UST_PAR_1_5M` | US Treasury / official | 1_5M | par yield | percent | same business day | 2025-02-18 | 2026-10-16 |
| `UST_PAR_2M` | US Treasury / official | 2M | par yield | percent | same business day | 2018-10-16 | 2026-10-16 |
| `UST_PAR_3M` | US Treasury / official | 3M | par yield | percent | same business day | 1990-01-02 | 2026-10-16 |
| `UST_PAR_4M` | US Treasury / official | 4M | par yield | percent | same business day | 2022-10-19 | 2026-10-16 |
| `UST_PAR_6M` | US Treasury / official | 6M | par yield | percent | same business day | 1990-01-02 | 2026-10-16 |
| `UST_PAR_1Y` | US Treasury / official | 1Y | par yield | percent | same business day | 1990-01-02 | 2026-10-16 |
| `UST_PAR_2Y` | US Treasury / official | 2Y | par yield | percent | same business day | 1990-01-02 | 2026-10-16 |
| `UST_PAR_3Y` | US Treasury / official | 3Y | par yield | percent | same business day | 1990-01-02 | 2026-10-16 |
| `UST_PAR_5Y` | US Treasury / official | 5Y | par yield | percent | same business day | 1990-01-02 | 2026-10-16 |
| `UST_PAR_7Y` | US Treasury / official | 7Y | par yield | percent | same business day | 1990-01-02 | 2026-10-16 |
| `UST_PAR_10Y` | US Treasury / official | 10Y | par yield | percent | same business day | 1990-01-02 | 2026-10-16 |
| `UST_PAR_20Y` | US Treasury / official | 20Y | par yield | percent | same business day | 1993-10-01 | 2026-10-16 |
| `UST_PAR_30Y` | US Treasury / official | 30Y | par yield | percent | same business day | 1990-01-02 | 2026-10-16 |
| `MOF_JGB_1Y` | Japan MoF / official | 1Y | constant maturity YTM, semiannual compounding | percent | 1 business day | 1974-09-24 | 2026-10-15 |
| `MOF_JGB_2Y` | Japan MoF / official | 2Y | constant maturity YTM, semiannual compounding | percent | 1 business day | 1974-09-24 | 2026-10-15 |
| `MOF_JGB_3Y` | Japan MoF / official | 3Y | constant maturity YTM, semiannual compounding | percent | 1 business day | 1974-09-24 | 2026-10-15 |
| `MOF_JGB_4Y` | Japan MoF / official | 4Y | constant maturity YTM, semiannual compounding | percent | 1 business day | 1974-09-24 | 2026-10-15 |
| `MOF_JGB_5Y` | Japan MoF / official | 5Y | constant maturity YTM, semiannual compounding | percent | 1 business day | 1974-09-24 | 2026-10-15 |
| `MOF_JGB_6Y` | Japan MoF / official | 6Y | constant maturity YTM, semiannual compounding | percent | 1 business day | 1974-09-24 | 2026-10-15 |
| `MOF_JGB_7Y` | Japan MoF / official | 7Y | constant maturity YTM, semiannual compounding | percent | 1 business day | 1974-09-24 | 2026-10-15 |
| `MOF_JGB_8Y` | Japan MoF / official | 8Y | constant maturity YTM, semiannual compounding | percent | 1 business day | 1974-09-24 | 2026-10-15 |
| `MOF_JGB_9Y` | Japan MoF / official | 9Y | constant maturity YTM, semiannual compounding | percent | 1 business day | 1974-09-24 | 2026-10-15 |
| `MOF_JGB_10Y` | Japan MoF / official | 10Y | constant maturity YTM, semiannual compounding | percent | 1 business day | 1986-07-05 | 2026-10-15 |
| `MOF_JGB_15Y` | Japan MoF / official | 15Y | constant maturity YTM, semiannual compounding | percent | 1 business day | 1991-08-30 | 2026-10-15 |
| `MOF_JGB_20Y` | Japan MoF / official | 20Y | constant maturity YTM, semiannual compounding | percent | 1 business day | 1986-12-01 | 2026-10-15 |
| `MOF_JGB_25Y` | Japan MoF / official | 25Y | constant maturity YTM, semiannual compounding | percent | 1 business day | 2004-03-22 | 2026-10-15 |
| `MOF_JGB_30Y` | Japan MoF / official | 30Y | constant maturity YTM, semiannual compounding | percent | 1 business day | 1999-09-02 | 2026-10-15 |
| `MOF_JGB_40Y` | Japan MoF / official | 40Y | constant maturity YTM, semiannual compounding | percent | 1 business day | 2007-11-06 | 2026-10-15 |
| `BOE_GILT_PAR_5Y` | Bank of England / official | 5Y | par yield | percent | 1-2 business days | unverified (HTTP 403) | unavailable (HTTP 403) |
| `BOE_GILT_PAR_10Y` | Bank of England / official | 10Y | par yield | percent | 1-2 business days | unverified (HTTP 403) | unavailable (HTTP 403) |
| `BOE_GILT_PAR_20Y` | Bank of England / official | 20Y | par yield | percent | 1-2 business days | unverified (HTTP 403) | unavailable (HTTP 403) |
| `BOC_2Y` | Bank of Canada / official | 2Y | benchmark bond YTM | percent | 1 business day | 2001-01-02 | 2026-10-15 |
| `BOC_5Y` | Bank of Canada / official | 5Y | benchmark bond YTM | percent | 1 business day | 2001-01-02 | 2026-10-15 |
| `BOC_10Y` | Bank of Canada / official | 10Y | benchmark bond YTM | percent | 1 business day | 2001-01-02 | 2026-10-15 |
| `BOC_LONG` | Bank of Canada / official | LONG | benchmark bond YTM | percent | 1 business day | 2001-01-02 | 2026-10-15 |
| `TV_US_2Y` | market:tradingview / market:tradingview | 2Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_US_5Y` | market:tradingview / market:tradingview | 5Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_US_10Y` | market:tradingview / market:tradingview | 10Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_US_30Y` | market:tradingview / market:tradingview | 30Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_DE_2Y` | market:tradingview / market:tradingview | 2Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_DE_5Y` | market:tradingview / market:tradingview | 5Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_DE_10Y` | market:tradingview / market:tradingview | 10Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_DE_30Y` | market:tradingview / market:tradingview | 30Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_IT_2Y` | market:tradingview / market:tradingview | 2Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_IT_5Y` | market:tradingview / market:tradingview | 5Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_IT_10Y` | market:tradingview / market:tradingview | 10Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_IT_30Y` | market:tradingview / market:tradingview | 30Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_FR_2Y` | market:tradingview / market:tradingview | 2Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_FR_5Y` | market:tradingview / market:tradingview | 5Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_FR_10Y` | market:tradingview / market:tradingview | 10Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_FR_30Y` | market:tradingview / market:tradingview | 30Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_ES_2Y` | market:tradingview / market:tradingview | 2Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_ES_5Y` | market:tradingview / market:tradingview | 5Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_ES_10Y` | market:tradingview / market:tradingview | 10Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_ES_30Y` | market:tradingview / market:tradingview | 30Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_GB_2Y` | market:tradingview / market:tradingview | 2Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_GB_5Y` | market:tradingview / market:tradingview | 5Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_GB_10Y` | market:tradingview / market:tradingview | 10Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_GB_30Y` | market:tradingview / market:tradingview | 30Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_JP_2Y` | market:tradingview / market:tradingview | 2Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_JP_5Y` | market:tradingview / market:tradingview | 5Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_JP_10Y` | market:tradingview / market:tradingview | 10Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_JP_30Y` | market:tradingview / market:tradingview | 30Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_CH_2Y` | market:tradingview / market:tradingview | 2Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_CH_5Y` | market:tradingview / market:tradingview | 5Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_CH_10Y` | market:tradingview / market:tradingview | 10Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_CH_30Y` | market:tradingview / market:tradingview | 30Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_CA_2Y` | market:tradingview / market:tradingview | 2Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_CA_5Y` | market:tradingview / market:tradingview | 5Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_CA_10Y` | market:tradingview / market:tradingview | 10Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_CA_30Y` | market:tradingview / market:tradingview | 30Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_AU_2Y` | market:tradingview / market:tradingview | 2Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_AU_5Y` | market:tradingview / market:tradingview | 5Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_AU_10Y` | market:tradingview / market:tradingview | 10Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_AU_30Y` | market:tradingview / market:tradingview | 30Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_BE_2Y` | market:tradingview / market:tradingview | 2Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_BE_5Y` | market:tradingview / market:tradingview | 5Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_BE_10Y` | market:tradingview / market:tradingview | 10Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_BE_30Y` | market:tradingview / market:tradingview | 30Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_PT_2Y` | market:tradingview / market:tradingview | 2Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_PT_5Y` | market:tradingview / market:tradingview | 5Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_PT_10Y` | market:tradingview / market:tradingview | 10Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_PT_30Y` | market:tradingview / market:tradingview | 30Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_GR_2Y` | market:tradingview / market:tradingview | 2Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_GR_5Y` | market:tradingview / market:tradingview | 5Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_GR_10Y` | market:tradingview / market:tradingview | 10Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_GR_30Y` | market:tradingview / market:tradingview | 30Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_NL_2Y` | market:tradingview / market:tradingview | 2Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_NL_5Y` | market:tradingview / market:tradingview | 5Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_NL_10Y` | market:tradingview / market:tradingview | 10Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_NL_30Y` | market:tradingview / market:tradingview | 30Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_AT_2Y` | market:tradingview / market:tradingview | 2Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_AT_5Y` | market:tradingview / market:tradingview | 5Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_AT_10Y` | market:tradingview / market:tradingview | 10Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_AT_30Y` | market:tradingview / market:tradingview | 30Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_IE_2Y` | market:tradingview / market:tradingview | 2Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_IE_5Y` | market:tradingview / market:tradingview | 5Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_IE_10Y` | market:tradingview / market:tradingview | 10Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_IE_30Y` | market:tradingview / market:tradingview | 30Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_SE_2Y` | market:tradingview / market:tradingview | 2Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_SE_5Y` | market:tradingview / market:tradingview | 5Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_SE_10Y` | market:tradingview / market:tradingview | 10Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_MX_5Y` | market:tradingview / market:tradingview | 5Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_MX_10Y` | market:tradingview / market:tradingview | 10Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_MX_30Y` | market:tradingview / market:tradingview | 30Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_BR_2Y` | market:tradingview / market:tradingview | 2Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_BR_5Y` | market:tradingview / market:tradingview | 5Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_BR_10Y` | market:tradingview / market:tradingview | 10Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_CN_2Y` | market:tradingview / market:tradingview | 2Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_CN_5Y` | market:tradingview / market:tradingview | 5Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_CN_10Y` | market:tradingview / market:tradingview | 10Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_CN_30Y` | market:tradingview / market:tradingview | 30Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_IN_2Y` | market:tradingview / market:tradingview | 2Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_IN_5Y` | market:tradingview / market:tradingview | 5Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_IN_10Y` | market:tradingview / market:tradingview | 10Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_IN_30Y` | market:tradingview / market:tradingview | 30Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_ID_5Y` | market:tradingview / market:tradingview | 5Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_ID_10Y` | market:tradingview / market:tradingview | 10Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-15 |
| `TV_ID_30Y` | market:tradingview / market:tradingview | 30Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_ZA_5Y` | market:tradingview / market:tradingview | 5Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_ZA_10Y` | market:tradingview / market:tradingview | 10Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_ZA_30Y` | market:tradingview / market:tradingview | 30Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_TR_2Y` | market:tradingview / market:tradingview | 2Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_TR_5Y` | market:tradingview / market:tradingview | 5Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_TR_10Y` | market:tradingview / market:tradingview | 10Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_PL_2Y` | market:tradingview / market:tradingview | 2Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_PL_5Y` | market:tradingview / market:tradingview | 5Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_PL_10Y` | market:tradingview / market:tradingview | 10Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_KR_2Y` | market:tradingview / market:tradingview | 2Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_KR_5Y` | market:tradingview / market:tradingview | 5Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_KR_10Y` | market:tradingview / market:tradingview | 10Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_KR_30Y` | market:tradingview / market:tradingview | 30Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_CO_5Y` | market:tradingview / market:tradingview | 5Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_CO_10Y` | market:tradingview / market:tradingview | 10Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_CL_2Y` | market:tradingview / market:tradingview | 2Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_CL_5Y` | market:tradingview / market:tradingview | 5Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_CL_10Y` | market:tradingview / market:tradingview | 10Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_CL_30Y` | market:tradingview / market:tradingview | 30Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_PE_5Y` | market:tradingview / market:tradingview | 5Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_PE_10Y` | market:tradingview / market:tradingview | 10Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
| `TV_PE_30Y` | market:tradingview / market:tradingview | 30Y | benchmark bond YTM (vendor methodology not specified) | percent | snapshot after local close; no historical API | first capture | 2026-10-16 |
