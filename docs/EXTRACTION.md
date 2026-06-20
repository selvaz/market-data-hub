# market_data_hub — Extraction & Discovery API

> The guide to consuming the hub: how an LLM **or** another tool (e.g. LazyHMM)
> discovers *what* is available and pulls *analysis-ready* time series out of the
> DuckDB. For the engine and DB internals see [ARCHITECTURE.md](ARCHITECTURE.md);
> for the catalogue of every stored series see [DATA_CATALOG.md](DATA_CATALOG.md).

---

## 1. Where this fits

The hub exposes four read layers, each building on the one below. You normally
touch the top three; `reader.py` stays available for hand-written SQL-style pulls.

```
┌──────────────────────────────────────────────────────────────────────┐
│ agent_tools.py   JSON tool functions + LazyBridge DataHubTools         │  ← LLMs
│                  (tool_list_symbols, tool_get_returns, tool_search …)   │
├──────────────────────────────────────────────────────────────────────┤
│ catalog.py       DISCOVERY — the "map": what exists, by asset class,    │  ← humans
│                  area, sector, pillar, country + coverage/quality       │     & tools
│ extract.py       EXTRACTION — analysis-ready (DataFrame, meta):         │
│                  log-returns, resampling, NaN handling                  │
├──────────────────────────────────────────────────────────────────────┤
│ reader.py        RAW read API (read_prices, read_macro, read_crypto …)  │  ← low level
├──────────────────────────────────────────────────────────────────────┤
│ DuckDB           prices_daily · macro_series · macro_panel · crypto …   │  (read-only)
└──────────────────────────────────────────────────────────────────────┘
```

**Design contract**

| Principle | What it means for you |
|-----------|-----------------------|
| Read-only & parallel-safe | every call opens the DB `read_only=True`; safe to run alongside the downloader |
| Discover, then extract | resolve identifiers with `catalog.*` before pulling — never hard-code guessed codes |
| Analysis-ready output | `extract.*` returns a `DatetimeIndex × symbols` frame, ready for `MSRegimeEngine.fit` |
| JSON-serializable everywhere | catalog returns DataFrames/dicts; tools return JSON strings; nothing leaks a live connection |
| Degrades gracefully | if the DB is empty, the static catalog still returns; coverage columns are just `NaN` |
| One logic, many surfaces | the LLM tools wrap the same functions, so behaviour is identical in code and in an agent |

---

## 2. Installation

```bash
pip install -e .            # core: catalog, extract, agent_tools (JSON functions)
pip install -e ".[agent]"   # + LazyBridge binding (DataHubTools ToolProvider)
```

Point the library at a database (precedence: explicit arg → env → `settings.yaml`
→ platform default):

```bash
export MARKET_DATA_DB=/path/to/market_data.duckdb
```

---

## 3. The mental model: discover → extract

```
            ┌─────────────┐      "US energy equities,        ┌──────────────┐
  question  │  catalog.*  │      weekly log-returns since     │  extract.*   │  (df, meta)
 ─────────▶ │  discovery  │ ───▶ 2015"                        │  extraction  │ ───────────▶ model / LLM
            └─────────────┘      resolves to ["XLE"]          └──────────────┘
```

1. **Discover** — `list_symbols` / `list_macro_*` / `search` / `describe_series`
   turn an intent ("equity emerging markets", "US sectors", "growth pillar")
   into concrete identifiers, and tell you the data quality.
2. **Extract** — feed those identifiers to `extract_series` / `extract_returns`
   and get a clean matrix plus a `meta` describing exactly what came back.

---

## 4. Discovery API — `market_data_hub.catalog`

### 4.1 Datasets overview

```python
catalog.list_datasets()
```
Returns one record per domain (`prices`, `macro`, `macro_panel`, `crypto`,
`factors`) with `table`, `primary_key`, `frequency`, `n_series`, a `description`
and the `discovery` entry point to call next.

