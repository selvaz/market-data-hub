# Eurostat national (DE, FR, IT, ES, PL) JSON-stat fixtures

Small JSON-stat 2.0 documents in the shape of the Eurostat dissemination API
(`/api/dissemination/statistics/1.0/data/<dataset>`), restricted to the exact
filters the `eurostat_national` collector requests.

Provenance: the Eurostat API was not reachable from the session that wrote
these files, so they are not raw API captures. Every value is copied from a
recorded Eurostat euro-indicator release already in this repository:

| Fixture | Values from | Table |
|---|---|---|
| `namq_10_gdp.json` | `../m9a2/ea-gdp-second.html` (2026-08-14) | GDP volume, % change on previous quarter, SCA |
| `une_rt_m.json` | `../m9b/ea-unemployment.html` (2026-10-01) | Seasonally adjusted unemployment rates |
| `sts_inpr_m.json` | `../m9b/ea-ip.html` (2026-10-15) | Industrial production, % change on previous month, SCA |

Added for DE/FR/IT coverage, built offline with the same envelope and the
exact dimensions of each dataset. Values are the owner's live Eurostat run of
2026-11-12, except the HICP files:

| Fixture | Values | Dimensions |
|---|---|---|
| `une_rt_m.json` | DE 4.1, FR 8.5 (Sep 2026) added to the IT rows above | freq, s_adj, age, unit, sex, geo, time |
| `sts_inpr_m.json` | DE 1.3 (Sep 2026) added to the rows above | freq, indic_bt, nace_r2, s_adj, unit, geo, time |
| `sts_trtu_m.json` | DE -2.0, FR -0.6, IT 0.1 (Sep 2026) | freq, indic_bt, nace_r2, s_adj, unit, geo, time |
| `ei_bssi_m_r2.json` | DE 94.1, FR 96.1, IT 100.2 (Oct 2026) | freq, indic, s_adj, geo, time |
| `prc_hicp_minr.json` | synthetic (Aug-Sep 2026) | freq, unit, coicop18, geo, time |
| `prc_hicp_fpd.json` | synthetic (Sep-Oct 2026, release FLS) | freq, unit, coicop18, release, geo, time |

The live run confirmed the HICP datasets and filters (data to October 2026)
but recorded no national HICP values, so those numbers are placeholders for
the parser, not observations. The `updated` timestamps of the added and
extended files are plausible dataset dates, not captured ones.

Added for Spain and Poland, built offline the same way. Values are the owner's
live Eurostat run of 2026-11-12, except the HICP m/m rates:

| Fixture | Values added |
|---|---|
| `namq_10_gdp.json` | ES 0.6 (2026-Q3), PL 1.1 (2026-Q2); `updated` moved to 2026-11-06 |
| `une_rt_m.json` | ES 10.3, PL 3.4 (Sep 2026) |
| `sts_inpr_m.json` | ES 0.9, PL 2.9 (Sep 2026) |
| `sts_trtu_m.json` | ES -0.4, PL -0.2 (Sep 2026) |
| `ei_bssi_m_r2.json` | ES 105.1, PL 99.0 (Oct 2026) |
| `prc_hicp_minr.json` | RCH_A ES 4.9 (Oct 2026), PL 4.0 (Sep 2026); RCH_M ES 0.5, PL 0.1 synthetic; `updated` moved to 2026-11-11 |
| `prc_hicp_fpd.json` | ES Oct 2026 flash, a copy of the regular values (the collector drops it because minr already has October) |

Poland is outside the euro area: `prc_hicp_fpd` has no Polish flash and the
collector does not request one.

The dataset envelope (`source`, `class`, `extension.id`, dimension order) follows
the recorded `../m9a2/ea-gdp.json` API capture. Replace these files with raw API
captures when the API is reachable.

The `une_rt_m` age code is `TOTAL` ("Total", ages 15-74 in this dataset).
`Y15-74` is not a code of `une_rt_m`. The owner's live check on 2026-11-12
confirmed this, and it is the code the collector requests.
