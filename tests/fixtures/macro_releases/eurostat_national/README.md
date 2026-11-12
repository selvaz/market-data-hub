# Eurostat national (big-four) JSON-stat fixtures

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

The dataset envelope (`source`, `class`, `extension.id`, dimension order) follows
the recorded `../m9a2/ea-gdp.json` API capture. Replace these files with raw API
captures when the API is reachable.

The `une_rt_m` age code is `TOTAL` ("Total", ages 15-74 in this dataset).
`Y15-74` is not a code of `une_rt_m`. The owner's live check on 2026-11-12
confirmed this, and it is the code the collector requests.
