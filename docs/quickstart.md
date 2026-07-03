# Quick start

## Install

```bash
git clone https://github.com/selvaz/market-data-hub
cd market-data-hub
pip install -e .          # or: pip install -r requirements.txt
```

market-data-hub is a **private, git-installed package** (not on PyPI). Other
projects that want the read API install it the same way:

```bash
pip install 'market-data-hub @ git+https://github.com/selvaz/market-data-hub.git'
```

## Configure

1. **FRED API key** (required on networks where the public CSV endpoint is
   blocked): set `fred_api_key` in `market_data_hub/config/settings.yaml`, or
   export `FRED_API_KEY`.
2. **Database path** — resolution order:
   explicit `db_path=` argument → `MARKET_DATA_DB` env var →
   `settings.yaml::db_path` → platform default
   (`D:\market_data\market_data.duckdb` on Windows,
   `~/.market_data/market_data.duckdb` elsewhere).
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

An HTML status report (and optional e-mail) comes from `make_report.py`; the
self-contained macro dashboard from `make_dalio_report.py`
(`python run_daily.py --report` bundles report generation into the daily run).

## Automate (Windows Task Scheduler)

```powershell
powershell -ExecutionPolicy Bypass -File D:\market_data\setup_scheduler.ps1
```

Creates three tasks: `MarketDataEOD` (22:00), `MarketDataWeekend`
(Sat 08:00, FRED refresh), `MarketDataLive` (16:00–22:00 hourly).
Remove them with `-Remove`.

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
