-- Preserve existing run evidence byte-for-byte while extending its status enum.
CREATE TABLE collect_runs_v5 (
 run_id TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
 status TEXT NOT NULL CHECK(status IN ('running','ok','no_change','failed','needs_review')),
 families TEXT NOT NULL, parameters TEXT NOT NULL, code_version TEXT NOT NULL, host TEXT NOT NULL,
 PRIMARY KEY(run_id,status),
 CHECK((status='running' AND finished_at IS NULL) OR (status!='running' AND finished_at>=started_at))
);
INSERT INTO collect_runs_v5 SELECT * FROM collect_runs;
CREATE TABLE collect_run_families_v5 (
 run_id TEXT NOT NULL, run_status TEXT NOT NULL DEFAULT 'running' CHECK(run_status='running'),
 family TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('ok','no_change','failed','needs_review')),
 finished_at TEXT NOT NULL, error_type TEXT, error_message TEXT CHECK(length(error_message)<=240),
 vintages_added INTEGER NOT NULL CHECK(vintages_added>=0),
 readings_added INTEGER NOT NULL CHECK(readings_added>=0),
 readings_skipped INTEGER NOT NULL CHECK(readings_skipped>=0),
 fetch_since TEXT NOT NULL, fetch_until TEXT NOT NULL,
 PRIMARY KEY(run_id,family), FOREIGN KEY(run_id,run_status) REFERENCES collect_runs_v5(run_id,status)
);
INSERT INTO collect_run_families_v5 SELECT * FROM collect_run_families;
DROP TABLE collect_run_families;
DROP TABLE collect_runs;
ALTER TABLE collect_runs_v5 RENAME TO collect_runs;
ALTER TABLE collect_run_families_v5 RENAME TO collect_run_families;
CREATE UNIQUE INDEX collect_runs_terminal ON collect_runs(run_id) WHERE status!='running';
CREATE INDEX collect_family_history ON collect_run_families(family,finished_at);
CREATE TRIGGER collect_runs_no_update BEFORE UPDATE ON collect_runs
BEGIN SELECT RAISE(ABORT, 'collect_runs is append-only'); END;
CREATE TRIGGER collect_runs_no_delete BEFORE DELETE ON collect_runs
BEGIN SELECT RAISE(ABORT, 'collect_runs is append-only'); END;
CREATE TRIGGER collect_run_families_no_update BEFORE UPDATE ON collect_run_families
BEGIN SELECT RAISE(ABORT, 'collect_run_families is append-only'); END;
CREATE TRIGGER collect_run_families_no_delete BEFORE DELETE ON collect_run_families
BEGIN SELECT RAISE(ABORT, 'collect_run_families is append-only'); END;
CREATE TABLE documents (
 document_hash TEXT PRIMARY KEY, raw_hash TEXT NOT NULL, url TEXT NOT NULL,
 retrieved_at TEXT NOT NULL, body BLOB NOT NULL, normalization_version TEXT NOT NULL
);
CREATE TABLE review_queue (
 id TEXT PRIMARY KEY, family TEXT NOT NULL, run_id TEXT,
 document_hash TEXT NOT NULL REFERENCES documents(document_hash),
 reasons TEXT NOT NULL CHECK(json_valid(reasons)), recorded_at TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status='pending')
);
CREATE INDEX review_family ON review_queue(family,recorded_at);
CREATE TRIGGER documents_no_update BEFORE UPDATE ON documents
BEGIN SELECT RAISE(ABORT, 'documents is append-only'); END;
CREATE TRIGGER documents_no_delete BEFORE DELETE ON documents
BEGIN SELECT RAISE(ABORT, 'documents is append-only'); END;
CREATE TRIGGER review_queue_no_update BEFORE UPDATE ON review_queue
BEGIN SELECT RAISE(ABORT, 'review_queue is append-only'); END;
CREATE TRIGGER review_queue_no_delete BEFORE DELETE ON review_queue
BEGIN SELECT RAISE(ABORT, 'review_queue is append-only'); END;
CREATE TABLE schema_compatibility (
 schema_version INTEGER PRIMARY KEY, reader_floor INTEGER NOT NULL, reader_ceiling INTEGER NOT NULL
);
INSERT INTO schema_compatibility VALUES(5,3,5);
