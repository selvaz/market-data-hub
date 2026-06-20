-- ============================================================================
-- market_data_hub — DuckDB schema
-- 5 tables + indexes + 4 views. Idempotent (CREATE IF NOT EXISTS).
-- ============================================================================

CREATE SEQUENCE IF NOT EXISTS seq_log_id START 1;

-- ----------------------------------------------------------------------------
-- 0. schema_meta — schema version + bookkeeping (one row per key)
--    Populated by connection.apply_schema(): schema_version, schema_applied_at.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_meta (
    key   VARCHAR PRIMARY KEY,
    value VARCHAR
);

-- ----------------------------------------------------------------------------
-- 1. prices_daily — daily OHLCV (equity, ETF, FX, VIX indices, crypto daily)
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
    is_live     BOOLEAN DEFAULT FALSE,  -- TRUE = live intraday row, overwritten by the EOD
    updated_at  TIMESTAMP,
    PRIMARY KEY (date, symbol)
);
CREATE INDEX IF NOT EXISTS idx_prices_symbol ON prices_daily (symbol);

-- ----------------------------------------------------------------------------
-- 2. crypto_ohlcv — Binance intraday data (1h, 4h, 1d)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS crypto_ohlcv (
    ts              TIMESTAMP   NOT NULL,   -- open time UTC
    symbol          VARCHAR     NOT NULL,   -- e.g. BTCUSDT
    timeframe       VARCHAR     NOT NULL,   -- '1h' | '4h' | '1d'
    open            DOUBLE,
    high            DOUBLE,
    low             DOUBLE,
    close           DOUBLE,
    volume          DOUBLE,
    volume_quote    DOUBLE,
    n_trades        INTEGER,
    taker_buy_base  DOUBLE,
    is_closed       BOOLEAN DEFAULT TRUE,   -- FALSE = incomplete candle (the last one)
    updated_at      TIMESTAMP,
    PRIMARY KEY (ts, symbol, timeframe)
);
CREATE INDEX IF NOT EXISTS idx_crypto_symbol_tf ON crypto_ohlcv (symbol, timeframe);

