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

M5 adds the manual official-source adapters described below. M6 and the future
daily runner still own scheduling, persistent fetch-failure/backlog work and
consumer resolution. A CLI failure is reported with a nonzero exit status;
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
owner's cadence remains one daily run after the US close, plus weekly review.

`Adapter.fetch(families, since)` returns typed `Observation` objects carrying a
`Document`, source identity, exact mapping, publication edition/stage, revision
kind, publication precision and provider vintage. `persist` uses only M4's typed
`Store.put` contracts. HTTP and parsing complete before opening the writer; no
network call holds its lock or a database transaction. A provider failure does
not prevent independent providers from completing. Output reports failures,
new/reused vintages, and explicitly unavailable observations.

| Family | Mapping and published transformation | Revision/history behavior |
| --- | --- | --- |
| `us_cpi` | BLS v2 `CUSR0000SA0`, `CUSR0000SA0L1E`: issuer `pct_changes[1]` for SA m/m. `CUUR0000SA0`, `CUUR0000SA0L1E`: issuer `pct_changes[12]` for NSA y/y. | Fetch five complete calendar years plus the current year, with calculation inputs, in one batch. Never synthesize a missing BLS calculation. Nonpublished October 2025 levels and the affected November m/m changes remain nonnumeric readings. |
| `us_payrolls` | BLS CES `CES0000000001`, issuer `net_changes[1]`; unit `persons`, scale 1000. | Always inspect at least the newest month and two prior months, even with a newer `--since`. All belong to one publication event. The current API does not recover old initial editions. |
| `us_gdp` | BEA NIPA `T10101`, line 1, `A191RL`, percent change at annual rate. No calculation from GDPC1 levels. | Current API plus issuer PDF archives of the advance/second estimates. An absent line raises `FetchError`; it can never return the remaining table. Older revised quarters share the headline publication event while retaining their own value stage. |
| `ea_hicp` | Eurostat ECOICOP v2 `prc_hicp_fpd`, **`release=FLS`**, for flash; `prc_hicp_minr` for current final. `coicop18=TOTAL`, `geo=EA20`, `RCH_A`/`RCH_M`. | Both stages use the same indicator ids. First-released flash history is preserved; earlier final periods in `minr` attach to the current final edition. Estimated cells are never labelled final. EA20 is the fixed geography of the golden set, not an implicit switch to EA21. |
| `uk_gdp` | ONS `gdp-to-four-decimal-places`, edition `time-series`, **version 69**, SIC `A--T`, UK. | The pinned API version ends in June 2026. Index levels stay separate from published growth. The July/August 2026 issuer bulletins provide monthly growth and the June revision, using a narrow deterministic HTML parser. |

All issuer API/CSV values have `extraction_method="api"` and verified status.
Archived BEA PDFs use `pdf`; ONS bulletin growth and BLS archive certification
use `html`. These extraction methods are explicit, not disguised API reads.
The adapter catalogue is hand-reviewed code, with separate SA/NSA indicators.

The BLS API does not expose publication dates or editions. JSON-stat's Eurostat
`updated` applies to the dataset, not each observation. BEA's `LastRevised` does
not name an estimate stage. Consequently publication/stage metadata is pinned
to reviewed editions in `bls.EDITIONS`, `bea.EDITIONS`, `eurostat.PUBLICATIONS`
and `ons.BULLETINS`. Unknown editions fail explicitly instead of inventing a
release time/stage from the retrieval date. **This is the certified 2026 slice;
review and extend those registries before collecting later publications.**
The ONS version pin also requires deliberate review before being advanced.

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
the saved issuer archive independently certifies August's initial payroll print.

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

| Golden family | Numeric cases passed | Failed |
| --- | ---: | ---: |
| US headline/core CPI, SA m/m and NSA y/y | 4 | 0 |
| US payroll initial/revised | 2 | 0 |
| US GDP advance/second/third | 3 | 0 |
| EA HICP flash/final | 2 | 0 |
| UK monthly GDP including June revision | 3 | 0 |
| Total within the M5 slice | 14 | 0 |

The other 18 golden cases are outside the requested families; the M4 suite
continues to exercise all 32 contracts. **One golden metadata exception remains:**
`ea_hicp_yy_final_2026-09` says 17 October. The saved issuer publication
`eurostat/final-publication.html` explicitly says **16 October 2026**, with the
same 3.8% y/y and 0.6% m/m. M5 preserves the original golden file and uses the
issuer date; this is numeric certification with an explicit date correction,
not a claim that all original golden metadata passed unchanged.

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

For an intentional fixture refresh, run
`python scripts/capture_macro_fixtures.py --plan scripts/macro_fixture_plan.json`
outside tests. Existing captures are skipped; the script uses the same limiter,
bounded HTTP and secret redaction. Review any deliberate replacement separately.
Tests never run that script and never read user API keys.
