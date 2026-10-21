# M2 round 2: issuer-event linking, 20 October 2026

The FF reference-period-text requirement is removed. Forecasts now inherit the
period, stage and release identity of exactly one stored issuer event whose
scheduled or actual date matches in the issuer's time zone. **The fresh-copy
backfill still inserts zero readings because the two manifest-backed snapshots
cover a week with no issuer events in this store.** The implementation is
validated with stored-event fixtures; a real populated 19-23 October card remains
unverified.

## Round-2 source inspection

The round-1 copy was preserved as `.tmp/macro_copy_round1.sqlite`. A new SQLite
backup from the production database, opened with `mode=ro` and `query_only`,
replaced `.tmp/macro_copy.sqlite`. No production write or network request was
performed. No issuer schedule, release event or reading was synthesized to make
the copy validation pass.

| Table | Fresh copy before | After backfill |
| --- | ---: | ---: |
| consensus_readings | 0 | 0 |
| release_events | 18 | 18 |
| schedule_versions | 0 | 0 |
| readings | 20,790 | 20,790 |
| value_vintages | 396 | 396 |

There are still 821 indicators and 2,108 imported aliases. Catalogue coverage is
distinct from release-event coverage. The latest dated issuer event is final
EA HICP on 16 October; the opaque ONS `time-series:69` edition is a historical
dataset publication, not an upcoming October calendar. There are no stored issuer
release dates for 19-23 October. A `release_events` row and schedule alone also
contain no indicator/period columns: explicit issuer reading/vintage membership
is needed to attach a forecast's reference period. No publication lag is inferred.

## Round-2 counts and comparison

| Measure | Round 1 | Round 2 |
| --- | ---: | ---: |
| Snapshots inspected | 11 | 11 |
| FF event occurrences | 1,110 | 1,110 |
| Exact country/title matches | 118 | 118 |
| Inserted readings | 0 | 0 |
| Unmapped | 992 | 992 |
| Missing forecast | 9 | 9 |
| Period unknown from FF | 109 | 0 |
| Missing manifest capture | masked by period gate | 95 |
| Issuer release event not found | masked by period gate | 14 |
| Ambiguous issuer event / stage conflict | 0 / 0 | 0 / 0 |

Primary skip reasons are mutually exclusive. The 95 missing-capture occurrences
are mapped, forecast-bearing events in the nine files without manifest evidence;
other occurrences in those files are counted as unmapped or forecast-missing.
Every event in all nine files remains ineligible for insertion. Round 2 neither
uses their mtimes nor changes the archive.

| Snapshot day | Inserted | Missing capture | Event not found | Missing forecast | Unmapped | Total skipped |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2026-10-09 | 0 | 9 | 0 | 1 | 73 | 83 |
| 2026-10-10 | 0 | 9 | 0 | 1 | 73 | 83 |
| 2026-10-11 | 0 | 11 | 0 | 1 | 97 | 109 |
| 2026-10-12 | 0 | 11 | 0 | 1 | 97 | 109 |
| 2026-10-13 | 0 | 11 | 0 | 1 | 98 | 110 |
| 2026-10-14 | 0 | 11 | 0 | 1 | 99 | 111 |
| 2026-10-15 | 0 | 11 | 0 | 1 | 99 | 111 |
| 2026-10-16 | 0 | 11 | 0 | 1 | 99 | 111 |
| 2026-10-17 | 0 | 11 | 0 | 1 | 99 | 111 |
| 2026-10-18 | 0 | 0 | 7 | 0 | 79 | 86 |
| 2026-10-19 | 0 | 0 | 7 | 0 | 79 | 86 |
| **Total** | **0** | **95** | **14** | **9** | **992** | **1,110** |

The manifest-backed not-found titles include UK Claimant Count Change,
Unemployment Rate and Public Sector Net Borrowing, plus US Building Permits and
Housing Starts (two occurrences each). None has a stored issuer event for its FF
release date. Missing events are counted even if an imported alias has an unknown
stage; a unique issuer event would supply its known stage. No new aliases were
added in round 2; the six reviewed mappings from round 1 are unchanged.

A second normal ingest bypassed all 11 snapshots and inserted zero. Forced
reprocessing also inserted zero. The run had no snapshot failures and passed
`PRAGMA foreign_key_check`. Checkpoints include an issuer-index fingerprint so
new authoritative schedule/period metadata can revisit not-found skips. Existing
readings retain their original cutoff metadata; retries cannot rewrite them.

## Round-2 examples and verification limits

