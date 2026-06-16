# market_data_hub — Architecture & Process Map

> Complete map of what the system does, how data flows, the main functions, and
> the database structure. For the catalogue of every series (with frequency,
> expected lag and group) see [DATA_CATALOG.md](DATA_CATALOG.md).

---

## 1. What the system does

`market_data_hub` is a single, automatable pipeline that downloads market and
macro data from three providers, stores it incrementally in one **DuckDB**
database, and continuously measures the quality/coverage of every series.

It replaces the scattered download scripts that previously lived inside
`quant_timeseries_suite`, `quant_vix_calibrator`, `zero_noise_pipeline`,
`crypto_ml_features` and `macro_dashboard_v2_bundle`. Those projects now read
from the shared database through `market_data_hub.reader` instead of fetching
their own copies.

**Design principles**

| Principle | Implementation |
|-----------|----------------|
| One source of truth | a single `market_data.duckdb` file |
| Incremental, not full | each run reads the last stored date per series and downloads only the gap (+ a short tail-refresh for revisions) |
| Idempotent | every write is an `INSERT OR REPLACE` on a primary key, so re-running is always safe |
| Self-monitoring | a coverage engine recomputes freshness, gaps and a 0–100 quality score on every run |
| Resilient | per-series try/except, exponential-backoff retries, SSL bootstrap for the corporate proxy; one failing series never aborts the run |

---

## 2. Process flow (a daily run)

```
run_daily.py
   │
   ▼
runner.run(mode="full")
   │
   ├─ run_yahoo() ─────► group symbols by effective_start (incremental)
   │                     └─► yahoo_batch() one HTTP call per start-group
   │                         └─► upsert → prices_daily   + download_log row
   │
   ├─ run_fred() ──────► per series: start = last_date − 95d (catch revisions)
   │                     └─► fetch_fred() (API key → JSON, else public CSV)
   │                         └─► upsert → macro_series    + download_log row
   │
   ├─ run_binance() ───► per (symbol, timeframe): start = last_ts − lookback
   │                     └─► fetch_klines() paginated, parallel (ThreadPool)
   │                         └─► upsert → crypto_ohlcv    + download_log row
   │
   ├─ run_live()  ─────► (full mode only) for liquid asset classes:
   │                     get_last_price_live() 3-fallback → adjusted delta map
   │                     └─► upsert today's row (is_live=TRUE) → prices_daily
   │
   └─ rebuild_coverage() ─► recompute coverage_report for every series,
                            print a stalled-symbols alert
```

**Run modes** (`run_daily.py` flags)

| Command | Effect |
|---------|--------|
| `python run_daily.py` | full: yahoo + fred + binance + live |
| `python run_daily.py --live-only` | only intraday live-price injection |
| `python run_daily.py --sources yahoo fred` | restrict to listed sources |
| `python run_daily.py --end 2024-12-31` | cap the end date |
| `python run_backfill.py` | force full history from `backfill_start` dates |

---

## 3. Module map

```
market_data_hub/
├── __init__.py              loads _ssl_bootstrap before any network import
├── _ssl_bootstrap.py        builds certifi + Windows-CA bundle → env vars
├── config_loader.py         cached YAML loaders (settings / tickers / fred)
├── runner.py                orchestration (run, run_yahoo/fred/binance/live)
├── reader.py                PUBLIC read API for other projects
│
├── sources/                 one module per provider, all return canonical frames
│   ├── base.py              SourceResult dataclass (status/rows/error)
│   ├── yahoo.py             yahoo_batch(), effective_start(), live prices
│   ├── binance.py           fetch_klines() paginated OHLCV
│   ├── fred.py              fetch_fred() API-key-or-CSV
│   ├── worldbank.py         fetch_worldbank() WDI/WGI per indicator×country
│   ├── imf.py               fetch_imf() WEO DataMapper (WAF-aware backoff)
│   └── macro_panel.py       fetch_indicator() primary→fallback orchestration
│
├── coverage/                data-quality engine (one concern per module)
│   ├── freq_detector.py     detect_frequency() → D/W/M/Q/A; threshold tables
│   ├── stalled_detector.py  lag_days(), is_stalled() (freq-aware)
│   ├── gap_detector.py      missing_pct(), gap_count(), date_span()
│   ├── quality_checks.py    clean_price_frame(), check_prices() flags
│   ├── score.py             coverage_score() 0–100
│   └── report.py            rebuild_coverage() → coverage_report table
│
├── db/
│   ├── schema.sql           6 tables + indexes + 3 views (idempotent)
│   ├── connection.py        get_conn() resolves path, applies schema
│   └── upsert.py            upsert() INSERT OR REPLACE, log_run()
│
└── config/
    ├── tickers.yaml         111 Yahoo symbols (symbol/asset_class/area/priority)
    ├── macro_series.yaml    45 FRED series (symbol/country/name/priority)
    ├── macro_panel.yaml     69 cross-country indicators (WB/WDI+WGI, IMF/WEO, BIS)
    ├── countries.yaml       64 countries (iso3/iso2/wb/imf)
    └── settings.yaml        db_path, backfill dates, parallelism, FRED key, crypto

run_daily.py · run_backfill.py · diagnose.py · validate_macro_panel.py · setup_scheduler.ps1
```

