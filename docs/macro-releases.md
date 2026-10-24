# Macro release contracts (M4)

`market_data_hub.macro_releases` owns a separate SQLite publication and evidence
store. M4 supplies contracts, migrations, a small data-access API, and a read-only
legacy importer. M5 adds manual collectors; M6 adds the resolver and read API in
shadow mode; M7a adds an explicit-path production daily CLI. LazyCommittee
consumes `ReadAPI.release_cards` through `macro_cards.read_cards(db, as_of, 7)`.

## Database and access

Deployment path (always supplied explicitly):
`C:\ProgramData\InvestmentCommittee\live\db\macro_releases.sqlite`.
The orchestrator will declare `MACRO_RELEASES_DB` in investmentcommittee's
`config/databases.toml`. This change does not declare it, create a live database,
or change scheduled tasks. The library requires an explicit path and has no
production default.

```python
from market_data_hub.macro_releases import open_reader, open_writer
from market_data_hub.macro_releases.legacy import import_calendar

# Example scratch files; provision/deployment is a separate operation.
with open_writer("scratch/macro_releases.sqlite") as writer:
    import_calendar(writer, "scratch/calendar.sqlite")

with open_reader("scratch/macro_releases.sqlite") as reader:
    first = reader.first_release()
    latest = reader.latest_official()
    known = reader.as_known_at("2026-10-16T23:59:59Z")
```

The writer configures WAL, `foreign_keys=ON`, and `busy_timeout=60000` ms.
A process-safe file lock permits one cooperating writer per database. The writer
holds the lock for its lifetime; SQLite transactions cover an individual
insert/worklist update or an explicit, fully fetched `Store.batch()`. Close it promptly. Never perform network/model calls
inside a transaction. The lock is released after a process crash by the OS.
The M7a daily runner adds an expiring run lease around these short writer
sessions; HTTP uses the provider-wide limiter described below.

Readers use SQLite URI `mode=ro`, plus `query_only=ON`; they neither migrate nor
create files. They read the live WAL normally (`immutable=1` is deliberately not
used). Query methods materialize their result and finish the read statement
before returning. Opening a missing file or unsupported schema fails explicitly.

`migrations.migrate` uses `PRAGMA user_version`, rejects newer schemas, and applies
DDL and version changes in one transaction. `schema.sql` is included in wheels.
Append-only triggers protect publication events, schedules, vintages, readings,
resolutions, consensus, claims/evidence, and discrepancies from update/delete.
Catalogue and worklist state are mutable.
Schema v2 migrates existing v1 stores transactionally, preserving raw evidence
and pending work while adding mutable legacy links and terminal worklist state.
It also corrects the derived `post_release` flag on old date-only consensus rows;
their original cutoff, precision, provider and capture timestamps remain intact.
Schema v3 adds `composition_id`, `issuer_local_time`, and `issuer_timezone` to
readings/vintages and tightens the placeholder views. Existing evidence remains
append-only; the known v2 tool-rendered BLS archive source is excluded from
official candidates without rewriting its rows.
Schema v4 adds the append-only daily run lifecycle and per-family outcomes;
it does not rewrite legacy readings, vintages, mapping links or worklists.
Schema v5 adds shared raw documents, the review queue and `needs_review` run
outcomes. Its migration preserves v4 run-log rows and all v3 evidence.
Schema v6 adds consensus snapshot checkpoints and separate consensus run
outcomes without rewriting existing evidence. The optional worklist resolver
creates its audit tables idempotently inside its own transaction on schema v5
or later. Ordinary `open_writer`/`migrate` calls do not create those audit tables.
The current reader floor is 3 and ceiling is 6. The worklist feature itself
does not change `user_version` or the compatibility registry.

`ResolutionVersion`, `Claim`, and `ClaimEvidence` require a caller-supplied
`idempotency_key`, unique within their table. A retry reuses its original row id;
reusing a key with a changed payload fails. A new decision or commentary version
needs a new key. These are storage contracts; M4 does not produce resolutions or
commentary automatically.

## Identities and contracts

Pydantic v2 contracts reject unknown fields, non-finite numeric values and naive
timestamps. Times are normalized to UTC ISO strings with fixed microsecond
precision. A date-only publication/schedule uses the start of the stated date in
UTC together with `date_only` precision; it does not assert an exact release
instant. When using an issuer-local date as a consensus cutoff, the caller must
convert the start of that local date to UTC and retain `date_only` precision.
Before that lower bound a capture is pre-release; on the release date its order
is uncertain, so both eligibility and `post_release` are false. Date-only
precision never sets `post_release` in M4, even for a later capture; M6 can
reconcile a subsequently known exact release time. If an 08:30 release time is
known, use `exact` and the full 08:30 timestamp with its issuer-local offset
(e.g. `08:30-04:00`, normalized to `12:30Z`). A 07:00 local snapshot is then
eligible. Do not substitute midnight for a known 08:30 time.

| Table | Identity and role |
| --- | --- |
| `indicators` | Stable id for the exact measure, geography, adjustment, transform, unit/scale, frequency, issuer, definition and tier. `measure_kind` can label a private survey. Catalogue classification and activation are explicit. |
| `source_series` | Indicator × source key, with issuer and distributor separately recorded, expected lag in seconds (null when unknown), published/derived flag and evidence role. The source key can encode a dataset and dimensions. |
| `release_events` | Stable id plus unique issuer × family × stage × edition. Expected timestamps never enter this identity. One event carries many indicators and reference periods, including revisions. |
| `schedule_versions` | Append-only expectations with UTC time, issuer's IANA time zone, exact/date-only precision, source and recorded time. A postponement adds a schedule version. |
| `value_vintages` | Indicator × known reference period attached to an issuer publication, mandatory stage, separate revision kind, value, printed text, unit/scale, publication precision, provider vintage id, first seen/retrieved times, document hash, parser and verification provenance. Derived figures can reference input vintage ids and a formula version in separate records. |
| `readings` | Every source observation, including duplicates, non-numeric prints, and disagreements; its evidence origin is independent of the searcher. Unknown reference periods require a reason; the original period key and payload remain available. |
| `resolution_versions` | Indicator × period × stage, exactly one selected reading or vintage, rejected alternatives, rule version, reason and decision time. M4 stores the contract; M6 will write decisions. |
| `consensus_readings` | Provider, optional survey identity, exact measure/period/stage, value and unit, published/captured times and the release cutoff used for eligibility. Providers stay separate. |
| `claims`, `claim_evidence` | Schema for release facts, attributed interpretations and market response. Evidence stores the verbatim passage, hash, URL, page id, origin, publication/retrieval times and news cutoff. New versions can supersede a claim. No commentary is generated in M4. |
| `discrepancies` | Reading linked to the selected official vintage, reason and recorded time. Adding a discrepancy cannot suppress an official value. |
| `legacy_candidates`, `indicator_aliases` | Every original code, raw catalogue metadata, aliases and conservative stage mapping; all remain candidates pending review. |
| `import_worklist` | Per-source occurrence backlog with reason, pending/complete/terminal status, first/last check and next check. No run watermark. |
| `worklist_resolutions`, `worklist_reverts` | Optional append-only run history, complete prior worklist state, selected vintage/evidence, and run-revert markers. The active view selects the latest non-reverted decision per item. |
| `legacy_reading_links` | Mutable links from raw legacy rows to the latest indicator, stage, occurrence, resolution metadata and run status. They never change a raw reading or its identity. |

An indicator's stage belongs to each observation/publication, not its measure
identity. For example HICP flash and final are one indicator. `revision_kind`
describes whether a value is initial, routine, benchmark, correction, unchanged,
etc.; it is independent of stage. Regular monthly series use `regular` as a stage;
GDP can use `advance`, `second`, and `third`. Unknown legacy stages are explicit
`unknown`. Event and value stages are separate because a publication can carry
previous periods alongside its headline estimate.

## Authority and vintage semantics

Authority follows the issuer of the exact measure and publication edition,
including private-survey issuers. It does not follow API/HTML/PDF format, URL
domain, or the number of searchers. A verified copy of an issuer publication may
be distributed by a mirror; record both parties. `evidence_role="issuer"` asserts
that the contents are that issuer publication. A mirror's own series observation
is `mirror`, even if its numeric extraction is verified. Web evidence is `web`.
The importer never makes the issuer-publication assertion for legacy web rows.

`Store.put` deduplicates readings on source, series, period key, edition and
content hash. Legacy hashes cover the complete source row envelope, source-table
name, value role and source index. Distinct legacy observations of the same figure
stay distinct readings, while reading the same row twice adds nothing. Official
adapters additionally skip unchanged semantic observations across document hashes,
as described in the daily collection section below.

Vintage retry identity is source series × period × release event × document hash;
source series identifies source and key, and the event identifies edition.
An unchanged value/unit/scale/stage within the same source series and event reuses the existing
vintage even if transport bytes changed. Every raw reread can still be stored in
`readings`. A publisher correction in that event creates a new vintage; a new
publication edition creates one even when the value is unchanged. Parser fixes
or a change of verification alone must not be presented as publisher revisions;
M6 will record verification/resolution evidence separately. Vintages are never
updated or deleted.
Re-reading an unchanged value with a different `verification_status` or
`extraction_method` raises an explicit conflict, for both same-document and
changed-byte retries. It cannot silently discard new verification evidence or
pretend that verification created a publisher revision.

