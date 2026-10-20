# Independent review round 1 — explicit-only period boundary

This supersedes the inference rules in the historical [first-pass investigation](worklist-period-formats-report.md). Rule version remains `legacy-worklist-v3`; no catalogue metadata, stored values, production state or final legacy dispositions are rewritten.

## Changes, tests and item impact

| Finding | Correction and regression evidence | Production-copy impact |
| --- | --- | --- |
| 1. Loose window keywords | Measure and definition are separate arguments. `catalogue_windows` requires a bounded measure-window label or an anchored definitional phrase, such as `Cumulative year-to-date`, `3-month moving average`, change over the latest three months against the previous three, or `reported as a rolling 3-month total`. Negative tests reject `ytd also shown`, comparisons against 13 months and incidental three-month mentions in either field. Mixed window/month tests cannot map and retain a pending window reason or terminate on conflicting explicit periods. | No actual window mapping changes: the retained catalogue definitions explicitly define their windows. |
| 2. Frequency-only quarter conversion | A bare `Apr-Jun 2026` on Q stays `period_is_rolling_window` unless the catalogue explicitly states that the reference period/release is the calendar quarter. An agreeing named quarter, e.g. `(Q2 2026)`, is cross-checked against the span. Tests reject a plain GDP definition, accept the exact calendar-quarter definition, and retain matching/mismatching annotation tests. | No current items change. UK GDP items 176 and 177 explicitly name Q2 in their text and remain mapped. |
| 3. Survey month interpreted as quarter | Quarterly `<Month> <Year> Survey` always returns `period_survey_quarter_unspecified`, irrespective of a survey keyword or quarterly frequency. Explicit quarter spellings remain supported. Tests cover September variants, a business survey and a next-quarter outlook. | Six of the original 100 change from mapped to pending; listed below. |
| 4. Full event date collapsed to month | Single dates retain their full date even with meeting/minutes definitions, `As on`, or explicit meeting/decision suffixes. Tests cover E and IRREGULAR, plain and prefixed dates, and a month/date conflict. Explicit day ranges retain the prior named-month rule. | Items 24 (Banxico) and 510 (NBP minutes) retain complete status with periods 2026-09-24 and 2026-09-09, respectively, instead of 2026-09. No item becomes pending from this finding. |

The shared v2 parser is restored verbatim from `origin/main`, including its original formatting. The comma-month repair now lives only in `period_evidence`; a regression proves v2 `explicit_period("September, 2026", "M")` rejects it while v3 accepts it. This also removes the earlier formatting-only rewrite of the v2 section. New v3 code is range-formatted; the updated existing callers are formatted only at the changed lines.

## Positive expectations changed

- `test_quarterly_survey_names_month_or_quarter` becomes `test_quarterly_survey_names_quarter`: survey-month-only variants move to negative tests, while explicitly named quarters stay positive.
- `test_event_dates_name_the_month` no longer expects single dates (`9 September 2026`, its meeting suffix, or `September 24, 2026 decision`) to become months. Those cases move to full-date tests. Month labels and valid day ranges keep their existing expectations.
- The rolling-window positive supplies `GDP 3-Month Average (3M/3M)` as the **measure** field rather than embedding a title anywhere in the definition.
- The read-overlay/revert integration fixture now uses two explicit descriptions of the same full date and expects `2026-09-09`, rather than asserting that a day and a month are equivalent.

## Fresh-copy dry run

A new SQLite backup was made with a read-only source URI (`mode=ro`) for `C:/ProgramData/InvestmentCommittee/live/db/macro_releases.sqlite`, writing only `.tmp/macro_round1.sqlite`. Both pre-review bfe09c0 and revised v3 previews use **2026-10-20T06:54:15.824936+00:00**, a 14-day lateness window, and this same untouched fresh backup. Each CLI preview verifies the copy's SHA-256 before and after. Source production state still has 698 pending items; time-dependent processing is held constant between the two previews.

Backup SHA-256: `5a3a3fed4cd95d7bd720ddc30fc19dd206b3ed217b6716e7a6c17d92bd549add`. Raw JSON is retained locally in `.tmp/round1-before.json` and `.tmp/round1-after.json`, with cutoff `.tmp/round1-cutoff.txt`.

| Pending reason | Pre-review v3 | Revised v3 |
| --- | ---: | ---: |
| `evidence_horizon_before_deadline` | 76 | 76 |
| `overdue` | 50 | 50 |
| `period_fiscal_year_ambiguous` | 1 | 1 |
| `period_format_unrecognised` | 7 | 7 |
| `period_frequency_mismatch` | 27 | 27 |
| `period_is_rolling_window` | 6 | 6 |
| `period_survey_quarter_unspecified` | 0 | 6 |
| `period_year_missing` | 12 | 12 |
| `release_in_future` | 263 | 263 |
| **Total pending** | **442** | **448** |

The revised preview has 448 pending, including 263 future releases and 185 non-future items. The total 200-pending contract is still not closed. Completion totals change from 710 to 704; terminal totals remain 2,088. `period_format_unrecognised` remains seven, with exactly the raw text listed in the first-pass report.

**6 of the original 100 move from mapped to pending.** Original-item outcomes are now 71 complete, 25 pending and four terminal.

| Item | Indicator | Raw period text | New reason |
| --- | --- | --- | --- |
| `116` | `lazycommittee_calendar:jp_tankan` | `Sep 2026 Survey` | `period_survey_quarter_unspecified` |
| `117` | `lazycommittee_calendar:jp_tankan_nonmfg` | `Sep 2026 Survey` | `period_survey_quarter_unspecified` |
| `145` | `lazycommittee_calendar:jp_tankan` | `Q3 2026 (Sep survey)`; `Sep 2026 survey` | `period_survey_quarter_unspecified` |
| `146` | `lazycommittee_calendar:jp_tankan_nonmfg` | `Q3 2026 (Sep survey)`; `Sep 2026 survey` | `period_survey_quarter_unspecified` |
| `519` | `lazycommittee_calendar:jp_boj_opinion_survey_di` | `September 2026 Survey` | `period_survey_quarter_unspecified` |
| `587` | `lazycommittee_calendar:jp_boj_opinion_survey_outlook_di` | `September 2026 Survey` | `period_survey_quarter_unspecified` |

Items 145 and 146 contain both a named quarter and a separate survey-month reading. The latter may describe an outlook for another quarter; revised v3 does not silently relate the two readings and keeps these items pending too.

## Verification and limits

- Focused worklist tests: 200 passed before the final full-suite run.
- Full suite: `C:/ProgramData/spyder-6/python.exe -m pytest -q --basetemp=.tmp/pytest-periods` — **1,685 passed, one skipped**, one existing ALFRED fixture warning, in 733.99 seconds. The pytest temp directory was deleted after verification.
- `ruff check .` passes. V3 parser additions are checked with `ruff format --check --range=112-`; the new test file passes full formatting, and only changed call lines are formatted in existing files. The v2 parser prefix is identical to the original UTF-8 source after Git line-ending normalization.
- No production apply/rerun, catalogue edits, scheduled tasks, `gh`, PR creation or main-branch push. No future-release closure or production contract verification is claimed.
