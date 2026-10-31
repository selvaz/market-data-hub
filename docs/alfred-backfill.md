# ALFRED real-time vintage history

The backfill fills `alfred_vintage_observations` with vendor-published history
for real-time indicator evaluation and the GDP nowcast pilot. This differs
from `macro_series_vintage`, which records when the hub first noticed a change.
The versioned universe is `market_data_hub/config/alfred_backfill.yaml` (the
package's `config/alfred_backfill.yaml`, included in wheels). Every entry has
its inclusion reason and source; excluded market series and external files
also have reasons. No analytics or new scheduled job is installed by this command.

## Command and result

```powershell
python -m market_data_hub.alfred_backfill --db C:\scratch\hub.duckdb --dry-run
python -m market_data_hub.alfred_backfill --db C:\scratch\hub.duckdb --series PAYEMS GDPC1 --max-requests 200
python -m market_data_hub.alfred_backfill --db C:\scratch\hub.duckdb --since-vintage 2010-01-01
```

`--db` is mandatory. The default universe is all configured series; `--series`
accepts space-separated or comma-separated configured IDs. `FRED_API_KEY` must
be injected through the environment. The command never logs the key, response
URLs, or raw HTTP exceptions. It uses `requests`, including on Windows.

One JSON record per series reports `vintages_added`, `rows_added`, HTTP attempt
count, pending vintage count, and status. Rows added count new primary keys;
an incremental validity change can report zero new rows but a new vintage.
Exit **0** means new rows or newly completed vintages, **3** means no new data,
and **1** means failure, interruption, lock contention, or an exhausted request
budget. Work stops at the first failed series. `--max-requests` counts every
HTTP attempt, including metadata and retries. Reaching it is deliberately an
incomplete run, not a successful full backfill. A metadata-only dry run returns
3 on success and creates neither a database, its directory, nor a lock file.

For a daily trigger, add `--incremental` to the same command. It uses the same
per-series checkpoint and pending-page resume logic, but returns **0** when
nothing is new (including a successful metadata-only dry run). Incomplete or
failed work still returns **1**. No scheduler configuration is changed here.

## Request strategy, rate limit, and resume

The job paginates the complete [vintage-date endpoint](https://fred.stlouisfed.org/docs/api/fred/series_vintagedates.html),
then requests [observations by real-time period (`output_type=1`)](https://fred.stlouisfed.org/docs/api/fred/series_observations.html).
This preserves all revisions and their inclusive validity periods without the
repeated unchanged observations in output type 2. Type 3 omits unchanged values;
type 1 supplies both the starting baseline and the validity boundaries needed
for withdrawals. The API caps observation pages at 100,000 rows. Queries span
up to 10,000 vintage dates, using real-time bounds rather than long URL lists.
All observation dates are included; no historical start date is imposed.

A single limiter shared by every series, metadata request, and retry spaces
attempts at **90/minute**. There are at most five attempts per request, with
exponential backoff for transport failures, 429 and 5xx. Numeric `Retry-After`
is honored up to 300 seconds. Other 4xx responses, including authentication
failures, stop immediately. The limiter is local to this process; leave key
capacity for other FRED consumers and run only one backfill at a time.

Schema v26 adds nullable `realtime_end` and two small checkpoint tables. The
existing `(series_id, date, as_of)` key remains authoritative. `as_of` holds
the interval's start (clipped to the query baseline where necessary); this is
a sparse history, not a dense snapshot at every vintage date. `realtime_end`
preserves the vendor's last valid date. Missing `.` values are retained as
NULL withdrawals, rather than discarded. Periods clipped at the final query
cutoff remain open until a subsequent incremental fetch supersedes them.

Each write takes the hub's existing `db_write_lock`, opens the connection,
upserts at most **5,000 rows**, and closes the connection before releasing the
lock. Network calls, rate-limit waits, and backoff hold no DuckDB connection
or write lock. A partial-page offset and the fixed query bounds are committed
atomically with each write. A killed process therefore resumes at the first
uncommitted row, even with a small request budget. The completed-vintage cursor
advances only with the final write of a complete query; its pending offset is
then deleted in that same transaction. After finishing a saved query the job
discovers any newer releases and continues. A rerun upserts safely and does not
duplicate rows. The previous completed vintage is refreshed as the next query's
baseline to close formerly open intervals, including withdrawn observations.

Run the **same command** to resume. Checkpoints are scoped by series and
`--since-vintage`, so a later full-history run cannot mistake a limited-history
run for complete coverage. Changing the floor starts a separate cursor and
reuses the same observation keys. Existing rows alone are never proof that a
series was fully loaded. Complete the backfill before evaluating a model:
partial committed history is available to readers during recovery.

## Reading what was known

```python
from market_data_hub.reader import alfred_as_known_at

observations = alfred_as_known_at("PAYEMS", "2024-02-15", db_path=db_path)
```

This returns a date-sorted DataFrame with value and vintage provenance. It picks
the latest stored `as_of <= as_of` per observation, then checks validity and
missingness. It never falls back to an obsolete value after a withdrawal.
Legacy stored snapshots with no validity end remain supported. The existing
`read_alfred_vintage` remains available for exact stored dates/vintages.
Knowledge dates are **calendar dates**, not intraday release timestamps. An
empty result before the first archived vintage is a coverage gap, not licence
to use today's revised history. For example, this capture starts USREC in 2014,
Philadelphia Fed survey vintages in 2015, and Sahm vintages in 2019.

## Estimate captured on 2026-10-18

The [machine-readable estimate](alfred-backfill-estimate.json) records per-series
vintage dates, row counts and request counts. Capture used 42 complete
vintage-date calls and 42 observations `limit=1` count probes, plus two small
fixture calls. It downloaded no full history and opened no production database.

| Measure | Estimate |
|---|---:|
| Unique selected series | 42 |
| Pilot inputs, target and benchmarks | 20 |
| v2 code and downloaded controls | 24 |
| Hub revised series | 23 |
| Direct probit label (also in v2) | 1 |
| Observation interval rows | 1,541,823 |
| Vintage metadata requests on a fresh run | 42 |
| Observation pages at 100,000 rows | 54 |
| Total requests, excluding retries | **96** |
| Pacing-only minimum | **63.3 seconds** |

Source counts overlap. The pilot's market yields/spreads/volatility stay in
`macro_series`; its revised NFCI input and STLENI descriptive benchmark are
included. The probit's EBP and GSW downloads have no ALFRED endpoint.
NFCI accounts for 928,896 rows and STLFSI4 for 315,107. Counts are not guarantees:
new releases, pagination, retries, and partially populated destinations change
the work. Metadata timings suggest **3–8 minutes** including large responses
and bounded writes; this is an extrapolation, not a measured production run.
Reserve **30 minutes**. Offline tests verify correctness, not live throughput.

## Production one-shot operation (orchestrator-owned)

Deploy a reviewed release containing this command to the `marketdata` environment
first. Use its installed package, not spyder-6's editable checkout. The
orchestrator injects `FRED_API_KEY`, sets the deployed runtime as working
directory, captures stdout/stderr, and treats 0 and 3 as success. Its exact
PowerShell command is:

```powershell
& 'C:\ProgramData\InvestmentCommittee\envs\marketdata\Scripts\python.exe' -m market_data_hub.alfred_backfill --db 'C:\ProgramData\InvestmentCommittee\live\db\market_data.duckdb' --max-requests 200
```

Recommended window: **22:00–22:30 America/Los_Angeles**. The read-only reviewed
`investmentcommittee-runtime-final/config/marketdata_jobs.toml` matrix places
EU18 at 09:00, USClose at 13:15, regime daily at 13:45, TreasuryFiscal at 15:00,
CFTC at 15:30, regime 8y at 16:30 and regime comparison at 18:30. Their configured
timeouts finish by 19:30; the next calendar job is at 06:00. Recheck that no
delayed writer is active when the one-shot starts. The existing advisory lock
and DuckDB lock still protect each batch; contention fails the command and the
same task can resume later. A 30-minute task timeout is safe because completed
row offsets survive termination. Do not run a production backfill as an
assistant-session child. No task or production file was created or changed
while implementing this feature.