### 4.2 The semantic taxonomy

The price universe is classified along four axes. The first two come straight
from config; the last two are **derived** for you (the sector/group only exist
inside the free-text series name).

| Axis | Values | Source |
|------|--------|--------|
| `asset_class` | `EQUITY`, `FIXED_INCOME`, `COMMODITIES`, `REAL_ESTATE`, `ALTERNATIVES`, `FX` | config |
| `area` | `Emerging Markets`, `USA`, `Europe`, `China`, `Japan`, `Global`, … (normalized: **`US` == `USA`**) | config + alias map |
| `sector` | GICS sector for sector ETFs: `Energy`→XLE, `Financials`→XLF, `Health Care`→XLV, `Information Technology`→VGT, … (US SPDR + STOXX Europe sleeves) | derived (`_SECTOR_BY_SYMBOL`) |
| `group` | name sub-token: `EM`, `US`, `Energy`, `Metals`, `Agriculture`, `Bitcoin`, … | derived (name token 1) |

### 4.3 `list_symbols`

```python
catalog.list_symbols(asset_class=None, area=None, sector=None, group=None,
                     with_coverage=True, db_path=None) -> pd.DataFrame
```

| Parameter | Meaning |
|-----------|---------|
| `asset_class` | exact match on the asset class |
| `area` | exact match on the **normalized** area (`US`/`USA` unified) |
| `sector` | exact GICS sector, **or `"*"`** to return only the sector ETFs |
| `group` | exact match on the name sub-group |
| `with_coverage` | join `coverage_report` (default `True`); set `False` for a pure static list |

**Columns:** `symbol, asset_class, area, area_norm, name, category, group, sector,
theme, priority` + (when `with_coverage`) `first_date, last_date, obs_count,
freq_detected, lag_days, stalled, coverage_score, status`.

```python
catalog.list_symbols(asset_class="EQUITY", area="Emerging Markets")  # IEMG, EMXC, VWO
catalog.list_symbols(asset_class="EQUITY", area="USA", sector="Energy")  # XLE
catalog.list_symbols(asset_class="EQUITY", area="USA", sector="*")  # all US sector ETFs
```

### 4.4 Other discovery functions

| Function | Returns |
|----------|---------|
| `list_sectors(area=None)` | each sector → its symbols and areas (`area="USA"` for US only) |
| `list_macro_series(frequency=None, category=None)` | FRED series; `category` filters the name prefix (`RATES`/`MACRO`/`CREDIT`/`RISK`/`LIQ`/`FX`) |
| `list_macro_indicators(pillar=None)` | cross-country indicators by `pillar` (growth/liquidity/external/debt_cycle/sovereign/banking/governance/geopolitical) + availability (`n_countries`, `coverage_pct`) |
| `list_countries(region=None, income=None, g7=None)` | country universe; `region` matches `region_group` **or** `region_geo` |
| `list_crypto_symbols()` | Binance symbol × timeframe present (DB-driven, config fallback) |
| `list_factor_sets()` | factor sets/factors present in `factor_returns` |
| `describe_series(symbol_or_id)` | one info card; auto-resolves the domain |
| `search(query)` | case-insensitive substring match across **all** domains → `(domain, key, name, tag, detail)` |

```python
catalog.list_macro_indicators(pillar="growth")     # real_gdp_growth, gdp_current_usd, …
catalog.list_countries(region="G7")
catalog.describe_series("DGS10")                    # {"domain": "macro", "series_id": "DGS10", …}
catalog.search("emerging markets bonds")            # spans prices + macro + panel
```

---

## 5. Extraction API — `market_data_hub.extract`

Every function returns `(df, meta)`: `df` is the analysis matrix
(`DatetimeIndex × symbols`); `meta` is a JSON-serializable description.

### 5.1 `extract_series` — the workhorse

