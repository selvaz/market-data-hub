-- M4 contracts. Selection views below are an M6 placeholder, not a resolver.
CREATE TABLE indicators (
    id TEXT PRIMARY KEY,
    measure TEXT NOT NULL,
    geography TEXT NOT NULL,
    seasonal_adjustment TEXT NOT NULL,
    transform TEXT NOT NULL CHECK (transform IN ('level','mm','yy','qq','qq_saar','change','unknown')),
    unit TEXT NOT NULL,
    scale REAL NOT NULL CHECK (scale > 0),
    frequency TEXT NOT NULL,
    issuer_id TEXT NOT NULL,
    tier TEXT CHECK (tier IN ('T1','T2','T3')),
    definition TEXT NOT NULL,
    measure_kind TEXT NOT NULL DEFAULT 'statistic',
    catalogue_status TEXT NOT NULL CHECK (catalogue_status IN ('supported','candidate','duplicate','out_of_scope')),
    active INTEGER NOT NULL CHECK (active IN (0,1))
);
CREATE TABLE source_series (
    id TEXT PRIMARY KEY,
    indicator_id TEXT NOT NULL REFERENCES indicators(id),
    source_id TEXT NOT NULL,
    issuer_id TEXT NOT NULL,
    distributor_id TEXT NOT NULL,
    source_key TEXT NOT NULL,
    expected_lag_seconds INTEGER CHECK (expected_lag_seconds >= 0),
    is_published INTEGER NOT NULL CHECK (is_published IN (0,1)),
    evidence_role TEXT NOT NULL CHECK (evidence_role IN ('issuer','mirror','web')),
    UNIQUE (indicator_id, source_id, source_key),
    UNIQUE (id, indicator_id)
);
CREATE TABLE release_events (
    id TEXT PRIMARY KEY,
    issuer_id TEXT NOT NULL,
    family TEXT NOT NULL,
    estimate_stage TEXT NOT NULL CHECK (length(estimate_stage) > 0),
    edition TEXT NOT NULL,
    UNIQUE (issuer_id, family, estimate_stage, edition)
);
CREATE TABLE schedule_versions (
    id INTEGER PRIMARY KEY,
    release_event_id TEXT NOT NULL REFERENCES release_events(id),
    expected_at TEXT NOT NULL,
    issuer_timezone TEXT NOT NULL,
    precision TEXT NOT NULL CHECK (precision IN ('exact','date_only')),
    source_id TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE (release_event_id, expected_at, issuer_timezone, precision, source_id, recorded_at)
);
CREATE TABLE value_vintages (
    id INTEGER PRIMARY KEY,
    indicator_id TEXT NOT NULL REFERENCES indicators(id),
    reference_period TEXT NOT NULL CHECK (reference_period NOT LIKE 'd:%' AND length(reference_period) > 0),
    release_event_id TEXT NOT NULL REFERENCES release_events(id),
    source_series_id TEXT NOT NULL,
    value REAL NOT NULL,
    published_as TEXT NOT NULL,
    unit TEXT NOT NULL,
    scale REAL NOT NULL CHECK (scale > 0),
    estimate_stage TEXT NOT NULL CHECK (length(estimate_stage) > 0),
    revision_kind TEXT NOT NULL,
    issuer_published_at TEXT NOT NULL,
    publication_precision TEXT NOT NULL CHECK (publication_precision IN ('exact','date_only')),
    provider_vintage_id TEXT,
    first_seen_at TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    document_hash TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    extraction_method TEXT NOT NULL,
    verification_status TEXT NOT NULL CHECK (verification_status IN ('unverified','verified','rejected')),
    derived_from_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(derived_from_json)),
    formula_version TEXT,
    FOREIGN KEY (source_series_id, indicator_id) REFERENCES source_series(id, indicator_id),
    UNIQUE (source_series_id, reference_period, release_event_id, document_hash)
);
CREATE TABLE readings (
    id INTEGER PRIMARY KEY,
    indicator_id TEXT REFERENCES indicators(id),
    release_event_id TEXT REFERENCES release_events(id),
    source_id TEXT NOT NULL,
    series_key TEXT NOT NULL,
    period_key TEXT NOT NULL,
    reference_period TEXT,
    period_unknown_reason TEXT,
    edition TEXT NOT NULL,
    estimate_stage TEXT NOT NULL CHECK (length(estimate_stage) > 0),
    reading_kind TEXT NOT NULL,
    value REAL,
    published_as TEXT,
    unit TEXT,
    scale REAL CHECK (scale > 0),
    issuer_id TEXT NOT NULL,
    distributor_id TEXT NOT NULL,
    evidence_origin_id TEXT NOT NULL,
    published_at TEXT,
    publication_precision TEXT CHECK (publication_precision IN ('exact','date_only')),
    first_seen_at TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    extraction_method TEXT NOT NULL,
    verification_status TEXT NOT NULL CHECK (verification_status IN ('unverified','verified','rejected')),
    url TEXT,
    raw_payload_json TEXT NOT NULL CHECK (json_valid(raw_payload_json)),
    CHECK ((reference_period IS NULL AND period_unknown_reason IS NOT NULL) OR
           (reference_period IS NOT NULL AND reference_period NOT LIKE 'd:%' AND period_unknown_reason IS NULL)),
    UNIQUE (source_id, series_key, period_key, edition, content_hash)
);
CREATE TABLE resolution_versions (
    id INTEGER PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    indicator_id TEXT NOT NULL REFERENCES indicators(id),
    reference_period TEXT NOT NULL,
    estimate_stage TEXT NOT NULL CHECK (length(estimate_stage) > 0),
    selected_reading_id INTEGER REFERENCES readings(id),
    selected_vintage_id INTEGER REFERENCES value_vintages(id),
    rejected_alternatives_json TEXT NOT NULL CHECK (json_valid(rejected_alternatives_json)),
    rule_version TEXT NOT NULL,
    reason TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    CHECK ((selected_reading_id IS NOT NULL) + (selected_vintage_id IS NOT NULL) = 1)
);
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
    CHECK (post_release = CASE WHEN release_cutoff_at IS NOT NULL AND captured_at >= release_cutoff_at THEN 1 ELSE 0 END),
    UNIQUE (provider, indicator_id, reference_period, estimate_stage, release_event_id, captured_at, content_hash)
);
CREATE TABLE claims (
    id INTEGER PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    release_event_id TEXT NOT NULL REFERENCES release_events(id),
    claim_type TEXT NOT NULL CHECK (claim_type IN ('release_fact','attributed_interpretation','market_response')),
    text TEXT NOT NULL,
    attributed_to TEXT,
    news_cutoff TEXT NOT NULL,
    created_at TEXT NOT NULL,
    supersedes_id INTEGER REFERENCES claims(id)
);
CREATE TABLE claim_evidence (
    id INTEGER PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    claim_id INTEGER NOT NULL REFERENCES claims(id),
    verbatim_passage TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    url TEXT NOT NULL,
    page_id TEXT,
    evidence_origin_id TEXT NOT NULL,
    published_at TEXT,
    retrieved_at TEXT NOT NULL,
    news_cutoff TEXT NOT NULL
);
CREATE TABLE discrepancies (
    id INTEGER PRIMARY KEY,
    reading_id INTEGER NOT NULL REFERENCES readings(id),
    selected_vintage_id INTEGER NOT NULL REFERENCES value_vintages(id),
    reason TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE (reading_id, selected_vintage_id)
);
-- Preserve every legacy row and alias as a candidate, even when two codes map together.
CREATE TABLE legacy_candidates (
    source_id TEXT NOT NULL,
    legacy_code TEXT NOT NULL,
    indicator_id TEXT NOT NULL REFERENCES indicators(id),
    estimate_stage TEXT NOT NULL,
    mapping_reason TEXT NOT NULL,
    raw_payload_json TEXT NOT NULL CHECK (json_valid(raw_payload_json)),
    PRIMARY KEY (source_id, legacy_code)
);
CREATE TABLE indicator_aliases (
    source_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    geography TEXT NOT NULL,
    legacy_code TEXT NOT NULL,
    indicator_id TEXT NOT NULL REFERENCES indicators(id),
    estimate_stage TEXT NOT NULL,
    catalogue_status TEXT NOT NULL DEFAULT 'candidate' CHECK (catalogue_status = 'candidate'),
    PRIMARY KEY (source_id, alias, geography)
);
CREATE TABLE import_worklist (
    source_id TEXT NOT NULL,
    occurrence_key TEXT NOT NULL,
    indicator_id TEXT REFERENCES indicators(id),
    reason TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','complete')),
    first_seen_at TEXT NOT NULL,
    last_checked_at TEXT NOT NULL,
    next_check_at TEXT,
    PRIMARY KEY (source_id, occurrence_key)
);
CREATE INDEX vintages_knowledge ON value_vintages(first_seen_at);
CREATE INDEX vintages_measure ON value_vintages(indicator_id, reference_period, issuer_published_at);
CREATE INDEX readings_origin ON readings(evidence_origin_id);
CREATE INDEX worklist_pending ON import_worklist(status, next_check_at);
-- Verified issuer publication contents can be served by a distributor. Format is irrelevant.
CREATE VIEW official_candidates AS
SELECT v.*, 'placeholder:m4-verified-issuer-v1' AS selection_rule_version
FROM value_vintages v
JOIN source_series s ON s.id = v.source_series_id
JOIN indicators i ON i.id = v.indicator_id
JOIN release_events e ON e.id = v.release_event_id
WHERE v.verification_status = 'verified' AND s.evidence_role = 'issuer'
  AND s.is_published = 1 AND s.issuer_id = i.issuer_id AND e.issuer_id = i.issuer_id;
CREATE VIEW first_release AS
SELECT * FROM (
    SELECT v.*, ROW_NUMBER() OVER (PARTITION BY indicator_id, reference_period
      ORDER BY issuer_published_at, first_seen_at, id) AS selection_rank
    FROM official_candidates v
) WHERE selection_rank = 1;
CREATE VIEW latest_official AS
SELECT * FROM (
    SELECT v.*, ROW_NUMBER() OVER (PARTITION BY indicator_id, reference_period
      ORDER BY issuer_published_at DESC, first_seen_at DESC, id DESC) AS selection_rank
    FROM official_candidates v
) WHERE selection_rank = 1;