The actual query `release_cards('2026-10-19', '2026-10-23')` on the fresh copy
returned **zero cards**. There is no real `ff_forecast` consensus section to
show for that week. The three historical cards reported below still have empty
consensus sections because all corresponding raw snapshots lack capture records.
Recovering a reference period from their stored issuer event does not repair a
missing capture time.

The three real historical API cards were rechecked on the refreshed copy during
the resumed validation:

| Release | Indicator | Issuer reference period | Actual | API consensus | Surprise |
| --- | --- | --- | ---: | --- | --- |
| 14 October | US headline CPI y/y | 2026-09 | 3.7% | `[]` | `null` |
| 15 October | UK GDP m/m | 2026-08 | 0.1% | `[]` | `null` |
| 16 October | EA21 final headline HICP y/y | 2026-09 | 3.8% | `[]` | `null` |

The new linker was also run read-only on three actual FF events from the
12 October snapshot. All three found exactly one issuer event, without an FF
period label. This confirms the replacement association rule works on the real
archive where issuer metadata exists:

| FF event / issuer-local date | Stored release_event_id | Inherited period | Stage | Insertion blocker |
| --- | --- | --- | --- | --- |
| USD CPI y/y / 14 October | BLS:us_cpi:regular:2026-10-14 | 2026-09 | regular | no manifest capture |
| GBP GDP m/m / 15 October | ONS:uk_gdp:regular:2026-10-15 | 2026-08 | regular | no manifest capture |
| EUR Final CPI y/y / 16 October | Eurostat:ea_hicp:final:2026-10-16 | 2026-09 | final | no manifest capture |

Their stored issuer cutoffs are respectively 14 October 12:30 UTC (`exact`),
14 October 23:00 UTC (start of 15 October in London, `date_only`) and
15 October 22:00 UTC (start of 16 October in Brussels, `date_only`). No readings
were inserted during this read-only matching check.

The offline scheduled-release test stores an explicit issuer period membership
without an actual and an official schedule, then ingests a plain `CPI y/y` FF
event with no period label. Its API card contains:

```json
{
  "reference_period": "2026-09",
  "stage": "regular",
  "status": "scheduled",
  "resolution": null,
  "actual": null,
  "surprise": null,
  "consensus": [{
    "provider": "ff_forecast",
    "kind": "ff_forecast",
    "normalized_value": 3.6,
    "surprise": null
  }]
}
```

This is explicitly a synthetic fixture, not a production-copy example. Forecast
cards require official issuer period membership and a schedule; they never count
as captured numeric releases in coverage. Other tests cover issuer-local date
conversion (including naive FF Eastern wall times), actual vs scheduled cutoffs,
revision periods, unknown/ambiguous event skips, Final-vs-flash stage conflicts,
provider separation, post-release exclusion and automatic retry after new issuer
metadata. The final targeted ingestion, read-API, consensus-time and daily-runner
run passed **141 tests**. The final full suite passed **1,728 tests, 1 skipped**
(the optional `.tmp/prod-copy.sqlite` integration test), with one expected ALFRED
fixture warning about truncated vendor vintage dates. Both runs used the requested
`--basetemp=.tmp\pytest-m2`. `ruff check .`, changed-range formatting,
`git diff --check` and byte compilation passed. `.tmp/pytest-m2` was deleted after
the full suite, and its absence was verified.

After the interrupted turn was resumed, the six-file diff was inspected and
the production copy was refreshed again with SQLite's backup API. All per-day
counts and the three historical cards above were unchanged. The test results
above are from this resumed validation, including the final post-release guard.

Local round-2 results and reproduction script are
`.tmp/m2-r2-validation.json` and `.tmp/validate_m2_r2.py`. The production database,
scheduled tasks and archive writer were untouched. Deployment, a live scheduled
run, and real populated forecast cards remain unverified; the missing input is
issuer event/schedule metadata for the manifest-backed week.

---

# Round 1 historical report (superseded period policy)

The remainder preserves the round-1 evidence and validation at `af16f18`.
Its FF-period-text requirement and remaining-evidence conclusions are historical;
round 2 above replaces that association policy.

The offline ingestion path, daily-run integration and separate run log are
implemented. **The existing archive cannot populate eligible historical
consensus under the brief's evidence constraints.** This report records the
zero-row outcome rather than treating the requested three populated cards as
verified.

## Scope and evidence

- Source database: `C:\ProgramData\InvestmentCommittee\live\db\macro_releases.sqlite`,
  opened with SQLite `mode=ro` and copied with the SQLite backup API into
  `.tmp/macro_copy.sqlite`. The copy initially contained 821 indicators,
  2,108 imported aliases and zero consensus readings, at schema v5.
