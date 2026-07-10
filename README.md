# market_data_hub

Unified market-data downloader with a **DuckDB** database, daily automation
and a coverage engine. It consolidates the downloads scattered across the
`quant_timeseries_suite`, `quant_vix_calibrator`, `zero_noise_pipeline`,
`crypto_ml_features` and `macro_dashboard_v2_bundle` projects into a single
pipeline.

## Documentation (English)

Browsable site: **https://selvaz.github.io/market-data-hub/** (mkdocs, built
from `docs/` by the `docs` workflow on every push to `main`).

- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — what the process does, the
  data-flow, the main functions, the full DB schema, the coverage engine.
- **[docs/LAZYDATACORE.md](docs/LAZYDATACORE.md)** — API reference for the shared
  `lazydatacore` contract (identity, resolver, series
  schemas, result envelopes, time) that the rest of the ecosystem imports.
- **[docs/DATA_CATALOG.md](docs/DATA_CATALOG.md)** — every series with provider,
  group, frequency, typical lag and history depth; plus the proposed
  cross-country WDI/WEO/WGI/BIS panel extension.

## What it downloads

| Source | What | Table | Frequency |
|----------|------|---------|-----------|
| Yahoo Finance | 111 symbols (ETFs, equity, FX, VIX indices, daily crypto) — OHLCV + adj_close | `prices_daily` | daily |
| Binance | 6 crypto symbols × {1h, 4h, 1d} — extended OHLCV | `crypto_ohlcv` | intraday |
| FRED | 77 macro series (US/EA rates, real yields, CPI, GDP, credit spreads, financial-conditions & liquidity, cross-country 10Y yields) | `macro_series` | D/M/Q |
| World Bank + IMF + BIS + ECB | 83 cross-country indicators (WDI/WGI/WEO/BIS/IMF SDMX/ECB) × 64 countries | `macro_panel` | annual |
| Ken French Data Library | Fama-French 5 factors + momentum (Mkt-RF, SMB, HML, RMW, CMA, Mom, RF) | `factor_returns` | D/M |

## Setup

```bash
pip install -r requirements.txt
```

On Windows, prefer the guided first-run setup:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_first_run.ps1
```

It installs the package, asks for local secrets such as `FRED_API_KEY` and
Telegram credentials, verifies the config, and can optionally configure the
scheduled tasks or start the historical backfill.

1. **FRED API key**: set the `FRED_API_KEY` environment variable. Do not commit
   keys in `settings.yaml`. If no key is set, the public FRED CSV endpoint is
   used, but some networks/proxies block or stall it.

2. SSL verification is handled automatically: on the first import,
   `ca_bundle.pem` is built (certifi + Windows root CA) to get past the
   corporate MITM/proxy. This applies to curl_cffi, requests and urllib.

## Usage

```bash
# initial historical load (Yahoo 2010, FRED 2000, Binance 2018)
python run_backfill.py

# incremental daily download (yahoo + fred + binance + live)
python run_daily.py

# neutral cross-country dashboard (facts, source dates, historical series)
python make_country_dashboard.py

# daily download + operational report + country dashboard
python run_daily.py --report

# intraday live price injection only
python run_daily.py --live-only

# diagnostics
python diagnose.py                 # full coverage table
python diagnose.py --stalled       # stalled series only
python diagnose.py --symbol SPY    # symbol detail
python diagnose.py --summary       # DB statistics
python diagnose.py --runs          # latest runs

