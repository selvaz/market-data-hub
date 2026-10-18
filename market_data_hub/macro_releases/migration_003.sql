-- Source composition and issuer-local publication evidence are additive.
ALTER TABLE value_vintages ADD COLUMN composition_id TEXT;
ALTER TABLE value_vintages ADD COLUMN issuer_local_time TEXT;
ALTER TABLE value_vintages ADD COLUMN issuer_timezone TEXT;
ALTER TABLE readings ADD COLUMN composition_id TEXT;
ALTER TABLE readings ADD COLUMN issuer_local_time TEXT;
ALTER TABLE readings ADD COLUMN issuer_timezone TEXT;

DROP VIEW first_release;
DROP VIEW latest_official;
DROP VIEW official_candidates;
CREATE VIEW official_candidates AS
SELECT v.*, 'placeholder:m5-verified-issuer-v2' AS selection_rule_version
FROM value_vintages v
JOIN source_series s ON s.id = v.source_series_id
JOIN indicators i ON i.id = v.indicator_id
JOIN release_events e ON e.id = v.release_event_id
WHERE v.verification_status = 'verified' AND s.evidence_role = 'issuer'
  AND s.is_published = 1 AND s.issuer_id = i.issuer_id AND e.issuer_id = i.issuer_id
  -- v2 stores may contain the old tool-rendered archive classification. Keep
  -- those append-only rows intact but exclude that known extraction from authority.
  AND NOT (s.source_id = 'BLS' AND s.source_key = 'CES0000000001:archive-change');
CREATE VIEW first_release AS
SELECT * FROM (
    SELECT v.*, ROW_NUMBER() OVER (PARTITION BY indicator_id, reference_period
      ORDER BY issuer_published_at, first_seen_at, id) AS selection_rank
    FROM official_candidates v WHERE revision_kind = 'initial'
) WHERE selection_rank = 1;
CREATE VIEW latest_official AS
SELECT * FROM (
    SELECT v.*, ROW_NUMBER() OVER (PARTITION BY indicator_id, reference_period
      ORDER BY issuer_published_at DESC, first_seen_at DESC, id DESC) AS selection_rank
    FROM official_candidates v
) WHERE selection_rank = 1;
