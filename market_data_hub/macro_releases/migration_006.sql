-- Consensus ingestion is additive; official collection statuses stay independent.
CREATE TABLE consensus_snapshots (
    snapshot_file TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    processed_at TEXT NOT NULL,
    result_json TEXT NOT NULL CHECK (json_valid(result_json)),
    PRIMARY KEY (snapshot_file, captured_at, content_hash, rule_version)
);
CREATE TABLE collect_run_consensus (
    run_id TEXT PRIMARY KEY,
    run_status TEXT NOT NULL DEFAULT 'running' CHECK (run_status = 'running'),
    status TEXT NOT NULL CHECK (status IN ('ok','no_change','failed')),
    finished_at TEXT NOT NULL,
    result_json TEXT NOT NULL CHECK (json_valid(result_json)),
    FOREIGN KEY (run_id, run_status) REFERENCES collect_runs(run_id, status)
);
CREATE TRIGGER consensus_snapshots_no_update BEFORE UPDATE ON consensus_snapshots
BEGIN SELECT RAISE(ABORT, 'consensus_snapshots is append-only'); END;
CREATE TRIGGER consensus_snapshots_no_delete BEFORE DELETE ON consensus_snapshots
BEGIN SELECT RAISE(ABORT, 'consensus_snapshots is append-only'); END;
CREATE TRIGGER collect_run_consensus_no_update BEFORE UPDATE ON collect_run_consensus
BEGIN SELECT RAISE(ABORT, 'collect_run_consensus is append-only'); END;
CREATE TRIGGER collect_run_consensus_no_delete BEFORE DELETE ON collect_run_consensus
BEGIN SELECT RAISE(ABORT, 'collect_run_consensus is append-only'); END;
INSERT INTO schema_compatibility VALUES (6,3,6);