- Raw archive: `C:\ProgramData\InvestmentCommittee\econ_calendar\raw`, read only.
  Eleven snapshot files exist for 9–19 October; no 8 October file was present.
  The manifest contains only two records, for 18 and 19 October. Both recorded
  SHA-256 hashes match their files. Older snapshot mtimes do not establish
  capture times and were never written into a consensus row.
- Manifest captures: `2026-10-18T21:30:05.880813Z` and
  `2026-10-19T21:30:07.510648Z`. Their event windows are 18–24 October, so they
  supply no pre-release evidence for the requested 12–17 October week.
- None of the mapped forecasts has an explicit reference-period field or fully
  dated title qualifier. Frequency alone does not identify the publication lag.
  No official values, release dates or file mtimes were substituted for those
  missing facts. The archive writer, scheduler and production database were
  not changed. No network requests were made for this validation.

## Backfill coverage

Counts below are event occurrences across snapshots, not distinct releases.
“Mapped” means an exact country/title catalogue or proposed-alias match. It
does not imply that period, stage or capture provenance is usable. Every
occurrence was skipped for insertion; mapped and skipped therefore overlap.

| Snapshot day | Events | Exact title mapped | Unmapped | Skipped for insertion | Rows inserted | Capture evidence |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 2026-10-09 | 83 | 10 | 73 | 83 | 0 | no manifest |
| 2026-10-10 | 83 | 10 | 73 | 83 | 0 | no manifest |
| 2026-10-11 | 109 | 12 | 97 | 109 | 0 | no manifest |
| 2026-10-12 | 109 | 12 | 97 | 109 | 0 | no manifest |
| 2026-10-13 | 110 | 12 | 98 | 110 | 0 | no manifest |
| 2026-10-14 | 111 | 12 | 99 | 111 | 0 | no manifest |
| 2026-10-15 | 111 | 12 | 99 | 111 | 0 | no manifest |
| 2026-10-16 | 111 | 12 | 99 | 111 | 0 | no manifest |
| 2026-10-17 | 111 | 12 | 99 | 111 | 0 | no manifest |
| 2026-10-18 | 86 | 7 | 79 | 86 | 0 | manifest |
| 2026-10-19 | 86 | 7 | 79 | 86 | 0 | manifest |
| **Total** | **1,110** | **118** | **992** | **1,110** | **0** | **2/11 files** |

Primary skip reasons: `unmapped` **992**, `period_unknown` **109**,
`forecast_missing` **9**. Reasons are evaluated in that order of applicable
mapping/forecast/period gates, so `period_unknown` can coexist with an absent
manifest. The snapshot diagnostics also retain `captured_at: null` and
`capture_source: mtime` for all nine files without capture provenance.

Top skipped titles for the requested currencies, including nonnumeric events:

| Currency | Title | Occurrences | Primary reason |
| --- | --- | ---: | --- |
| USD | FOMC Member Bowman Speaks | 16 | unmapped |
| USD | FOMC Member Barkin Speaks | 14 | unmapped |
| USD | FOMC Member Hammack Speaks | 14 | unmapped |
| USD | ADP Weekly Employment Change | 13 | unmapped |
| USD | Unemployment Claims | 11 | unmapped |
| EUR | German Buba President Nagel Speaks | 18 | unmapped |
| EUR | ECB President Lagarde Speaks | 14 | unmapped |
| EUR | EU Economic Summit | 10 | unmapped |
| GBP | BOE Gov Bailey Speaks | 32 | unmapped |
| GBP | MPC Member Breeden Speaks | 21 | unmapped |
| GBP | MPC Member Greene Speaks | 18 | unmapped |
| GBP | MPC Member Pill Speaks | 16 | unmapped |

Forecast-bearing titles also skipped include all four US CPI titles, UK
`GDP m/m` and EUR `Final CPI y/y`, seven occurrences each, because their periods
are absent. Unmapped forecast-bearing titles include USD `Unemployment Claims`
(11), `NFIB Small Business Index` (7), `Existing Home Sales` (7), GBP
`BRC Retail Sales Monitor y/y` (7), `Construction Output m/m` (7), EUR
`German Final CPI m/m` (7), `German WPI m/m` (7) and `French Final CPI m/m` (7).
Member-state CPI is deliberately not assigned to euro-area HICP.

## Three real read-API cards

Query: `release_cards('2026-10-12', '2026-10-17', indicators=[
'us_cpi_headline_yy', 'uk_gdp_mm', 'ea21_hicp_yy'])` on the backfilled copy.
The API returns these three cards as `official`:

