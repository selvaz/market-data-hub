# Changelog

All notable changes to this project are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions are released as `vX.Y.Z` tags; the supported combination with the other
ecosystem repositories is set by `LazyTools/ecosystem/compatibility.toml`.

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