The SQL views `first_release` and `latest_official`, and Python
`Store.as_known_at(t)`, are **M4 deterministic placeholders**, not the M6 resolver.
They consider verified, published issuer vintages whose issuer agrees with the
indicator and event. Results carry `selection_rule_version` with the explicit
`placeholder:m5-verified-issuer-v2` identifier. They order by issuer publication time, then first seen time
and row id for reproducibility. They do not infer authority from extraction
method. `first_release` selects the earliest proven `revision_kind='initial'`
publication per indicator/period; historical snapshots marked `backfill`, routine
revisions and benchmarks cannot masquerade as first prints. An absent initial
print yields no `first_release` row even when `latest_official` is available;
`latest_official` selects the latest across stages. All stages remain stored.
`as_known_at` filters `first_seen_at <= t` **before** selecting the latest
publication. Downloading an old publication later cannot leak into an earlier
knowledge snapshot or replace a newer publication.

These compatibility views do not resolve equally authoritative conflicts or
append `resolution_versions` implicitly. An empty list only means no qualifying
vintage in this M4 view. New consumers should use the M6 read API below, which
attaches coverage/failure/unresolved reasons.

Consensus is never averaged across providers. `Store.put(ConsensusReading(...))`
computes eligibility strictly as `captured_at < release_cutoff_at`; equality and
later captures are `post_release` only for an exact cutoff. Date-only cutoffs
are lower bounds with uncertain within-day order as described above. Unknown
cutoffs set both flags false. The
cutoff and its actual/scheduled basis and precision are preserved, rather than
recomputed from a later schedule version. Actual time should be supplied when
known. M6 will add provider-specific frozen pre-release selection and reconcile
newly learned release times; M4 does not claim an unverified historical forecast
was captured pre-release.

The numeric helper preserves percentage-point values, signs, decimal commas,
thousands grouping and explicit magnitude suffixes (including B/bn and T/tn);
currency symbols before or after a sign do not change its direction, and the raw print stays stored.
It requires a single numeric cell. Ambiguous commas need an explicit decimal
separator; it does not infer measures or calculate transformations from levels.
Period bases such as m/m, t/t, q/q, y/y and k/k are never magnitude suffixes.
A magnitude must end the suffix or be followed by whitespace; a slash cannot
terminate one. Bare m/t require `allow_bare_magnitudes=True`. The legacy importer
opts in only when the immutable raw print itself explicitly identifies currency;
otherwise an ambiguous bare m/t remains raw evidence without a numeric companion.

## Legacy pull and backlog

The legacy source is LazyCommittee's
`C:\ProgramData\InvestmentCommittee\lazycommittee\calendar.sqlite`, schema v4
defined in `src/lazycommittee/calendar/store.py`. `import_calendar` opens the
supplied source read-only and performs no DDL there. It does not attach or migrate
the source. M4 has been tested only against temporary fixtures.

Catalogue rows and aliases are retained as inactive candidates. Exact terminal
`(flash)`/`(final)` pairs merge only if the base name matches and the jurisdiction,
agency, unit, transform, basis, frequency and scale all match and are non-missing.
The two legacy codes and their stages remain in `legacy_candidates`. Other names,
ambiguous pairs and unresolved sightings stay candidates; no fuzzy resolver is
introduced. Saved code mappings remain stable if the source catalogue later grows;
reviewing a later proposed merge belongs to catalogue maintenance.

Raw per-searcher sightings become `web_search` / `unverified` readings, retaining
the actual/consensus/previous fields and cited sources. Their content hash covers
only the sighting id and the raw columns protected by the legacy trigger. Their
series key is based on sighting id, value role and source index. Mutable
`indicator_code`, `resolution`, `occurrence_id`, `unresolved_reason` and run status
never enter the hash or key. Those mappings live in `legacy_reading_links`, and
new raw sighting readings keep their indicator/stage unknown. Updating a legacy
resolution updates the link, without adding or rewriting raw evidence. Pre-fix
v1 sighting imports are recognized by their original raw row id and source index
and linked without adding another reading.

LazyCommittee observations are merged outputs: their actual can be a median,
and their consensus is a vote. Each value role is imported once as
`legacy_merged_actual`, `legacy_merged_consensus` or `legacy_merged_previous`, with
`extraction_method="legacy_merge"`, distributor LazyCommittee and verification
unverified. The complete cited-origin list remains in the payload; the aggregate
is never fanned out or attributed to each URL. Its identity hashes only the
append-only observation columns and uses its row id. `official_source=1` stays
raw metadata and never verifies either kind of evidence. No aggregate becomes
an official vintage or a provider-specific consensus reading.
The importer does not invent issuer events or publication timestamps from
scheduled/release dates. Legacy forecasts remain readings, since their original
provider/capture/cutoff eligibility is not reliably certified.
The legacy `previous` field has no explicit period or stage of its own: those
remain unknown, with a reason, rather than assigning the current period to it.

`first_seen_at` is when the new store imports the evidence. The run's original
`started_at` is stored explicitly as `legacy_first_seen_at` in the payload for
both sightings and merged observations, without backdating our knowledge.
`d:YYYY-MM-DD`, missing periods and unrecognised text have null reference period
and a stored reason. No release date is converted into a fake reference month.

Evidence origins prefer an explicitly supplied origin/wire id, then a normalized
URL (tracking parameters/fragments removed). Unattributed evidence shares an
`unknown` origin rather than gaining independence per searcher. Cross-URL
syndication without explicit origin metadata cannot be established by M4; M6
must handle that evidence conservatively.

Each pull rescans source rows idempotently, so a late observation in an older run
is found. The worklist tracks unlinked sightings, unknown periods, incomplete/overdue releases and
values first observed after release day. Completed occurrences close existing
backlog entries; late values remain pending review. It uses latest published-run
observation per occurrence to assess completeness. Running runs are deferred:
their sightings and observations do not enter `readings` until the run finishes.
Failed-run sightings remain unverified evidence, but their sighting work items
are terminal with no next check; repeated pulls do not requeue or advance that
terminal check time. A subsequent source transition to published can reopen or
complete the work item. The next pending check is proposed one day later; M4
does not schedule it.

### Resolving the imported worklist

Use an existing local copy, including its WAL, and retain a SQLite backup before
applying. The command requires `--db`, refuses any `ProgramData` path, performs no
network calls, and adds no aliases. The preserved `.tmp/macro_before.sqlite` was
made from the supplied worktree copy before the first apply; no production file
is opened. A fresh run deletes the previous `macro_copy.sqlite*` files and copies
that untouched v5 backup, preserving committed WAL data through SQLite backup.

```powershell
python -m market_data_hub.macro_releases resolve-worklist --db .tmp/macro_copy.sqlite --dry-run --as-of 2026-10-19T10:45:00Z --lateness-days 14
python -m market_data_hub.macro_releases resolve-worklist --db .tmp/macro_copy.sqlite --as-of 2026-10-19T10:45:00Z --lateness-days 14
```

`--as-of` defaults to now and cannot exceed current UTC time. A cutoff before
the last legacy import produces a warning and excludes later imported evidence.
`--lateness-days` defaults to 14 and has a minimum of 1; the chosen window is
recorded with every decision. JSON includes `run_id`, before/after counts,
changed-item counts per rule, evidence, and the final pending count. Exit 0 means
the pass succeeded, even if honest pending items exceed the acceptance target.
Invalid arguments, database errors and held leases/locks exit non-zero with a
message. No pending count target influences decisions.

Dry-run takes the daily runner's stable database/WAL snapshot and applies only
in memory, creating no files beside the target. Apply and revert acquire the
same `RunLease` and writer file lock as `run_daily`, refusing a live lease or held
writer lock. Writes occur in one short transaction. Optional audit tables/views
are created with `IF NOT EXISTS`; the worklist feature adds no schema version
of its own. Opening the writer first applies the current additive migrations
(currently v6), preserving old v5 worklist audit data.

Rules in `legacy-worklist-v3` (rules 1, 2 and 4–6 retain v2 behavior):

1. Use published-run evidence known at the cutoff. Missing/invalid release dates
   skip the item as pending `release_date_missing` / `release_date_invalid`.
   If the latest retained release date is after the cutoff date, leave pending
   as `release_in_future`, even when an actual, period or official vintage exists.
2. Resolve names using exact normalized spelling plus source/jurisdiction against
   the existing catalogue and aliases. Unmatched names terminate as
   `outside_catalogue_scope`; names found only under a different jurisdiction use
   `catalogue_jurisdiction_mismatch`; ambiguous identities use `indicator_ambiguous`.
   These reasons describe this catalogue's scope, not the existence of a statistic.
