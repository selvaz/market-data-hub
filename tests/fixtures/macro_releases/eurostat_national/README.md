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

The dataset envelope (`source`, `class`, `extension.id`, dimension order) follows
the recorded `../m9a2/ea-gdp.json` API capture. Replace these files with raw API
captures when the API is reachable.

The `une_rt_m` age code is `TOTAL` ("Total", ages 15-74 in this dataset).
`Y15-74` is not a code of `une_rt_m`. The owner's live check on 2026-11-12
confirmed this, and it is the code the collector requests.
