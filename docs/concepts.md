# Concept registry

`market_data_hub.concepts.load_concepts()` loads the union of
`market_data_hub/config/concepts/*.yaml` and returns a mapping keyed by concept
id. It validates configuration offline, without opening a database or fetching
observations. `validate_concepts(entries, catalogues=None)` validates an in-memory
list with the same rules. `load_source_catalogues()` exposes the static source
metadata used by validation.

Each family file contains a `concepts` list. A concept declares `concept`,
`family`, `label`, `unit`, `frequency`, `calendar`, and ordered `sources`.
Each source declares `kind` (`macro`, `price`, `derived`, or `release`), `id`,
`role`, `max_age_business_days`, and `convention`. `comparability_note` is optional.
Price sources also declare `field` (`close` or `adj_close`) and `live` (`never`,
`post_close`, or `intraday`). Proxy sources declare their own `unit`.

Source ids must exist in the macro/sovereign catalogues, the effective Yahoo
universe, other registered concepts, or the macro-release `INDICATORS` catalogue,
respectively. Unknown ids, duplicate concept ids across files, duplicate YAML
keys, missing units, invalid age limits, and derived cycles fail at load time.
Allowed-key lists reject unknown fields at the family, concept, source, check,
history, and legacy pair-tolerance levels, so a typo such as `pair_tolerance`
cannot silently disable disagreement handling.
`EUR_STR` has `status: pending_source`, an explanatory `pending_reason`, and no
sources until H1b adds its collector. Pending concepts cannot hide source ids.

A chain starts with exactly one primary. Nine inherited monthly-only sovereign
chains retain their single OECD fallback and are the exception to this rule.
Roles are `primary`, `secondary`, `fallback`, `proxy`, and `history_only`.
Their resolution semantics belong to H2; the registry preserves source order.

An optional `check` names two declared source `ids`, a nonnegative `tolerance`,
a `unit` (`bp`, `pct`, or `absolute`), and an explanatory `note`. It may declare
`basis: rolling` and a positive `window` of common dates. A pair may have
different conventions: the declaration makes the intended comparison explicit.
These checks are metadata for H2 and do not change the existing rate reader's
disagreement behavior.

Legacy `pair_tolerances` remains authoritative for the sovereign resolver's
pair-specific disagreement tolerances until H2 replaces it. Where an entry
declares both `check` and `pair_tolerances`, the existing reader uses
`pair_tolerances`; `check` remains descriptive H2 metadata. Both declarations
are intentionally retained during H1, with no change to runtime voting.

`history` optionally declares `mode: chain` or `mode: single` with the id of a
declared source. A derived concept declares `kind: derived`, an `expression`
of the form `CONCEPT_A - CONCEPT_B` (or `+`), `alignment: same_date`, and an
optional output `scale`. `IT_DE_10Y` scales the difference of percentage yields
by 100 to obtain basis points. Dependencies may refer forward across files.

`equity.yaml` declares `generate: yahoo_prices`. The loader generates one
`PX:<symbol>` entry for every effective Yahoo ticker. Funds and equities use
`adj_close`; FX, futures, indices and direct crypto pairs use `close`. The
canonical listing supplies the currency and session calendar (`calendar:
listing`); units are `quote_currency/share` or `quote_currency`. Price sources
declare `live: post_close` for H2's provisional-current-value behavior.

`releases.yaml` declares the United States release concepts (H2). Each id is
the `macro_releases.catalogue` indicator id; the primary is the issuer release,
the optional secondary is a FRED series from the indicator card's `ms:` ids.
Macro sources may declare `transform` (`level`, `diff`, `pct_change_mm`,
`pct_change_qq_saar` or `pct_change_yy`) and a positive `scale` multiplier, so
a stored index or level feeds a percentage or change concept in the release
unit (for example `ICSA` claims times 0.001 gives thousands). Validation
requires a monthly series for `pct_change_mm`, a quarterly one for
`pct_change_qq_saar`, and a monthly, quarterly or weekly one for
`pct_change_yy`; other source kinds reject both keys. Where the issuer publishes
a change from a non-seasonally-adjusted index that macro_series does not hold
(CPI and PPI y/y), the FRED secondary is omitted and a comment says why.
`max_age_business_days` follows `ceil((period_days + issuer_lag_days + 7) * 5 / 7)`
with period days M 31, W 7, Q 92 and E 49, and the issuer lag from
`EXPECTED_LAG_DAYS`; every source of a concept shares it.

