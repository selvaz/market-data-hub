-- Preserve raw observations while retiring a corrected issuer association.
CREATE TABLE consensus_link_versions (
    id INTEGER PRIMARY KEY,
    consensus_reading_id INTEGER NOT NULL REFERENCES consensus_readings(id),
    recorded_at TEXT NOT NULL
);
CREATE INDEX consensus_observation_identity
ON consensus_readings(provider, captured_at, content_hash);
CREATE INDEX consensus_link_target ON consensus_link_versions(consensus_reading_id, id);
CREATE TRIGGER consensus_link_versions_no_update BEFORE UPDATE ON consensus_link_versions
BEGIN SELECT RAISE(ABORT, 'consensus_link_versions is append-only'); END;
CREATE TRIGGER consensus_link_versions_no_delete BEFORE DELETE ON consensus_link_versions
BEGIN SELECT RAISE(ABORT, 'consensus_link_versions is append-only'); END;
INSERT INTO schema_compatibility VALUES (7,3,7);
