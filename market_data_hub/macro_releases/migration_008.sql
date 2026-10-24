-- Preserve complete policy decisions with the exact immutable value vintage.
ALTER TABLE value_vintages ADD COLUMN policy_decision_json TEXT;
INSERT INTO schema_compatibility VALUES (8,3,8);