```python
extract_series(symbols, start=None, end=None, *,
               domain="prices", field="adj_close", transform="level",
               frequency=None, fillna="none", align=True, db_path=None)
        -> tuple[pd.DataFrame, dict]
```

| Parameter | Values | Notes |
|-----------|--------|-------|
| `symbols` | `str` or `list[str]` | prices/crypto symbols, FRED `series_id`s, or factor names |
| `domain` | `prices` \| `macro` \| `crypto` \| `factors` | selects the source table |
| `field` | OHLCV field (`adj_close` default) | for `crypto`, this is the **timeframe** (`1d`, `1h`, …) |
| `transform` | `level` \| `log_return` \| `pct_change` \| `diff` | see matrix below |
| `frequency` | `None` \| `D` \| `W` \| `M` \| `Q` | resampling target (`W` = `W-FRI`, `M` = month-end, `Q` = quarter-end) |
| `fillna` | `none` \| `ffill` \| `zero` \| `drop` | applied after the transform |
| `align` | `bool` | drop all-NaN rows (default `True`) |

**Transform × resampling matrix**

| transform | levels resampled how | what the column holds |
|-----------|----------------------|------------------------|
| `level` | `last()` in bucket | the raw level (price, yield, index) |
| `log_return` | **summed** in bucket (correct compounding) | `ln(pₜ / pₜ₋₁)` |
| `pct_change` | `last()` then recompute | `pₜ / pₜ₋₁ − 1` |
| `diff` | `last()` then recompute | `pₜ − pₜ₋₁` (good for rates/spreads) |

> **Fast path.** Daily `adj_close` log-returns (`domain="prices",
> transform="log_return", field="adj_close", frequency in {None, "D"}`) are served
> directly from the `v_returns` SQL view. `meta["used_returns_view"]` reports it.

### 5.2 Convenience wrappers

```python
extract_returns(symbols, start=None, end=None, *, frequency="W",
                field="adj_close", fillna="none", db_path=None)
extract_macro(series_ids, start=None, end=None, *, transform="level",
              frequency=None, fillna="ffill", db_path=None)
extract_panel(indicator, countries=None, start=None, end=None, asof=None, db_path=None)
```

- `extract_returns` — log-returns, **default weekly (W-FRI)** — the exact shape
  LazyHMM expects. For `W`/`M`/`Q` it resamples levels first, then takes the
  return (correct compounding).
- `extract_macro` — wide FRED matrix, `ffill` by default (macro is sparse).
- `extract_panel` — a single cross-country indicator pivoted `date × country`;
  pass `asof=YYYY-MM-DD` for a point-in-time (revision-safe) read.

### 5.3 The `meta` object

```jsonc
{
  "domain": "prices",
  "symbols": ["SPY", "TLT"],
  "field": "adj_close",
  "transform": "log_return",
  "frequency": "W",
  "fillna": "none",
  "used_returns_view": false,
  "n_rows": 731,
  "n_cols": 2,
  "columns": ["SPY", "TLT"],
  "missing": [],                       // requested symbols that returned no data
  "date_start": "2010-01-08",
  "date_end": "2024-01-05",
  "missing_pct": {"SPY": 0.0, "TLT": 0.14},
  "source": "market-data-hub",
  "quality": {                          // per-symbol coverage snapshot (prices/crypto)
    "SPY": {"last_date": "2024-01-05", "lag_days": 1, "coverage_score": 98.5,
            "stalled": false, "freq_detected": "D", "status": "ok"}
  }
}
```

---

## 6. LLM / agent layer — `market_data_hub.agent_tools`

Same logic, two surfaces: plain JSON functions (no third-party dependency) and an
optional LazyBridge `ToolProvider`.

### 6.1 Tool reference

