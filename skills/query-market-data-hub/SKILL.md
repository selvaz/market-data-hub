---
name: query-market-data-hub
description: How to discover and extract market data (prices, macro, cross-country panel, crypto, factors) from the market-data-hub DuckDB. Use when an agent needs to find which series/symbols exist (by asset class, geographic area, sector, macro pillar, country) and pull analysis-ready time series or returns — e.g. to feed regime/HMM analysis (LazyHMM) or any quant model.
---

# Querying market-data-hub

`market-data-hub` is a single DuckDB consolidating five data domains. You query
it through two Python modules (or their `tool_*` / `datahub_*` wrappers):

- `market_data_hub.catalog` — **discovery**: *what* is available.
- `market_data_hub.extract` — **extraction**: analysis-ready `(DataFrame, meta)`.

**Golden rule: discover first, then extract.** Never guess symbol codes — resolve
them with the catalog, then pass the exact keys to extraction.

## The five domains

| domain | what | key identifier | discovery |
|---|---|---|---|
| `prices` | daily OHLCV: equities/ETFs, FX, VIX | symbol (e.g. `SPY`, `^VIX`, `EURUSD=X`) | `list_symbols()` |
| `macro` | FRED single-value series | series_id (e.g. `DGS10`, `CPIAUCSL`) | `list_macro_series()` |
| `macro_panel` | cross-country panel (WB/IMF/BIS) | indicator_id + country_iso3 | `list_macro_indicators()`, `list_countries()` |
| `crypto` | Binance intraday OHLCV | symbol (e.g. `BTCUSDT`) + timeframe | `list_crypto_symbols()` |
| `factors` | Fama-French / momentum returns | factor (e.g. `Mkt-RF`, `SMB`) | `list_factor_sets()` |

Call `catalog.list_datasets()` for this map at runtime.

## Discovery: semantic cuts

The price universe is classified by **asset_class**, **area** (geography),
**sector** (GICS, for sector ETFs) and **group**.

- Asset classes: `EQUITY`, `FIXED_INCOME`, `COMMODITIES`, `REAL_ESTATE`,
  `ALTERNATIVES`, `FX`.
- `area` is normalized (`US` == `USA`). Examples: `Emerging Markets`, `USA`,
  `Europe`, `China`, `Japan`, `Global`.
- US/EU **sectors** live in dedicated ETFs (the sector is derived for you):
  `Energy`→XLE, `Financials`→XLF, `Health Care`→XLV, `Information Technology`→VGT, …

```python
from market_data_hub import catalog
catalog.list_symbols(asset_class="EQUITY", area="Emerging Markets")  # IEMG, EMXC, VWO, ...
catalog.list_symbols(asset_class="EQUITY", area="USA", sector="Energy")  # XLE
catalog.list_symbols(asset_class="EQUITY", area="USA", sector="*")  # all US sector ETFs
catalog.list_sectors(area="USA")                # sectors -> symbols map
catalog.list_macro_indicators(pillar="growth")  # cross-country growth indicators
catalog.search("emerging markets bonds")        # free-text across all domains
catalog.describe_series("SPY")                   # one info card (domain, coverage, ...)
```

Every `list_*` result carries **coverage** when the DB is populated
(`first_date`, `last_date`, `obs_count`, `freq_detected`, `lag_days`, `stalled`,
`coverage_score`). Check `stalled`/`lag_days` before trusting a series.

## Extraction: analysis-ready series

`extract_series(...)` returns `(df, meta)`: `df` has a DatetimeIndex and one
column per symbol; `meta` is JSON describing the pull + per-symbol quality.

```python
from market_data_hub.extract import extract_series, extract_returns, extract_macro

# Weekly log-returns — the shape LazyHMM expects:
df, meta = extract_returns(["SPY", "TLT", "^VIX"], start="2010-01-01", frequency="W")

# Raw daily adj_close levels:
px, _ = extract_series(["XLE", "XLF"], domain="prices", field="adj_close")

# Macro, forward-filled, first difference:
m, _ = extract_macro(["DGS10", "T10Y2Y"], transform="diff")
```

