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
publication frequency and fixing time. Fitted zero-coupon curves are not
substitutes for observed bond yields. A fallback never votes when a higher-ranked
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

To add or change a source, first register its actual series in the hub, review
country, tenor, convention, units and publication lag, then edit the ordered
chain with its freshness limit and role. Add a specific comparability note when
conventions differ, run the validator and targeted tests, and review the
resulting history and source provenance. The validator rejects unknown IDs,
duplicates and undocumented convention differences.

Open gap: no official daily German yield with long history in the hub;
candidates: Bundesbank SDMX (blocked by bot verification for unattended
access), other official routes to be evaluated.
