# lazydatacore — the shared data contract (API reference)

`market_data_hub.lazydatacore` is the single, dependency-light vocabulary every
tool in the ecosystem imports so that **identity**, **time**, **series** and
**analysis results** mean the same thing everywhere. Only `pydantic` is required
at import time; pandas is imported lazily where needed. The DuckDB warehouse is
never touched by it — identity is namespaced and a resolver maps it to the flat
warehouse keys.

Numeric policy: **`float`** for series / analysis / charts; **`Decimal`** only
for monetary values (`Money`). Every model round-trips cleanly through
`model_dump(mode="json")` / `model_validate_json`.

The design rationale (Italian) lives in
[`ECOSYSTEM_RATIONALIZATION.md`](ECOSYSTEM_RATIONALIZATION.md); this page is the
English API reference.

```python
from market_data_hub.lazydatacore import (
    InstrumentId, Domain, CurrencyCode,                     # identity
    to_duckdb, ResolvedRef, NotResolvableError,            # resolver
    PriceBar, OHLCV_COLUMNS, Frequency, ReturnKind,        # series
    validate_wide_prices, validate_long_prices,
    AnalysisResult, ResultKind, Provenance, SourceRef,     # result envelopes
    Money, LazyDataModel,
    now_utc, ensure_utc, to_iso, parse_iso,                # time
)
```

## Identity

Canonical instrument identity as a namespaced string `"<domain>:<key>[@<qualifier>]"`.

- **`InstrumentId`** — immutable pydantic model. Construct from parts or
  `InstrumentId.parse("ticker:AAPL")`; it serialises back to that string.
- **`Domain`** — `ticker` (canonical for `prices_daily`: equities/ETFs/FX/VIX),
  `price` (input alias normalised to `ticker`), `crypto` (takes a `@timeframe`
  qualifier), `macro`, `macro_panel`, `factor`, `cik`, `isin`. `cik`/`isin` are
  reference identities (LazyFin/EDGAR), not warehouse rows.
- **`CurrencyCode`** — constrained ISO-4217 3-letter string.

```python
InstrumentId.parse("crypto:BTCUSDT@1h")        # domain=crypto, key=BTCUSDT, qualifier=1h
InstrumentId.parse("price:AAPL")               # == InstrumentId.parse("ticker:AAPL")
str(InstrumentId(domain=Domain.CIK, key="320193"))   # "cik:0000320193" (SEC 10-digit)
```

## Resolver — identity → warehouse

The single place namespaced identity is translated to the physical DuckDB layout.

- **`to_duckdb(instrument) -> ResolvedRef`** — returns the `dataset`, `table`,
  column `filters` and the matching `reader.py` function name.
- **`ResolvedRef`** — frozen dataclass `(dataset, table, filters, reader)`.
- **`NotResolvableError`** — raised for reference-only identities (`cik:`,
  `isin:`) that have no warehouse rows.

```python
to_duckdb("ticker:AAPL")
# ResolvedRef(dataset='prices', table='prices_daily',
#             filters={'symbol': 'AAPL'}, reader='read_prices')
```

## Series schemas

- **`PriceBar`** — one OHLCV bar (`open/high/low/close/adj_close/volume`).
- **`OHLCV_COLUMNS`** — the canonical column tuple.
- **`Frequency`** / **`ReturnKind`** — enums (D/W/M/Q… and simple/log).
- **`validate_wide_prices(df)` / `validate_long_prices(df)`** — validate a
  pandas frame against the wide / long price contract (pandas imported lazily).

## Result envelopes

The standard envelope any tool's output travels in.

- **`AnalysisResult`** — `kind: ResultKind`, `produced_by`, `instruments:
  List[InstrumentId]`, `payload: dict`, `provenance`, `created_at`. Consumed by
  LazyHMM (regime signals).
- **`ResultKind`** — `signal | score | forecast | report | series | other`.
- **`Provenance`** — `source: SourceRef`, `as_of`, `tool_version`.
- **`SourceRef`** — `source`, optional `source_id`/`url`/`retrieved_at`,
  `content_is_untrusted` (defaults `True` for web-fetched content).
- **`Money`** — `amount: Decimal`, `currency: CurrencyCode` — the **only**
  `Decimal` in the contract.
- **`LazyDataModel`** — shared base (`extra="forbid"`, UTC-normalised timestamps).

```python
AnalysisResult(
    kind=ResultKind.SIGNAL, produced_by="lazyhmm.regime.v1",
    instruments=[InstrumentId.parse("ticker:SPY")],
    payload={"current_label": "high_vol", "prob_high_vol": 0.83},
    provenance=Provenance(source=SourceRef(source="lazyhmm"), as_of=now_utc()),
)
```

## Time

All timestamps are UTC tz-aware, ISO-8601. `now_utc()`, `ensure_utc(dt)`,
`to_iso(dt)`, `parse_iso(s)`.

## Reading by canonical identity

`reader.read_instrument(instrument, **kwargs)` is the lazydatacore-aware adapter
over `reader.py`: it resolves an `InstrumentId` (or its string) via the resolver
and dispatches to the right reader, so callers can read by canonical identity
without knowing the warehouse layout.

```python
from market_data_hub.reader import read_instrument
df = read_instrument("ticker:SPY", start="2020-01-01")
df = read_instrument("crypto:BTCUSDT@1h", start="2024-01-01")
```
