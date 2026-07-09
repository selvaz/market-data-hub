# market-data-hub

**One DuckDB database of market & macro data — downloaded incrementally,
quality-scored on every run, and queryable by humans, tools and LLM agents.**

market-data-hub is the data backbone of the Lazy ecosystem: a single,
automatable pipeline that pulls prices, macro series, cross-country indicators
and factor returns from seven providers into one `market_data.duckdb` file,
then measures the freshness and coverage of every series it stores. Everything
downstream — [LazyHMM](https://github.com/selvaz/LazyHMM) regime detection,
[LazyFin](https://github.com/selvaz/LazyFin) monitors, LazyBridge agents via
the [LazyTools](https://github.com/selvaz/LazyTools) `datahub` connector —
reads from this one database instead of fetching its own copies.

```python
from market_data_hub import catalog, extract

catalog.search("vix")                                    # discover what exists
df, meta = extract.extract_returns(["SPY", "TLT", "^VIX"],
                                   start="2015-01-01", frequency="W")
# df: weekly log-returns, ready for LazyHMM; meta: coverage & quality per symbol
```

## What it downloads

| Source | What | Table | Frequency |
|--------|------|-------|-----------|
| Yahoo Finance | 111 symbols (ETFs, equity, FX, VIX indices) — OHLCV + adj_close, plus intraday live injection | `prices_daily` | daily |
| Binance | 6 crypto symbols × {1h, 4h, 1d} — extended OHLCV | `crypto_ohlcv` | intraday |
| FRED | 77 macro series (rates, real yields, CPI, GDP, credit spreads, financial conditions, liquidity, cross-country 10Y yields) | `macro_series` | D/M/Q |
| World Bank + IMF + BIS + ECB | 83 cross-country indicators (WDI/WGI/WEO/BIS/IMF SDMX/ECB) × 64 countries, with primary→fallback source logic | `macro_panel` | annual |
| Ken French Data Library | Fama-French 5 factors + momentum | `factor_returns` | D/M |

The full series-by-series map (provider, group, native frequency, typical lag,
history depth) is in the [Data catalogue](DATA_CATALOG.md).

## How it works

Every run is **incremental** (it reads the last stored date per series and
downloads only the gap, plus a short tail-refresh for revisions), **idempotent**
(every write is an `INSERT OR REPLACE` on a primary key) and **serialized**
(a cross-process file lock guarantees the single-writer DuckDB is never written
by two tasks at once).

```
run_daily.py ──► runner.run("full")
                   ├─ yahoo / fred / binance / macro_panel / factors
                   │    fetch gap → upsert → download_log row (per series)
                   ├─ live price injection (adjusted-ratio mapping)
                   ├─ coverage engine: freshness, gaps, 0–100 score per series
                   └─ analytical layer: debt-cycle phases + 4-box regimes (dalio.py)

run_dalio_v2.py ─► dalio_v2.runner.run_dalio_v2()   (separate, additive entry point)
                   └─ 5 independent country risk engines: sovereign solvency,
                      political execution, private credit cycle, external
                      currency constraint, funding liquidity → engine_scores
                      table + HTML/CSV report
```

After the downloads, the **coverage engine** recomputes, for every series: the
detected frequency, days of lag, gap count, missing %, price-quality flags and
a 0–100 coverage score — so a stalled or degraded feed is visible immediately
(`python diagnose.py --stalled`). The **analytical layer** then derives
cross-country debt-cycle phases and growth/inflation regime boxes from the
macro panel. **Dalio v2** (`dalio_v2/`, run via `run_dalio_v2.py`) is a
separate, additive 5-engine risk architecture that does not replace
`dalio.py` — see
[DALIO_5ENGINE_IMPLEMENTATION_PLAN_2026-07.md](DALIO_5ENGINE_IMPLEMENTATION_PLAN_2026-07.md).
The whole process map is in [Architecture & process](ARCHITECTURE.md).

## How you query it

Four read layers, each building on the one below — pick your altitude:

1. **`agent_tools.tool_*`** — JSON-string tools for LLMs / MCP (discovery +
   extraction, read-only by default, opt-in refresh write tool).
2. **`extract`** — analysis-ready wide matrices: transform (`level`,
   `log_return`, `pct_change`, `diff`), resample (`D/W/M/Q`), fill policy, and
   a `meta` dict with per-symbol coverage.
3. **`catalog`** — discovery: what exists, filtered by asset class / area /
   sector / pillar / country, joined with live coverage.
4. **`reader`** — raw reads of the stored shapes (wide prices, long panel,
   point-in-time `asof` vintages).

All of it is documented in the [Extraction & discovery API](EXTRACTION.md).
LLM agents get the same surface through the LazyTools `datahub` connector
(11 read-only `datahub_*` tools + opt-in `datahub_refresh_prices`), and the
shared identity/result vocabulary lives in the
[lazydatacore contract](LAZYDATACORE.md).

## Automation

Three Windows Task Scheduler jobs (`setup_scheduler.ps1`) keep the DB fresh:
EOD at 22:00, a weekend FRED refresh, and an hourly live-price injection during
market hours. See the [Quick start](quickstart.md) for setup, backfill and
diagnostics.

## Project status & history

The design rationale for the shared contract is in
[Ecosystem rationalization](ECOSYSTEM_RATIONALIZATION.md) (Italian), and the
latest full code audit — bugs found and fixed, surface pruned, open
recommendations — is the [Deep audit report (2026-07)](DEEP_AUDIT_2026-07.md).
