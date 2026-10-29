# Changelog

All notable changes to this project are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions are released as `vX.Y.Z` tags; the supported combination with the other
ecosystem repositories is set by `LazyTools/ecosystem/compatibility.toml`.

## [Unreleased]

### Added
- Public read-only `ReadAPI.release_documents` and `ReadAPI.decision_history`
  (also available on `open_reader` handles). Consumers can retrieve saved issuer
  PDF/HTML text with a shared character budget and inspect dated policy rates,
  stored basis-point changes and votes for all eight central-bank decision
  indicators. Both APIs filter knowledge before selection and explain empty
  results; no downloads, FRED mapping or schema migration are required.

## [0.3.10] — 2026-10-28

### Added
- processo-macro M9d-1 (#139): official Japan and China tier-1 release cards. They cover:
  - Japan: Cabinet Office GDP, national and Tokyo CPI, the BoJ Tankan and the MOF trade balance;
  - China: NBS GDP, CPI, official PMI and monthly activity.

  Editions are discovered from the issuer's own listings. A due edition without a reviewed URL is reported as `scheduled_unreviewed` instead of aborting the family. NBS editions are dated at the first official CST publication.
- processo-macro M9i (#140): official Latin American policy and inflation cards. They cover:
  - BCB Copom Selic decisions, gated at 18:30 São Paulo;
  - Banxico policy-rate decisions, with statements discovered from Banxico's listing;
  - INEGI monthly INPC, headline and core.

  Each edition is isolated, so a parse failure, a 404 or a missing preceding statement affects only that edition.

### Known gaps
- IBGE IPCA and IPCA-15 are not collected, because IBGE returns 403 to this server.
- METI industrial production is not collected (403).
- `mx_core_cpi_yy` has no approved indicator card.

## [0.3.9] — 2026-10-25

### Added
- processo-macro M9h (#135): when BLS is unreachable (transport, HTTP or blocked responses only), the BLS families fall back to the ALFRED first print available at the BLS release instant. Rules:
  - the vintage date must match the scheduled edition, otherwise the value goes to `needs_review`;
  - the value is labelled `redistributor`, with BLS as the original issuer, on every read path;
  - official BLS values take precedence without rewriting history.
- US unemployment rate (U-3, SA) and average hourly earnings (total private, m/m and y/y, SA), on the Employment Situation calendar.

### Fixed
- CPI, PPI and AHE fallback percentages are rounded half-up to the one decimal BLS publishes.
- The generic FRED history collector no longer writes rows for PPI, JOLTS, unemployment or AHE.
- BLS content failures stay `needs_review` instead of triggering the fallback, and the original reason is kept in the review queue.

## [0.3.8] — 2026-10-25

### Added
- Indicator cards v2.2 (#132): the 24 tier-1 concepts approved by the owner on 2026-10-24 are now part of the approved dataset; all 86/86 tier-1 measures resolve to approved cards. The approval record is in the `promotions` block of `timing_v2.2.json`; `flag` keeps caveats only.
- processo-macro M9f (#130): fifteen housing families with issuer-certified editions, calendars and golden cases. They are:
  - US: FHFA HPI, Census construction spending, Freddie Mac PMMS, and the private-source headlines for NAR existing and pending home sales and for S&P Case-Shiller;
  - euro area: Eurostat EA21 HPI, ECB house-purchase loans and the cost of borrowing;
  - other countries: UK HPI, BoE mortgage approvals, Destatis residential permits, CMHC housing starts, MLIT housing starts and NBS new-home prices by city tier.
- Indicator cards v2.3 (#134): the 12 housing concepts approved by the owner on 2026-10-25 (306 cards). FHFA, Case-Shiller, EA HPI and UK HPI are COIN/DEFINITION; PMMS is CTX; the other seven stay UNKNOWN.

### Fixed
- Private-source classification is carried by the resolution itself, so NAR and Case-Shiller are reported as `private`, and FRED/ALFRED mirrors as redistributors, on every read path (cards, history, coverage, CLI, nested previous/revision/consensus fields).

### Known limitations
- Housing prior-period revisions are not yet ingested, so the latest value for an earlier period can stay at its first print. NBS publishes no single national 70-city aggregate. See `docs/macro-housing.md`.

## [0.3.7] — 2026-10-24

### Added
- processo-macro M9b (#128): ten more official families — euro-area unemployment, retail volume, industrial production, goods trade balance (SA), core HICP, EC consumer confidence (flash) and ESI (final); UK retail sales and total pay; US new home sales and University of Michigan sentiment.
- processo-macro M9c (#128): official policy-decision cards for FOMC, ECB, BoE, BoJ, PBOC LPR and Bank of Canada, with reviewed 2026 meeting calendars, vote splits and the previous stored decision (an uncollected reviewed meeting makes `previous` unknown, never an older decision).
- processo-macro M2b (#128): Forex Factory consensus linked to reviewed scheduled release events for every certified family (exact title aliases; forecasts captured before the release instant; surprises still suppressed). Scheduled events are seeded per edition stage even when the FF archive is unavailable.
- `docs/macro-indicators.md`: which macro indicators are tracked, how they are classified and where each is stored. Part B is generated by `python -m market_data_hub.indicator_cards.docs` (`--check` in `tests/test_macro_indicators_docs.py`) from the cards, the certified release families, the FF aliases and the edition calendars.
- Schema v8 (policy facts) and v9 (append-only BoJ publication-time enrichment): additive migrations.

### Fixed
- FOMC vote split across every dissenter clause; EC survey latest-page rollover; release-instant gating for Census, UMich and EC; rate-limiter slots reserved without sleeping under the ledger lock; duplicate indicator-card ids; BoJ timestamp evidence no longer conflicts with an unchanged statement PDF.
- CI: duckdb bounded below 2.0; replay tests no longer read host credentials; Eurostat compact UTC offsets parse on Python 3.9/3.10 (#129).


## [0.3.6] — 2026-10-24

### Added
- processo-macro M9a (#121): twelve new official families in the daily macro collection.
  - US: PPI, JOLTS, PCE, retail sales, housing starts and permits, durable goods, industrial production and capacity utilization, trade balance, weekly initial jobless claims.
  - UK: CPI and core CPI; unemployment and regular pay.
  - Euro area: EA21 GDP, flash and later estimates.
- Figures come from the issuer publications (BLS, BEA, Census, Fed, DOL, ONS, Eurostat). FRED/ALFRED serves only as a labelled redistributor, for history.
- Reviewed edition calendars run to 31/12/2026. An edition is due from its release instant: before that it is "scheduled", and a short publication-delay grace applies.
- Indicator-card aliases cover every new catalogue id.

### Fixed
- Euro-area GDP snapshots older than their edition's release are rejected, and identical nightly refreshes add no vintage.
- Census prior-month figures carry the issuer's own estimate stage.
- The M3 full report must match the due final edition.
- PCE rate tables are checked against their month headers, and a PCE schema change is routed to the review queue.

## [0.3.5] — 2026-10-24

### Added
- Offline daily Forex Factory forecast ingestion into `consensus_readings`,
  labelled `ff_forecast`, with manifest capture times, verified archive hashes,
  exact country/catalogue aliases and issuer-event reference periods. Schema v6
  adds atomic snapshot checkpoints and a separate consensus run log; failures
  leave official collection status and exit codes unchanged. Six versioned
  tier-1 US/EA/UK aliases include title justifications. The production-copy
  validation report records missing capture records and missing issuer events and
  the resulting zero-row backfill; no historical consensus is fabricated.

### Fixed
- Materialize reviewed issuer edition calendars as nonnumeric scheduled event
  membership and append-only schedule versions before FF consensus ingestion.
  Pre-release forecasts and subsequent official figures share the adapter's
  issuer/family/stage/edition identity. Existing schedules are preserved;
  unlisted dates remain `release_event_not_found`. Recognize policy decision
  periods, percent-per-annum units and FF's reviewed GDP stage vocabulary;
  keep FF surprises suppressed. Daily actuals wait for the known issuer release
  instant, including DST, rather than just its calendar date.
- Retire a linked FF consensus observation when the current issuer index no longer places it on a unique event (`release_event_not_found` / `release_event_ambiguous`): an append-only v7 journal row with `retired=1` hides it from snapshots and cards, a later valid match restores it, and the checkpoint version includes the journal position so a snapshot is reprocessed when the index returns to an earlier state.
- Include normalized stored indicator aliases in FF checkpoint fingerprints so
  catalogue additions automatically retry previously unmapped captures.
- Suppress FF forecast surprises through provider eligibility; atomically retire
  corrected issuer links with an append-only schema v7 journal, and refuse
  conflicting issuer time zones when matching archived FF events.
- Link FF forecasts only to one stored issuer release event on the same
  issuer-local scheduled or actual date, inheriting its period and stage.
  Count absent/ambiguous events and stage conflicts; never create an event
  or require a reference-period label in the FF feed. Revisit skipped captures
  when issuer metadata changes. Scheduled events with explicit issuer period
  membership can expose forecast-only cards with null actual and surprise.
- Skip unchanged official readings across document/envelope changes, retaining
  the original first-seen time and counting daily retries as `readings_skipped`.
  Preserve real corrections, stages, editions and verification provenance.
- Use the ONS index dataset's own `release_date` for its publication edition,
  retaining the dataset version as provenance. Diagnose pre-existing malformed
  index editions explicitly; never borrow a monthly GDP bulletin's date.
- Select the edition's head period before card filters, retaining older values
  as revision evidence. Suppress stale EA20 cards when EA21 has advanced and
  expose `discontinued_series` with the successor and last reference periods.

## [0.3.4] — 2026-10-20

Release set `ecosystem-2026.10.7`: `resolve-worklist` rule `legacy-worklist-v3` (#115).

### Fixed
- Tighten v3's explicit-only boundary after independent review: require
  separate, word-bounded catalogue window definitions; require an explicit
  calendar quarter for quarterly ranges; keep quarterly survey months pending
  without a named quarter; preserve full event dates. Confine comma-month repair
  to v3 so shared v2 parsing stays unchanged.
- Resolve explicit imported reference periods under `legacy-worklist-v3` without
  letting placeholders or agreeing yearless labels veto fully dated evidence.
  Support the reviewed survey, meeting, annotated quarter, localized weekly and
  first-half-month spellings. Gate cumulative/rolling reporting-month indices by
  the catalogue definition, retaining precise reasons for unsupported windows,
  missing years, fiscal ambiguity, issue identifiers and bond maturity dates.
  Preserve the v3 period interpretation in read overlays; retain raw evidence,
  audit/revert behavior and unverified legacy values. The production-copy report
  documents the 100-item investigation and remaining pending evidence.

## [0.3.3] — 2026-10-19

Release set `ecosystem-2026.10.6`.

### Added
- Offline `macro_releases resolve-worklist --db <copy> [--dry-run]`, with
  exact existing catalogue/alias matching, explicit period parsing, consistent
  official vintage links and specific terminal reasons. The fresh-copy fix
  run moves 2,633 pending to **698**: 579 complete and 1,356 new terminals;
  607 existing failed-run terminals stay unchanged. Future releases remain
  pending. No aliases or values are added.
- Optional idempotently created audit tables **at schema v5**, retaining the full
  prior state of every changed item. `--revert <run_id>` restores one run atomically
  without deleting evidence/history, and refuses concurrent/superseding changes.

### Fixed
- Reject future evaluation cutoffs, and cutoffs earlier than the worklist state the pass
  would read (the worklist keeps only its latest state); skip missing release dates; keep future
  releases and unrecognized non-empty periods pending. Parse numeric/localized,
  ordinal-quarter, explicit ROC-calendar and week-ending formats consistently.
- Check indicator, period, release date and available actual values for official
  links. Released evidence cannot expire as never-arrived; expired items can
  resolve against later evidence. The retry window is configurable with
  `--lateness-days` (default 14, minimum 1), bounded by retained evidence.
- Apply/revert use the daily runner's lease and writer lock and refuse a held
  lease/lock. SQLite version, migration ceilings and reader compatibility remain
  unchanged at v5; no automatic v6 migration is introduced.

## [0.3.2] — 2026-10-18

Release train `ecosystem-2026.10.5`. Ships the indicator cards, the two-layer sovereign
yields and the macro read-API fixes that the processo-macro recaps read. No new runtime
dependency.

### Added
- **Indicator cards v2.1** (#108, #111), `market_data_hub.indicator_cards`: the
  owner-approved classification (270 concept rows, including the UK monthly GDP row)
  shipped as package data with a hashed timing extract, explicit catalogue aliases,
  strict models, id resolution, deterministic compact rendering and an offline coverage
  report. No certified gaps remain.
- **Sovereign yields, two layers** (#109): official issuer histories (US Treasury,
  MoF Japan, BoE par curve, BoC) and a dated TradingView `TVC:` market snapshot, with
  separate market ids, explicit convention metadata, snapshot budgets and divergence
  reports (`market_data_hub.sovereign_refresh`, `config/sovereign_layers.yaml`). Wired
  into the existing daily and backfill jobs and exposed in discovery. BoE ships verified
  5y, 10y and 20y par histories (the earlier HTTP 403 came from the default User-Agent
  and is kept only as a negative test fixture); the unverified BoE 2y/30y par codes are
  the remaining gaps, with no substitutes.

### Fixed
- Macro read API: legacy naive `event_time` values are normalised at the read boundary
  instead of crashing (#110); date-only consensus forecasts no longer count as
  pre-release snapshots for release-day results.

### Changed — schema migration (additive)
- **Hub DuckDB schema v26 → v27**: new tables `sovereign_series_metadata`,
  `sovereign_snapshot_attempts` and `sovereign_cross_checks`. Existing tables are
  unchanged. `macro_releases` SQLite stays at v5.

## [0.3.1] — 2026-10-18

Release train `ecosystem-2026.10.4`. Ships the production daily macro runner and the
ALFRED backfill to the `marketdata` environment. No new runtime dependency.

### Added
- **Daily official macro collection** (#105, M7a), `python -m market_data_hub.macro_releases.run_daily --db <path>`:
  discovers issuer editions (BLS, BEA, Eurostat, ONS) instead of fixed next-publication
  dates, verifies identity, structure, publication date and sanity bands
  (`--sanity-bands <JSON>` overrides), and routes unconfirmed observations to an
  idempotent `review_queue` (`needs_review` status; `review-queue` read-only CLI command).
  Lease-locked single writer, content-addressed `documents` table, bounded retries with
  exponential backoff and `Retry-After`, a dry-run that mirrors the real exit code.
  Exit codes: 0 new vintages, 3 nothing new (including review-only runs), 1 provider or
  infrastructure failure, 2 invalid arguments. `--db` stays mandatory; the manual
  development collector keeps its ProgramData refusal.
- **ALFRED vintage backfill** (#106), `python -m market_data_hub.alfred_backfill --db <path>`:
  a pinned 42-series universe (`config/alfred_backfill.yaml`, shipped in the wheel),
  paginated real-time periods, resumable row offsets and completed-vintage cursors,
  at most 5,000 rows per transaction under the hub's advisory lock, no DuckDB connection
  held during HTTP. New reader `reader.alfred_as_known_at`.

### Changed — schema migrations (both additive)
- **Hub DuckDB schema v25 → v26**: `alfred_vintage_observations` gains a nullable
  `realtime_end` column (`ALTER TABLE … ADD COLUMN`); new tables
  `alfred_backfill_progress` and `alfred_backfill_pending`. The
  `(series_id, date, as_of)` key is unchanged.
- **`macro_releases` SQLite schema v3 → v5**: v4 adds the run log (`collect_runs`,
  `collect_run_families`), v5 the shared `documents` table, the `review_queue` and the
  `needs_review` statuses. v3 evidence is untouched and v4 log rows are copied
  unchanged. Readers now accept versions 3–5; writers still reject newer schemas.

### Deployment note
- A v0.3.0 reader still requires `macro_releases` `user_version == 3`. Every
  environment that reads `macro_releases.sqlite` through `market_data_hub` must be on
  v0.3.1 **before** the first `run_daily` migrates that database to v5.

## [0.3.0] — 2026-10-18

Release train `ecosystem-2026.10.3`.

### Added
- **`market_data_hub.macro_releases`**, a new package for macro releases with separate
  publication history, knowledge history and raw evidence:
  - M4 (#101): SQLite contracts and migrations (schema v2/v3, shipped in the wheel as
    `macro_releases/*.sql`), typed writes and reads (`first_release`, `latest_official`,
    `as_known_at(t)`), and a read-only importer of the legacy LazyCommittee calendar.
  - M5 (#102): certified official adapters (BLS, BEA, Eurostat, ONS; FRED/ALFRED kept as
    unverified mirror evidence) and a manual collector CLI. The CLI requires an explicit
    database path, refuses ProgramData paths and creates no scheduled task.
  - M6 (#103): shadow release resolver and a read-only consumer API (release cards, empty-result
    reasons, family coverage, surprises at the inputs' decimal precision).
- Forex Factory raw archive (#100): a `manifest.jsonl` line per capture attempt, one retry on
  transient errors, `load_snapshots()`.

### Changed
- New runtime dependency `tzdata` on Windows (issuer-local release times need the IANA database).
- The `validate` extra requires `lazybridge[claude-code]>=1.8.1`, in line with the manifest.
- CI pins the shared `check_pins.py` at the candidate-aware commit (#99).

## [0.2.1] — 2026-10-15

### Changed
- No implicit database path: `MARKET_DATA_DB` or an explicit `db_path` is required (#97).

## [0.2.0] — 2026-10-15

### Changed
- DataSpace pinned to its `v0.1.0` tag (release train `ecosystem-2026.10`, #95).