3. Parse fully explicit periods compatible with frequency: ISO, numeric `M/YYYY`,
   `Qn/YYYY`, ordinal/word/Roman quarters (including `2nd Quarter 2026`), localized
   month names, and fully dated `week ending ...` text. Three-digit ROC years
   convert by +1911 only with a ROC marker or Taiwan geography; compact `11509`
   requires Taiwan. Weekly/biweekly/fortnightly share date-period handling;
   event/aperiodic frequency aliases allow explicit event periods without inferring
   one from the release date. Neither a `d:` key nor a missing year supplies a
   reference period. Conflicts terminate as `conflicting_reference_periods`;
   non-empty unknown formats or incompatible granularity stay pending as
   `period_format_unrecognised` / `period_frequency_mismatch`. A truly absent or
   release-date-only period without an exact issuer match uses `period_not_derivable`.

   V3 adds these explicit spellings and evidence rules, based on the 19 October
   period inventory:

   - Ignore `legacy-occurrence:N`, literal `missing`, blank and `d:` labels as
     non-periods. A yearless month/quarter/week-ending label stays pending as
     `period_year_missing` unless its named components agree with one independently
     explicit period on the same item. This never supplies a year or resolves two
     conflicting explicit periods. Plain/localized months and `Sept.` with a year
     already worked in v2; their yearless companion labels were vetoing them.
   - V3 alone accepts the comma in `September, 2026` and the bounded qualifiers `(advance)`,
     `(P)` and `(prelim)`. Arbitrary trailing text and spelling corrections remain
     unsupported (`Septermber 2026` is not silently corrected).
   - `Sep 2026 Survey` / `September 2026 Survey` is a month for a monthly indicator.
     On quarterly indicators the survey month does not identify its reference
     quarter (it may concern a future outlook): keep pending as
     `period_survey_quarter_unspecified`. An independently named quarter in
     `Q3 2026 (Sep survey)` or `QIII 2026` is explicit. A separate unqualified
     survey-month reading still blocks an item, even with a quarter on another
     reading; their relationship is not inferred.
   - Meeting/MPC/FOMC/decision text explicitly naming a month/year, including
     `Sep 15-16, 2026 FOMC meeting`, `Meeting of 9-10 Sep 2026`,
     `FOMC meeting 15-16 Sep 2026`, `Oct 2026 MPC meeting (Oct 5-7)` and named
     date ranges, uses that **named event month** for irregular/event catalogue
     frequencies. Monthly policy/meeting/rate-decision indicators use that month
     too (e.g. Poland NBP). Full single dates always retain their day, including
     `9 September 2026 meeting` and `As on October 9, 2026` on meeting/minutes
     indicators. A date and a month are distinct periods and may conflict; a word
     in the definition never collapses their precision.
     Both range endpoints must be valid and ordered; parenthetical MPC dates must
     agree. Ordinary daily periods retain their precision. Meeting dates never
     supply a month from a numeric/release date. A monthly price index does not
     gain a reference month from meeting text. Day ranges normalize en/em dashes.
   - Weekly `As on October 2, 2026`, `semana al 9 de octubre de 2026` and
     `al 9 de octubre de 2026` retain the explicit date. For weekly/biweekly
     indicators whose catalogue explicitly measures the first half of a month,
     `Primera quincena de Septiembre de 2026` uses the period-end date
     `2026-09-15`. Without that definition use `period_is_submonthly_window`.
   - Cumulative ranges (`Jan-Aug 2026`, `January to September 2026`,
     `January-August 2026`, `First three quarters of 2026`) use a reporting-month
     index only for a monthly catalogue measure naming YTD/year-to-date as its
     window, or an anchored definitional phrase such as `Cumulative year-to-date`
     or a cumulative flow `from the start of the calendar year through the reporting
     period`. Measure and definition fields are checked separately, with word
     boundaries. Passing mentions such as `ytd also shown` do not qualify. They continue to
     represent cumulative measures; they are never converted into single-month
     values. Other definitions stay pending as `period_is_cumulative_window`.
   - Three-month ranges (`Jun-Aug 2026`, `three months to August 2026`,
     `3 months to Aug 2026`, `trimestre móvel Jun-Aug 2026`,
     `quarter ending/ended Aug 2026`) use a reporting-month index only when the
     monthly catalogue measure names a 3-month average/total/growth or 3M/3M,
     or the definition explicitly defines that window (e.g. `3-month moving
     average`, a change over the latest three months against the previous three,
     or `reported as a rolling 3-month total`). Comparators and incidental mentions
     do not qualify; `13 months` cannot match `3 months`. For quarterly indicators,
     a calendar-aligned range needs an agreeing quarter in its text, as in
     `Apr-Jun 2026 (Q2 2026)`, or the exact catalogue statement `The reference
     period is the calendar quarter` / `The release is a calendar quarter`.
     Frequency Q alone is insufficient. Other windows stay `period_is_rolling_window`;
     even a companion `8/2026` cannot override that incompatibility. Brazil
     unemployment's current definition does not document its rolling window,
     so it remains pending. A known incompatible window takes precedence over
     unknown text in the item's reason; changed decisions retain every raw failure
     in their audit evidence.
   - `Issue 1413` / `Issue Number 188` terminate as
     `period_is_document_identifier`, and `bond maturing 2028-11-01` as
     `period_is_maturity_date`. These are not observation periods. `2026-27`
     stays pending as `period_fiscal_year_ambiguous`: no fiscal convention or
     century expansion is invented. These pending reasons are reconsidered on
     later runs. No catalogue definitions or frequencies are modified.

   The [period-format investigation](worklist-period-formats-report.md) records
   every supplied shape, all 100 item outcomes and the production-copy comparison.
   The [independent review round 1](worklist-period-formats-review-round1.md)
   supersedes its initial inference rules and records the stricter preview.
4. `official_vintage_linked` requires an existing verified published issuer vintage,
   an approved exact-measure bridge, the correct indicator and period, a compatible
   known stage, the **same latest recorded release date**, and agreement with
   **every available actual** in compatible explicit units/scales. An unparseable
   actual blocks an official link. Missing-period inference additionally needs an
   explicit stage and an exactly matching actual and unique issuer period. No
   legacy actual is promoted to official. A failed official comparison may still
   leave a valid legacy catalogue/period mapping, explicitly unverified.
5. Released status or any raw/parsed actual blocks `lateness_window_expired`.
   Map its known indicator/period without validating the value. Reserve expiry
   for overdue items with no actual or released status, more than the configured
   window past the latest release, and retained observation evidence reaching
   that deadline. Otherwise leave `overdue` or `evidence_horizon_before_deadline`
   pending. This window limits retries of the legacy calendar; it does not assert
   an issuer publication time. Exactly 14 days remains pending at the default.
6. Complete other past-dated exact mappings as `catalogue_period_mapped`, or
   `late_value_mapped_unverified` for late observations. Missing actuals remain
   missing. Catalogue candidates remain candidates. No release-calendar lag
   inference is implemented.

Every changed item, including a pending reason change, records its complete prior
worklist row plus `prior_status` and `prior_reason` under `run_id`. A no-change
rerun appends nothing. Expired items are reconsidered: a later matching vintage
or released evidence can supersede expiry. Importer guards ignore expired,
pending and reverted decisions. Other active final decisions survive imports.
The read API overlays completed non-official mappings only from their decision
cutoff onward, checks row-level period agreement, and keeps previous periods and
stages unknown. Historical snapshots filter decisions and reversals before
choosing an active mapping.

To reverse one run, use its JSON `run_id` (the dry-run id is only a preview id):

```powershell
python -m market_data_hub.macro_releases resolve-worklist --db .tmp/macro_copy.sqlite --revert <run_id> --dry-run
python -m market_data_hub.macro_releases resolve-worklist --db .tmp/macro_copy.sqlite --revert <run_id>
```

Revert restores **all** original worklist columns atomically and appends a revert
marker without deleting audit history. Repeating the same revert is a no-op.
If any item has changed since that run or a newer run supersedes it, revert
refuses the entire operation; revert newer runs first. Fresh evidence is never
deleted. The restored items can be resolved again in a new run.

The fresh-copy fix-round run at the cutoff above reduces **2,633 pending to 698**:
579 complete (332 catalogue/period mappings, 245 late unverified mappings, 2
consistent official links), 1,356 new specific terminals, and 607 existing
failed-run terminals unchanged. The 698 pending comprise 447 future releases,
100 unknown formats, 27 frequency conflicts, 75 insufficient evidence horizons,
and 49 recent overdue items. The <=200 target cannot be met honestly under the
future-release rule alone. `.tmp/worklist-report.md` contains the new counts,
sampled evidence, active run id, and a verified full-run revert/reapply check.

## Fixtures and later steps

`tests/fixtures/macro_releases_golden.json` is a byte-for-byte copy of the design
folder's 32 cases; the original is preserved. Tests load these into temporary
databases with synthetic fixture hashes/times and check revisions, unchanged
editions, shared flash/final indicators, SA/NSA separation, knowledge cutoffs,
and numeric transcription. The shared flash/final indicator-id assertion is a
fixture-consistency check; behavior is tested by `first_release` and
`latest_official`, alongside the independent legacy stage-mapping fixture.
The fixture's confidence/verification notes are kept
unchanged: some primary URLs/dates were not independently re-fetched in the
research pass. Loading test vintages does not certify those publications for
production. This task did not revalidate external issuer values.

`tests/fixtures/legacy_calendar_v4.sql` contains the four DDL blocks copied
verbatim from LazyCommittee's `store.py` on 2026-10-17, including its original
constraints and append-only triggers. Tests build their calendar from that
snapshot, with no dependency on a sibling checkout or live database in CI.
Refresh the snapshot from source when explicitly adding support for a new
legacy schema version; unsupported versions fail rather than silently drifting.