---

## 4. Main functions (reference)

### 4.1 Public read API — `market_data_hub.reader`

Open the DB **read-only** (many processes can read at once). Returned frames
mirror the parquet/CSV layout the projects already used.

| Function | Returns |
|----------|---------|
| `read_prices(symbols, start, end, field="adj_close", wide=True, include_live=False)` | wide frame (date × symbol) of `field`, or long OHLCV when `wide=False` |
| `read_macro(series_ids, start, end, wide=True, asof=None)` | wide frame (date × series_id) of macro values; `asof=<date>` reads the point-in-time vintage (value as known then) |
| `read_macro_panel(indicators, countries, start, end, wide=False, asof=None)` | cross-country panel; `wide=True` pivots a single indicator by country; `asof=<date>` for point-in-time |
| `read_crypto(symbols, timeframe="1h", start, end)` | long OHLCV from `crypto_ohlcv` |
| `read_factors(factors, factor_set, start, end, wide=True)` | Fama-French / momentum factor returns from `factor_returns` |
| `get_coverage(symbols=None)` | the `coverage_report` table (quality per series) |
| `get_macro_panel_coverage()` | cross-country availability per macro_panel indicator (`macro_panel_coverage`) |
| `get_stalled()` | only series flagged `stalled` |
| `get_latest(symbol)` | last close/adj_close + lag_days + coverage_score |

```python
from market_data_hub.reader import read_prices, read_macro, read_crypto
px  = read_prices(["SPY", "^VIX"], start="2020-01-01")
vix = read_prices(["^VIX9D","^VIX","^VIX3M","^VIX6M"])   # term structure
ir  = read_macro(["DGS10", "DGS2", "T10Y2Y"])
btc = read_crypto("BTCUSDT", "1h", start="2024-01-01")
```

### 4.2 Source functions

| Function | Purpose |
|----------|---------|
| `sources.yahoo.yahoo_batch(tickers, start, end)` | one `yf.download` call → `{symbol: OHLCV frame}` |
| `sources.yahoo.effective_start(last_date, global_start, tail)` | next start = `last_date − tail` (revision overlap) |
| `sources.yahoo.get_last_price_live(ticker)` | 3-fallback live price: fast_info → regularMarketPrice → 1-min bar |
| `sources.yahoo.adjusted_live_price(live, adj_eod, close_eod)` | map live price into adjusted space via additive delta |
| `sources.binance.fetch_klines(symbol, tf, start, end)` | paginated klines → canonical `crypto_ohlcv` frame |
| `sources.fred.fetch_fred(series_id, start, end, api_key=…)` | FRED series via official API (key) or public CSV |

### 4.3 Coverage engine

| Function | Purpose |
|----------|---------|
| `coverage.freq_detector.detect_frequency(dates)` | median-spacing → `D/W/M/Q/A/irregular_Xd/UNKNOWN` |
| `coverage.stalled_detector.lag_days(last_date)` | days since last observation |
| `coverage.stalled_detector.is_stalled(last_date, freq)` | `lag_days > threshold(freq)` |
| `coverage.gap_detector.missing_pct(dates, freq)` | fraction of expected observations missing (business-day-aware for D) |
| `coverage.gap_detector.gap_count(dates, freq)` | number of holes in the series |
| `coverage.quality_checks.check_prices(df)` | flags: zero price, negative, adj/close ratio anomaly |
| `coverage.score.coverage_score(obs, missing, lag, priority, freq)` | composite 0–100 |
| `coverage.report.rebuild_coverage(con, run_id)` | rebuild the whole `coverage_report` table |
| `coverage.report.rebuild_macro_panel_coverage(con, run_id, n_countries_total)` | score cross-country availability into `macro_panel_coverage` |

