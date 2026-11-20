# XBRL mapping versions

Mappings live in `market_data_hub/config/xbrl_mapping_v<N>.yaml` and are
immutable per version: new semantics mean a new file and a new version.
`get_xbrl_mapping(version)` loads one; `get_statement`, `get_facts` and the
agent tools take `mapping_version`.

| Version | Content |
|---|---|
| 1 | Six lines: revenue, net_income, assets, liabilities, equity, operating_cash_flow. |
| 2 | v1 plus cost of revenue, gross profit, operating income and debt lines. |
| 3 | v2 plus `derived_lines` (computed lines, always labelled). **Default.** |

## Default version

v3 is the default. Every v2 line is identical, but a v3 `get_statement`
response also has the derived key `liabilities_derived` in `lines`, in
`line_status` (so it can raise a staleness `warnings` entry) and, because its
period ends join the response, possibly in `periods`. Callers that iterate all
lines must handle the extra key. Pass `mapping_version=2` or `1` to reproduce
older output.

## Derived lines (v3)

`liabilities_derived` = `LiabilitiesAndStockholdersEquity` minus equity.
Many issuers (AMZN, LLY, ABBV, MRK, TMO, ABT, ORCL, INTC, AMD, DIS, CMCSA, VZ,
WMT, TGT, MCD, NKE) never file the plain `Liabilities` concept.

Rules:

- Inputs are instant (balance-sheet) USD facts only: duration facts and other
  units are ignored. Both inputs must have the same period end; the latest
  filed fact per period wins for each.
- Computed per period, only where the filed `liabilities` line has no value.
- A separate key: a filed value is never overwritten, and `liabilities` itself
  is never filled by a computation.
- Equity is `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest`
  when present, otherwise `StockholdersEquity` (the result then also contains
  any noncontrolling or redeemable interest; the `subtrahend.concept` in the
  provenance shows which was used).
- Every value has `derived: true`, `formula` and `inputs.minuend` /
  `inputs.subtrahend`, each with concept, accession, filed date and value.

Consumers (agents included) must say that the number is derived, not filed.
