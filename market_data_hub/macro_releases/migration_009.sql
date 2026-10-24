-- Release-clock enrichment must not rewrite an immutable BOJ decision vintage.
CREATE TABLE boj_publication_times (
    id INTEGER PRIMARY KEY,
    value_vintage_id INTEGER NOT NULL REFERENCES value_vintages(id),
    issuer_published_at TEXT NOT NULL,
    publication_precision TEXT NOT NULL CHECK (publication_precision = 'exact'),
    issuer_local_time TEXT NOT NULL,
    issuer_timezone TEXT NOT NULL CHECK (issuer_timezone = 'Asia/Tokyo'),
    policy_decision_json TEXT NOT NULL CHECK (json_valid(policy_decision_json)),
    recorded_at TEXT NOT NULL,
    UNIQUE (value_vintage_id)
);
CREATE TRIGGER boj_publication_times_no_update BEFORE UPDATE ON boj_publication_times
BEGIN SELECT RAISE(ABORT, 'boj_publication_times is append-only'); END;
CREATE TRIGGER boj_publication_times_no_delete BEFORE DELETE ON boj_publication_times
BEGIN SELECT RAISE(ABORT, 'boj_publication_times is append-only'); END;
INSERT INTO schema_compatibility VALUES (9,3,9);