| Tool function | LazyBridge name | Arguments |
|---------------|-----------------|-----------|
| `tool_list_datasets` | `datahub_list_datasets` | — |
| `tool_list_symbols` | `datahub_list_symbols` | `asset_class, area, sector, group` |
| `tool_list_sectors` | `datahub_list_sectors` | `area` |
| `tool_list_macro` | `datahub_list_macro` | `frequency, category` |
| `tool_list_indicators` | `datahub_list_indicators` | `pillar` |
| `tool_list_countries` | `datahub_list_countries` | `region, income` |
| `tool_describe` | `datahub_describe` | `symbol_or_id` |
| `tool_search` | `datahub_search` | `query` |
| `tool_get_series` | `datahub_get_series` | `symbols, start, end, domain, field, transform, frequency` |
| `tool_get_returns` | `datahub_get_returns` | `symbols, start, end, frequency` |
| `tool_get_coverage` | `datahub_get_coverage` | `symbols` |

- All arguments are primitives; lists are passed **comma-separated** (`"SPY,TLT"`)
  because LLMs send strings.
- Extraction tools return `{"meta": …, "data": [...records...], "truncated": bool}`
  and cap inline rows at **500** (`_MAX_ROWS`); `meta.n_rows` always holds the true
  count. Narrow the date window or resample to a coarser `frequency` for fewer rows.

### 6.2 Use as plain JSON

```python
from market_data_hub.agent_tools import tool_list_symbols, tool_get_returns
tool_list_symbols(asset_class="EQUITY", area="Emerging Markets")  # -> JSON string
tool_get_returns("SPY,TLT,^VIX", start="2015-01-01", frequency="W")
```

### 6.3 Use with LazyBridge (`agent` extra)

```python
from lazybridge import Agent
from market_data_hub.agent_tools import DataHubTools

agent = Agent("claude-opus-4-8", tools=[DataHubTools()])
agent("Find the US equity sectors and give me their weekly log-returns since 2018.")
# the model calls datahub_list_symbols(asset_class="EQUITY", area="USA", sector="*")
# then datahub_get_returns(...)
```

`DataHubTools` is a structural `ToolProvider` (`_is_lazy_tool_provider = True`,
`as_tools()`), so it composes with any other tools in the same agent.

---

## 7. Cookbook

### Feed LazyHMM regime detection
```python
from market_data_hub.extract import extract_returns
from lazyhmm import MSRegimeEngine

df, meta = extract_returns(["SPY", "TLT", "^VIX"], start="2010-01-01", frequency="W")
run = MSRegimeEngine(S_max=4, n_starts=10, criterion="bic").fit(df, model="panel")
```

### US sectors, discovered then extracted
```python
syms = catalog.list_symbols(asset_class="EQUITY", area="USA",
                            sector="*")["symbol"].tolist()
df, _ = extract_returns(syms, start="2015-01-01", frequency="W")
```

### Macro features (rates level + curve change)
```python
levels, _ = extract_macro(["DGS2", "DGS10"], transform="level")
curve,  _ = extract_macro(["T10Y2Y"], transform="diff")
```

### Crypto daily returns
```python
df, _ = extract_series(["BTCUSDT", "ETHUSDT"], domain="crypto",
                       field="1d", transform="log_return")
```

### Point-in-time macro (revision-safe backtest)
```python
from market_data_hub.extract import extract_panel
gdp_asof_2018, _ = extract_panel("real_gdp_growth", countries=["USA", "DEU"],
                                 asof="2018-12-31")
```

---

## 8. Data quality — reading coverage

Discovery results and `meta["quality"]` expose the coverage engine's verdict.
Check these before trusting a series:

| Field | Meaning | Watch for |
|-------|---------|-----------|
| `coverage_score` | 0–100 freq-aware quality | low → gaps / short history |
| `lag_days` | age of the newest point | large → stale upstream |
| `stalled` | `True` past the freq threshold (D 3d · W 10 · M 45 · Q 120 · A 400) | drop or flag it |
| `freq_detected` | inferred native frequency | mismatch vs your `frequency` request |
| `missing_pct` (in `meta`) | % NaN per column after the transform | high → consider `fillna` or `align` |

