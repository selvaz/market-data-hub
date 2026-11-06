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
This is an observation-date cutoff, not a vintage or release-time backtest.

Only comparable conventions belong in a chain. A documented comparability note
explains any differences, including benchmark selection, interpolation,
publication frequency and fixing time. Fitted zero-coupon curves are not
substitutes for observed bond yields. A fallback never votes when a higher-ranked
source is fresh; only the first eligible fallback is selected when all higher
sources are stale or missing. History-only sources never vote. Monthly and
daily observations never vote against each other.

Voting compares fresh sources with the same convention and frequency. A concept
may declare a `pair_tolerances` list of `series_ids` (two IDs), `tolerance_bp`,
and a reason in `note` to allow a specific cross-convention comparison. Pairwise
comparability is not assumed to be transitive: starting with the first fresh
source, later sources vote only if comparable to every source already in the
voting set. Other fresh candidates remain visible as alternatives, with signed
`basis_bp` (alternative minus selected value) and whether they voted.

Tolerance is in basis points. When voting values differ by more than their
applicable tolerance, a strict majority of at least three voting sources must
agree pairwise within tolerance to overrule an outlier; otherwise the result is
disputed and the first fresh source is retained. A tie or multiple competing
majorities is disputed. No value is fabricated. History reads select
one explicitly named configured source, or the primary by default, and never
splice series. History-only entries are excluded from current resolution.

To add or change a source, first register its actual series in the hub, review
country, tenor, convention, units and publication lag, then edit the ordered
chain with its freshness limit and role. Add a specific comparability note when
conventions differ, run the validator and targeted tests, and review the
resulting history and source provenance. The validator rejects unknown IDs,
duplicates and undocumented convention differences.

Open gap: no official daily German yield with long history in the hub;
candidates: Bundesbank SDMX (blocked by bot verification for unattended
access), other official routes to be evaluated.
