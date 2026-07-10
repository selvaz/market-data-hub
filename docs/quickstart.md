# Quick start

## Install

```bash
git clone https://github.com/selvaz/market-data-hub
cd market-data-hub
pip install -e .          # or: pip install -r requirements.txt
```

On Windows, the recommended first run is interactive:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_first_run.ps1
```

The script installs dependencies, asks for local secrets, writes user-level
environment variables, verifies the configuration, and can optionally create
scheduled tasks or start the historical backfill:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_first_run.ps1 -ConfigureScheduler
powershell -ExecutionPolicy Bypass -File .\setup_first_run.ps1 -RunBackfill
```

market-data-hub is a **private, git-installed package** (not on PyPI). Other
projects that want the read API install it the same way:

```bash
pip install 'market-data-hub @ git+https://github.com/selvaz/market-data-hub.git'
```

## Configure

1. **FRED API key** (required on networks where the public CSV endpoint is
   blocked): set the `FRED_API_KEY` environment variable. Do not commit keys in
   `settings.yaml`.
2. **Database path** — resolution order:
   explicit `db_path=` argument — `MARKET_DATA_DB` env var ---
   `settings.yaml::db_path` — platform default
   (`.\market_data.duckdb`, relative to the repo root).
3. **SSL / corporate proxy** — handled automatically on first import:
   `_ssl_bootstrap` builds a CA bundle (certifi + Windows root CAs) and exports
   it to `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE` / `CURL_CA_BUNDLE`, so
   curl_cffi and requests work behind a MITM proxy.

The universe itself (which symbols, series, indicators, countries) lives in
four YAML catalogs under `market_data_hub/config/` — see the
[Data catalogue](DATA_CATALOG.md). An Excel round-trip
(`export_to_excel.py` / `import_from_excel.py`) exists for spreadsheet editing,
with `validate_config.py` guarding consistency.

## First load, then daily

```bash
# initial historical load (Yahoo 2010, FRED 2000, Binance 2018 — per-source
# dates from settings.yaml; runs under the writer lock, safe to re-run)
python run_backfill.py

# incremental daily download (yahoo + fred + binance + panel + factors + live)
python run_daily.py

# intraday live price injection only
python run_daily.py --live-only

# restrict to some sources / cap the end date
python run_daily.py --sources yahoo fred
python run_daily.py --end 2024-12-31
```

## Check the data

```bash
python diagnose.py                 # full coverage table (score, lag, gaps)
python diagnose.py --stalled       # stalled series only
python diagnose.py --symbol SPY    # symbol detail
python diagnose.py --summary       # DB statistics
python diagnose.py --runs          # latest runs from download_log

# validate cross-country codes (WDI/WGI/WEO) against the live APIs
python validate_macro_panel.py            # sample of 5 countries
python validate_macro_panel.py --full     # all 64 countries
```

An HTML status report comes from `make_report.py` (`python run_daily.py
--report` bundles report generation into the daily run). A neutral
cross-country dashboard comes from `make_country_dashboard.py`.
`send_telegram_run_report.py` sends the run report to Telegram; add
`--dashboard` to send the country dashboard instead. `run_daily_with_telegram.ps1`
(used by the scheduled tasks below) calls it twice per run — once plain, once
with `--dashboard` — so both land in Telegram as separate messages.

The Ray Dalio-style debt-cycle / growth-inflation regime classifier and the
5-engine country risk architecture (`make_dalio_report.py`, `run_dalio_v2.py`)
have moved to the separate [LazyRay](https://github.com/selvaz/LazyRay) repo,
which reads this hub's macro panel read-only via
`reader.read_macro_panel_ext()` and keeps its own output storage.

## Automate (Windows Task Scheduler)

```powershell
powershell -ExecutionPolicy Bypass -File C:\Users\Administrator\Documents\GitHub\market-data-hub\setup_scheduler.ps1
```

Creates three tasks: `MarketData_EU18` (daily 09:00 Pacific, roughly 18:00
Europe/Rome), `MarketData_USClose` (Mon-Fri 13:15 Pacific, shortly after the
US cash close) — both run the daily refresh and then send the run report
*and* the country dashboard to Telegram as two separate messages — and
`MarketData_HMMRegime` (Mon-Fri 13:45 Pacific), which independently runs the
per-symbol HMM regime monitor and sends its own report. Logs append into
`logs/<task>.log`. Remove all three with `-Remove`. See
[Architecture & process](ARCHITECTURE.md#7-automation) for the full task
table and why the scheduled action uses `-Command` instead of `-File`.

## Read it from your code

```python
from market_data_hub import catalog, extract, reader

# discover
catalog.list_symbols(asset_class="EQUITY", area="USA", sector="*")
catalog.search("inflation")

# analysis-ready matrices
df, meta = extract.extract_returns(["SPY", "TLT"], frequency="W")
macro, m  = extract.extract_series(["DGS10", "T10Y2Y"], domain="macro",
                                   transform="diff")

# raw reads (read-only connection, safe while the downloader runs)
px  = reader.read_prices(["SPY"], start="2020-01-01")
pit = reader.read_macro(["CPIAUCSL"], asof="2023-01-15")   # point-in-time
```

The full API (all four layers, LLM tools included) is in
[Extraction & discovery](EXTRACTION.md). For wiring the hub into a LazyBridge
agent, install [LazyTools](https://github.com/selvaz/LazyTools) and use
`lazytools.connectors.datahub.DataHubTools`.


