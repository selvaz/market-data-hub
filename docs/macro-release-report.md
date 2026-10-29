# Daily macro release report

`run_macro_release_report.py` produces HTML and a JSON file beside it, using only
the hub's existing `macro_releases` SQLite database. It makes no downloads and uses
no LLM. Telegram sending is off unless `--telegram` is supplied.

```powershell
python run_macro_release_report.py --db D:/Temp/mdh-relreport/macro_releases.sqlite --out-dir reports/macro_releases
```

Options:

| Option | Default | Meaning |
| --- | --- | --- |
| `--db` | `MACRO_RELEASES_DB` environment variable | Existing macro SQLite database; an explicit path or this variable is required |
| `--out-dir` | Hub's configured reports directory, under `macro_releases/` | Directory for the HTML and JSON |
| `--since-hours` | `24` | Positive, finite duration of the rolling first-seen window |
| `--telegram` | Off | Send the saved HTML using `send_telegram_run_report.send_report_document` |

Files have the same timestamped basename: `macro_release_report_YYYYMMDD_HHMMSS_ffffff`.
They are always written before a send is attempted. Telegram uses the existing
`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` environment variables and the hub's
optional LazyTools Telegram connector. A missing token, missing connector or failed
send returns 1, leaving the local artifacts available for inspection and retry.

## Selection and content

The single run timestamp is the UTC knowledge cutoff and window end. A default run
selects first captures in `[run time − 24 hours, run time)`: the lower bound is
included and the upper bound is excluded. The read API's
`ReadAPI.first_seen_release_cards(since, until, as_known_at=...)` assembles the
cards and latest family outcomes from one knowledge-filtered read snapshot.
The SQLite connection uses `mode=ro` and `query_only`; no migration or database
creation is attempted. The read API never downloads issuer documents.

Selection uses the earliest verified issuer actual for a headline
`(release event, indicator, reference period, stage)`, not publication time or the
latest download time. An older publication collected for the first time today is
included. A repeat fetch or correction to an already captured release does not
announce it again. Resolution uses only evidence known at the run timestamp;
values collected later cannot leak into an earlier report.

The existing publication-card resolver supplies the actual, previous value,
publication precision and forecast eligibility. Provisional, disputed and
redistributor readings are excluded. Verified private issuers such as NAR retain
their `private` label. A missing previous value or document is explicit; dates
with no exact publication time retain the `date only` label. Stored units and
scales are displayed explicitly (for example, `28 × 1000 persons`).

Groups appear in this order, when populated:

1. Central banks (all policy decisions, including BCB and Banxico).
2. USA.
3. Euro area and its countries (the original geography code is retained).
4. UK.
5. Japan.
6. China, including the stored city tiers.
7. Canada.
8. Others, retaining each geography code.

Each release shows its name and identifier, reference period, official value and
unit/scale, previous value, publication time, issuer and issuer-document link.
Pre-release Forex Factory readings accepted by the read API appear as **FF
forecast**, with `kind: ff_forecast` and `surprise_eligible: false` in JSON.
They are never presented as consensus or used to calculate a surprise.

Each figure is followed by its compact [indicator card](indicator-cards.md),
resolved with `indicator_card("cat:" + indicator_id)` using the same catalogue
lookup as other consumers. The package's `render_card` preserves the approved
timing class, reference and evidence caveats; all card text is HTML-escaped.
JSON stores the full card under `indicator_card` (including `key`, `timing.class`,
rationale and reference notes) and its compact text under `indicator_card_text`.
An unmapped release stores null for both fields and displays **no card yet**.
The summary includes `indicator_card_count`, `missing_indicator_card_count` and
`missing_indicator_card_ids`. Counts are per release, even if releases share a
concept card; the missing-id list is sorted and unique. No replacement context
is inferred for missing cards.

The **Needs review / failed** section lists the latest saved
`collect_run_families` outcome per family at the knowledge cutoff, with status,
time and recorded reason. A later successful result clears an earlier failure.
Both `ok` and `no_change` are healthy; other statuses are listed. This section is
independent of the release window: a collection failure records a failed attempt,
not proof that a release was due. Persisted collection-history limitations are
included in the JSON and HTML.

If no release qualifies, both artifacts explicitly say:
“No official releases were first seen in this window.” Collection issues, if any,
are still listed, and `--telegram` still sends that informative HTML.

| Exit | Meaning |
| --- | --- |
| `0` | Releases written, and sent if requested |
| `3` | No releases in the window; files still written, and sent if requested |
| `1` | Invalid arguments, database/output error or Telegram failure |

## Future job cutover

This change does not alter investmentcommittee or any scheduled task. After
deploying a release containing this script into the marketdata runtime, the
replacement job command is:

```powershell
& 'C:\ProgramData\InvestmentCommittee\envs\marketdata\Scripts\python.exe' 'C:\Users\Administrator\Documents\GitHub\market-data-hub-runtime-marketdata\run_macro_release_report.py' --db 'C:\ProgramData\InvestmentCommittee\live\db\macro_releases.sqlite' --out-dir 'C:\ProgramData\InvestmentCommittee\live\reports\macro_releases' --since-hours 24 --telegram
```

The job must receive the existing Telegram environment variables and accept exit
codes 0 and 3. Rewiring and deploying this command are separate production steps.

## Offline validation

```powershell
& C:\ProgramData\spyder-6\python.exe -m pytest tests/test_macro_release_report.py tests/test_macro_read_api.py tests/test_indicator_cards.py --basetemp=D:/Temp/mdh-relreport -q
& C:\ProgramData\spyder-6\python.exe -m ruff check .
```

Copy a live database for manual inspection **after** running pytest, since pytest
clears its base temporary directory. Include a stable committed WAL when present;
never copy live SHM locking state. Render the copy without `--telegram`.
