# M9a saved evidence

Captured from real issuer APIs, FRED/ALFRED, and issuer publications on
2026-10-24. Each `.meta.json` records the endpoint, nonsecret request parameters,
capture time and SHA-256 of the retained body. Credentials are excluded from
metadata and redacted from BEA response bodies.

- `bls.json`: BLS v2 PPI SA/NSA and JOLTS, with issuer calculations/levels.
- `T20807.json`: BEA monthly published PCE price changes, all table rows retained
  to test exact headline/core selection.
- `<FRED-series>-<date>.json`: explicit ALFRED as-of requests, including unchanged
  editions and revisions. They are redistributor evidence.
- `<FRED-series>-current.json`: current-head freshness probes. Their values are
  never assigned to a reviewed historical edition.
- `housing*.pdf`, `retail.pdf`, `durable*.pdf`: actual Census publications.
- `ip*.html`, `pce*.html`, `trade*.html`: actual Fed/BEA publications.
- `census-2026.html`: the issuer's 2026 release schedule.
- `golden.json`: 24 new issuer-publication cases, including a previous-month
  revision for every family. Prior BLS values were checked on the cited issuer
  archive pages through web access. Python publication fetches returned 403;
  the current API fixture is not presented as an older API vintage.

See `docs/macro-us-monthly.md` for definitions, edition schedules, golden pairs,
live validation results and the limits of historical issuer verification.
