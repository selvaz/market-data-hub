# Macro release history probe

The manual collector defaults to four months; the daily collector defaults to
24 months. ONS rate APIs return current snapshots and filter periods by `since`;
their current release edition must be reviewed. GDP growth and retail are
bulletin parsers limited to explicitly reviewed editions, so widening the
window alone cannot retrieve their full history. Eurostat activity similarly
parses reviewed headline publications rather than a historical series API.

`value_vintages` keeps publication/edition, document identity, revision kind,
and actual retrieval/first-seen time. `persist` labels a routine snapshot with
no earlier stored issuer observation `backfill`. The migrated `first_release`
view filters to `initial`, but the resolver has a separate evidence-selection
path that must be assessed. `first_seen_at` supports as-known-at filtering,
but late observation alone is not proof of an initial value.

## UK copy run, 2026-11-05

The SQLite backup API used a read-only source and `.tmp` destination; the
copy passed `quick_check`. One `collect` invocation with `--since 2018-01`
for GDP, CPI, labour, total pay and retail finished in 60.25 s with 11 ONS
HTTP attempts and no reported errors. It returned 636 observations and added
487 vintages. Each of GDP index, CPI, core CPI, unemployment, regular pay and
total pay gained 81 distinct periods, from 23 or 24 to 104 or 105, reaching
2018-01. GDP m/m remained at four periods and retail m/m at three. The
snapshot values are the current edition's historical rates, not reconstructed
first releases. The extra vintage beyond the 486 gained periods is an
additional edition for an already-covered period.
