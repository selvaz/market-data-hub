# Resolved sovereign rates

A concept such as `DE_10Y` names a country and tenor. Its ordered source chain
is configured in `market_data_hub/config/resolved_rates.yaml`; observations
remain in their original series and retain their dates and provenance. Values
are yields in percent. A concept is a practical comparison, not a claim that
every benchmark uses the same security or fixing time.

The reader considers observations dated on or before `as_of`, taking the latest
non-null observation per source. Age counts Monday through Friday after the
observation date through the cutoff, without a holiday calendar. A source is
fresh at or below its configured maximum age. Monthly OECD observations use a
larger limit reflecting their publication lag and appear only as last fallback.
This is an observation-date cutoff, not a vintage or release-time backtest.

Only comparable conventions belong in a chain. A documented comparability note
explains any differences, including benchmark selection, interpolation,
publication frequency and fixing time. Fitted zero-coupon curves are not
substitutes for observed bond yields. Tolerance is in basis points. When fresh
values differ by more than tolerance, a strict majority of at least three fresh
sources must agree pairwise within tolerance to overrule an outlier; otherwise
the result is disputed and the first fresh source is retained. A tie or multiple
competing majorities is disputed. No value is fabricated. History reads select
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