M5 adds the manual official-source adapters described below. M6 owns consumer
resolution; M7a persists daily fetch failures. Scheduling and persistent backlog
orchestration remain separate deployment work. A CLI failure has a nonzero exit status;
it is not a statement that no release occurred.

M6 replaces placeholder selection with the single versioned deterministic
resolver and consumer read API: semantic/edition compatibility, explicit unit
conversion and issuer precision, revision relationships, official discrepancies,
equally authoritative conflicts, frozen provider forecasts, freshness/coverage,
and explicit empty-result reasons. Later work adds D+1 evidence-backed claims,
briefs, and consumer migration. No z-scores, cross-measure surprise ranking,
provider averaging, or LLM selection of official numbers belongs in this layer.

## Official-source collection (M5)

Run explicitly against a development database:

```console
python -m market_data_hub.macro_releases.collect --db scratch/macro.sqlite --families us_cpi,us_payrolls,us_gdp,ea_hicp,uk_gdp --since 2026-06
```

`--db` and `--families` are mandatory; there is no environment/production path
fallback. The CLI also refuses paths containing `ProgramData`. `--since` is a
reference-month cutoff (inclusive of a quarter ending in that month). Without
it the cutoff is four months before the current month. It is a finite manual
run: no scheduled task, timer, intraday polling or production deployment. The
production cadence is described below; source certification still needs review.

`Adapter.fetch(families, since)` returns typed `Observation` objects carrying a
`Document`, source identity, exact mapping, publication edition/stage, revision
kind, publication precision and provider vintage. `persist` uses only M4's typed
`Store.put` contracts. HTTP and parsing complete before opening the writer; no
network call holds its lock or a database transaction. Each provider batch is
atomic: an unexpected fetch/parse/write exception records the affected families,
rolls back its entire batch and allows independent providers to complete. Output reports failures,
new/reused vintages, and explicitly unavailable observations.

| Family | Mapping and published transformation | Revision/history behavior |
| --- | --- | --- |
| `us_cpi` | BLS v2 `CUSR0000SA0`, `CUSR0000SA0L1E`: issuer `pct_changes[1]` for SA m/m. `CUUR0000SA0`, `CUUR0000SA0L1E`: issuer `pct_changes[12]` for NSA y/y. | Fetch five complete calendar years plus the current year, with calculation inputs, in one batch. Never synthesize a missing BLS calculation. Nonpublished October 2025 levels and the affected November m/m changes remain nonnumeric readings. |
| `us_payrolls` | BLS CES `CES0000000001`, issuer `net_changes[1]`; unit `persons`, scale 1000. | Inspect at least the newest month and two prior months, even with a newer `--since`. February editions inspect five complete calendar years plus the current year and mark historical changes `benchmark`. All belong to one publication event. The current API does not recover old initial editions. |
| `us_gdp` | BEA NIPA `T10101`, line 1, `A191RL`, percent change at annual rate. No calculation from GDPC1 levels. | Current API plus issuer PDF archives of the advance/second estimates. An absent line raises `FetchError`; it can never return the remaining table. Older revised quarters share the headline publication event while retaining their own value stage. |
| `ea_hicp` | Eurostat ECOICOP v2 `prc_hicp_fpd`, **`release=FLS`**, for flash; `prc_hicp_minr` for current final. `coicop18=TOTAL`, evolving **`geo=EA`**, `RCH_A`/`RCH_M`. | Resolve membership by reference period: EA20 in 2023–2025, EA21 in 2026; unreviewed periods fail explicitly. `ea_hicp_*` remains the fixed EA20 golden identity; `ea21_hicp_*` is EA21. Each composition shares flash/final indicator ids and records `composition_id` in readings/vintages. Historical flash is preserved; `minr` supplies current-edition history, classified against stored issuer vintages. Estimated cells are never labelled final. |
| `uk_gdp` | ONS `gdp-to-four-decimal-places`, edition `time-series`, **version 69**, SIC `A--T`, UK. | Check the current edition and bulletin index before reading the pin; newer versions/bulletins fail for review. Index levels end in June 2026 and use the dataset metadata's **24 August** `release_date`, independently of the June bulletin's 13 August publication; first-load history is backfill. July/August bulletins provide published growth/revisions via a narrow HTML parser. |

All issuer API/CSV values have `extraction_method="api"` and verified status.
Archived BEA PDFs use `pdf`; ONS bulletin growth uses `html`. BLS tool-rendered
archives use `archive_text` and remain **unverified**, excluded from official
selection. They certify numeric transcription only, not issuer transport.
These extraction methods are explicit, not disguised API reads.
The adapter catalogue is hand-reviewed code, with separate SA/NSA indicators.

The BLS API does not expose publication dates or editions. JSON-stat's Eurostat
`updated` applies to the dataset, not each observation. BEA's `LastRevised` does
not name an estimate stage. Consequently publication/stage metadata is pinned
to reviewed editions in `bls.EDITIONS`, `bea.EDITIONS`, `eurostat.PUBLICATIONS`
and `ons.BULLETINS`. Unknown editions fail explicitly instead of inventing a
release time/stage from the retrieval date. **This is the certified 2026 slice;
review and extend those registries before collecting later publications.**
The ONS version pin also requires deliberate review before being advanced.
BLS and BEA retain 08:30 issuer-local time and `America/New_York`, converting
with `zoneinfo`: winter releases are 13:30Z and summer releases 12:30Z.
Historical snapshots cannot supply first-print history. Re-fetching unchanged
values in the same edition reuses vintages even when retrieval time/hash changes;
a changed value or new edition appends a vintage.
`adapters.revision_kind` classifies ordinary editions centrally. A historical
snapshot with no earlier published issuer vintage for that measure/period is
`backfill`; with issuer history it is `routine`. The lookup spans value stages,
so flash to final and advance to second to third are routine revisions of the
same measure/period family, including unchanged estimates. It compares issuer
publication times, so a newer edition loaded first cannot revise an older
archive. Mirror and nonpublished source series do not supply issuer history.
Explicit initial publications and benchmark revisions retain their meaning.

Add `--with-redistributors` for FRED/ALFRED evidence. CPI uses the corresponding
SA/NSA levels and FRED's `pch`/`pc1` transformations; payrolls use `PAYEMS/chg`;
GDP uses the published-rate series `A191RL1Q225SBEA/lin`. Every observation keeps
the actual issuer and `distributor_id="FRED"`, with `evidence_role="mirror"`
and unverified status. It cannot enter M4's official view. Numeric differences
in an exactly matching indicator/period/stage/edition create a discrepancy
against the verified issuer vintage, including when the mirror arrived first.
There is no cross-edition comparison or majority/average selection here.

ALFRED's change-only history omits an unchanged second GDP estimate. The adapter
makes explicit as-of requests for all three reviewed publication dates instead;
their provider vintage stays distinct from issuer publication time and our
retrieval/first-availability time. Historical BLS API editions remain unavailable;
the saved tool-rendered archive supports August's initial numeric transcription
but remains unverified evidence, so August has no certified first-print row.

`derived.percent_change` is opt-in. It reads two stored level vintage ids,
checks measure/adjustment/geography and the exact m/m or y/y lag, and writes a
separate nonpublished source-series record with both input ids and
`formula_version="percent-change:v1"`. Calculated values have method
`calculation` and unverified status; they cannot displace a published rate.

HTTP uses `requests` with certifi, a named per-provider limiter with a shared
file-lock ledger under the user temporary directory (`mdh-macro-rates`), one
second spacing and a BLS ceiling of 500 calls per UTC day. Different databases
share the same provider ledger. Each request has a connect/read timeout and
at most **one** retry with one-second backoff on transport/429/transient-5xx
failures. Authentication/403/schema errors do not retry. There is no pagination
or retry-until-success loop; truncated FRED results fail explicitly. Redirects
fail, and the ONS CSV is pinned to its verified canonical `static.ons.gov.uk`
URL. API keys come from process variables or Windows user environment variables;
errors never include credential-bearing URLs, bodies or tracebacks.

## M5 certification and source evidence

`tests/test_macro_official_adapters.py` forbids HTTP and replays saved issuer
responses from `tests/fixtures/macro_releases/`. Every saved document has a
SHA-256 and retrieval metadata. `.gitattributes` preserves captured bytes across
Windows/Linux checkouts, including issuer line endings and whitespace.
BEA echoes its key in the response; that one
field is redacted in the fixture, whose hash explicitly covers the redacted
bytes. Runtime HTTP hashes the original bytes before redacting storage payloads.
The two BLS archive snapshots are JSON-encoded `web.run` issuer-page responses,
labelled as such in metadata: direct local BLS website requests returned 403.
They are independent archive oracles, not manufactured BLS v2 responses.
Review-round captures use lossless zlib/base64 `.response.json` envelopes with
the raw-body SHA-256, request URL/parameters, response status/location and
retrieval time. The replay helper decodes and checks the body hash offline.

| Golden family | Numeric cases passed | Failed |
| --- | ---: | ---: |
| US headline/core CPI, SA m/m and NSA y/y | 4 | 0 |
| US payroll initial/revised | 2 | 0 |
| US GDP advance/second/third | 3 | 0 |
| EA20 HICP flash/final, original golden | 2 | 0 |
| UK monthly GDP including June revision | 3 | 0 |
| Total within the M5 slice | 14 | 0 |

