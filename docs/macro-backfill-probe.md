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

All 486 newly covered 2018-01 through 2024-09 vintage rows are `backfill`;
their first-seen timestamps record collection time. The copy's actual migrated
`first_release` view requires `revision_kind = 'initial'`, so it excludes
these rows. `latest_official` includes them by design. `resolve_evidence` also
selects the latest compatible issuer edition without filtering revision kind;
it returns kind in evidence provenance, not as a prominent selected-value
field. It filters observations by first-seen for an as-known-at query. Thus the
store already has the necessary marker, but a consumer must explicitly choose
initial-only history versus revised snapshots. Before production, expose the
selected revision kind prominently and require an explicit history policy in
the macro framework, with regression tests for both policies and cutoff time.
Use `first_release` only where genuine initial observations are available;
tagged snapshots do not create historical point-in-time observations. Check
other adapters as well: archived final headlines may also be marked `backfill`
when no prior observation exists, even if the bulletin is a true first print.

## Eurostat copy run

The same copy then ran activity IP and retail with `--since 2018-01`: 60.81 s,
five Eurostat HTTP attempts, four observations reused, zero new vintages, and
no reported errors. Both indicators remained at two periods (2026-07 and
2026-08). The calendar request is fixed to September 2026 to January 2027,
and the reviewed edition map is narrow; each parsed publication yields its
headline only. A longer `since` does not discover older releases. Deep
first-print history requires reviewed archived bulletins and composition
validation; a separate current-series API would instead yield revised history
and needs explicit vintage labeling, including geography/composition checks.

## Policy

`resolve(store, indicator, period, policy=..., as_known_at=...)` selects a
value for a period and exposes `revision_kind` directly, as well as in
provenance. `ReadAPI.release_cards`, `first_seen_release_cards` and
`decision_history` accept the same policy; `Store.release_cards` and
`Store.decision_history` forward it. Card resolution and previous/revision
objects carry the selected kind, and cards with an actual also expose it at
the top level. A selected reading without a stored kind returns `None`, not a
guessed kind. Cards are event-based, so use `resolve` for one selected value
per period.

* `first_release`: only verified issuer vintages marked `initial`, earliest
  publication first; unknown or backfill kinds are ineligible. Missing
  initial evidence returns no resolution, not a substitute.
* `as_known_at`: latest compatible value known by the required `as_known_at`
  timestamp; a backfill first appears at its actual first-seen timestamp.
* `revised_history`: latest compatible value per period, including marked
  backfills. This is the default for compatibility with existing consumers.
  It still honors an `as_known_at` cutoff if supplied; without one, the cutoff
  is the current time. It is not a first-release or historical simulation.

`latest_official` continues including backfills and returns their existing
`revision_kind` field; `first_release` filters for `initial`. Callers building
point-in-time or first-print series must explicitly choose their policy. The
default preserves current report and calendar behavior, so those consumers
need no signature changes, but they must opt in before treating values as
first releases. Stored classification is only as reliable as the adapter's
evidence; these policies cannot reconstruct missing original publications.

`first_seen_release_cards` defaults to the dedicated `new_release` policy:
it resolves the latest value but excludes a card if its selected evidence or
its earliest official headline evidence is a backfill. That exclusion applies
even when a caller explicitly selects another history policy. A reading and a
vintage first seen at the same instant are treated together, so an unclassified
reading cannot hide a backfill vintage. The daily report displays the selected
kind. Other card queries retain their existing defaults.

Under the default history policy, a card's `previous_revised` can contain a
later backfill. It is labelled as revised and its resolution dictionary
contains `revision_kind`. Likewise `latest_official` includes labelled
backfills: it is a latest-value view, not a first-print view.