### 4.4 DB layer

| Function | Purpose |
|----------|---------|
| `db.connection.get_conn(db_path=None, read_only=False)` | open DuckDB, apply schema, resolve path from settings/env |
| `db.upsert.upsert(con, table, df)` | atomic `INSERT OR REPLACE`; returns `(added, updated)` |
| `db.upsert.record_vintage(con, table, df, vintage_date)` | append-on-change to `{table}_vintage` for point-in-time history (macro_series, macro_panel) |
| `db.upsert.log_run(con, …)` | append one row to `download_log` |

---

## 5. Database structure

DuckDB file `market_data.duckdb`. 5 tables + 3 views. All writes are
`INSERT OR REPLACE` on the primary key.

### prices_daily — daily OHLCV (equity, ETF, FX, VIX indices, crypto-daily)
`PRIMARY KEY (date, symbol)` · index on `symbol`

| column | type | note |
|--------|------|------|
| date | DATE | trading day |
| symbol | VARCHAR | Yahoo ticker (`SPY`, `^VIX`, `BTC-USD`) |
| open / high / low / close | DOUBLE | raw prices |
| adj_close | DOUBLE | split/dividend adjusted |
| volume | BIGINT | |
| source | VARCHAR | `yahoo` / `binance_daily` |
| is_live | BOOLEAN | TRUE = intraday live row, overwritten at EOD |
| updated_at | TIMESTAMP | last upsert |

### crypto_ohlcv — Binance intraday/daily
`PRIMARY KEY (ts, symbol, timeframe)` · index on `(symbol, timeframe)`

| column | type | note |
|--------|------|------|
| ts | TIMESTAMP | candle open time (UTC) |
| symbol | VARCHAR | `BTCUSDT`, … |
| timeframe | VARCHAR | `1h` / `4h` / `1d` |
| open/high/low/close | DOUBLE | |
| volume | DOUBLE | base-asset volume |
| volume_quote | DOUBLE | quote-asset (USDT) volume |
| n_trades | INTEGER | trade count |
| taker_buy_base | DOUBLE | order-flow proxy |
| is_closed | BOOLEAN | FALSE = still-forming candle |

### macro_series — single-value macro series (FRED)
`PRIMARY KEY (date, series_id)` · index on `series_id`

| column | type | note |
|--------|------|------|
| date | DATE | |
| series_id | VARCHAR | `DGS10`, `CPIAUCSL`, … |
| value | DOUBLE | |
| series_name | VARCHAR | human label |
| unit | VARCHAR | |
| frequency | VARCHAR | `D/M/Q` (also inferred by coverage) |
| source | VARCHAR | `fred` |
| country | VARCHAR | `US` / `EA` |

### macro_panel — cross-country macro panel (World Bank WDI/WGI + IMF WEO)
`PRIMARY KEY (date, country_iso3, indicator_id)` · indexes on `country_iso3`, `indicator_id`

| column | type | note |
|--------|------|------|
| date | DATE | year-end of the annual observation |
| country_iso3 | VARCHAR | `USA`, `ITA`, … (64 countries) |
| indicator_id | VARCHAR | `real_gdp_growth`, `public_debt_gdp`, … |
| value | DOUBLE | |
| indicator_name | VARCHAR | human label |
| pillar | VARCHAR | growth / liquidity / external / debt_cycle / sovereign / banking / governance / geopolitical |
| orientation | INTEGER | +1 healthier / −1 worse / 0 neutral |
| source | VARCHAR | `worldbank` / `imf` |
| provider_dataset | VARCHAR | WDI / WGI / WEO |
| provider_code | VARCHAR | native provider code |
| unit / frequency | VARCHAR | unit; `A` annual |

### coverage_report — quality status per series (rebuilt each run)
`PRIMARY KEY (symbol, source)`