The other 18 golden cases are outside the requested families; the M4 suite
continues to exercise all 32 contracts. **One golden metadata exception remains:**
`ea_hicp_yy_final_2026-09` says 17 October. The saved issuer publication
`eurostat/final-publication.html` explicitly says **16 October 2026**, with the
same 3.8% y/y and 0.6% m/m. M5 preserves the original golden file and uses the
issuer date; this is numeric certification with an explicit date correction,
not a claim that all original golden metadata passed unchanged.
The golden file also specifies fixed **EA20**, which is preserved. It does not
describe the 2026 headline area. `eurostat/headline-cases.json` adds six EA21
checks: June y/y 2.8% flash/current-final and September 3.8% y/y, 0.6% m/m in
both stages. Live `geo=EA` captures explicitly label EA21 from 2026; the June
EA20 current-final value is 2.7%, stored separately with its own composition.
The original payroll initial numeric case passes transcription but remains
unverified, as its saved archive was tool-rendered.

Additional assertions cover one payroll event carrying the new month and both
revisions; unchanged GDP editions; first/latest/as-known-at views; reread
idempotency even when transport bytes change; flash/final identity; unavailable
BLS observations; explicit derived inputs/formula; mirror discrepancies in
both arrival orders; the missing-BEA-line regression; bounded retries and
shared quotas; required CLI path and partial-provider failure reporting.

Live-verified endpoints (17 October 2026):

- BLS POST `https://api.bls.gov/publicAPI/v2/timeseries/data/` with `calculations=true`.
- BEA GET `https://apps.bea.gov/api/data/`, NIPA `T10101`, and the issuer's `gdp2q26-adv.pdf` / `gdp2q26-2nd.pdf` archives.
- Eurostat GET `https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_fpd?release=FLS` and `.../prc_hicp_minr` with the dimensions above. `prc_hicp_flash` is 404; old `prc_hicp_manr` is archived at 2025.
- ONS GET `https://api.beta.ons.gov.uk/v1/datasets/gdp-to-four-decimal-places/editions/time-series/versions/69`; CSV `https://static.ons.gov.uk/datasets/gdp-to-four-decimal-places-time-series-v69.csv`; issuer July/August 2026 monthly GDP bulletins.
- FRED/ALFRED GET `https://api.stlouisfed.org/fred/series/observations`, including exact GDP as-of requests for 30 July, 26 August and 30 September.

Review-round verification (17 October): both Eurostat datasets return HTTP 200
with `geo=EA`, including the composition label and June/September values above.
The ONS `download.ons.gov.uk/.../69.csv` URL returns 301; its canonical
`static.ons.gov.uk` target returns **200 without a redirect** and exactly matches
the saved CSV hash. The current edition endpoint still reports version 69; the
`.../gdpmonthlyestimateuk/previousreleases` index names August 2026 as latest.
The June bulletin was also captured to establish its publication date. Only
fixture captures used live HTTP in this review round; all tests run offline.

For an intentional fixture refresh, run
`python scripts/capture_macro_fixtures.py --plan scripts/macro_fixture_plan.json`
outside tests. Existing captures are skipped; the script uses the same limiter,
bounded HTTP and secret redaction. Review any deliberate replacement separately.
Tests never run that script and never read user API keys.

## Resolver and read API

M6 is a **read-only shadow API**. `resolve.py` computes decisions without storing
`resolution_versions`; existing SQL views remain compatibility placeholders.
There is no consumer wiring, scheduler, production path default, or ECO recap
migration. Open an explicit database with `open_reader`:

```python
from market_data_hub.macro_releases import open_reader, resolve

with open_reader(".tmp/macro_releases.sqlite") as reader:
    result = reader.release_cards(
        "2026-10-10", "2026-10-17",
        indicators=["us_cpi_headline_yy", "ea_hicp_yy"],
        as_known_at="2026-10-18T23:00:00Z",
    )
    coverage = reader.coverage("2026-10-10", "2026-10-17")
    why = reader.explain_empty("us_cpi_headline_yy", "2026-09")
    decision = resolve(reader, "us_cpi_headline_yy", "2026-09", "regular")
```

`ReadAPI(reader)` exposes the same three consumer methods. `release_cards`
returns an envelope with `cards`, `empty`, normalized bounds, the knowledge
cutoff and limitations. Every explicitly requested indicator without a card
has an entry in `empty`. Omitting indicators requests both the adapter catalogue
and stored indicators. A geography filter uses exact catalogue geography
(EA20 and EA21 are separate). Timestamp windows are half-open `[since, until)`;
date-only bounds include the whole final UTC date. Publication precision remains
explicit. The read API defaults its knowledge cutoff to now; the pure resolver
defaults to all stored knowledge. Neither downloads anything.

Stored timestamps are normalized before knowledge filtering or ordering. The
LazyCommittee calendar contract (`calendar/prompts.toml` and
`docs/calendar-collector-contract.md` in LazyCommittee) defines
`release_at_utc` as UTC `YYYY-MM-DDTHH:MM` or a bare date; its merge copies that
value to `scheduled_at_utc`. Naive timed values from those fields retain their
UTC time with read-side precision `assumed_tz`. Calendar `observed_at` and run
start timestamps use the calendar store's UTC clock and follow the same rule.
Bare dates retain `date_only`. Other naive values with no documented timezone
retain only their date, represented by a UTC midnight date bucket, never an
exact release instant. Date-only knowledge timestamps become available only
at the end of that day. Offset-aware timestamps are converted to UTC.

Evidence provenance includes `timestamp_precisions` for the normalized fields;
the imported raw payload is preserved. An invalid timestamp or legacy envelope
skips that item and adds a row identifier and diagnostic to response
`limitations`, including `explain_empty` and coverage responses. Write contracts
still reject naive timestamps. The local integration test
`tests/test_macro_legacy_time.py::test_production_copy_last_eight_weeks` reads
only `.tmp/prod-copy.sqlite`, checks the 14 October 2026 official CPI cards, and
skips when that copy is absent. Create the copy with SQLite's backup API from
a `mode=ro`, `query_only` source connection; never point the test at a live DB.

Each card represents **one event × indicator**, with the event id shared by
measures from one publication. The largest known reference period is the
headline; earlier periods in that event are revision evidence. An unavailable
headline never causes an older revision to masquerade as the headline.
The card includes indicator name/id, geography, period, stage, release-window
time/basis, resolution, both previous values, revisions, stage change, consensus, surprise
and provenance (including every period carried by the release).

| Resolution status | Meaning |
| --- | --- |
| `official` | A verified, published copy of the exact issuer edition. A verified issuer publication served by a distributor also qualifies. |
| `provisional` | Only unverified issuer, mirror, web, or legacy evidence is available. |
| `disputed` | Compatible values in the same edition disagree between verified issuers, or an unverified issuer HTML/LLM extraction disagrees with structured evidence. |
| `issuer_pending` | A mirror has a strictly newer publication edition than the captured issuer edition: issuer not yet captured. The displayed value/edition is the mirror's, with the older issuer figure retained as a discrepancy. |

Rules are versioned as `macro-resolver:m6-v1` and documented in `resolve.py`.
Filter `first_seen_at <= t` before ranking, and never admit a publication dated
after t. Match exact indicator/period/stage and explicit composition; incompatible
units and derived/rejected values cannot win. Choose the latest issuer edition,
then its verified publication. Without issuer evidence, prefer structured mirror
evidence, then other mirror/web/legacy evidence. A new unverified issuer edition
does not inherit verification from an older edition. An older or same-edition
mirror discrepancy cannot override a verified issuer. Opaque edition names do
not establish which edition is newer. Values from different editions never
constitute a dispute.

Latest vintages within a source series/edition supersede that stream's earlier
corrections; raw rereads cannot supersede its vintage. Remaining selection ties
use ascending edition, source id, series key, content hash, vintage before
reading, then row id. This chooses a stable display figure, with all provenance
and conflicting values still visible. No number of searchers forms an authority
vote. Explicit scale conversion and `%`/`percent`/`percentage`/`pct` aliases are
supported. Transforms, seasonal adjustment and euro-area membership are never
inferred; no rounding is used to hide disagreement (only floating-point noise
is ignored). Resolution values use the indicator's unit/scale/transform.

The owner decision is to expose **both** prior-period values:

- For calendar-frequency series, `previous_before_release` is the immediately
  prior calendar period's latest edition/stage known to us strictly before this
  event. For event-frequency series it is the preceding decision's value **as at
  this release**, with the same strict publication and capture-time bounds.
- For calendar-frequency series, `previous_revised` applies this event's revision of that prior period. It
  equals `previous_before_release` when this release makes no revision; a later
  release's revision cannot leak into it. If the old figure was unknown, it
  remains null before release even when this event supplies the prior figure.
- For calendar-frequency series, `previous` remains a compatibility alias for
  `previous_before_release`.
- For event-frequency (`E`) series, `previous_period` is the preceding decision
  date of the same indicator, and `previous` resolves that decision **as known at
  the query cutoff** (`as_known_at`), including corrections captured by that cutoff.
  Empty scheduled events and other indicators do not establish a predecessor.
  `previous_revised` resolves corrections to that decision known at the query
  cutoff. A late archive capture can establish these fields while
  `previous_before_release` remains null; it never backdates historical knowledge.
  If a reviewed meeting between the candidate and this release was not collected,
  `previous_period` identifies that missing meeting, the previous fields are null,
  and `previous_missing_reason` explicitly names the gap; an older decision cannot
  silently replace it. A bank's first stored decision has null previous fields and
  `previous_period`.

