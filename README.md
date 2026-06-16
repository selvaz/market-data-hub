# market_data_hub

Unified market-data downloader with a **DuckDB** database, daily automation
and a coverage engine. It consolidates the downloads scattered across the
`quant_timeseries_suite`, `quant_vix_calibrator`, `zero_noise_pipeline`,
`crypto_ml_features` and `macro_dashboard_v2_bundle` projects into a single
pipeline.

## Documentation (English)

- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — what the process does, the
  data-flow, the main functions, the full DB schema, the coverage engine.
- **[docs/DATA_CATALOG.md](docs/DATA_CATALOG.md)** — every series with provider,
  group, frequency, typical lag and history depth; plus the proposed
  cross-country WDI/WEO/WGI/BIS panel extension.

## What it downloads

| Source | What | Table | Frequency |
|----------|------|---------|-----------|
| Yahoo Finance | 111 symbols (ETFs, equity, FX, VIX indices, daily crypto) — OHLCV + adj_close | `prices_daily` | daily |
| Binance | 6 crypto symbols × {1h, 4h, 1d} — extended OHLCV | `crypto_ohlcv` | intraday |
| FRED | 38 macro series (US/EA rates, CPI, GDP, credit spreads, ...) | `macro_series` | D/M/Q |
| World Bank + IMF + BIS | 69 cross-country indicators (WDI/WGI/WEO/BIS) × 64 countries | `macro_panel` | annual |

## Setup

```bash
pip install -r requirements.txt
```

1. **FRED API key** (required on this network): open
   `market_data_hub/config/settings.yaml` and set `fred_api_key: "YOUR_KEY"`.
   Alternatively, export the `FRED_API_KEY` environment variable.
   > On this machine the proxy blocks FRED's public CSV endpoint; the official
   > API (with a key) is reachable instead.

2. SSL verification is handled automatically: on the first import,
   `ca_bundle.pem` is built (certifi + Windows root CA) to get past the
   corporate MITM/proxy. This applies to yfinance (curl_cffi), requests and urllib.

## Usage

```bash
# initial historical load (Yahoo 2010, FRED 2000, Binance 2018)
python run_backfill.py

# incremental daily download (yahoo + fred + binance + live)
python run_daily.py

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

## Automation (Windows Task Scheduler)

```powershell
powershell -ExecutionPolicy Bypass -File D:\market_data\setup_scheduler.ps1
```

Creates 3 tasks: `MarketDataEOD` (22:00), `MarketDataWeekend` (Sat 08:00, FRED
refresh), `MarketDataLive` (16:00-22:00 every hour). To remove: add `-Remove`.

## Reading from the existing projects

```python
from market_data_hub.reader import read_prices, read_macro, read_crypto, get_coverage

px   = read_prices(["SPY", "^VIX"], start="2020-01-01")          # wide adj_close
vix  = read_prices(["^VIX9D","^VIX","^VIX3M","^VIX6M"])           # term structure
mac  = read_macro(["DGS10", "CPIAUCSL"])
btc  = read_crypto("BTCUSDT", "1h", start="2024-01-01")
cov  = get_coverage()                                            # quality status
```

## Coverage engine (data quality)

On every run the `coverage_report` table is rebuilt with, for each series:
detected frequency, `last_date`, `lag_days`, **stalled** flag (freq-aware:
D=3d, W=10, M=45, Q=120, A=400), gap count, `missing_pct`, **coverage
score 0-100** and quality flags (zero/negative price, adj/close anomalies).
Logic ported from `checks1_improved.py` and `macro_dashboard.py`.

## Structure

```
market_data_hub/
  sources/    yahoo.py  binance.py  fred.py  base.py
  coverage/   freq_detector  stalled_detector  gap_detector  quality_checks  score  report
  db/         schema.sql  connection.py  upsert.py
  config/     tickers.yaml (111)  macro_series.yaml (38)  settings.yaml
  reader.py   config_loader.py  runner.py  _ssl_bootstrap.py
run_daily.py  run_backfill.py  diagnose.py  setup_scheduler.ps1
```
