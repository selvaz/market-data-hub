-- Additive: why a family needed review, and closure of superseded review rows.
-- Existing tables and rows are untouched; both new tables are append-only.
CREATE TABLE collect_run_family_reviews (
 run_id TEXT NOT NULL, family TEXT NOT NULL,
 error_category TEXT CHECK(error_category IS NULL OR length(error_category)<=40),
 review_reasons TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(review_reasons) AND length(review_reasons)<=16000),
 recorded_at TEXT NOT NULL,
 PRIMARY KEY(run_id,family),
 FOREIGN KEY(run_id,family) REFERENCES collect_run_families(run_id,family)
);
CREATE TRIGGER collect_run_family_reviews_no_update BEFORE UPDATE ON collect_run_family_reviews
BEGIN SELECT RAISE(ABORT, 'collect_run_family_reviews is append-only'); END;
CREATE TRIGGER collect_run_family_reviews_no_delete BEFORE DELETE ON collect_run_family_reviews
BEGIN SELECT RAISE(ABORT, 'collect_run_family_reviews is append-only'); END;
-- review_queue keeps its original rows ('pending'); a resolution row moves the
-- effective status to 'resolved' (see review_queue_current).
CREATE TABLE review_queue_resolutions (
 review_id TEXT PRIMARY KEY REFERENCES review_queue(id),
 status TEXT NOT NULL DEFAULT 'resolved' CHECK(status='resolved'),
 resolved_at TEXT NOT NULL,
 run_id TEXT NOT NULL,
 basis TEXT NOT NULL CHECK(basis IN ('same_edition_verified','same_url_verified'))
);
CREATE TRIGGER review_queue_resolutions_no_update BEFORE UPDATE ON review_queue_resolutions
BEGIN SELECT RAISE(ABORT, 'review_queue_resolutions is append-only'); END;
CREATE TRIGGER review_queue_resolutions_no_delete BEFORE DELETE ON review_queue_resolutions
BEGIN SELECT RAISE(ABORT, 'review_queue_resolutions is append-only'); END;
CREATE VIEW review_queue_current AS
SELECT q.id, q.family, q.run_id, q.document_hash, q.reasons, q.recorded_at,
       COALESCE(r.status, q.status) AS status, r.resolved_at, r.run_id AS resolved_run_id,
       r.basis AS resolution_basis
FROM review_queue q LEFT JOIN review_queue_resolutions r ON r.review_id=q.id;
-- A review row is superseded by a later clean (ok/no_change) run of its family
-- once every (measure, edition, period) read from the reviewed document has a verified
-- issuer reading or vintage known by that run (a clean re-read of identical
-- bytes adds a verified vintage; the original reading row stays unverified); a document that yielded no readings is
-- superseded by a verified reading from the same issuer URL first seen after
-- the review was recorded.
CREATE VIEW review_queue_supersessions AS
SELECT q.id AS review_id, f.run_id AS clean_run_id, f.finished_at AS clean_finished_at,
       CASE WHEN EXISTS (SELECT 1 FROM readings d WHERE d.content_hash=q.document_hash)
            THEN 'same_edition_verified' ELSE 'same_url_verified' END AS basis
FROM review_queue q
JOIN collect_run_families f
  ON f.family=q.family AND f.status IN ('ok','no_change') AND f.finished_at>q.recorded_at
 AND f.run_id IS NOT q.run_id
WHERE (
  EXISTS (SELECT 1 FROM readings d WHERE d.content_hash=q.document_hash)
  AND NOT EXISTS (
    SELECT 1 FROM readings d WHERE d.content_hash=q.document_hash
      AND NOT EXISTS (
        SELECT 1 FROM readings v WHERE v.indicator_id IS d.indicator_id AND v.edition=d.edition
          AND v.reference_period IS d.reference_period
          AND v.verification_status='verified' AND v.first_seen_at<=f.finished_at)
      AND NOT EXISTS (
        SELECT 1 FROM value_vintages w WHERE w.indicator_id=d.indicator_id
          AND w.release_event_id=d.release_event_id AND w.reference_period=d.reference_period
          AND w.verification_status='verified' AND w.first_seen_at<=f.finished_at))
) OR (
  NOT EXISTS (SELECT 1 FROM readings d WHERE d.content_hash=q.document_hash)
  AND EXISTS (
    SELECT 1 FROM documents doc JOIN readings v ON v.url=doc.url
    WHERE doc.document_hash=q.document_hash AND v.verification_status='verified'
      AND v.first_seen_at>q.recorded_at AND v.first_seen_at<=f.finished_at)
);
CREATE INDEX IF NOT EXISTS readings_content_hash ON readings(content_hash);
CREATE INDEX IF NOT EXISTS readings_indicator_edition ON readings(indicator_id, edition);
CREATE INDEX IF NOT EXISTS readings_url ON readings(url);
CREATE INDEX IF NOT EXISTS value_vintages_indicator_event ON value_vintages(indicator_id, release_event_id);
-- One-off backfill: close rows already superseded by the earliest such run.
INSERT INTO review_queue_resolutions (review_id,status,resolved_at,run_id,basis)
SELECT s.review_id, 'resolved', s.clean_finished_at, s.clean_run_id, s.basis
FROM review_queue_supersessions s
WHERE s.clean_finished_at = (SELECT MIN(t.clean_finished_at) FROM review_queue_supersessions t
                             WHERE t.review_id=s.review_id)
GROUP BY s.review_id;
INSERT INTO schema_compatibility VALUES (10,3,10);