A missing prior period stays null with a reason; the API never skips a gap or
backdates an archive capture. `revisions` compare prior periods in the event with older publication
history available at the query cutoff; each includes full `old`/`new` resolutions
and its comparison basis. An archive captured later can establish that history
without changing the historical `previous_before_release`. `stage_change` shows the same
period's flash → final (or GDP advance → second → third) relationship, including
unchanged values; it is separate from prior-period revisions. Rereading a
publication does not create a revision.

Card windows, coverage and stage ordering share an event clock. The first known
exact issuer publication for the event/edition is the release instant, including
when the selected figure is a later correction. Non-issuer evidence uses the
stored schedule and its precision when available; a mirror timestamp never
overrides that schedule. Its original timestamp remains in the resolution and
provenance. Without an issuer or schedule, source/legacy calendar times remain
explicitly labelled. Stages rank by event chronology with `publication_order()`
as a secondary ordering key, so a later legacy final can beat an earlier flash,
including a dated flash. Retrieval time is not a publication instant.

`consensus` contains one frozen row per provider: the latest compatible snapshot
strictly before release, captured by the knowledge cutoff. Providers are never
averaged. The event's **first** exact issuer publication time reconciles earlier
scheduled/date-only eligibility; a correction cannot move the boundary later.
An exact stored schedule supplies the boundary for a mirror-only event.
Date-only releases retain the saved conservative issuer-local midnight cutoff,
even when its UTC value is later than the source's midnight date placeholder.
This includes captures at 02:00Z and 03:59Z before a saved 04:00Z lower bound.
Date-only consensus `captured_at` or `published_at` values (including naive
timestamps reduced to dates at the read boundary) are eligible only when their
calendar date strictly precedes the release date in the stored issuer timezone.
Same-day or later dates are excluded with
`date_only_consensus_not_pre_release` in `consensus_diagnostics`; an unavailable
or invalid issuer timezone excludes date-only rows with `unknown_issuer_timezone`.
The midnight placeholder for a consensus date is never converted to an issuer
instant. Exact timestamp eligibility and saved issuer-local release cutoffs
retain their existing behavior.
When no date-only cutoff is stored, a date-only release retains its conservative
date boundary and excludes
uncertain same-day captures. Measure, event, period, stage, unit and scale must
match. Forex Factory is labelled `ff_forecast` with `surprise_eligible: false`;
its reading-level surprise is null and it never enters the card's surprise list.
A card with only pre-release FF forecasts has an empty surprise list. Survey
providers have `surprise_eligible: true`. Their `surprise` is actual minus each
survey value in the indicator's scale (percentage-point differences for
percentages). Decimal arithmetic preserves input precision without binary
subtraction artifacts. Every surprise entry includes `resolution_status` and an
explicit `issuer_pending` flag. For `disputed`, both the card's surprise and
each consensus row's surprise are **null**; the arbitrary display actual is not
used for a directional comparison. With no eligible consensus surprise is also
null. The latest pre-release snapshot is selected per provider, retaining its
`survey_identity`; multiple surveys from a provider are never averaged. An
unconvertible latest unit produces `consensus_diagnostics` with the provider,
survey, capture time, unit and reason instead of silently disappearing or
reviving an older snapshot. Raw legacy forecasts
appear separately in `legacy_consensus_evidence`; their uncertified provider or
capture cannot produce surprise. **Owner decision:** a legacy observation
captured after release stays evidence, never pre-release consensus, and its
surprise stays null. This is the accepted behavior of the shadow parity report.

**Daily FF capture (M2, schema v7).** After all official families, the existing
daily runner consumes the raw archive under its same `macro_releases` run lease
and writer lock. It makes no network requests. `--consensus-raw-dir` overrides
`C:\ProgramData\InvestmentCommittee\econ_calendar\raw`. A separate
`collect_run_consensus` row retains status, inserted counts, skip reasons and
per-snapshot diagnostics. Consensus failure does not change the official family
results, terminal official run status, or official exit code. The regular dry run
evaluates the step on its private preview database.

Mapping uses exact titles and explicit FF currency/country, first against the
six proposed, versioned tier-1 mappings in
[`ff_aliases_v1.json`](../market_data_hub/macro_releases/ff_aliases_v1.json), then
against unambiguous stored catalogue aliases. The file supplements aliases at
read time; it does not rewrite imported catalogue identities. It maps four US
CPI measures, UK monthly GDP, and euro-area final headline HICP for explicit
EA21 issuer periods from 2026-01. Country prefixes and stage qualifiers are preserved;
German/French CPI cannot become euro-area HICP. Each proposed alias explains its
FF title. A catalogue alias with unknown stage may inherit a known issuer event
stage. A known catalogue stage or a stage named by FF (Final, Flash, Prelim,
Advance, Second/2nd, Third/3rd) must agree with that event; disagreement is
`stage_conflict`. Prelim is compared as `preliminary`, never guessed to mean
advance or second estimate. Unqualified CPI/GDP aliases distinguish `regular`
from staged releases; the HICP alias retains its explicit `final` qualifier.

**Round 2 takes the period from the issuer event (`ff-consensus-v3` after review).**
For the exact mapped indicator, compare FF's release date with each stored issuer
event's latest official schedule date or first actual publication date in the
issuer's time zone. Exactly one event must match before checking its stage.
Zero candidates are `release_event_not_found`; multiple candidates are
`release_event_ambiguous`, even if a stage qualifier could choose one. No event
is created from FF, and no reference period is calculated from a publication lag
or taken from FF's period text. Offset-aware FF times are converted to the
issuer zone; naive FF wall times use documented US Eastern. Bare dates remain
calendar labels. The existing read-side clock helpers handle stored timestamps.
Stored issuer zones must agree across the event and its schedule; conflicts
are refused. Without a stored zone, the versioned BLS/BEA/ONS/Eurostat issuer
zone defaults apply. No evidence-row order selects a timezone.

The schema's `release_events` and `schedule_versions` have no indicator/period
columns. Explicit issuer `readings` or `value_vintages` establish event membership
and the reference period. Mirrors and imported legacy calendar evidence cannot
establish it. As in the read API, the largest explicit stored period for that
indicator/event is the headline; earlier periods carried in the same publication
are revision rows. The stored release event supplies `estimate_stage`. A schedule
with no issuer indicator/period membership is insufficient and is counted as
not found. An issuer reading with a known period but no numeric value can supply
membership for a scheduled release. Monthly frequency only validates that stored
period's format; it never establishes a one- or two-month lag.

`load_snapshots` supplies the earliest manifest capture evidence for each file;
its mtime fallback is diagnostic only. A manifest SHA-256 must match the file.
`published_at` and `survey_identity` remain null because FF does not supply them.
The first exact issuer publication supplies the cutoff (`actual`), then an
exact official schedule (`scheduled`), then a conservative date-only issuer
clock. Date-only cutoffs are the start of the issuer-local release date; same-day
ordering remains uncertain. FF's date is a matching key and does not replace a
stored issuer cutoff. Store CHECK constraints derive eligibility/post-release
flags. Metadata found after capture can establish an event association without
backdating the FF capture. The read API reconciles later issuer clocks while
the captured reading remains append-only. Missing forecasts produce neither
consensus nor surprise.

An issuer schedule and explicit period membership can produce a forecast-only
card before an actual exists. Such cards have `status: scheduled`,
`resolution: null`, `actual: null`, and `surprise: null`; their consensus section
retains each provider separately with null surprise. A forecast alone cannot
create a scheduled card. These cards never count as captured numeric releases
in `coverage`.

`consensus_snapshots` checkpoints each filename, manifest capture token, raw
content hash and parser/alias/issuer-index fingerprint atomically with its
readings. The first run backfills; later runs bypass already checkpointed
snapshots while their metadata context is unchanged. The alias fingerprint
includes both the reviewed FF alias file and the normalized stored
`indicator_aliases` mappings (geography, title, indicator and stage). Catalogue
additions therefore retry unmapped titles without `--reprocess`; row order and
duplicate equivalent mappings leave the fingerprint unchanged. New issuer schedules,
publication clocks, period membership or stages revisit previously skipped
captures automatically. Existing captured rows are not rewritten.
If a corrected issuer index associates the same FF event, capture time and
content hash with a different event or period, its original raw row is retained.
Schema v7's append-only `consensus_link_versions` selects one live association in
the same transaction as the replacement row and checkpoint. The read API excludes
the other associations, including when a later correction restores an older row.
Before the latest correction's recorded time, historical queries conservatively
omit that observation rather than revive a known incorrect association.
Diagnostic
skips are checkpointed too. Missing-manifest files use an empty capture token,
so newly supplied authoritative manifest evidence can trigger a retry. Failed
snapshots have no checkpoint and retry next run. After a catalogue correction,
use `--reprocess` to revisit prior skips; the consensus UNIQUE key still prevents
duplicate readings. Nothing changes the archive writer or its retry discipline.

