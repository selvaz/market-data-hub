# M2 consensus capture: production-copy validation, 20 October 2026

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