| Release date | Indicator | Official reference period | Stage | Actual | API consensus | API surprise |
| --- | --- | --- | --- | ---: | --- | --- |
| 14 October, 12:30 UTC | US headline CPI y/y | 2026-09 | regular | 3.7% | `[]` | `null` |
| 15 October, date only | UK GDP m/m | 2026-08 | regular | 0.1% | `[]` | `null` |
| 16 October, date only | EA21 headline HICP y/y | 2026-09 | final | 3.8% | `[]` | `null` |

For source inspection only, the 12 October raw file contains `CPI y/y` with
`forecast: "3.6%"` scheduled on 14 October; `GDP m/m` with `forecast: "-0.1%"`
scheduled on 15 October; and `Final CPI y/y` with `forecast: "3.8%"` scheduled
on 16 October. These raw prints are **ineligible evidence**: no manifest capture
and no explicit reference period. They are not API consensus and produce no
surprise. The requested actual-plus-eligible-forecast examples remain unverified.

## Proposed aliases

The versioned `market_data_hub/macro_releases/ff_aliases_v1.json` supplements
stored aliases without rewriting them. All six targets already exist in the
tier-1 catalogue; each entry includes its title justification. Tests validate
the scope, currency, geography, exact title and explicit stage rules.

| Currency / exact title | Target | Reason |
| --- | --- | --- |
| USD / CPI m/m | us_cpi_headline_mm | Headline US CPI monthly percentage change. |
| USD / CPI y/y | us_cpi_headline_yy | Headline US CPI annual percentage change. |
| USD / Core CPI m/m | us_cpi_core_mm | Explicit core and monthly qualifiers. |
| USD / Core CPI y/y | us_cpi_core_yy | Explicit core and annual qualifiers. |
| GBP / GDP m/m | uk_gdp_mm | Monthly GDP growth, distinct from quarterly or rolling measures. |
| EUR / Final CPI y/y | ea21_hicp_yy | Final euro-area headline HICP, restricted to explicit EA21 periods from 2026-01. |

## Runner validation

The copy was backed up into `.tmp/macro_runner_copy.sqlite`. A local offline
replay reconstructed observations from its stored official vintages and raw
documents; `requests.Session.request` was patched to reject every request.
The same `run_daily.execute(..., dry_run=True)` path ran first with an empty
archive and then with the actual archive. The second preview used `reprocess`
inside the private preview solely to reproduce full backfill diagnostics after
the copy had already been checkpointed.

All five family results were identical between the two previews. Official run
status stayed `no_change`, exit code **3**, with zero added official vintages.
Replay counts: US CPI 270, payrolls 24, GDP 7, EA HICP 48, UK GDP 21.
GDP replay added seven readings in memory because reconstructed provenance was
distinct; it added no vintages and occurred identically in both previews.
All copy-file hashes before/after the dry runs matched. Consensus reported
11 processed snapshots, 118 mapped events, 1,110 skipped events, zero inserted
readings and no snapshot failures. A subsequent non-dry offline replay on the
second disposable copy recorded its independent `collect_run_consensus`
`no_change` result, bypassing all 11 previously checkpointed snapshots.

Focused tests exercise actual and dry daily-run paths, same-lease ordering,
separate failure status, exact/post/date-only cutoffs, manifest capture
microseconds, hash checks, snapshot rollback/retry, idempotency and provider
separation. Validation completed:

- Full suite: `pytest -q --basetemp=.tmp\pytest-m2 --tb=short` — **1,717 passed,
  1 skipped** (the optional `.tmp/prod-copy.sqlite` integration test).
- After the final recovery/cutoff edits: consensus ingestion and daily-runner
  tests — **53 passed**, with the same requested basetemp.
- `ruff check .`, changed-range formatting and `git diff --check` passed.
- A wheel built with `--no-isolation`; its ZIP contains the consensus module,
  versioned alias JSON and v6 migration. No dependency download was needed.
- `.tmp/pytest-m2` was removed after the final test run.

## Remaining evidence limits

No production deployment, scheduled run, current official source fetch or
historical capture reconstruction was attempted. The normal official collector
retains its existing network behavior; the new consensus step has none.
Eligible real cards require authoritative capture records and explicit FF
reference periods, or a separately approved change to the period-evidence
policy. A recovered manifest alone does not solve the missing-period problem.
After a reviewed catalogue change, `--reprocess` revisits stored skips;
new manifest evidence automatically changes a missing-capture snapshot's key.

Local detailed artifacts are `.tmp/m2-backfill.json`, `.tmp/m2-cards.json`,
`.tmp/m2-runner-preview.json`, `.tmp/m2-runner-logged.json` and the offline replay
script `.tmp/validate_m2.py`. They are deliberately excluded from Git, along
with the SQLite copies. The tracked report contains the material results.
