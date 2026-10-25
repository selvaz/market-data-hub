# Indicator cards (classification v2.2)

Indicator cards provide the same owner-approved context to domain recaps and
LazyCommittee. They describe a concept, theme, nature, timing reference, evidence,
revision behaviour and caveats. They contain no market views or conclusions.
The 294 concept rows and their timing evidence ship as versioned package data;
reading cards requires no database or network access. For the method behind the
classification and a generated table of every concept, its timing class and the
stores it lives in, see [Macro indicators](macro-indicators.md).

```python
from market_data_hub.indicator_cards import (
    cards_for, dataset, indicator_card, render_card, unmapped,
)

card = indicator_card("cat:us_payrolls_change")  # also "us_payrolls", "ms:PAYEMS"
if card is not None:
    context = render_card(card)                 # deterministic, <=600 characters
    structured = card.model_dump(mode="json", by_alias=True)

cards = cards_for(["cal:us_nfp", "es:nonfarm_payrolls", "cal:us_claims"])
assert [c.key for c in cards] == ["us_payrolls", "us_claims_initial"]
assert unmapped(["cat:unknown"]) == ["cat:unknown"]
assert dataset().classification_version == "v2.2"
```

Models are strict, immutable Pydantic models; unknown fields and type coercion
are rejected. `indicator_card` accepts a concept key, a prefixed store id
(`cat:`, `cal:`, `ms:`, `es:`; also `pan:` and `hub:`), or a bare id.
It returns `None` for unmapped ids. `cards_for` drops unmapped inputs and
de-duplicates by card key in first-input order. `unmapped` retains input order
and duplicates. Id matching is case sensitive and trims surrounding whitespace.

Exact mappings take precedence over the explicit wildcard patterns in the
approved table. A shared id first resolves to the row whose own id list is
exactly that id with the same prefix; otherwise competing matches sort
lexicographically by `(country, concept, key)`, independently of source-row
order. Prefer prefixed ids. The table's shared `pan:bis_policy_rate` (and bare
`bis_policy_rate`) resolves to its own row `pan_policy_rate_bis`, not to the
ECB, BoE or EFFR rows that merely also carry it; `pan:imf_policy_rate` resolves
to `pan_policy_rate_imf`. The coverage CLI lists candidates and winners.
`indicator_card(None)` returns `None`; `unmapped` and `cards_for` raise
`TypeError` for a bare string rather than iterating its characters.
These panel aliases are contextual and do not select a country inside a panel.
`derived ms:DGS10,DGS3MO` describes spread ingredients, not aliases: `ms:DGS10`
keeps its own yield card. Missing-store annotations never become fabricated ids.

## Timing and evidence semantics

`timing.class` and `timing.basis` describe a band relative to the row's named
reference. The independent `predictive_screen` records retain each relationship's
direction and transforms in `pair`, result, `pass_horizons`, and any partial
horizons. A relationship can concern the indicator as a reference rather than
as a predictor: Q18 is **IP → payrolls**, not payrolls → IP.

- `PRED-PASS` supports quoting predictive content **only at its pass horizons**.
  The screen uses revised data and point estimates; it does not establish
  real-time availability, significant out-of-sample improvement, causation,
  or a turning-point lead.
- A descriptive peak is **not a validated lead**, even when a forward screen
  passes. Peak text retains its sign, interval, edge warnings and relationship
  ids verbatim. Positive k means the pair's X precedes Y; it is not necessarily
  the current card's lead. Event-timing descriptions use the same guardrail.
- `HYPOTHESIS` is explicitly unvalidated; `DEFINITION` describes a measurement
  identity. `UNKNOWN` renders as **timing not classified**. `CTX` is a chosen
  contextual use with no timing claim. UNKNOWN and CTX remain distinct.
- Measurement, mechanism and local timing evidence are separate labels.
  Matched turning-point leads remain **not estimated**.

