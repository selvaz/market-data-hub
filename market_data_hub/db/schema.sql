-- ============================================================================
-- market_data_hub — DuckDB schema
-- 5 tabelle + indici + 4 view. Idempotente (CREATE IF NOT EXISTS).
-- ============================================================================

CREATE SEQUENCE IF NOT EXISTS seq_log_id START 1;

-- ----------------------------------------------------------------------------
-- 1. prices_daily — OHLCV giornaliero (equity, ETF, FX, VIX indices, crypto daily)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS prices_daily (
    date        DATE        NOT NULL,
    symbol      VARCHAR     NOT NULL,
    open        DOUBLE,
    high        DOUBLE,
    low         DOUBLE,
    close       DOUBLE,
    adj_close   DOUBLE,
    volume      BIGINT,
    source      VARCHAR,           -- 'yahoo' | 'binance_daily'
    is_live     BOOLEAN DEFAULT FALSE,  -- TRUE = riga intraday live, sovrascritta dall'EOD
    updated_at  TIMESTAMP,
    PRIMARY KEY (date, symbol)
);
CREATE INDEX IF NOT EXISTS idx_prices_symbol ON prices_daily (symbol);

-- ----------------------------------------------------------------------------
-- 2. crypto_ohlcv — dati intraday Binance (1h, 4h, 1d)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS crypto_ohlcv (
    ts              TIMESTAMP   NOT NULL,   -- open time UTC
    symbol          VARCHAR     NOT NULL,   -- es. BTCUSDT
    timeframe       VARCHAR     NOT NULL,   -- '1h' | '4h' | '1d'
    open            DOUBLE,
    high            DOUBLE,
    low             DOUBLE,
    close           DOUBLE,
    volume          DOUBLE,
    volume_quote    DOUBLE,
    n_trades        INTEGER,
    taker_buy_base  DOUBLE,
    is_closed       BOOLEAN DEFAULT TRUE,   -- FALSE = candela incompleta (ultima)
    updated_at      TIMESTAMP,
    PRIMARY KEY (ts, symbol, timeframe)
);
CREATE INDEX IF NOT EXISTS idx_crypto_symbol_tf ON crypto_ohlcv (symbol, timeframe);

-- ----------------------------------------------------------------------------
-- 3. macro_series — serie macro single-value (FRED: tassi, CPI, GDP, credit, ...)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS macro_series (
    date        DATE        NOT NULL,
    series_id   VARCHAR     NOT NULL,   -- es. 'FEDFUNDS', 'DGS10', 'CPIAUCSL'
    value       DOUBLE,
    series_name VARCHAR,
    unit        VARCHAR,
    frequency   VARCHAR,                -- 'D' | 'M' | 'Q' | 'A'
    source      VARCHAR,                -- 'fred'
    country     VARCHAR,                -- 'US' | 'EA' | ...
    updated_at  TIMESTAMP,
    PRIMARY KEY (date, series_id)
);
CREATE INDEX IF NOT EXISTS idx_macro_series ON macro_series (series_id);

-- ----------------------------------------------------------------------------
-- 3b. macro_panel — panel macro cross-country (World Bank WDI/WGI, IMF WEO)
--     Modello diverso da macro_series: chiave (date, country, indicator).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS macro_panel (
    date             DATE        NOT NULL,
    country_iso3     VARCHAR     NOT NULL,
    indicator_id     VARCHAR     NOT NULL,   -- es. 'real_gdp_growth'
    value            DOUBLE,
    indicator_name   VARCHAR,
    pillar           VARCHAR,                -- growth/liquidity/external/...
    orientation      INTEGER,                -- +1 healthier / -1 worse / 0
    source           VARCHAR,                -- 'worldbank' | 'imf'
    provider_dataset VARCHAR,                -- WDI | WGI | WEO
    provider_code    VARCHAR,                -- codice nativo provider
    unit             VARCHAR,
    frequency        VARCHAR,                -- 'A' (annuale) / 'Q'
    updated_at       TIMESTAMP,
    PRIMARY KEY (date, country_iso3, indicator_id)
);
CREATE INDEX IF NOT EXISTS idx_panel_country ON macro_panel (country_iso3);
CREATE INDEX IF NOT EXISTS idx_panel_indicator ON macro_panel (indicator_id);

-- ----------------------------------------------------------------------------
-- 3c. Layer analitico "Ray Dalio" (calcolato da dalio.py sopra macro_panel)
--     - dalio_signals : z-score (x direction) per (paese, indicatore)
--     - pillar_scores : score per pilastro + composite + 3 etichette categoriche
--     - regime_state  : four-box growth/inflation + fase ciclo debito + deleveraging
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dalio_signals (
    country_iso3  VARCHAR NOT NULL,
    ref_date      DATE    NOT NULL,
    indicator_id  VARCHAR NOT NULL,
    pillar        VARCHAR,
    value         DOUBLE,                 -- ultimo valore
    z_score       DOUBLE,                 -- (x-mean)/std finestra 10y, gia' x direction
    z_window_n    INTEGER,                -- n osservazioni usate
    signal        VARCHAR,                -- POS / NEG / NEUTRAL
    computed_at   TIMESTAMP,
    PRIMARY KEY (country_iso3, ref_date, indicator_id)
);

