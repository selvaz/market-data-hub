-- Verbatim _SCHEMA_V1 through _SCHEMA_V4 from LazyCommittee calendar/store.py
-- Captured 2026-10-17; source schema version 4. No live DB used.

CREATE TABLE runs (
    run_id TEXT PRIMARY KEY,
    as_of TEXT NOT NULL,
    knowledge_cutoff TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'published', 'failed')),
    config_fingerprint TEXT NOT NULL,
    summary_json TEXT
);

CREATE TABLE run_tasks (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    searcher TEXT NOT NULL,
    region TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ok', 'failed', 'invalid_json', 'timeout')),
    attempts INTEGER NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    error TEXT,
    n_rows INTEGER NOT NULL,
    raw_response TEXT,
    PRIMARY KEY (run_id, searcher, region)
);

CREATE TABLE sightings (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    searcher TEXT NOT NULL,
    region TEXT NOT NULL,
    jurisdiction TEXT NOT NULL,
    raw_name TEXT NOT NULL,
    raw_period TEXT,
    release_at_utc TEXT NOT NULL,
    release_date TEXT NOT NULL,
    raw_actual TEXT,
    raw_consensus TEXT,
    raw_previous TEXT,
    unit_hint TEXT,
    sources_json TEXT NOT NULL CHECK (json_valid(sources_json)),
    indicator_code TEXT,
    resolution TEXT CHECK (resolution IN ('alias', 'fuzzy', 'cataloguer', 'unresolved')),
    occurrence_id INTEGER
);

CREATE TABLE indicators (
    code TEXT PRIMARY KEY,
    jurisdiction TEXT NOT NULL,
    name TEXT NOT NULL,
    area TEXT,
    kind TEXT,
    explanation TEXT,
    methodology TEXT,
    agency TEXT,
    tier TEXT CHECK (tier IN ('T1', 'T2', 'T3')),
    unit_code TEXT,
    transform TEXT,
    basis TEXT,
    frequency TEXT,
    created_at TEXT NOT NULL,
    created_by_run TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE indicator_aliases (
    alias_norm TEXT NOT NULL,
    jurisdiction TEXT NOT NULL,
    code TEXT NOT NULL REFERENCES indicators(code),
    created_by_run TEXT NOT NULL,
    PRIMARY KEY (alias_norm, jurisdiction)
);

CREATE TABLE occurrences (
    id INTEGER PRIMARY KEY,
    indicator_code TEXT NOT NULL REFERENCES indicators(code),
    period_norm TEXT NOT NULL,
    release_date TEXT NOT NULL,
    scheduled_at_utc TEXT,
    created_by_run TEXT NOT NULL,
    UNIQUE (indicator_code, period_norm)
);

CREATE TABLE observations (
    id INTEGER PRIMARY KEY,
    occurrence_id INTEGER NOT NULL REFERENCES occurrences(id),
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    observed_at TEXT NOT NULL,
    release_date TEXT NOT NULL,
    scheduled_at_utc TEXT,
    actual_raw TEXT,
    actual_value REAL,
    consensus_raw TEXT,
    consensus_value REAL,
    previous_raw TEXT,
    previous_value REAL,
    unit_code TEXT,
    scale INTEGER,
    status TEXT NOT NULL CHECK (
        status IN ('scheduled', 'released', 'released_no_value', 'disputed')
    ),
    n_searchers INTEGER NOT NULL,
    n_domains INTEGER NOT NULL,
    official_source INTEGER NOT NULL,
    news_mentions INTEGER,
    sources_json TEXT NOT NULL CHECK (json_valid(sources_json)),
    UNIQUE (occurrence_id, run_id)
);

CREATE INDEX ix_sightings_run ON sightings(run_id);
CREATE INDEX ix_observations_run ON observations(run_id);
CREATE INDEX ix_occurrences_release ON occurrences(release_date);
CREATE INDEX ix_runs_as_of ON runs(status, as_of);

-- Raw evidence never changes.  Resolution metadata is deliberately excluded:
-- the required resolve/publish lifecycle fills those three nullable columns.
CREATE TRIGGER sightings_no_raw_update BEFORE UPDATE OF
    run_id, searcher, region, jurisdiction, raw_name, raw_period,
    release_at_utc, release_date, raw_actual, raw_consensus, raw_previous,
    unit_hint, sources_json ON sightings BEGIN
    SELECT RAISE(ABORT, 'sightings is append-only');
END;

CREATE TRIGGER sightings_no_delete BEFORE DELETE ON sightings BEGIN
    SELECT RAISE(ABORT, 'sightings is append-only');
END;

CREATE TRIGGER observations_no_update BEFORE UPDATE ON observations BEGIN
    SELECT RAISE(ABORT, 'observations is append-only');
END;

CREATE TRIGGER observations_no_delete BEFORE DELETE ON observations BEGIN
    SELECT RAISE(ABORT, 'observations is append-only');
END;

CREATE TABLE digests (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    region TEXT NOT NULL,
    body TEXT NOT NULL,
    claims_json TEXT NOT NULL CHECK (json_valid(claims_json)),
    model TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, region)
);

ALTER TABLE indicators ADD COLUMN scale INTEGER;

ALTER TABLE sightings ADD COLUMN unresolved_reason TEXT;

PRAGMA user_version = 4;