```powershell
# Explicit local copy; backfill without running any official/network collectors.
python -m market_data_hub.macro_releases.consensus --db .tmp/macro_copy.sqlite
# Normal daily-run option; --dry-run also previews consensus on a private copy.
python -m market_data_hub.macro_releases.run_daily --db .tmp/macro_copy.sqlite --dry-run --consensus-raw-dir C:/ProgramData/InvestmentCommittee/econ_calendar/raw
```

The second command retains the existing official collectors' network behavior;
the M2 copy validation instead replays stored official observations offline.
See the [production-copy validation report](m2-consensus-ingest-report.md).
As inspected on 20 October, the fresh production copy has 18 issuer release
events, zero schedule rows and no issuer event dates in 19-23 October. The nine
older snapshots still have no manifest capture records. Round 2 therefore still
inserts **zero rows**: the two manifest-backed snapshots each have seven mapped
forecasts with no matching issuer event. This is a missing issuer-event gap;
the FF reference-period-text requirement has been removed.

| Empty reason | Meaning |
| --- | --- |
| `not_covered` | Indicator is absent from both stored and adapter catalogues. |
| `schedule_unknown` | No matching release evidence or period-linked schedule is stored. A missing calendar cannot establish that no release was scheduled. |
| `no_release_scheduled` | Stored schedule revisions explicitly moved the known events outside the requested window; event ids are returned. This is scoped evidence, not a complete-calendar guarantee. |
| `scheduled_not_captured` | A stored release is past due but has no captured publication evidence. It does not claim a source failure. |
| `not_yet_released` | The known schedule or requested future window/period is after the knowledge cutoff. |
| `published_not_resolved` | Publication/unavailable/undated evidence cannot produce an eligible card. Same-family publication evidence in the window also yields this reason for a missing indicator, such as core CPI missing from a captured CPI release. |
| `source_failed` | The latest covering daily family attempt failed, or imported legacy metadata records a failed run. Daily failures include family, run id, error category, fetch window and family completion time (`collect_family_finished_at`). Legacy failures retain their original run-start time and labelled basis. |
| `filtered_out` | The geography filter excluded the indicator, or the requested indicator selection was empty. |
| `invalid_query` | A malformed reference period/window was passed to `explain_empty`; the diagnostic is returned instead of raising a raw date error. |

**Failure/coverage limitations:** Schema v4 persists daily collector outcomes.
`explain_empty` uses the latest overlapping family fetch window known at the query
cutoff; a subsequent successful or unchanged attempt clears an older failure.
Reference-period queries are matched to the recorded reference-month fetch span;
release-window queries match an overlapping span and cannot precede the failed
attempt's window. This diagnoses a fetch attempt, not proof that an issuer
publication was due or absent. `coverage` exposes `collection_failures` per family,
even when no event/schedule was captured, and includes family/time diagnostics on
missing events. Failures never inflate expected/missing event counts.
Manual M5 failures before/after migration remain unlogged; absence of values alone
never establishes failure. A failed imported legacy run is diagnosable, but its exact failure instant is unavailable;
the retained start time is labelled, never presented as a finish time. Schedule
rows have no reference-period or indicator membership: matching uses reviewed
family mappings, and a family schedule is not assigned to an arbitrary period.
Legacy mapping links have only their latest version; a link updated after a
historical cutoff is conservatively unavailable rather than leaked backwards.
As accepted in review (P3), `indicators` metadata and
`source_series.is_published` are read as of today; they are not a versioned
historical catalogue. First-seen filters do not imply historical reconstruction
of those mutable tables. As accepted for F7, a disputed resolution's display
value follows the deterministic tie-break and is not a verified truth choice.
The documented F10 golden-date exception remains unchanged.

`coverage` counts distinct events per family. `expected` is the union of stored
schedules and captured events; `captured` means at least one numeric card for the
event. Captured status buckets are mutually exclusive, using the worst card
status (`disputed`, `issuer_pending`, `provisional`, `official`). Missing events
include reasons. Latest publication and knowledge timestamps expose freshness.
This is event-level coverage, not certification that every measure in a family
is captured. Unknown schedules prevent a completeness guarantee; zero expected
does not prove that no release was due.

```powershell
python -m market_data_hub.macro_releases cards --since 2026-10-10 --until 2026-10-17 --indicator us_cpi_headline_yy --indicator ea_hicp_yy --as-known-at 2026-10-18T23:00:00Z --db .tmp/macro_releases.sqlite --json
python -m market_data_hub.macro_releases coverage --since 2026-10-10 --until 2026-10-17 --db .tmp/macro_releases.sqlite --json
```

Both commands open SQLite with `mode=ro` and `query_only=ON`, perform no migrations
and fail on a missing DB. The CLI emits JSON by default (`--json` is explicit).
Offline certification in `tests/test_macro_read_api.py` covers all 32 synthetic
golden contracts and the 14 saved issuer-adapter cases. The known tool-rendered
payroll initial remains provisional; the issuer HICP date correction stays
explicit. [The fixture shadow parity report](macro-releases-shadow-parity.md)
covers the legacy-imported 10–17 October week without accessing a live file.

## Production daily run

The production collection entry point requires an explicit `--db`. There is no
environment-variable or default path fallback. It accepts ProgramData paths;
the development `collect` CLI retains its ProgramData refusal. This change neither
opens a live database nor installs a scheduled task. Deployment through
investmentcommittee's job TOML and Windows Task Scheduler is a separate step.

```powershell
& 'C:\ProgramData\InvestmentCommittee\envs\marketdata\Scripts\python.exe' -m market_data_hub.macro_releases.run_daily --db 'C:\ProgramData\InvestmentCommittee\live\db\macro_releases.sqlite' --lookback-months 24
```

Omitting `--families` collects all five certified families:
`us_cpi,us_payrolls,us_gdp,ea_hicp,uk_gdp`. A subset accepts comma-separated or
space-separated names, for example `--families us_cpi,us_payrolls`. The runner
collects issuer evidence only; FRED remains an optional manual redistributor
adapter and is not required for official cards. Keys are read by the adapters
from `BLS_API_KEY` and `BEA_API_KEY` in the job environment (the existing Windows
user-environment fallback remains supported). `FRED_API_KEY` is used only by the
manual FRED adapter. Census is outside the certified set.

Recommend one daily trigger at **06:30 America/Los_Angeles / 09:30 America/New_York**,
including weekends for recovery. This allows an hour after the usual US 08:30 ET
releases and is after euro-area 11:00 CET/CEST and UK 07:00 local publications,
including the weeks when Europe and the US change clocks on different dates.
Use a named time zone/DST-aware local task trigger, rather than a fixed UTC time.
The marketdata environment must contain the released package with schema v6;
this PR does not install it or move a runtime pin.

| Exit | Meaning |
| --- | --- |
| `0` | At least one new vintage was committed, with no family failures. A dry run would add at least one vintage. |
| `3` | No new vintage, including a run where every family needs review. New readings, documents, queue items and log rows alone do not count as new data. |
| `1` | A family or runner failed, including a live lease blocking the attempt. Other families may already have committed successfully. |
| `2` | Invalid/missing CLI arguments. |

Standard output is one JSON summary with run id, database, dry-run flag,
requested cutoff, effective family fetch windows, status and exit code. Each
family includes vintages/readings added, readings skipped by deduplication and,
on failure, a short safe error category/message. Arbitrary exception text,
credential-bearing URLs, response bodies and tracebacks are never printed or
stored in the log. Non-numeric issuer observations still count as readings.

The **24-month default lookback** covers routine revisions while limiting initial
history/storage volume. Payroll benchmark coverage is independent: BLS widens
February payroll and CPI collection to five complete prior calendar years plus
the current year and includes calculation inputs. The JSON and
log retain the actual requested reference-month window, with the fetch date as
its inclusive upper bound; a provider may return a narrower available history.
Eurostat now discovers historical/publication dates through the issuer calendar;
it is not clipped to the original four reviewed months. Unconfirmed dates enter
review. `--lookback-months` accepts 1–180 months.

Daily collection discovers new editions automatically; the original reviewed
editions remain known-good bootstrap anchors. The manual development collector
retains its reviewed-edition behavior. Daily discovery uses these issuer sources:

| Family | Edition and publication evidence |
| --- | --- |
| `us_cpi` | BLS v2 series/calculation metadata plus `https://www.bls.gov/feed/cpi.rss`; RSS title/reference month and publication date must match the API headline. |
| `us_payrolls` | BLS v2 CES data plus `https://www.bls.gov/feed/empsit.rss`, with the same period/date binding. |
| `us_gdp` | NIPA `T10101`/`A191RL` identity and `LastRevised`, checked against `https://www.bea.gov/news/current-releases` for quarter, estimate stage and date. |
| `ea_hicp` | JSON-stat dataset/dimensions, checked against the issuer release-calendar feed `https://ec.europa.eu/eurostat/o/calendars/eventsJson` for each reference month and flash/final stage. The dataset `updated` timestamp is not a publication date. |
| `uk_gdp` | ONS current-edition `latest_version` and version metadata, plus the GDP bulletin `previousreleases` index and each bulletin's publication date. CSV levels use their dataset's own `release_date`; monthly growth uses the corresponding bulletin date. |

New editions need the same dataset/series identity, expected structure, units and
headline periods, a verified prior baseline within the sanity band, and consistent
issuer publication metadata. No retrieval date substitutes for publication time.
BLS's two metadata endpoints returned HTTP errors during fixture capture in this
environment: new API values with unavailable publication evidence enter review;
known-good anchors remain usable. An unavailable source never grants verification.
The captured BEA listing and Eurostat calendar are replayed offline in tests.

