# Changelog

All notable changes to this project are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions are released as `vX.Y.Z` tags; the supported combination with the other
ecosystem repositories is set by `LazyTools/ecosystem/compatibility.toml`.

## [Unreleased]

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
