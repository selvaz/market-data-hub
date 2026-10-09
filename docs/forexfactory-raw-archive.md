# Forex Factory raw archive

Run from this checkout with the approved Python interpreter:

```powershell
& C:/ProgramData/spyder-6/python.exe run_econ_calendar.py --archive-only
```

The default destination is `C:\ProgramData\InvestmentCommittee\econ_calendar\raw`.
Override it with `--raw-dir <directory>`. No `--db` is needed. Archive mode
returns before the ingestion path, opens no DB, takes no `market_data` lock,
and performs exactly one GET of `ff_calendar_thisweek.json` (30-second timeout,
no retries). It preserves the response body bytes without parsing or formatting.

The local fetch date names the first snapshot `YYYY-MM-DD.json`. A changed
response is written as `YYYY-MM-DD.HHMMSS.json`; subsequent runs compare with
the latest snapshot for that date. An identical response creates no file.
Existing snapshots are never replaced. If a different response collides with
an existing timestamp in the same second, the run reports a fault; retry later.
On the supported Windows host, rename also refuses a destination created by
a concurrent writer.

Writes use a temporary file in the destination directory, flush and fsync it,
close it, then rename it. A failed write or rename removes the temporary file.
A fetch failure creates no archive file. Exit codes are `0` written, `3`
unchanged, and `1` fetch or filesystem failure; callers must preserve them.

Nightly scheduling is an operator responsibility. This change does not
register or modify any scheduled task. Configure the operator's task to invoke
the command above directly with this checkout as its working directory.