Verification failures append `review_queue` entries with controlled reasons and
store safely mapped values as unverified readings (`provisional` when resolvable).
Unparseable or incompatible measures retain their raw document in the queue;
the runner does not invent mappings or values. Review candidates create no
vintages. A family is `needs_review`, with reasons in its JSON summary, rather
than `failed`; HTTP/transport and database/infrastructure failures remain failed.
The queue is content-addressed/idempotent and can be listed without writing:

```powershell
python -m market_data_hub.macro_releases review-queue --db .tmp/macro.sqlite --family us_cpi
```

Sanity bands are maximum absolute changes against a previously stored verified
issuer vintage, preferring the same period, otherwise the latest stored period.
During first collection, reviewed editions can bootstrap their own backfill and
supply the initial comparison for another edition of the same indicator. Without
either baseline a new edition enters review. These comparisons use evidence at
collection time; they do not backdate availability or alter historical resolution.

| Indicator group | Default maximum absolute change |
| --- | --- |
| CPI and HICP m/m | 3 percentage points |
| CPI and HICP y/y | 5 percentage points |
| US payroll monthly change | 1,500 thousand persons |
| US GDP q/q SAAR | 15 percentage points |
| UK GDP m/m | 5 percentage points |
| UK GDP index | 20 index points |

Override individual bands with `--sanity-bands <JSON file>`, for example
`{"us_payrolls_change": 2000, "us_gdp_qq_saar": 20}`. Unknown indicators,
non-positive and non-finite overrides are usage errors. Absolute bootstrap guards
also reject magnitudes above 25,000 thousand payrolls, 500 index points, or 100
percentage points. These thresholds are review gates, not statistical forecasts.

The euro-area rule retains EA20 before 2026 and EA21 from 2026 onward, with the
issuer's membership timeline checked against each period. A later/new membership
or missing composition evidence uses the explicit `EA_PENDING` / `ea_future_hicp_*`
placeholder and enters review. Unknown future composition never becomes official.

ONS Cloudflare ray/timestamp tokens and BLS elapsed response times are excluded
from document identity hashes; release dates, periods, values and units remain.
`documents` stores the first raw/redacted body once per normalized document hash,
with a raw-body hash, URL, retrieval time and normalization version. New readings
carry a `document_hash` reference instead of a repeated full body. Existing raw
reading envelopes remain append-only and are not rewritten by migration.

Official `Reading` retries also compare the latest observation's semantics
within source/series/period/edition/stage/event/kind. Document hashes can change
because of an envelope or another observation without changing this value.
An unchanged retry preserves the first reading and its `first_seen_at`, and
increments `readings_skipped`; its raw document remains content-addressed.
Corrections, new editions/stages and changed verification/extraction provenance
are distinct. This check also recognizes pre-fix readings. Existing duplicate
rows remain append-only; no cleanup is needed or performed by this change.

The ONS four-decimal GDP index has an independent dataset publication:
`release_date` in version metadata supplies the date-only edition and
`published_at` (v69: **2026-08-24**). `last_updated` is not a release clock;
the June GDP bulletin's **2026-08-13** date does not date the dataset.
The version remains `provider_vintage_id`, rather than an edition such as
`time-series:69`. Missing/invalid `release_date` makes level parsing fail
explicitly as not tier-1-resolvable; daily discovery records
`index_not_tier1_resolvable_missing_release_date`. The saved v69 levels still
end at **2026-06**: assigning their correct publication date does not manufacture
August or September levels. Pre-fix malformed index evidence produces
`invalid_publication_edition` in the read API with a recollection instruction.
It stays stored for audit, but is excluded from cards. Recollecting adds a
correct edition without rewriting that evidence. Corrected index evidence with
no publication in the requested window reports `no_release_in_window`, with
its last publication and reference period, independently of GDP m/m releases.

Cards select the head reference period across an event's comparable frequency
before indicator/geography filters. Re-observed older periods stay available
as vintage and revision evidence; they do not become separate release cards
when the same edition includes a new composition's head period. EA20 HICP
(`ea_hicp_mm/yy`) explicitly names EA21 (`ea21_hicp_mm/yy`) as its successor.
If the old head is at least **3 months** before publication and its successor
has a newer official numeric period published by that date, current cards for
the old series are suppressed even across different events. The empty result
reports `discontinued_series`, successor, both head periods and threshold.
This is a read-time classification: it does not change catalogue identity or
delete history. The query's `as_known_at` cutoff and each event's publication
date bound successor evidence, preserving legitimate historical EA20 cards.
Shared-family membership alone does not establish succession.

One HTTP client and one limiter per provider are shared across all families in
the run, including the separate CPI and payroll fetches. Each provider has
one-second request spacing; BLS has a 500-call UTC-day ceiling. Normal runs reuse
the existing process-safe provider ledger under the user temporary directory,
even across different databases. Each request has 10-second connect and
45-second read timeouts. Transient connection/timeouts, HTTP 429 and
500/502/503/504 receive at most two retries (three total attempts), with
exponential backoff of one then two seconds. `Retry-After` delta seconds and
HTTP dates override shorter backoff. Long waits renew the lease every 30 seconds.
TLS verification failures, other request failures, authentication/403, parsing
and schema/certification errors are not retried. Every attempt uses its limiter.

The `<db>.daily.lease.json` lease expires after **30 minutes** and is renewed
before each bounded HTTP attempt and family persistence. Acquiring, renewing,
releasing and reclaiming it use the existing `<db>.writer.lock`. Network and
parsing hold neither that writer lock nor a SQLite transaction. Before writing,
the owner validates its unexpired random token under the writer lock, fencing
off an older process after takeover. A live lease returns failure without fetching;
an expired lease is reclaimed. Empty, truncated or otherwise invalid metadata
uses the file mtime plus the same 30-minute timeout; a fresh file receives that
grace period and cannot block future days. Reclamation is recorded in the run's
`parameters.lease_events`. Lease writes flush and fsync before atomic rename.
Writer-lock acquisition waits in short five-second intervals so contention does
not itself kill a live run. A process crash releases the OS file lock.

Migrations v4/v5/v6 run automatically under `open_writer`, in one transaction, on
the first real run. Schema v3 evidence remains intact; a pre-existing v4 log is
copied unchanged while extending its status enum. Readers never migrate and now
declare a compatibility floor **3** and ceiling **6**. They accept the additive
v4/v5/v6 schemas and read older v3 stores, treating absent logs/queues as unavailable.
The v5/v6 databases also declare their reader compatibility range. Outside-range or
non-additive versions fail explicitly; writers still reject newer schemas.

**Deployment constraint:** an already-installed, unmodified v0.3.0 reader has a
hard-coded `user_version == 3` check. This PR cannot retroactively change that
binary. Deploy/backport the `open_reader` range check before migrating a database
used by that binary. Older v5 readers also need their ceiling updated before a
shared store is migrated to v6. The v3 read contract itself is unchanged and
supports v4/v5/v6 once that range check is patched; no production deployment is
performed here.
`collect_runs` contains immutable lifecycle records: one `running` record
(`finished_at=NULL`) and one terminal `ok` / `no_change` / `needs_review` / `failed` record for a
logical `run_id`, with identical start time, families, parameters, package version
and host. Use `WHERE status != 'running'` for terminal summaries. A missing terminal
record identifies an interrupted run. The next lease owner appends a failed
completion and `InterruptedRun` outcomes for unfinished families, retaining
already committed family successes.

`collect_run_families` has one immutable outcome per run/family, with the fetch
window, completion time, safe error type/message and counts. A fully fetched
family's data and success outcome commit together; a persistence error rolls
back that whole family, then appends its failure outcome in a separate short
transaction. Other families continue, including another family from the same
provider. The final run status is failed if any family failed, otherwise ok if
any vintage was added, otherwise needs_review if any family needs review,
otherwise no_change. Additional raw readings alone never produce `ok`.

Add `--dry-run` to fetch and parse normally, then run the identical persistence
and deduplication logic on an in-memory snapshot of the target. Snapshotting
requires two identical reads of the database/WAL bytes (up to three tries),
opens only a private temporary copy, checks its integrity, then backs it up to
memory and removes the copy. A continuously changing target fails the preview;
opening SQLite directly with `mode=ro` could create live WAL/SHM sidecars.
An absent target
uses a fresh in-memory store; a v3 target migrates only in memory. No target DB,
run-log, queue, document, lease or writer-lock files are written to the target.
Preview HTTP reserves the same shared provider rate/quota ledger as a real run,
so concurrent previews cannot double the request rate. Proposed counts have the
same names as real counts, with `dry_run=true`; exit status mirrors a real run:
0 for new vintages, 3 for no new vintages/review-only, 1 for infrastructure failure.

Offline certification in `tests/test_macro_daily_runner.py` and
`tests/test_macro_daily_review.py` replays saved M5/discovery
responses and fakes. It covers logged failures, official cards, idempotency,
partial-provider failure, rollback, lease fencing/recovery, fake-clock rate
spacing, bounded transient retries, dry-run immutability and migration of a
schema v3 fixture with 20,000 legacy readings. No test opens a live database or
makes a live network request.