-- ----------------------------------------------------------------------------
-- 3. macro_series — single-value macro series (FRED: rates, CPI, GDP, credit, ...)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS macro_series (
    date        DATE        NOT NULL,
    series_id   VARCHAR     NOT NULL,   -- e.g. 'FEDFUNDS', 'DGS10', 'CPIAUCSL'
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
-- 3b. macro_panel — cross-country macro panel (World Bank WDI/WGI, IMF WEO)
--     Different model from macro_series: key (date, country, indicator).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS macro_panel (
    date             DATE        NOT NULL,
    country_iso3     VARCHAR     NOT NULL,
    indicator_id     VARCHAR     NOT NULL,   -- e.g. 'real_gdp_growth'
    value            DOUBLE,
    indicator_name   VARCHAR,
    pillar           VARCHAR,                -- growth/liquidity/external/...
    orientation      INTEGER,                -- +1 healthier / -1 worse / 0
    source           VARCHAR,                -- 'worldbank' | 'imf'
    provider_dataset VARCHAR,                -- WDI | WGI | WEO
    provider_code    VARCHAR,                -- provider's native code
    unit             VARCHAR,
    frequency        VARCHAR,                -- 'A' (annual) / 'Q'
    updated_at       TIMESTAMP,
    PRIMARY KEY (date, country_iso3, indicator_id)
);
CREATE INDEX IF NOT EXISTS idx_panel_country ON macro_panel (country_iso3);
CREATE INDEX IF NOT EXISTS idx_panel_indicator ON macro_panel (indicator_id);

-- ----------------------------------------------------------------------------
-- 3c. "Ray Dalio" analytical layer (computed by dalio.py on top of macro_panel)
--     - dalio_signals : z-score (x direction) per (country, indicator)
--     - pillar_scores : score per pillar + composite + 3 categorical labels
--     - regime_state  : four-box growth/inflation + debt-cycle phase + deleveraging
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dalio_signals (
    country_iso3  VARCHAR NOT NULL,
    ref_date      DATE    NOT NULL,
    indicator_id  VARCHAR NOT NULL,
    pillar        VARCHAR,
    value         DOUBLE,                 -- last value
    z_score       DOUBLE,                 -- (x-mean)/std over 10y window, already x direction
    z_window_n    INTEGER,                -- n observations used
    signal        VARCHAR,                -- POS / NEG / NEUTRAL
    computed_at   TIMESTAMP,
    PRIMARY KEY (country_iso3, ref_date, indicator_id)
);

CREATE TABLE IF NOT EXISTS pillar_scores (
    country_iso3     VARCHAR NOT NULL,
    ref_date         DATE    NOT NULL,
    pillar           VARCHAR NOT NULL,    -- 'COMPOSITE' for the aggregate row
    score            DOUBLE,              -- mean z x direction of the pillar
    n_indicators     INTEGER,
    debt_cycle_phase VARCHAR,             -- only on the COMPOSITE row
    short_cycle_pos  VARCHAR,
    gi_regime        VARCHAR,             -- Q1..Q4
    computed_at      TIMESTAMP,
    PRIMARY KEY (country_iso3, ref_date, pillar)
);

CREATE TABLE IF NOT EXISTS regime_state (
    country_iso3         VARCHAR NOT NULL,
    ref_date             DATE    NOT NULL,
    growth_delta         DOUBLE,          -- growth vs trend/expected
    infl_delta           DOUBLE,          -- inflation vs trend/expected
    quadrant             VARCHAR,         -- Q1/Q2/Q3/Q4
    debt_cycle_phase     VARCHAR,         -- from classify_debt_cycle_phase()
    nom_growth           DOUBLE,
    nom_rate             DOUBLE,
    deleveraging_quality VARCHAR,         -- BEAUTIFUL / UGLY / NA
    credit_gap           DOUBLE,
    dsr                  DOUBLE,
    debt_income_gap      DOUBLE,
    debt_trend           DOUBLE,          -- debt/GDP trajectory (pp/year, incl. forecast)
    computed_at          TIMESTAMP,
    PRIMARY KEY (country_iso3, ref_date)
);

-- ----------------------------------------------------------------------------
-- 4. download_log — audit trail of every run (one row per symbol per run)
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
-- 5. coverage_report — status for each (symbol, source), updated on every run
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS coverage_report (
    symbol         VARCHAR     NOT NULL,
    source         VARCHAR     NOT NULL,
    asset_class    VARCHAR,
    first_date     DATE,
    last_date      DATE,
    obs_count      INTEGER,
    freq_detected  VARCHAR,            -- 'D' | 'W' | 'M' | 'Q' | 'A' | 'irregular_Xd'
    lag_days       INTEGER,            -- days from last_date to today
    stalled        BOOLEAN,            -- lag_days > threshold per frequency
    gap_count      INTEGER,            -- number of gaps in the time series
    missing_pct    DOUBLE,             -- % of expected dates missing
    coverage_score DOUBLE,             -- 0-100, freq-aware
    has_zero_price BOOLEAN,
    has_negative   BOOLEAN,
    status         VARCHAR,            -- 'ok' | 'stalled' | 'error' | 'empty'
    error_msg      VARCHAR,
    last_run_id    VARCHAR,
    updated_at     TIMESTAMP,
    PRIMARY KEY (symbol, source)
);

-- macro_panel_coverage — cross-country availability score per indicator.
-- The macro_panel is a (date, country, indicator) panel, so the standard
-- per-symbol coverage_report does not fit. This scores, for each indicator,
-- how many of the expected countries have data, the freshest date, detected
-- frequency, and a freq-aware stalled flag — using the same coverage engine.
CREATE TABLE IF NOT EXISTS macro_panel_coverage (
    indicator_id      VARCHAR NOT NULL,
    pillar            VARCHAR,
    source            VARCHAR,            -- distinct provider(s) actually used
    n_sources         INTEGER,
    frequency         VARCHAR,            -- declared (A/Q/M)
    freq_detected     VARCHAR,            -- detected on the densest country
    n_countries       INTEGER,            -- countries with >=1 non-null value
    n_countries_total INTEGER,            -- expected (config country universe)
    coverage_pct      DOUBLE,             -- 100 * n_countries / n_countries_total
    first_date        DATE,
    last_date         DATE,
    lag_days          INTEGER,
    stalled           BOOLEAN,
    obs_count         INTEGER,
    status            VARCHAR,            -- 'ok' | 'stalled' | 'empty'
    last_run_id       VARCHAR,
    updated_at        TIMESTAMP,
    PRIMARY KEY (indicator_id)
);


-- Long format: one row per (date, factor_set, factor). Values are DECIMAL
-- returns (Ken French publishes percent; the source converts). factor_set
-- identifies the dataset+frequency, e.g. 'FF5_daily' with factors Mkt-RF, SMB,
-- HML, RMW, CMA, RF; 'MOM_daily' with Mom.
CREATE TABLE IF NOT EXISTS factor_returns (
    date        DATE    NOT NULL,
    factor_set  VARCHAR NOT NULL,
    factor      VARCHAR NOT NULL,
    value       DOUBLE,                 -- decimal return (e.g. 0.0123 = 1.23%)
    frequency   VARCHAR,                -- 'D' | 'M'
    source      VARCHAR,
    updated_at  TIMESTAMP,
    PRIMARY KEY (date, factor_set, factor)
);
CREATE INDEX IF NOT EXISTS idx_factor_returns ON factor_returns (factor_set, factor);


-- ============================================================================
-- POINT-IN-TIME VINTAGES (revisable macro data)
-- ============================================================================
-- FRED/WEO/WDI series are revised after first release. The main macro_series /
-- macro_panel tables keep only the LATEST value, which is correct for monitoring
-- but injects look-ahead bias into backtests. These append-on-change history
-- tables record each distinct value together with the vintage_date on which our
-- ingest first observed it, so a backtest can ask "what was known as of date X".
-- A reader picks the row with the greatest vintage_date <= the as-of date.

CREATE TABLE IF NOT EXISTS macro_series_vintage (
    date         DATE    NOT NULL,
    series_id    VARCHAR NOT NULL,
    value        DOUBLE,
    vintage_date DATE    NOT NULL,    -- ingest date this value was first seen
    source       VARCHAR,
    PRIMARY KEY (date, series_id, vintage_date)
);
CREATE INDEX IF NOT EXISTS idx_msv ON macro_series_vintage (series_id, date);

CREATE TABLE IF NOT EXISTS macro_panel_vintage (
    date          DATE    NOT NULL,
    country_iso3  VARCHAR NOT NULL,
    indicator_id  VARCHAR NOT NULL,
    value         DOUBLE,
    vintage_date  DATE    NOT NULL,
    source        VARCHAR,
    PRIMARY KEY (date, country_iso3, indicator_id, vintage_date)
);
CREATE INDEX IF NOT EXISTS idx_mpv ON macro_panel_vintage (indicator_id, country_iso3, date);


-- ============================================================================
-- VIEWS
-- ============================================================================

-- v_returns — daily log returns from adj_close
CREATE OR REPLACE VIEW v_returns AS
SELECT
    date,
    symbol,
    adj_close,
    ln(adj_close / lag(adj_close) OVER (PARTITION BY symbol ORDER BY date)) AS log_return
FROM prices_daily
WHERE adj_close IS NOT NULL AND adj_close > 0;

-- v_vix_term_structure — pivot of the VIX term structure by date
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

-- (the wide pivot of the macro series is provided by reader.read_macro(wide=True):
--  DuckDB does not allow a dynamic PIVOT inside a VIEW)

-- v_stalled — only the symbols flagged as stalled
CREATE OR REPLACE VIEW v_stalled AS
SELECT symbol, source, asset_class, last_date, lag_days, freq_detected,
       coverage_score, status, error_msg
FROM coverage_report
WHERE stalled = TRUE
ORDER BY lag_days DESC;