CREATE TABLE IF NOT EXISTS pillar_scores (
    country_iso3     VARCHAR NOT NULL,
    ref_date         DATE    NOT NULL,
    pillar           VARCHAR NOT NULL,    -- 'COMPOSITE' per la riga aggregata
    score            DOUBLE,              -- media z x direction del pilastro
    n_indicators     INTEGER,
    debt_cycle_phase VARCHAR,             -- solo sulla riga COMPOSITE
    short_cycle_pos  VARCHAR,
    gi_regime        VARCHAR,             -- Q1..Q4
    computed_at      TIMESTAMP,
    PRIMARY KEY (country_iso3, ref_date, pillar)
);

CREATE TABLE IF NOT EXISTS regime_state (
    country_iso3         VARCHAR NOT NULL,
    ref_date             DATE    NOT NULL,
    growth_delta         DOUBLE,          -- crescita vs trend/attesa
    infl_delta           DOUBLE,          -- inflazione vs trend/attesa
    quadrant             VARCHAR,         -- Q1/Q2/Q3/Q4
    debt_cycle_phase     VARCHAR,         -- da classify_debt_cycle_phase()
    nom_growth           DOUBLE,
    nom_rate             DOUBLE,
    deleveraging_quality VARCHAR,         -- BEAUTIFUL / UGLY / NA
    credit_gap           DOUBLE,
    dsr                  DOUBLE,
    debt_income_gap      DOUBLE,
    debt_trend           DOUBLE,          -- traiettoria debito/PIL (pp/anno, incl. forecast)
    computed_at          TIMESTAMP,
    PRIMARY KEY (country_iso3, ref_date)
);

-- ----------------------------------------------------------------------------
-- 4. download_log — audit trail di ogni run (una riga per symbol per run)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS download_log (
    log_id        BIGINT      DEFAULT nextval('seq_log_id'),
    run_id        VARCHAR     NOT NULL,
    started_at    TIMESTAMP,
    ended_at      TIMESTAMP,
    source        VARCHAR,
    symbol        VARCHAR,
    rows_added    INTEGER,
    rows_updated  INTEGER,
    status        VARCHAR,            -- 'ok' | 'error' | 'skipped' | 'empty'
    error_msg     VARCHAR,
    duration_sec  DOUBLE
);

-- ----------------------------------------------------------------------------
-- 5. coverage_report — stato per ogni (symbol, source), aggiornato ad ogni run
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS coverage_report (
    symbol         VARCHAR     NOT NULL,
    source         VARCHAR     NOT NULL,
    asset_class    VARCHAR,
    first_date     DATE,
    last_date      DATE,
    obs_count      INTEGER,
    freq_detected  VARCHAR,            -- 'D' | 'W' | 'M' | 'Q' | 'A' | 'irregular_Xd'
    lag_days       INTEGER,            -- giorni da last_date a oggi
    stalled        BOOLEAN,            -- lag_days > soglia per frequenza
    gap_count      INTEGER,            -- numero di buchi nel time series
    missing_pct    DOUBLE,             -- % date attese mancanti
    coverage_score DOUBLE,             -- 0-100, freq-aware
    has_zero_price BOOLEAN,
    has_negative   BOOLEAN,
    status         VARCHAR,            -- 'ok' | 'stalled' | 'error' | 'empty'
    error_msg      VARCHAR,
    last_run_id    VARCHAR,
    updated_at     TIMESTAMP,
    PRIMARY KEY (symbol, source)
);

-- ============================================================================
-- VIEWS
-- ============================================================================

-- v_returns — log returns giornalieri da adj_close
CREATE OR REPLACE VIEW v_returns AS
SELECT
    date,
    symbol,
    adj_close,
    ln(adj_close / lag(adj_close) OVER (PARTITION BY symbol ORDER BY date)) AS log_return
FROM prices_daily
WHERE adj_close IS NOT NULL AND adj_close > 0;

-- v_vix_term_structure — pivot della term structure VIX per data
CREATE OR REPLACE VIEW v_vix_term_structure AS
SELECT
    date,
    MAX(CASE WHEN symbol = '^VIX9D' THEN adj_close END) AS vix9d,
    MAX(CASE WHEN symbol = '^VIX'   THEN adj_close END) AS vix,
    MAX(CASE WHEN symbol = '^VIX3M' THEN adj_close END) AS vix3m,
    MAX(CASE WHEN symbol = '^VIX6M' THEN adj_close END) AS vix6m,
    MAX(CASE WHEN symbol = '^VVIX'  THEN adj_close END) AS vvix,
    MAX(CASE WHEN symbol = '^VXN'   THEN adj_close END) AS vxn
FROM prices_daily
WHERE symbol IN ('^VIX9D','^VIX','^VIX3M','^VIX6M','^VVIX','^VXN')
GROUP BY date;

-- (il pivot wide delle serie macro e' fornito da reader.read_macro(wide=True):
--  DuckDB non ammette PIVOT dinamico dentro una VIEW)

-- v_stalled — solo i simboli marcati come fermi
CREATE OR REPLACE VIEW v_stalled AS
SELECT symbol, source, asset_class, last_date, lag_days, freq_detected,
       coverage_score, status, error_msg
FROM coverage_report
WHERE stalled = TRUE
ORDER BY lag_days DESC;