# validation of cross-country codes (WDI/WGI/WEO) against the live APIs
python validate_macro_panel.py            # sample of 5 countries
python validate_macro_panel.py --full     # all 64 countries
```

The Ray Dalio-style debt-cycle / growth-inflation regime classifier and the
5-engine country risk architecture live in the separate
[LazyRay](https://github.com/selvaz/LazyRay) repo, which consumes this hub
read-only via `reader.read_macro_panel_ext()` and keeps its own output
storage.

## Automation (Windows Task Scheduler)

```powershell
powershell -ExecutionPolicy Bypass -File C:\Users\Administrator\Documents\GitHub\market-data-hub\setup_scheduler.ps1
```

Creates three tasks:

| task | when | runs |
|------|------|------|
| `MarketData_EU18` | daily 09:00 Pacific (~18:00 Europe/Rome) | `run_daily_with_telegram.ps1` |
| `MarketData_USClose` | Mon-Fri 13:15 Pacific (shortly after US close) | `run_daily_with_telegram.ps1` |
| `MarketData_HMMRegime` | Mon-Fri 13:45 Pacific | `run_regime_daily_with_telegram.ps1` |

The two daily-refresh tasks run the download pipeline, then send **two**
Telegram messages: the operational run report (rows added/updated, errors,
coverage, per-country new/changed indicators) and the neutral country-data
dashboard, as separate document attachments. `MarketData_HMMRegime` is
independent — it runs the per-symbol HMM regime monitor
(`run_regime_daily.py`) and sends its own Telegram report. All three append
their output to `logs/<task>.log` (no rotation). To remove: add `-Remove`.

## Country data dashboard

`make_country_dashboard.py` generates a standalone HTML dashboard from the
hub database only. It contains country reference tags, source periods, simple
net-fuel-trade exposure when the required inputs are fresh, descriptive
cross-country percentiles, and historical macro series (charts expand on
click; no JavaScript is used, so the file stays viewable anywhere, including
Telegram's in-app document preview). It contains no investment scores, cycle
labels, or downstream analytical interpretations.

## Reading from the existing projects

```python
from market_data_hub.reader import read_prices, read_macro, read_crypto, get_coverage

px   = read_prices(["SPY", "^VIX"], start="2020-01-01")          # wide adj_close
vix  = read_prices(["^VIX9D","^VIX","^VIX3M","^VIX6M"])           # term structure
mac  = read_macro(["DGS10", "CPIAUCSL"])
btc  = read_crypto("BTCUSDT", "1h", start="2024-01-01")
cov  = get_coverage()                                            # quality status

# Or read by canonical lazydatacore identity (resolver picks the right table):
from market_data_hub.reader import read_instrument
spy  = read_instrument("ticker:SPY", start="2020-01-01")
btc2 = read_instrument("crypto:BTCUSDT@1h", start="2024-01-01")
```

See [`docs/LAZYDATACORE.md`](docs/LAZYDATACORE.md) for the full shared-contract API.

## Coverage engine (data quality)

On every run the `coverage_report` table is rebuilt with, for each series:
detected frequency, `last_date`, `lag_days`, **stalled** flag (freq-aware:
D=3d, W=10, M=45, Q=120, A=400), gap count, `missing_pct`, **coverage
score 0-100** and quality flags (zero/negative price, adj/close anomalies).
Logic ported from `checks1_improved.py` and `macro_dashboard.py`.

## Extraction & discovery API (for tools / LLMs)

Beyond the raw `reader.py`, the hub exposes a discovery + analysis-ready
extraction layer, consumable from Python or by an LLM via function-calling:

```python
from market_data_hub import catalog, extract
catalog.list_symbols(asset_class="EQUITY", area="Emerging Markets")
catalog.list_symbols(asset_class="EQUITY", area="USA", sector="Energy")  # → XLE
df, meta = extract.extract_returns(["SPY", "TLT", "^VIX"], frequency="W") # ready for LazyHMM
```

See [`docs/EXTRACTION.md`](docs/EXTRACTION.md) (full reference) and the agent
skill `skills/query-market-data-hub/SKILL.md`. JSON tools live in
`market_data_hub.agent_tools`; the LazyBridge `ToolProvider` binding
(`DataHubTools`, `datahub_*` tool names) ships in LazyTools
(`lazytools.connectors.datahub`).

## Structure

```
market_data_hub/
  sources/    yahoo.py  binance.py  fred.py  worldbank.py  imf.py  imf_sdmx.py  bis.py  ecb.py
  coverage/   freq_detector  stalled_detector  gap_detector  quality_checks  score  report
  db/         schema.sql  connection.py  upsert.py
  config/     tickers.yaml (111)  macro_series.yaml (77)  macro_panel.yaml (83)  countries.yaml (64)  settings.yaml
  regime/     estimate.py  report.py                         per-symbol HMM regime monitor (needs LazyHMM)
  reader.py   catalog.py  extract.py  agent_tools.py  config_loader.py  runner.py  country_dashboard.py
run_daily.py  run_backfill.py  diagnose.py  setup_scheduler.ps1  make_country_dashboard.py
run_regime_daily.py  make_report.py  send_telegram_run_report.py
run_daily_with_telegram.ps1  run_regime_daily_with_telegram.ps1
```

