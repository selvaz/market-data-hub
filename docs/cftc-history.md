# CFTC positioning history

## Findings

`run_cftc_cot.py` currently requests the current Futures Only datasets from
CFTC's Socrata API: [TFF](https://publicreporting.cftc.gov/resource/gpe5-46if.json)
and [Legacy](https://publicreporting.cftc.gov/resource/6dca-aqww.json). The
connector pages each response in 50,000-row chunks and applies an inclusive
`report_date` filter. The normal runner defaults to the latest 56 days. It does
not request archive ZIPs, and it does not advance its start date from the
oldest row in the database.

The observed table range, July 7 through September 22, 2026, contains 12 weekly
report dates. That bounded history is a consequence of repeatedly running the
date-window API path without a historical backfill. The checked-in runner's
default window is 56 days; the 77-day observed range is longer, so the
repository alone cannot establish whether the external `MarketData_CFTC_COT`
job overrides that value or when its current table contents were first seeded.
There is no CFTC retention rule in `db/retention.py` that deletes older
positioning rows.

The dedicated CFTC runner does not write to `download_log` or
`ingestion_runs`. The latter is the ledger used by on-demand `ensure_*`
services, and the batch runner's `download_log` calls do not cover this script.
The new historical path records each completed archive in `download_log` with
source `cftc_cot_history`; dry runs do not connect to or modify a database.

Both destination tables are already specialized for Futures Only reports:
`cftc_tff_positioning` receives TFF Futures Only rows and
`cftc_legacy_positioning` receives Legacy Futures Only rows. Their primary key
is `(report_date, cftc_contract_market_code)`. The schema had no report-format
discriminator, so schema v24 adds a nullable `report_variant` column. Existing
rows and new ingestion default to `futures_only`. The backfill still excludes
Futures-and-Options Combined rows because the current primary key cannot safely
store both variants for the same contract and date.

## Backfill implementation

`run_cftc_cot.py --backfill-years N` downloads official CFTC ZIPs, validates
all selected archives before opening the database, then streams CSV rows in
batches of at most 5,000 into the existing `upsert()` path. In 2026,
`--backfill-years 20` covers January 1, 2006 through today's date. Legacy uses
individual annual ZIPs (`deacotYYYY.zip`). TFF uses CFTC's 2006–2016 bundle
plus annual ZIPs from 2017 onward. Both parsers map archive headings to the
same normalized DataFrame columns as the live Socrata connector. Missing CSV
members, missing required headers, malformed dates or rows, and unexpected
TFF report variants fail preflight before any write.

The backfill stages downloads outside the writer lock, then takes the hub's
database lock for the upserts. Each batch uses the existing primary key and
`INSERT OR REPLACE` semantics. The new `report_variant` field defaults to
`futures_only` for rows from both the live Socrata run and archive backfill.
Existing recent rows are refreshed from the archive and receive a new
`updated_at`; running the command again adds no duplicate primary keys.
Completed archive writes are recorded in
`download_log`. `--dry-run` validates and counts archive rows by table and
report year without opening the database.

## Official CFTC history files

The CFTC [Historical Compressed index](https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalCompressed/index.htm)
lists the report families and year-by-year Text downloads. The direct file
patterns used here are:

| Report | Archive | Example |
| --- | --- | --- |
| Legacy Futures Only | `deacotYYYY.zip` | [2025 archive](https://www.cftc.gov/files/dea/history/deacot2025.zip) |
| TFF Futures Only, 2006–2016 | `fin_fut_txt_2006_2016.zip` | [combined historical archive](https://www.cftc.gov/files/dea/history/fin_fut_txt_2006_2016.zip) |
| TFF Futures Only, 2017 onward | `fut_fin_txt_YYYY.zip` | [2025 archive](https://www.cftc.gov/files/dea/history/fut_fin_txt_2025.zip) |
| Legacy Futures Only, 1986–2016 | `deacot1986_2016.zip` | [combined historical archive](https://www.cftc.gov/files/dea/history/deacot1986_2016.zip) |

Each archive contains a comma-delimited `.txt` member. The modern TFF headings
use underscore names; Legacy headings use descriptive names with spaces and
punctuation. The older TFF bundle also stores report dates as values such as
`12/27/2016 12:00:00 AM`, while newer annual files use ISO dates. The parser
handles both forms. The CFTC's [About the COT Reports](https://www.cftc.gov/MarketReports/CommitmentsofTraders/AbouttheCOTReports/cot_about)
page describes the available historical COT series and their report formats.

## Dry-run counts and expected size

On September 26, 2026, a network dry run of
`--backfill-years 20` validated the current CFTC archives for
`2006-01-01` through `2026-09-26`. It counted 46,744 TFF and 234,342 Legacy
rows eligible for upsert. These are archive candidate rows; recent keys
already in the hub will be updated rather than inserted. On a database holding
only the stated recent history, the final table counts should be close to
46,744 TFF rows and 234,342 Legacy rows. The current archive can change when
CFTC revises a report, so the command's dry-run output is the authoritative
preflight for the day it runs.

| Report year | TFF rows | Legacy rows |
| ---: | ---: | ---: |
| 2006 | 905 | 4,926 |
| 2007 | 1,703 | 5,394 |
| 2008 | 1,655 | 5,631 |
| 2009 | 1,597 | 6,570 |
| 2010 | 1,761 | 7,308 |
| 2011 | 1,879 | 7,365 |
| 2012 | 1,900 | 7,838 |
| 2013 | 1,889 | 10,159 |
| 2014 | 1,860 | 12,131 |
| 2015 | 1,848 | 11,610 |
| 2016 | 1,914 | 12,434 |
| 2017 | 2,110 | 12,314 |
| 2018 | 2,538 | 13,410 |
| 2019 | 2,518 | 13,468 |
| 2020 | 2,345 | 13,131 |
| 2021 | 2,603 | 13,307 |
| 2022 | 2,719 | 14,245 |
| 2023 | 2,809 | 15,343 |
| 2024 | 3,163 | 16,764 |
| 2025 | 3,686 | 17,316 |
| 2026 through Sep. 26 | 3,342 | 13,678 |
| **Total** | **46,744** | **234,342** |

## Production run

Do not run the production write while `MarketData_CFTC_COT` or another writer
is connected to the database. Pause that daily 15:30 job, wait for the
`market_data` writer lock to clear, and take a file backup first. From the
deployed `market-data-hub` repository root, run this PowerShell preflight:

```powershell
$db = 'C:\ProgramData\InvestmentCommittee\live\db\market_data.duckdb'
$backup = Join-Path (Split-Path $db) ("market_data.duckdb.pre-cftc-backfill-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.bak')
Copy-Item -LiteralPath $db -Destination $backup
if ((Get-FileHash -LiteralPath $db).Hash -ne (Get-FileHash -LiteralPath $backup).Hash) { throw 'Backup verification failed' }
Get-Item -LiteralPath $backup | Select-Object FullName, Length, LastWriteTime
```

Optionally repeat the read-only preflight against the deployed checkout:

```powershell
& 'C:\ProgramData\spyder-6\python.exe' .\run_cftc_cot.py --db $db --backfill-years 20 --dry-run
if ($LASTEXITCODE -ne 0) { throw 'CFTC archive dry run failed; do not start the write' }
```

Then run the backfill:

```powershell
& 'C:\ProgramData\spyder-6\python.exe' .\run_cftc_cot.py --db $db --backfill-years 20
if ($LASTEXITCODE -ne 0) { throw 'CFTC history backfill failed; inspect the error and download_log' }
```

After success, confirm both audit lines from the runner show history beginning
in 2006 and the newest report date, and compare final table counts with the
dry-run totals above. Resume `MarketData_CFTC_COT` after the backfill process
has closed its database connection.

## Risks and tests

- Schema v24 adds nullable `report_variant VARCHAR DEFAULT 'futures_only'` to
  both CFTC tables and fills existing rows as `futures_only`. The
  `(report_date, cftc_contract_market_code)` key stays unchanged; Futures-and-
  Options Combined files are not included because they could collide with the
  Futures Only rows.
- The production `MarketData_CFTC_COT` job and other hub writers share the
  `market_data` lock. The supervisor should pause the job for the backup and
  write window; the backfill also acquires the resolved database's hub lock.
- Each batch is atomic and rerunnable. If a database write fails after earlier
  batches committed, rerunning safely resumes by the same primary keys.
- No live database write was performed for this work package. Fixture tests
  use fresh DuckDB files under the ignored `tests/.tmp` directory.

Tests run with the repository's Spyder Python environment:

```powershell
$env:TEMP = (Resolve-Path tests\.tmp).Path
$env:TMP = $env:TEMP
& 'C:\ProgramData\spyder-6\python.exe' -m pytest tests\test_sources_cftc_cot.py tests\test_cftc_history.py -q -p no:cacheprovider
```

The CFTC tests passed (`11 passed`). The full repository suite also passed
(`495 passed`, one existing ALFRED vintage-limit warning) with the same
`-q -p no:cacheprovider` options.
