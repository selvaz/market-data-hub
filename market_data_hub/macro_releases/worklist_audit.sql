-- Optional resolver feature at schema v5; ordinary readers/writers ignore it.
CREATE TABLE IF NOT EXISTS worklist_resolutions (
 audit_id INTEGER PRIMARY KEY, run_id TEXT NOT NULL,
 source_id TEXT NOT NULL, occurrence_key TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','complete','terminal')),
 reason TEXT NOT NULL, indicator_id TEXT REFERENCES indicators(id),
 reference_period TEXT, estimate_stage TEXT NOT NULL,
 selected_vintage_id INTEGER REFERENCES value_vintages(id),
 rule_version TEXT NOT NULL, resolved_at TEXT NOT NULL,
 evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
 prior_status TEXT NOT NULL, prior_reason TEXT NOT NULL,
 prior_state_json TEXT NOT NULL CHECK(json_valid(prior_state_json)),
 UNIQUE(run_id,source_id,occurrence_key),
 FOREIGN KEY(source_id,occurrence_key) REFERENCES import_worklist(source_id,occurrence_key),
 CHECK(status!='complete' OR (indicator_id IS NOT NULL AND reference_period IS NOT NULL)),
 CHECK(reference_period IS NULL OR reference_period NOT LIKE 'd:%')
);
CREATE TABLE IF NOT EXISTS worklist_reverts (
 run_id TEXT PRIMARY KEY, reverted_at TEXT NOT NULL
);
CREATE VIEW IF NOT EXISTS active_worklist_resolutions AS
SELECT * FROM (
 SELECT w.*,ROW_NUMBER() OVER(PARTITION BY source_id,occurrence_key ORDER BY audit_id DESC) AS resolution_rank
 FROM worklist_resolutions w WHERE NOT EXISTS(SELECT 1 FROM worklist_reverts r WHERE r.run_id=w.run_id)
) WHERE resolution_rank=1;
CREATE TRIGGER IF NOT EXISTS worklist_resolutions_no_update BEFORE UPDATE ON worklist_resolutions
BEGIN SELECT RAISE(ABORT, 'worklist_resolutions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS worklist_resolutions_no_delete BEFORE DELETE ON worklist_resolutions
BEGIN SELECT RAISE(ABORT, 'worklist_resolutions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS worklist_reverts_no_update BEFORE UPDATE ON worklist_reverts
BEGIN SELECT RAISE(ABORT, 'worklist_reverts is append-only'); END;
CREATE TRIGGER IF NOT EXISTS worklist_reverts_no_delete BEFORE DELETE ON worklist_reverts
BEGIN SELECT RAISE(ABORT, 'worklist_reverts is append-only'); END;
