# Macro release contracts (M4)

`market_data_hub.macro_releases` owns a separate SQLite publication and evidence
store. M4 supplies contracts, migrations, a small data-access API, and a read-only
legacy importer. It has no collectors, scheduled runner, consumer integration,
or production resolver.

## Database and access

Proposed deployment path:
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
holds the lock for its lifetime; SQLite transactions cover only an individual
insert or worklist update. Close it promptly. Never perform network/model calls
inside a transaction. The lock is released after a process crash by the OS.
This is the M4 local lock; a scheduled runner's lease and provider-wide rate
limiting belong to later work.

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
name, value role and source index. Distinct observations of the same figure stay
distinct readings, while reading the same row twice adds nothing.

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
`placeholder:m4-verified-issuer-v1` identifier. They order by issuer publication time, then first seen time
and row id for reproducibility. They do not infer authority from extraction
method. `first_release` selects the earliest publication per indicator/period;
`latest_official` selects the latest across stages. All stages remain stored.
`as_known_at` filters `first_seen_at <= t` **before** selecting the latest
publication. Downloading an old publication later cannot leak into an earlier
knowledge snapshot or replace a newer publication.

These placeholders do not resolve equally authoritative conflicts, perform
precision-based numeric comparisons, or append `resolution_versions` implicitly.
An empty list only means no qualifying vintage in this M4 store; the consumer API
with coverage/failure/unresolved reasons arrives in M6.

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

M5 adds certified collectors and schedule/source mappings against saved issuer
publications: CPI SA/NSA, payroll revision windows, GDP estimates, HICP stages,
and an ONS family. It must use the issuer's published transformations, maintain
short writes, and keep failed/overdue releases in the daily backlog.

M6 replaces placeholder selection with the single versioned deterministic
resolver and consumer read API: semantic/edition compatibility, explicit unit
conversion and issuer precision, revision relationships, official discrepancies,
equally authoritative conflicts, frozen provider forecasts, freshness/coverage,
and explicit empty-result reasons. Later work adds D+1 evidence-backed claims,
briefs, and consumer migration. No z-scores, cross-measure surprise ranking,
provider averaging, or LLM selection of official numbers belongs in this layer.
