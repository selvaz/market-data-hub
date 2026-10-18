# ALFRED response fixtures

Captured with `requests` on 2026-10-18. No credentials or request URLs are stored.
Both calls use `fred/series/observations`, JSON, `output_type=1`, `limit=1000`,
`realtime_start=2024-01-01`, `realtime_end=2024-04-30`.

* `payroll.json`: PAYEMS, observation dates 2023-11-01 through 2024-01-01;
  nine real-time intervals demonstrate successive monthly payroll revisions.
* `no_revisions.json`: USREC, observation dates 2020-01-01 through 2020-03-01;
  three unchanged observations across this real-time window. This fixture
  describes that window, not a claim that recession labels are never revised.

Tests simulate pagination, interruptions, withdrawals, HTTP failures, and
incremental releases around these captured payloads, without any live network.
