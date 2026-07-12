# lazydatacore contract fixtures — v1

Canonical, frozen example payloads for `market_data_hub.lazydatacore`
(schema version `1.0`, see
[`market_data_hub/lazydatacore/version.py`](../../market_data_hub/lazydatacore/version.py)).
Every ecosystem consumer of this contract (LazyStats, LazyRay, ...) validates
against these fixtures, not against ad-hoc examples of its own — this is how
a cross-repo contract stays enforced instead of just documented.

## Files

| File | Validated by | Shape |
|---|---|---|
| `analysis_result.json` | `AnalysisResult.model_validate` | pydantic object |
| `provenance.json` | `Provenance.model_validate` | pydantic object |
| `instrument_id.json` | `InstrumentId.model_validate` per element | JSON array of canonical strings |
| `price_series.json` | `validate_long_prices` (after reconstructing a DataFrame from `rows`) | long records: one row per (symbol, date) |
| `return_series.json` | `validate_wide_prices` (after reconstructing a DataFrame from `rows`) | wide records: one row per date, one column per instrument |

`price_series.json` and `return_series.json` are **not** pydantic-validated
row by row — `lazydatacore` doesn't own a series *envelope* model, only the
DataFrame-shape validators used by `market_data_hub.reader`/`extract`. The
`_shape` key in each file documents exactly what to reconstruct and which
validator to call; it is metadata for humans/consumers, not part of the
payload itself (strip it before doing anything shape-specific).

## How a consumer validates these fixtures

Since `market-data-hub` is distributed exclusively via GitHub (not PyPI —
see the ecosystem stabilization plan), a consumer references a fixture set
by pinning a tag or commit. `market-data-hub` does not tag releases yet
(ECO-007), so today that means an immutable commit SHA, e.g.:

```python
import json
import urllib.request

REF = "1c25e2c5801eff8df70fb4f839a204bbb1be1d44"  # pin to an immutable commit
url = f"https://raw.githubusercontent.com/selvaz/market-data-hub/{REF}/contracts/v1/analysis_result.json"
fixture = json.loads(urllib.request.urlopen(url).read())
```

Switch to a tag (`v0.1.1`, ...) once `market-data-hub` starts publishing
GitHub Releases; the URL shape is identical.

or, if the consumer already depends on `market-data-hub` as a Git package,
by reading the file directly out of its own installed copy of the
repository (the fixtures ship inside the sdist/repo, not as installed
package data — they are a contract test fixture, not runtime data).

## Compatibility rule

- Additive, optional fields are compatible within `1.x` — do not bump this
  directory for those.
- Removing or renaming an existing field, or changing a field's meaning,
  unit, or timezone convention, requires a new major: create
  `contracts/v2/` alongside (not instead of) `v1/`, bump
  `SCHEMA_VERSION`, and give consumers a migration window.
- Every fixture change needs a corresponding update to
  `AnalysisResult`/`Provenance`/`InstrumentId`/the series validators (or
  vice versa) in the same PR — the fixtures and the code must never drift.
