# Forex Factory raw archive

Run from this checkout with the approved Python interpreter:

```powershell
& C:/ProgramData/spyder-6/python.exe run_econ_calendar.py --archive-only
```

The default destination is `C:\ProgramData\InvestmentCommittee\econ_calendar\raw`.
Override it with `--raw-dir <directory>`. No `--db` is needed. Archive mode
returns before the ingestion path, opens no DB, takes no `market_data` lock,
and performs one GET of `ff_calendar_thisweek.json` (30-second timeout) and at
most one retry (see Retry). It preserves the response body bytes without parsing or formatting.

The local fetch date names the first snapshot `YYYY-MM-DD.json`. A changed
response is written as `YYYY-MM-DD.HHMMSS.json`; subsequent runs compare with
the latest snapshot for that date. An identical response creates no file.
Existing snapshots are never replaced. If a different response collides with
an existing timestamp in the same second, the run reports a fault; retry later.
On the supported Windows host, rename also refuses a destination created by
a concurrent writer.

Writes use a temporary file in the destination directory, flush and fsync it,
close it, then rename it. A failed write or rename removes the temporary file.
A fetch failure creates no snapshot file.

## Manifest

Every attempt appends one JSON line to `manifest.jsonl` next to the snapshots,
whether it wrote, found nothing new, or failed. The line is written (append
mode, one write, flush, fsync) before the process exits, including on failure;
earlier lines are never rewritten. One line per run, not per HTTP call.

| field | meaning |
|---|---|
| `captured_at_utc` | ISO 8601 UTC with microseconds and `Z` (e.g. `2026-10-17T21:30:00.123456Z`), taken when the final response arrived (when the last attempt ended, for a failure) |
| `local_date` | local date of that moment (the snapshot's file-name date) |
| `outcome` | `written`, `unchanged` or `failed` |
| `file` | snapshot path; for `unchanged`, the identical existing snapshot; `null` for `failed` |
| `sha256`, `bytes` | of the response body, when a body was received |
| `http_status` | status of the final attempt; `null` for a connection error or timeout |
| `error` | `null`, or a short single-line reason (max 200 characters, URLs removed) |
| `feed_week_start`, `feed_week_end` | min and max event date in the feed (as written in its `date` field), when it parsed |

A manifest write failure after a successful snapshot is itself a failure
(exit 1); the snapshot stays and `load_snapshots` falls back to its mtime.

## Retry

A connection error, timeout, HTTP 429 or 5xx is retried once after a 60-second
backoff (`RETRY_BACKOFF_S`). Any other status (403, 404, ...) and a body that
is not a JSON list are not retried. A run therefore makes at most 2 HTTP calls.

## Exit codes

`0` written, `3` unchanged, `1` any failure (fetch, non-feed body, filesystem).
Callers, including the job_health watchdog, must preserve them.

## Reading the snapshots back

`load_snapshots(directory)` returns `[(captured_at_utc, path)]`, oldest first,
with timezone-aware UTC datetimes. Times come from the manifest's `written`
lines; snapshot files the manifest does not mention (history before the
manifest existed) fall back to their file mtime, and each entry's
`captured_at_source` attribute says `"manifest"` or `"mtime"`. To pick the
snapshot for a release, take the last entry with `captured_at_utc` strictly
before the release time. mtime times are approximate (a copy or restore changes
them); do not treat them as proof of capture before release. It reads files
only and ingests nothing.

Nightly scheduling is an operator responsibility. This change does not
register or modify any scheduled task. Configure the operator's task to invoke
the command above directly with this checkout as its working directory.

The scheduled time will move to 14:30 Pacific, so the snapshot precedes the next day's Asia, Europe and US releases; the operator makes that change, not this repo.

## Reading archived forecasts

```python
from datetime import datetime, timezone
from market_data_hub.econ_calendar.archive_forecasts import read_archived_forecasts

forecasts = read_archived_forecasts(
    "path/to/raw",
    known_at=datetime(2026, 10, 29, 12, tzinfo=timezone.utc),
    release_from=datetime(2026, 10, 29, tzinfo=timezone.utc),
    release_to=datetime(2026, 10, 30, tzinfo=timezone.utc),
)
```

The function returns frozen `ArchivedForecast` records with `provider`
(`"ff_forecast"`), `jurisdiction`, exact `title`, UTC `release_at`, literal
`forecast_raw`, UTC `captured_at`, `snapshot_path` and `snapshot_sha256`.
All query datetimes must be timezone-aware; release bounds are inclusive.
Only hash-verified `written`/`unchanged` manifest captures qualify, with
`captured_at <= known_at` and `captured_at < release_at`. The latest nonblank
forecast per `(jurisdiction, title, release_at)` wins, including a later
`unchanged` capture. Missing archives/manifests return an empty list. Files
without a manifest record, bad hashes, malformed snapshots and releases
without an explicit timestamp offset are ignored. No time is inferred from
filenames, mtimes or the host timezone.

Currencies map to ISO-3 jurisdictions. EUR titles starting with German,
French, Italian, Spanish or Belgian prefixes map to the national jurisdiction;
other EUR titles map to EMU. `All` maps to GLOBAL; unknown currencies are
ignored. Results sort by release time, jurisdiction and title; equal capture
times prefer the lexicographically first filename. The reader only reads
local files and opens no database or network connection. Display these
literals as **ForexFactory forecast**, with capture time. They remain
ineligible for surprise calculations (`surprise_eligible=False` in the hub's
provider policy); the record does not add a consensus or surprise field.
