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
    from_symbol, to_symbol, from_duckdb,                   # registry
    log_returns, simple_returns, pct_change,               # quant
    cumulative_return, annualized_return,
    annualized_volatility, max_drawdown, performance_summary,
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

## Registry — symbol ⇄ identity (inverse of the resolver)

- **`from_symbol(symbol, *, domain=Domain.TICKER, qualifier=None) -> InstrumentId`**
  — canonicalise a bare warehouse symbol (`"AAPL"` → `ticker:AAPL`); namespaced
  strings / `InstrumentId` pass through.
- **`from_duckdb(table, key, *, qualifier=None) -> InstrumentId`** — reconstruct
  the canonical id from a warehouse row (the exact inverse of `to_duckdb`).
- **`to_symbol(instrument) -> str`** — the flat warehouse key for an id.

```python
from_symbol("AAPL")                                  # ticker:AAPL
from_duckdb("crypto_ohlcv", "BTCUSDT", qualifier="1h")  # crypto:BTCUSDT@1h
to_symbol("factor:FF5_daily/Mkt-RF")                 # "FF5_daily/Mkt-RF"
```

## Quant — return / risk primitives (float)

The single implementation of the return math (the pandas variant in
`extract.py` and LazyFin's Decimal metrics are pinned to these by
numeric-equivalence tests). Inputs: a value/price series, oldest first, finite
and strictly positive.

| Function | Returns |
|---|---|
| `log_returns(values)` | `ln(V_t/V_{t-1})` list |
| `simple_returns(values)` / `pct_change` | `V_t/V_{t-1} - 1` list |
| `cumulative_return(values)` | `V_n/V_0 - 1` |
| `annualized_return(values, *, periods_per_year)` | geometric annualized |
| `annualized_volatility(values, *, periods_per_year)` | annualized sample stdev (ddof=1) |
| `max_drawdown(values)` | largest peak-to-trough fraction |
| `performance_summary(values, *, periods_per_year=252)` | dict of the above |

```python
performance_summary([100, 102, 99, 105, 110], periods_per_year=252)
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
