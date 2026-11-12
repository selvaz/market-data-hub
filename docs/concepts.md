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

`releases.yaml` reserves the release family; release declarations and the
adapter are deferred to H4. No value resolver, reader API, session/calendar
implementation, or point-in-time schema migration is included here.

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