Parameters worth knowing:
- `transform`: `level` | `log_return` | `pct_change` | `diff`.
- `frequency`: `None` (native) | `D` | `W` (Friday) | `M` | `Q`. For returns the
  levels are resampled, then the return is computed (correct compounding).
- `fillna`: `none` | `ffill` | `zero` | `drop`.

## Revisions & point-in-time (vintage) reads

Macro data (FRED, WEO, WDI, BIS) gets revised after first release. The main
tables always hold the **latest** value; an append-on-change history
(`macro_series_vintage` / `macro_panel_vintage`) records every value a
(date, key) has ever had, tagged with `vintage_date`, the writing `run_id`,
`change_type` (`'new'` = date never seen before, `'revised'` = existing date
whose value changed) and `prior_value` (what a revision replaced).

```python
# Backtest-safe reads: the value as it was KNOWN on a date, not as it is now
reader.read_macro("CPIAUCSL", asof="2024-05-15")
reader.read_macro_panel("public_debt_gdp", wide=True, asof="2023-06-30")
extract.extract_panel("real_gdp_growth", countries=["USA"], asof="2018-12-31")

# Data-quality check before trusting a series: what changed recently, and how?
from market_data_hub.db.connection import get_conn
con = get_conn(read_only=True)
con.execute("""
    SELECT date, prior_value, value, vintage_date FROM macro_panel_vintage
    WHERE country_iso3='HRV' AND indicator_id='bis_policy_rate'
      AND change_type='revised' ORDER BY vintage_date DESC LIMIT 10
""").fetch_df()
```

Caveats: history exists only from when vintage ingestion began (an `asof`
earlier than the first vintage returns empty); rows written before run
tracking have NULL `run_id`/`change_type`; the day is the vintage unit — one
row per key per calendar day, holding the end-of-day value vs the previous
day's knowledge (intraday steps are not preserved). Full recipes (revision history of
one observation, everything a specific run changed) are in the repo's
`docs/EXTRACTION.md`, section "Point-in-time / vintage reads". The JSON
`datahub_*` tools do not expose vintage reads — they serve current values
only; use the Python `reader`/`extract` layer for point-in-time work.

## Recipe: feed LazyHMM regime detection

```python
from market_data_hub.extract import extract_returns
from lazyhmm import MSRegimeEngine

df, meta = extract_returns(["SPY", "TLT", "^VIX"], start="2010-01-01", frequency="W")
run = MSRegimeEngine(S_max=4, n_starts=10, criterion="bic").fit(df, model="panel")
```

LazyHMM also ships an official loader (`lazyhmm[datahub]`) that wraps this for
you: `lazyhmm.datasources.load_from_datahub(...)` calls `extract_returns(...)`
and feeds `fit_regimes(data_key=...)`.

## Recipe: US sectors for a regime scan

```python
syms = catalog.list_symbols(asset_class="EQUITY", area="USA", sector="*")["symbol"].tolist()
df, _ = extract_returns(syms, start="2015-01-01", frequency="W")
```

## As LLM tools

The same logic is exposed as JSON tools in `market_data_hub.agent_tools`
(`tool_list_symbols`, `tool_search`, `tool_get_returns`, …) — plain functions,
callable from any framework or MCP server. The one LazyBridge `ToolProvider`
binding is the LazyTools `datahub` connector, which wraps them as `datahub_*`
(install `lazytoolkit` plus this package):

```python
from lazybridge import Agent
from lazytools.connectors.datahub import DataHubTools
agent = Agent("claude-opus-4-8", tools=[DataHubTools()])
```

The agent surface is **read-only by default**. To let an agent download and
**persist** missing price series on demand, opt in with `allow_refresh=True`,
which additionally exposes `datahub_refresh_prices(symbols, start)` (a thin
wrapper over the official `runner.run_yahoo` downloader; Yahoo needs no key):

```python
agent = Agent("claude-opus-4-8", tools=[DataHubTools(allow_refresh=True)])
```

## Configuration

The DB path resolves from the `db_path` argument, then `MARKET_DATA_DB`, then
`settings.yaml`, then a platform default. Reads are always read-only (safe to run
in parallel with the downloader).