The compact renderer preserves the timing guardrails and every passing horizon.
Every screen names its relationship: the full pair for one or two screens, and
a short direction such as `Q17 IP->unrate` or `Q20 unrate->wages` for longer
sets (transforms are in the full card). A pass therefore always belongs to the
named pair, never to the card's indicator by default. HYPOTHESIS cards say
"hypothesis" in words. The caveat is never clipped: the theme, revisions and
source are shortened or dropped first. Use the structured card for complete
rationale, flags and peak estimates. Revision wording is copied only from explicit revision clauses in
the approved rationale. Otherwise it says **Not specified in approved row**;
no default revision policy is inferred from HARD or MARKET nature. Flags are
preserved in `known_failure_modes`, and the full rationale carries further
limitations; an empty flag list does not establish absence of failure modes.

## Data and regeneration

Package resources live under `market_data_hub/indicator_cards/`:

- `master_table_v2.psv`: the owner-approved table (270 existing rows plus the
  24 tier-1 rows approved on 2026-10-24).
- `timing_v2.2.json`: existing verbatim columns (a)/(b) from section 3 of the
  approved `04-classification-v2.md`, plus the 24 promoted timing entries.
  The original source-guide SHA-256 covers the existing evidence; `promotions`
  records the approval, approved keys and current PSV SHA-256. The original
  hashes from PR #125 are retained separately as `draft_source_table_sha256`
  and `draft_timing_extract_sha256`. The build checks each promotion's current
  table hash and requires distinct, existing PSV keys without overlapping
  promotion records. The PSV supplies relationship keys but does not contain
  the numeric horizons or peak estimates.
- `aliases_v2.2.json`: explicit, explained aliases from current catalogue ids
  to concepts already present in the table, plus documented coverage gaps.
- `cards_v2.2.json`: generated strict cards, classification/schema versions,
  and SHA-256 hashes of the table, timing extract and aliases.
- `tier1_v1.json`: the portable snapshot of the 86 external tier-1 measure ids.
- `draft_rows_v2.2.psv` and `timing_v2.2-draft.json`: empty after promotion,
  retained as the promoted generation. Drafts never enter the approved build.
- `draft_rows_v2.3.psv` and `timing_v2.3-draft.json`: five new M9f housing
  proposals, with `approval_status: pending_owner_approval` in timing metadata,
  timing UNKNOWN and only real caveats in the PSV `flag` column.

The JSON filenames follow the existing classification-version convention, so
timing, aliases and generated cards move together from v2.1 to v2.2. The master
table retains its established `master_table_v2.psv` name: it describes the table
format rather than the minor classification version. `CLASSIFICATION_VERSION`
selects the JSON resources; the strict model and build reject mixed versions.

Regenerate or check deterministically from the shipped sources:

```console
python -m market_data_hub.indicator_cards.build
python -m market_data_hub.indicator_cards.build --check
python -m market_data_hub.indicator_cards.coverage
python -m market_data_hub.indicator_cards.coverage --with-drafts
python -m market_data_hub.indicator_cards.docs          # regenerate Part B of docs/macro-indicators.md
python -m market_data_hub.indicator_cards.docs --check  # fail when it is stale
```

`docs/macro-indicators.md` Part B is rendered from the cards, the certified
`macro_releases` families, the FF consensus aliases and the edition calendars.
After merging a branch that adds families or aliases, re-run the generator and
commit the page; `tests/test_macro_indicators_docs.py` fails while it is stale.

When an approved guide/table changes, refresh the extract before rebuilding:

```console
python -m market_data_hub.indicator_cards.build --import-guide .tmp/cards-src/04-classification-v2.md
```

`--import-guide ... --check` verifies the extract without writing. The importer
requires a matching concept and store-id row for every PSV key. Freshness tests
rebuild the JSON and compare it byte for byte. Git preserves source data bytes
across operating systems so the provenance hashes remain stable.

## Owner approval on 2026-10-24

All 24 rows from PR #125 ship as approved cards. The approval record
`approved v2.2 by owner 2026-10-24` lives only in the `promotions` block of
`timing_v2.2.json`. The `flag` column carries actual caveats, since it feeds
`known_failure_modes` and the rendered Caveat text. Both promoted PMI rows
carry `never captured (licensed)`; other flags identify documented issuer-code,
adjustment-metadata or geography limitations, and are empty where no separate
caveat is supported. Approval wording is never a failure mode.