See [DATA_CATALOG.md §4](DATA_CATALOG.md) for the lag/stalled table per group.

---

## 9. Behaviour & edge cases

- **Unknown symbol** → silently absent from `df`; listed in `meta["missing"]`. No
  exception (lets you request a basket without it failing on one bad ticker).
- **Empty DB / empty result** → empty `df`, `meta` with `n_rows: 0`. Catalog
  static lists still work; coverage columns are `NaN`.
- **Invalid `field`** → `ValueError` (whitelisted against the OHLCV columns in
  `reader._ALLOWED_PRICE_FIELDS`).
- **Invalid `transform` / `frequency` / `fillna`** → `ValueError` with the allowed set.
- **Column order** → preserved to match the requested `symbols` order where present.
- **Timezones** → indices are `DatetimeIndex`; crypto `ts` is UTC.

---

## 10. Configuration & safety

| Concern | Behaviour |
|---------|-----------|
| DB path | `db_path` arg → `MARKET_DATA_DB` env → `settings.yaml::db_path` → platform default |
| Concurrency | all reads are `read_only=True`; never block the writer, never write |
| Secrets | none required for reading; the extraction layer touches no network |
| Dependencies | core needs only `duckdb`/`pandas`/`numpy`/`pyyaml`; `DataHubTools` needs `lazybridge` (`agent` extra) |

---

## 11. Downstream integrations

### Shipped

- **LazyTools connector** — LazyTools now ships an official `datahub` connector
  (`DataHubTools`, tools named `datahub_*`) that wraps this hub's `agent_tools`,
  installable via `lazytoolkit[datahub]`. The wrapper lives on the LazyTools
  side; `market_data_hub.agent_tools` keeps its own `tool_*` / `DataHubTools`
  surface unchanged. Because the `tool_*` functions are pure functions, the
  connector is a re-wrap, not a rewrite.
- **LazyHMM loader** — LazyHMM now ships
  `lazyhmm.datasources.load_from_datahub(...)`, installable via
  `lazyhmm[datahub]`. It calls `market_data_hub.extract.extract_returns(...)` and
  feeds `fit_regimes(data_key=...)`. (`extract_returns` already returns a frame
  `MSRegimeEngine.fit` accepts, so the loader is a thin convenience layer on the
  LazyHMM side.)

### Planned (not yet shipped)

- **MCP server** — expose the same tools over MCP for external clients
  (Claude Desktop / Claude Code).

---

## 12. Quick reference

```python
from market_data_hub import catalog, extract

# DISCOVER
catalog.list_datasets()
catalog.list_symbols(asset_class="EQUITY", area="Emerging Markets")
catalog.list_symbols(asset_class="EQUITY", area="USA", sector="Energy")
catalog.list_sectors(area="USA")
catalog.list_macro_series(category="RATES")
catalog.list_macro_indicators(pillar="growth")
catalog.list_countries(region="G7")
catalog.search("bitcoin")
catalog.describe_series("SPY")

# EXTRACT  -> (df, meta)
extract.extract_series(["SPY"], domain="prices", transform="log_return")
extract.extract_returns(["SPY", "TLT", "^VIX"], frequency="W")          # ready for LazyHMM
extract.extract_macro(["DGS10", "T10Y2Y"], transform="diff")
extract.extract_panel("real_gdp_growth", countries=["USA"], asof="2018-12-31")
```

| Module | Role | Read more |
|--------|------|-----------|
| `catalog.py` | discovery / the map | this doc §4 |
| `extract.py` | analysis-ready series | this doc §5 |
| `agent_tools.py` | LLM JSON tools + `DataHubTools` | this doc §6 |
| `reader.py` | raw read API | [ARCHITECTURE.md](ARCHITECTURE.md) |
| agent how-to | step-by-step for an LLM | [`skills/query-market-data-hub/SKILL.md`](../skills/query-market-data-hub/SKILL.md) |
