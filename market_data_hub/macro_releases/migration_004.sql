-- Immutable lifecycle records: one running header and one terminal summary.
CREATE TABLE collect_runs (
    run_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'ok', 'no_change', 'failed')),
    families TEXT NOT NULL,
    parameters TEXT NOT NULL,
    code_version TEXT NOT NULL,
    host TEXT NOT NULL,
    PRIMARY KEY (run_id, status),
    CHECK ((status = 'running' AND finished_at IS NULL) OR
           (status != 'running' AND finished_at >= started_at))
);
CREATE UNIQUE INDEX collect_runs_terminal ON collect_runs(run_id) WHERE status != 'running';
CREATE TABLE collect_run_families (
    run_id TEXT NOT NULL,
    run_status TEXT NOT NULL DEFAULT 'running' CHECK (run_status = 'running'),
    family TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ok', 'no_change', 'failed')),
    finished_at TEXT NOT NULL,
    error_type TEXT,
    error_message TEXT CHECK (length(error_message) <= 240),
    vintages_added INTEGER NOT NULL CHECK (vintages_added >= 0),
    readings_added INTEGER NOT NULL CHECK (readings_added >= 0),
    readings_skipped INTEGER NOT NULL CHECK (readings_skipped >= 0),
    fetch_since TEXT NOT NULL,
    fetch_until TEXT NOT NULL,
    PRIMARY KEY (run_id, family),
    FOREIGN KEY (run_id, run_status) REFERENCES collect_runs(run_id, status)
);
CREATE INDEX collect_family_history ON collect_run_families(family, finished_at);
CREATE TRIGGER collect_runs_no_update BEFORE UPDATE ON collect_runs
BEGIN SELECT RAISE(ABORT, 'collect_runs is append-only'); END;
CREATE TRIGGER collect_runs_no_delete BEFORE DELETE ON collect_runs
BEGIN SELECT RAISE(ABORT, 'collect_runs is append-only'); END;
CREATE TRIGGER collect_run_families_no_update BEFORE UPDATE ON collect_run_families
BEGIN SELECT RAISE(ABORT, 'collect_run_families is append-only'); END;
CREATE TRIGGER collect_run_families_no_delete BEFORE DELETE ON collect_run_families
BEGIN SELECT RAISE(ABORT, 'collect_run_families is append-only'); END;
