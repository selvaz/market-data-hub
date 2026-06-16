# market_data_hub

Downloader unificato di dati di mercato con database **DuckDB**, automazione
giornaliera e coverage engine. Consolida i download sparsi nei progetti
`quant_timeseries_suite`, `quant_vix_calibrator`, `zero_noise_pipeline`,
`crypto_ml_features` e `macro_dashboard_v2_bundle` in un'unica pipeline.

## Documentation (English)

- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — what the process does, the
  data-flow, the main functions, the full DB schema, the coverage engine.
- **[docs/DATA_CATALOG.md](docs/DATA_CATALOG.md)** — every series with provider,
  group, frequency, typical lag and history depth; plus the proposed
  cross-country WDI/WEO/WGI/BIS panel extension.

## Cosa scarica

| Sorgente | Cosa | Tabella | Frequenza |
|----------|------|---------|-----------|
| Yahoo Finance | 111 simboli (ETF, equity, FX, indici VIX, crypto daily) — OHLCV + adj_close | `prices_daily` | daily |
| Binance | 6 simboli crypto × {1h, 4h, 1d} — OHLCV esteso | `crypto_ohlcv` | intraday |
| FRED | 38 serie macro (tassi US/EA, CPI, GDP, credit spreads, ...) | `macro_series` | D/M/Q |
| World Bank + IMF | 39 indicatori cross-country (WDI/WGI/WEO) × 64 paesi | `macro_panel` | annual |

## Setup

```bash
pip install -r requirements.txt
```

1. **FRED API key** (necessaria su questa rete): apri
   `market_data_hub/config/settings.yaml` e imposta `fred_api_key: "LA_TUA_KEY"`.
   In alternativa esporta la variabile d'ambiente `FRED_API_KEY`.
   > Su questa macchina il proxy blocca l'endpoint pubblico CSV di FRED; l'API
   > ufficiale (con key) e' invece raggiungibile.

2. La verifica SSL e' gestita automaticamente: al primo import viene costruito
   `ca_bundle.pem` (certifi + root CA di Windows) per superare il MITM/proxy
   aziendale. Vale per yfinance (curl_cffi), requests e urllib.

## Uso

```bash
# caricamento storico iniziale (Yahoo 2010, FRED 2000, Binance 2018)
python run_backfill.py

# download giornaliero incrementale (yahoo + fred + binance + live)
python run_daily.py

# solo live price injection intraday
python run_daily.py --live-only

# diagnostica
python diagnose.py                 # tabella coverage completa
python diagnose.py --stalled       # solo serie ferme
python diagnose.py --symbol SPY    # dettaglio simbolo
python diagnose.py --summary       # statistiche DB
python diagnose.py --runs          # ultimi run

# validazione codici cross-country (WDI/WGI/WEO) contro le API live
python validate_macro_panel.py            # campione 5 paesi
python validate_macro_panel.py --full     # tutti i 64 paesi
```

## Automazione (Windows Task Scheduler)

```powershell
powershell -ExecutionPolicy Bypass -File D:\market_data\setup_scheduler.ps1
```

Crea 3 task: `MarketDataEOD` (22:00), `MarketDataWeekend` (sab 08:00, refresh
FRED), `MarketDataLive` (16:00-22:00 ogni ora). Rimozione: aggiungi `-Remove`.

## Lettura dai progetti esistenti

```python
from market_data_hub.reader import read_prices, read_macro, read_crypto, get_coverage

px   = read_prices(["SPY", "^VIX"], start="2020-01-01")          # wide adj_close
vix  = read_prices(["^VIX9D","^VIX","^VIX3M","^VIX6M"])           # term structure
mac  = read_macro(["DGS10", "CPIAUCSL"])
btc  = read_crypto("BTCUSDT", "1h", start="2024-01-01")
cov  = get_coverage()                                            # stato qualita'
```

## Coverage engine (qualita' dati)

Ad ogni run viene ricostruita la tabella `coverage_report` con, per ogni serie:
frequenza rilevata, `last_date`, `lag_days`, flag **stalled** (freq-aware:
D=3gg, W=10, M=45, Q=120, A=400), conteggio gap, `missing_pct`, **coverage
score 0-100** e flag di qualita' (zero/negative price, anomalie adj/close).
Logica portata da `checks1_improved.py` e `macro_dashboard.py`.

## Struttura

```
market_data_hub/
  sources/    yahoo.py  binance.py  fred.py  base.py
  coverage/   freq_detector  stalled_detector  gap_detector  quality_checks  score  report
  db/         schema.sql  connection.py  upsert.py
  config/     tickers.yaml (111)  macro_series.yaml (38)  settings.yaml
  reader.py   config_loader.py  runner.py  _ssl_bootstrap.py
run_daily.py  run_backfill.py  diagnose.py  setup_scheduler.ps1
```
