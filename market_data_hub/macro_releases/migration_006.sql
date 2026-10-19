-- Final import decisions are separate from immutable legacy observations.
CREATE TABLE worklist_resolutions (
 source_id TEXT NOT NULL, occurrence_key TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('complete','terminal')),
 reason TEXT NOT NULL, indicator_id TEXT REFERENCES indicators(id),
 reference_period TEXT, estimate_stage TEXT NOT NULL,
 selected_vintage_id INTEGER REFERENCES value_vintages(id),
 rule_version TEXT NOT NULL, resolved_at TEXT NOT NULL,
 evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
 PRIMARY KEY(source_id,occurrence_key),
 FOREIGN KEY(source_id,occurrence_key) REFERENCES import_worklist(source_id,occurrence_key),
 CHECK(status!='complete' OR (indicator_id IS NOT NULL AND reference_period IS NOT NULL)),
 CHECK(reference_period IS NULL OR reference_period NOT LIKE 'd:%')
);
CREATE TRIGGER worklist_resolutions_no_update BEFORE UPDATE ON worklist_resolutions
BEGIN SELECT RAISE(ABORT, 'worklist_resolutions is append-only'); END;
CREATE TRIGGER worklist_resolutions_no_delete BEFORE DELETE ON worklist_resolutions
BEGIN SELECT RAISE(ABORT, 'worklist_resolutions is append-only'); END;
INSERT INTO schema_compatibility VALUES(6,3,6);
