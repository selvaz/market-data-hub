-- Schema v2: immutable raw identity links, terminal backlog, safe date-only classification.
ALTER TABLE import_worklist RENAME TO import_worklist_v1;
CREATE TABLE import_worklist (
    source_id TEXT NOT NULL,
    occurrence_key TEXT NOT NULL,
    indicator_id TEXT REFERENCES indicators(id),
    reason TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','complete','terminal')),
    first_seen_at TEXT NOT NULL,
    last_checked_at TEXT NOT NULL,
    next_check_at TEXT,
    PRIMARY KEY (source_id, occurrence_key)
);
INSERT INTO import_worklist SELECT * FROM import_worklist_v1;
DROP TABLE import_worklist_v1;
CREATE INDEX worklist_pending ON import_worklist(status, next_check_at);
CREATE TABLE legacy_reading_links (
    reading_id INTEGER PRIMARY KEY REFERENCES readings(id),
    source_id TEXT NOT NULL,
    legacy_table TEXT NOT NULL CHECK (legacy_table IN ('sightings','observations')),
    legacy_row_id INTEGER NOT NULL,
    reading_kind TEXT NOT NULL,
    source_index INTEGER NOT NULL,
    legacy_code TEXT,
    indicator_id TEXT REFERENCES indicators(id),
    estimate_stage TEXT NOT NULL,
    occurrence_key TEXT,
    resolution TEXT,
    unresolved_reason TEXT,
    legacy_run_status TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (source_id, legacy_table, legacy_row_id, reading_kind, source_index)
);
CREATE INDEX legacy_links_indicator ON legacy_reading_links(indicator_id);
ALTER TABLE consensus_readings RENAME TO consensus_readings_v1;
CREATE TABLE consensus_readings (
    id INTEGER PRIMARY KEY,
    provider TEXT NOT NULL,
    survey_identity TEXT,
    indicator_id TEXT NOT NULL REFERENCES indicators(id),
    release_event_id TEXT NOT NULL REFERENCES release_events(id),
    measure TEXT NOT NULL,
    reference_period TEXT NOT NULL,
    estimate_stage TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    scale REAL NOT NULL CHECK (scale > 0),
    published_at TEXT,
    captured_at TEXT NOT NULL,
    release_cutoff_at TEXT,
    cutoff_precision TEXT CHECK (cutoff_precision IN ('exact','date_only')),
    cutoff_basis TEXT NOT NULL CHECK (cutoff_basis IN ('scheduled','actual','unknown')),
    pre_release_eligible INTEGER NOT NULL CHECK (pre_release_eligible IN (0,1)),
    post_release INTEGER NOT NULL CHECK (post_release IN (0,1)),
    content_hash TEXT NOT NULL,
    CHECK (pre_release_eligible = CASE WHEN release_cutoff_at IS NOT NULL AND captured_at < release_cutoff_at THEN 1 ELSE 0 END),
    CHECK (post_release = CASE WHEN cutoff_precision = 'exact' AND release_cutoff_at IS NOT NULL AND captured_at >= release_cutoff_at THEN 1 ELSE 0 END),
    UNIQUE (provider, indicator_id, reference_period, estimate_stage, release_event_id, captured_at, content_hash)
);
INSERT INTO consensus_readings (id, provider, survey_identity, indicator_id, release_event_id, measure, reference_period, estimate_stage, value, unit, scale, published_at, captured_at, release_cutoff_at, cutoff_precision, cutoff_basis, pre_release_eligible, post_release, content_hash) SELECT id, provider, survey_identity, indicator_id, release_event_id, measure, reference_period, estimate_stage, value, unit, scale, published_at, captured_at, release_cutoff_at, cutoff_precision, cutoff_basis, pre_release_eligible, CASE WHEN cutoff_precision='exact' AND release_cutoff_at IS NOT NULL AND captured_at >= release_cutoff_at THEN 1 ELSE 0 END, content_hash FROM consensus_readings_v1;
DROP TABLE consensus_readings_v1;
