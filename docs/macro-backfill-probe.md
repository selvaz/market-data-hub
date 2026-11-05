# Macro release history probe

The manual collector defaults to four months; the daily collector defaults to
24 months. ONS rate APIs return current snapshots and filter periods by `since`;
their current release edition must be reviewed. GDP growth and retail are
bulletin parsers limited to explicitly reviewed editions, so widening the
window alone cannot retrieve their full history. Eurostat activity similarly
parses reviewed headline publications rather than a historical series API.

`value_vintages` keeps publication/edition, document identity, revision kind,
and actual retrieval/first-seen time. `persist` labels a routine snapshot with
no earlier stored issuer observation `backfill`. However `first_release`
selects the earliest stored official edition without excluding backfills; it
does not prove an observation was the original release. `first_seen_at` supports
as-known-at filtering, but late observation alone is not proof of an initial
value. The probe below measures the existing behavior, not a production change.