| column | type | note |
|--------|------|------|
| symbol / source / asset_class | VARCHAR | identity + group |
| first_date / last_date | DATE | stored span |
| obs_count | INTEGER | non-null observations |
| freq_detected | VARCHAR | `D/W/M/Q/A/irregular_Xd` |
| lag_days | INTEGER | days since last_date |
| stalled | BOOLEAN | lag beyond freq threshold |
| gap_count | INTEGER | holes in the series |
| missing_pct | DOUBLE | fraction of expected obs missing |
| coverage_score | DOUBLE | 0–100, freq-aware |
| has_zero_price / has_negative | BOOLEAN | quality flags |
| status | VARCHAR | `ok / stalled / empty / error` |
| error_msg | VARCHAR | last error |
| last_run_id / updated_at | VARCHAR / TS | |

### download_log — audit trail (one row per series per run)
`run_id, started_at, ended_at, source, symbol, rows_added, rows_updated, status, error_msg, duration_sec`

### Views
| view | content |
|------|---------|
| `v_returns` | daily log-returns from `adj_close` (`ln(p/lag(p))`) |
| `v_vix_term_structure` | per-date pivot of VIX9D / VIX / VIX3M / VIX6M / VVIX / VXN |
| `v_stalled` | `coverage_report WHERE stalled = TRUE`, ordered by lag |

*(The wide macro pivot is provided by `reader.read_macro(wide=True)` — DuckDB
does not allow a dynamic PIVOT inside a view.)*

---

## 6. Coverage engine reference

### Frequency detection (median spacing of dates)
`≤3d → D · ≤10d → W · ≤45d → M · ≤135d → Q · ≤400d → A · else irregular_Xd`

### Stalled thresholds (lag in days before "stalled")
| freq | D | W | M | Q | A | UNKNOWN |
|------|---|---|---|---|---|---------|
| days | 3 | 10 | 45 | 120 | 400 | 30 |

### Coverage score 0–100 (freq-aware)
```
obs_component       = min(obs / min_obs[freq], 1) * 40
missing_component   = (1 − missing_pct)           * 25
freshness_component = max(0, 1 − lag/(2·tol[freq]))* 25
priority_component  = {1:10, 2:7, 3:4, 4:1}[priority]
```
`min_obs[freq] = A:10 Q:20 M:36 W:52 D:250` ·
`tol[freq] = A:500 Q:270 M:120 W:45 D:21` (days)

An annual series is therefore not penalised for a normal ~12-month reporting lag.

---

## 7. Automation

`setup_scheduler.ps1` registers three Windows scheduled tasks:

| task | when | command |
|------|------|---------|
| MarketDataEOD | daily 22:00 | `run_daily.py` |
| MarketDataWeekend | Sat 08:00 | `run_daily.py --sources fred` |
| MarketDataLive | hourly 16:00–22:00 | `run_daily.py --live-only` |

Logs rotate into `logs/<task>.log`. Remove with `setup_scheduler.ps1 -Remove`.

---

## 8. Operational notes (this machine)

- **SSL / corporate proxy** — HTTPS is MITM-intercepted; `_ssl_bootstrap.py`
  builds `ca_bundle.pem` (certifi + Windows ROOT/CA) and sets
  `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`. yfinance uses
  curl_cffi, so `CURL_CA_BUNDLE` is what makes it work.
- **FRED** — the public CSV endpoint (`fredgraph.csv`) is blocked by the proxy
  (systematic timeouts); the official API (`api.stlouisfed.org`) is reachable.
  Set `fred_api_key` in `settings.yaml` so FRED uses the API path.
- **IMF DataMapper WAF** — the IMF endpoint returns `403 Access Denied` on
  bursts of rapid requests and clears after ~15–20 s. `imf.py` handles 403 with
  a dedicated long backoff; the runner spaces IMF calls by `imf_sleep` (8 s).
  The ~5 IMF WEO indicators each have a World Bank fallback, so the panel is
  populated even when IMF is temporarily blocked.

## 9. Code validation

Every cross-country indicator code is checked against the live provider APIs by
`validate_macro_panel.py`, which writes `macro_panel_validation.csv`. The last
run: **38 / 39 working** (33 direct OK + 5 World-Bank fallback). This process
caught and fixed three real issues:
- WGI governance codes had migrated from `RL.EST` → `GOV_WGI_RL.EST` (×6);
- `labor_productivity` growth code was discontinued → switched to the level
  code `SL.GDP.PCAP.EM.KD`;
- the IMF endpoint needs the country list in the URL path and is WAF-rate-limited.

Re-run any time with `python validate_macro_panel.py` (add `--full` to probe all
64 countries instead of the 5-country sample).