The rationales retain their measurement details, issuer metadata limitations,
local-validation caveats and `Source:` URLs. Proposal sentences and draft-stage
wording have been removed.

| Promoted keys | Timing class / basis |
|---|---|
| `de_gdp_real`, `fr_gdp_real`, `it_gdp_real`, `es_gdp_real`, `jp_gdp_real`, `ca_gdp_monthly` | COIN / DEFINITION; reference: itself (national growth reference) |
| `fr_cpi` | COIN / DEFINITION; reference: EA HICP |
| `ea_pmi_composite`, `uk_pmi_mfg` | COIN / HYPOTHESIS; local timing evidence: lit-local |
| `us_existing_home_sales`, `ea_retail_sales`, `ea_indpro`, `ea_trade_balance`, `fr_indpro`, `fr_unemployment`, `it_indpro`, `es_unemployment`, `es_indpro`, `uk_retail_sales`, `jp_indpro`, `jp_trade_balance`, `cn_indpro`, `cn_retail_sales`, `ca_retail_sales` | UNKNOWN; no timing basis |

The seven definition rows carry the same measurement/mechanism/timing evidence
as `ea_gdp_real` or `de_cpi`: H / n.a. / n.a. The two PMI hypotheses carry
M / mixed / lit-local, like `ea_pmi`. The seven definition rows also use their
twins' exact timing entries: descriptive peak **n.a.** and predictive screen
**n.a. (reference or measurement identity)**. The other 17 promoted rows retain
predictive screens **not tested** and descriptive peaks **not estimated**.
Matched turning-point leads remain **not estimated** for every card.

For future proposals, add a `draft_rows_<version>.psv` with the same 19-column
header and a matching `timing_<version>-draft.json`. `--with-drafts` discovers
these pairs, verifies their hashes and pending-approval metadata, and rejects ids
that duplicate approved cards or another draft file. Proposed timing must stay
UNKNOWN and untested. The default coverage report and shipped card index use
approved cards only. Nonempty draft metadata requires
`approval_status: pending_owner_approval`; the `flag` column contains real
caveats, never approval text. With the opt-in, `draft_cards` lists proposals
outside the tier-1 snapshot too, without adding them to approved coverage.

## Coverage

The offline report uses the tier-1 snapshot, `macro_releases.catalogue.INDICATORS`, active T1 entries
in `econ_calendar.yaml`, and enabled entries in `macro_series.yaml`. It measures
configured-id context coverage, not live ingestion or historical completeness.

| Universe | Measures / ids with approved cards |
|---|---:|
| External tier-1 measures | 86 / 86 |
| Release catalogue | 57 / 65 |
| Active tier-1 calendar | 65 / 65 |
| Enabled hub macro series | 107 / 107 |

UK monthly GDP (`uk_gdp_mm`, `uk_gdp_index`) is covered by the owner-approved
row `uk_gdp_monthly` (added 18/10/2026, mirroring the US GDP row: COIN by
definition, HARD, ONS). Main's original certified families remain fully covered.
Housing adds approved construction and existing-home-sales context, plus five
new v2.3 proposals for eight measures; those measures remain explicit concept
gaps in approved coverage. Tier-1 coverage is 86 approved, zero draft resolutions
and zero unmapped ids, with or without `--with-drafts`; the opt-in separately
lists all five housing proposals in `draft_cards`. Main's M9b/M9c
aliases and per-card id de-duplication are retained; the shared
`cat:us_new_home_sales` alias appears once with main's M9b explanation.

Catalogue aliases attach **concept context**, never numerical equivalence:
levels, changes, seasonal adjustments and stages retain their original ids.
EA20, EA21 and unresolved membership HICP ids share headline HICP context;
the approved EA19-versus-EA21 geography warning remains in the card. No histories
are spliced and no membership version is inferred for unresolved placeholders.