## Latest value (`market_data_hub.latest`)

`read_latest(concept, *, as_of=None, as_known_at=None, con=None)` returns the
most recent correct value of a `macro`, `derived` or `release` concept with its
declared source; `read_latest_many` resolves a list with one DuckDB connection
and one release snapshot. `price` concepts raise `NotImplementedError` until a
later PR adds live/post-close rules and exchange calendars. The legacy
`read_resolved_*` rate readers are unchanged.

For each non-`history_only` source the resolver takes the newest usable
observation: a `macro_series` row dated on or before `as_of` (after its
transform), or an official/private release card released by `as_known_at`
(default: end of the `as_of` day) whose reference period has started by
`as_of`. Monthly and quarterly observations are dated at the period end. A
source is fresh when its age in business days is at most its
`max_age_business_days`. The tier is the first non-empty fresh set among
{primary, secondary}, {fallback}, {proxy}; inside it the newest observation
wins and ties go to the higher-ranked source. Status is `primary`,
`newer_secondary`, `fallback` or `proxy`; without a fresh source the newest
stale observation is returned as `stale`, and without any as `unavailable`.
Sources are never averaged or reconciled.

Derived concepts evaluate their `expression` over resolved inputs at a common
date (re-resolving at the older input date when needed) and report each
input's source; a price input makes the result `unavailable`.
`read_concept_history(concept, start, end)` reads the single `history` source
(or the primary macro source), applying its transform; chains are not spliced.

A concept may declare `decimals` (0 to 6), the issuer's published precision.
The chosen value and every alternative are rounded half up to it, so a FRED
fallback reads as the issuer would print it (CPI m/m 0.35028 becomes 0.4);
`value_raw` keeps the unrounded number and derived concepts compute from raw
inputs. The US release concepts declare it: one decimal for percentages, whole
thousands for payrolls, claims, starts, permits and new home sales, two
decimals for existing home sales (millions) and the FOMC upper bound.

The output keys are stable: `concept`, `label`, `family`, `unit`, `value`,
`value_raw`, `observation_date`, `reference_period`, `source_id`, `source_kind`, `role`,
`status`, `age_bd`, `max_age_bd`, `released_at`, `consensus`, `alternatives`,
`inputs`, `reason` and `registry_version` (a hash of the concept files).

`python -m market_data_hub.latest --as-of YYYY-MM-DD [--concept X ...]
[--family releases] [--db PATH] [--releases-db PATH] [--json]` prints a table.
The release store defaults to `MACRO_RELEASES_DB`.

Known limits: business days are Monday to Friday with no holiday calendar, so
ages around holidays are overstated by the holidays. Macro rows are read as
currently stored: `as_known_at` applies only to release cards, so FRED values
may include later revisions.

`resolved_rates.load_resolved_rates()` projects the registry's 116 existing
sovereign concepts back to the legacy shape, including catalogue frequencies.
It preserves all source attributes and ordering. Explicit paths to legacy
configuration files remain supported until H5. The original packaged
`resolved_rates.yaml` has moved to `concepts/rates.yaml`; a frozen test fixture
guards compatibility.

Validated loads are cached per process by absolute file paths and modification
times, including the source catalogue dependencies, and return deep copies.
The resolved-rate reader validates `rates.yaml` only against macro catalogues;
unrelated family, price-universe, or release declarations cannot block it.
`load_concepts()` and CI/`validate_config.py` validate the complete union.
`T10Y2Y` belongs to the nominal rates/curve family, outside the 116-chain
compatibility projection. `DTWEXBGS` retains DXY only as `history_only` context:
its different currency basket prevents substituting its current index level.
