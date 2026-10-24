# US monthly release families (M9a)

The official daily collector now accepts eight additional monthly US families.
Its default catalogue includes all thirteen families. Weekly claims remain out of
scope. No dependencies or database migrations are added.

## Measures, sources and reviewed editions

Dates below are publication editions, not reference months. All times are in
America/New_York. The machine-readable registry is
`market_data_hub/macro_releases/us_editions.py`.

| Family | Measures | Issuer | Structured source class | Reviewed editions released since August 2026 | Issuer schedule |
| --- | --- | --- | --- | --- | --- |
| `us_ppi` | Final-demand PPI m/m SA (`WPSFD4`) and y/y NSA (`WPUFD4`) | BLS | Issuer API, published calculations | Aug 13, Sep 10, Oct 15, 08:30 | [BLS 2026](https://www.bls.gov/schedule/2026/home.htm) |
| `us_jolts` | Total nonfarm job openings, SA, thousands (`JTS000000000000000JOL`) | BLS | Issuer API, level | Aug 4, Sep 1, Sep 29, 10:00 | [BLS 2026](https://www.bls.gov/schedule/2026/home.htm) |
| `us_pce` | PCE and core PCE prices, m/m and y/y, SA | BEA | Issuer API `T20807` lines 1/25 for m/m; issuer release for y/y | Aug 26, Sep 30, 08:30 | [BEA schedule](https://www.bea.gov/news/schedule/full) |
| `us_retail_sales` | Retail and food services m/m SA; ex motor vehicles and parts m/m SA | Census | FRED/ALFRED redistributor: `RSAFS`, `RSFSXMV` | Aug 14, Sep 16, Oct 15, 08:30 | [Census 2026](https://www.census.gov/economic-indicators/calendar-listview-2026.html) |
| `us_housing_starts` | Total starts and permits, thousands of units SAAR | Census | FRED/ALFRED redistributor: `HOUST`, `PERMIT` | Aug 18, Sep 17, Oct 20, 08:30 | [Census 2026](https://www.census.gov/economic-indicators/calendar-listview-2026.html) |
| `us_durable_goods` | New orders m/m SA; ex transportation m/m SA | Census | FRED/ALFRED redistributor: `DGORDER`, `ADXTNO` | Aug 26 and Sep 25 advance, 08:30; Aug 4, Sep 2 and Oct 2 full report, 10:00 | [Census 2026](https://www.census.gov/economic-indicators/calendar-listview-2026.html) |
| `us_industrial_production` | IP m/m SA; total capacity utilization % | Federal Reserve Board, G.17 | FRED/ALFRED redistributor: `INDPRO`, `TCU` | Aug 18, Sep 18, Oct 16, 09:15 | [G.17 dates](https://www.federalreserve.gov/releases/g17/) |
| `us_trade_balance` | Goods and services trade balance, SA, USD billions | Census/BEA jointly | FRED/ALFRED redistributor: `BOPGSTB`, explicitly converted from millions | Aug 4, Sep 3, Oct 6, 08:30 | [BEA schedule](https://www.bea.gov/news/schedule/full) |

The design catalogue's existing definition strings are preserved. The brief adds
measures absent from that catalogue (JOLTS, PPI/PCE m/m, durable goods excluding
transportation), and explicitly corrects its PPI y/y adjustment to NSA. Housing
and JOLTS use explicit scale 1,000; trade uses scale 1 billion USD.

## Authority and fail-closed behavior

Every new observation has an edition, stage and provider vintage. Stages are
separate from indicator IDs. A provider date, headline month, series or requested
line that does not match the reviewed metadata raises `FetchError`; new editions
require review. Current FRED headline probes check freshness without assigning
their values to an older edition. Each historical request specifies an explicit
ALFRED as-of date; unchanged republications are retained.

FRED remains `source_id=FRED`, `distributor_id=FRED`, `evidence_role=mirror`, with
the statistical issuer stored separately. Its computed rates retain their full
provider precision and are never promoted to issuer evidence or rounded into an
official print. Independent deterministic issuer parsers supply published
figures from the current Census retail, housing and advance durable-goods PDFs,
the reviewed G.17 releases, and the reviewed BEA trade releases. This supplies
official current-release cards while retaining the redistributor history.

Older retail/housing editions and durable-goods full-report editions for which
this collector has only ALFRED evidence remain provisional. It does not claim
issuer verification for them. Revised building-permit releases on separate dates
and the retail annual benchmark publication are not separate events in this
monthly headline slice; revisions printed in collected editions are retained.

## Offline certification

`tests/fixtures/macro_releases/us_monthly/` contains saved real responses,
request parameters without credentials, retrieval times and SHA-256 hashes.
`golden.json` adds 24 cases covering all 16 measures and eight previous-month
revision cases. The original golden set is unchanged.

| Family | Headline golden values | Previous-month revision golden case |
| --- | --- | --- |
| PPI | Sep m/m 1.3%; y/y 6.3% | Aug m/m 0.4% to 0.5% in Oct 15 edition |
| JOLTS | Aug 7,079 thousand | Jul 7,271 to 7,335 thousand in Sep 29 edition |
| PCE | Aug headline m/m 0.3%, y/y 3.4%; core m/m 0.2%, y/y 3.0% | Jul headline m/m 0.2% to 0.1% in Sep 30 edition |
| Retail | Sep headline 0.8%; ex autos 0.9% | Aug headline 1.1% to 1.3% in Oct 15 edition |
| Housing | Sep starts 1,221; permits 1,400 thousand SAAR | Aug starts 1,275 to 1,288 in Oct 20 edition |
| Durable goods | Aug headline 0.0%; ex transportation 0.3% | Jul headline 1.1% to 0.9% in Sep 25 edition |
| G.17 | Sep IP 0.7%; utilization 76.9% | Aug IP 0.0% to 0.1% in Oct 16 edition |
| Trade | Aug balance -105.6 billion USD | Jul balance -88.6 to -92.8 billion USD in Oct 6 edition |

Each case includes the issuer publication URL. Earlier BLS prints were checked
on issuer archives through web access; the current BLS API cannot reconstruct
their older vintages, and direct Python access to BLS publication HTML returned
403. Tests certify the saved API's current and revised values without presenting
it as a historical BLS API snapshot. The retail PDF itself prints both the old
and revised August rates. The remaining revision pairs have saved issuer
publications for both editions.

The new tests disable network access and cover parser values, provenance,
edition/measure rejection, release clocks, unit scales, daily collection,
idempotency and official read cards. The two original five-family replay harnesses
are explicitly scoped to their original fixtures; their assertions are retained.

Validation: `tests/test_macro_*.py` returned **649 passed, 1 skipped**. After the
final new-family guards were added, `tests/test_macro_us_monthly.py` returned
**41 passed**. Ruff and `git diff --check` passed. No full repository test suite
was run locally; that remains the lead's check.

## Live verification, 2026-10-24

`scripts/verify_macro_us_monthly.py` reads stable source main/WAL bytes into a
private snapshot and uses SQLite's backup API to create a temporary database.
SQLite never opens the production path. Both collection modes run against the
private destination. An empty consensus archive can isolate this official-data
check from consensus ingestion.

```powershell
& 'C:\ProgramData\spyder-6\python.exe' scripts\verify_macro_us_monthly.py `
  --source 'C:\ProgramData\InvestmentCommittee\live\db\macro_releases.sqlite' `
  --destination-root 'D:\Temp'
```

The live backup was `D:\Temp\mdh-m9a-onpy3ywf\macro_releases.sqlite`.
Both the real-network dry run and subsequent collection on the copy returned
`status=ok`, exit code 0, and the following `vintages_added`:

| Family | Dry run | Collection on copy |
| --- | ---: | ---: |
| `us_ppi` | 138 | 138 |
| `us_jolts` | 68 | 68 |
| `us_pce` | 58 | 58 |
| `us_retail_sales` | 142 | 142 |
| `us_housing_starts` | 142 | 142 |
| `us_durable_goods` | 226 | 226 |
| `us_industrial_production` | 150 | 150 |
| `us_trade_balance` | 72 | 72 |

The read API returned two official cards for October 19–23, both dated October
20 at 08:30 New York time: September housing starts 1,221 thousand SAAR and
building permits 1,400 thousand SAAR. Both select Census evidence. The copy
passed SQLite `quick_check`. The complete local report is
`D:\Temp\mdh-m9a-onpy3ywf\verification.json`.

Consensus was deliberately outside this validation. That first run used a
nonexistent test archive and reported an independent consensus failure; it did
not affect any official-family outcome. The verification helper now creates an
empty archive. No scheduled jobs, runtime worktrees or production data were
modified.
