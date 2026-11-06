# Resolved sovereign rates

A concept such as `DE_10Y` names a country and tenor. Its ordered source chain
is configured in `market_data_hub/config/resolved_rates.yaml`; observations
remain in their original series and retain their dates and provenance. Values
are yields in percent. A concept is a practical comparison, not a claim that
every benchmark uses the same security or fixing time.

The reader considers observations dated on or before `as_of`, taking the latest
non-null observation per source. Age counts Monday through Friday strictly after
the observation date up to and including the cutoff, without a holiday calendar.
The age is zero on the observation date; Tuesday to Saturday counts Wednesday,
Thursday and Friday (three days), and Friday to Monday counts one day. A source is
fresh at or below its configured maximum age. Monthly OECD observations use a
larger limit reflecting their publication lag and appear only as last fallback.
The current value uses the latest fresh daily observation date across the chain,
then the highest-priority source on that date. A lagged primary replaces the
lower-priority observation for that date when it arrives. A fresh monthly
fallback is used only when no daily source is fresh. A newer secondary returns
`latest:<series_id>` when a higher-priority fresh source has an older date.
This is an observation-date cutoff, not a vintage or release-time backtest.

Only comparable conventions belong in a chain. A documented comparability note
explains any differences, including benchmark selection, interpolation,
publication frequency and fixing time. The owner explicitly allows the Swiss
spot and ChinaBond curve series in their country chains alongside benchmark
bonds; they differ in convention and do not vote against one another. Their
per-date composition can introduce a basis and does not make them equivalent.
Other fitted zero-coupon curves are not substitutes for observed bond yields.
A fallback never votes when a higher-ranked
source has an observation on the chosen date. History-only sources are excluded
from current resolution. Monthly and daily observations never vote against each
other.

Voting compares fresh sources on the selected date with the same convention and frequency. A concept
may declare a `pair_tolerances` list of `series_ids` (two IDs), `tolerance_bp`,
and a reason in `note` to set a specific tolerance for a same-convention pair. Pairwise
comparability is not assumed to be transitive: starting with the first fresh
source, later sources vote only if comparable to every source already in the
voting set. Other fresh candidates remain visible as alternatives, with signed
`basis_bp` (alternative minus selected value) for the same date and whether they
voted. Basis for alternatives from a different date is unavailable.

Tolerance is in basis points. When voting values differ by more than their
applicable tolerance, a strict majority of at least three voting sources must
agree pairwise within tolerance to overrule an outlier; otherwise the result is
disputed and the first source on that date is retained. A tie or multiple competing
majorities is disputed. No value is fabricated. History reads compose each date
using the highest-priority available source, including history-only sources;
daily observations take precedence over monthly values on the same date. Each
row names its source and role. `single_source` returns only one configured
series. Counts per series are in `DataFrame.attrs['source_counts']`.

When consecutive composed points have different conventions, the latter row is
marked `mixed_source`. Its `basis_bp` is the new source minus the previous
source at their latest overlapping date on or before the change, with that date
in `basis_date`; both are null when no overlap exists. No adjustment is applied,
and a missing overlap does not imply a zero basis. A `start` filter does not
hide preceding observations used to identify a change or historical overlap.

## Official-source inventory

The remaining configured official sovereign series are excluded deliberately:

- `CB_EA_AAA_2Y`, `CB_EA_AAA_10Y`, `CB_EA_ALL_2Y`, and `CB_EA_ALL_10Y`
  are euro-area fitted spot curves, not national observed-bond concepts.
- `CB_GB_10Y` is a fitted zero-coupon curve; the gilt par and benchmark chain
  already exists. `CB_JP_2Y` and `CB_JP_10Y` duplicate the corresponding
  `MOF_JGB_*` feeds and must not add a second vote.
- `CB_DE_2Y`, `CB_DE_10Y`, `CB_KR_10Y`, `CB_PT_10Y`, `CB_GR_10Y`,
  `CB_NL_10Y`, `CB_AT_10Y`, `CB_IE_10Y`, `CB_MX_10Y`, `CB_BR_10Y`,
  `CB_AR_10Y`, `CB_IN_10Y`, `CB_ID_10Y`, `CB_ZA_10Y`, `CB_TR_10Y`,
  `CB_PL_10Y`, `CB_CO_10Y`, `CB_CL_10Y`, and `CB_PE_10Y` are disabled
  or unresolved sources, without an established current data feed.
- `CB_ZA_R209`, `CB_ZA_R2030`, `CB_BR_NTNF_2033`, and `CB_BR_NTNF_2037`
  have fixed maturities that drift away from the constant-tenor concepts.
- `UST_PAR_1M`, `UST_PAR_1_5M`, `UST_PAR_2M`, `UST_PAR_3M`, `UST_PAR_4M`,
  `UST_PAR_6M`, `UST_PAR_1Y`, `UST_PAR_3Y`, `UST_PAR_7Y`, `UST_PAR_20Y`,
  `MOF_JGB_1Y`, `MOF_JGB_3Y`, `MOF_JGB_4Y`, `MOF_JGB_6Y`, `MOF_JGB_7Y`,
  `MOF_JGB_8Y`, `MOF_JGB_9Y`, `MOF_JGB_15Y`, `MOF_JGB_20Y`, `MOF_JGB_25Y`,
  `MOF_JGB_40Y`, and `BOE_GILT_PAR_20Y` have tenors outside the current
  configured concept set. `BOC_LONG` is not a fixed-tenor benchmark.

## Descriptive percentiles

`read_resolved_percentile` ranks the current resolved value against monthly
history in the requested calendar-year window, using the share of observations
less than or equal to that value. OECD monthly history is preferred because it
typically extends much further back than daily snapshots. When the concept has
no OECD series, the longest single daily series in the window is aggregated to
monthly means; fewer than 36 observed months makes the percentile unavailable.
The selected history series, method, window and month count are returned.

For OECD history, use the latest month within the window in which both the
current daily source and OECD have observations. Subtract that month's daily
mean minus the OECD observation from the current value before ranking. When
the current source has no common month, try the daily primary instead and
report that fallback. The returned basis identifies the month, source and
overlap count. One common month is sufficient.
When no qualifying overlap exists, no correction is applied and the status is
`basis_not_estimated`. The same status applies to the daily-history fallback,
where an OECD basis cannot be estimated. Dates are restricted to the cutoff,
including a potentially partial current month, but publication and revision
vintages are not reconstructed. OECD monthly averaging, benchmark selection and
the daily source's convention may differ; a single-month basis need not be stable.
The percentile is descriptive, not a claim of an equivalent historical series.

To add or change a source, first register its actual series in the hub, review
country, tenor, convention, units and publication lag, then edit the ordered
chain with its freshness limit and role. Add a specific comparability note when
conventions differ, run the validator and targeted tests, and review the
resulting history and source provenance. The validator rejects unknown IDs,
duplicates and undocumented convention differences.

Open gap: no official daily German yield with long history in the hub;
candidates: Bundesbank SDMX (blocked by bot verification for unattended
access), other official routes to be evaluated.
