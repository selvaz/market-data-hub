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
-- Keyed by LISTING, not by bare symbol (audit CA-01): two listings sharing a
-- ticker (dual listing, ADR, venue) can never overwrite each other. symbol
-- stays as a denormalized read convenience for univocal mappings; upsert()
-- attaches listing_id automatically for batch writers and refuses ambiguous
-- symbols. No secondary index on symbol (duckdb 1.4.x INSERT OR REPLACE
-- keeps the OLD value of an indexed non-key column on the conflict path).
CREATE TABLE IF NOT EXISTS prices_daily (
    date        DATE        NOT NULL,
    listing_id  VARCHAR     NOT NULL,
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
    PRIMARY KEY (date, listing_id)
);

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

-- ----------------------------------------------------------------------------
-- 6. custom_series — user/app-published series (NOT written by the hub's own
--    connectors). Downstream apps (e.g. LazyFin portfolio NAV histories,
--    custom composite indicators, series from providers the hub has no
--    connector for) expand the hub through market_data_hub.custom.store_series
--    and read back via reader.read_custom / extract_series(domain="custom").
--    Kept separate from macro_series so a custom series_id can never collide
--    with (or silently overwrite) a curated FRED id.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS custom_series (
    date        DATE        NOT NULL,
    series_id   VARCHAR     NOT NULL,   -- publisher-chosen id, e.g. 'lazyfin:nav:pf-1'
    value       DOUBLE,
    series_name VARCHAR,
    unit        VARCHAR,                -- free text: 'USD', 'index', 'ratio', ...
    frequency   VARCHAR,                -- 'D' | 'W' | 'M' | 'Q' | 'A' (declared)
    source      VARCHAR,                -- publishing app, e.g. 'lazyfin'
    updated_at  TIMESTAMP,
    PRIMARY KEY (date, series_id)
);
CREATE INDEX IF NOT EXISTS idx_custom_series ON custom_series (series_id);


-- ============================================================================
-- POINT-IN-TIME VINTAGES (revisable macro data)
-- ============================================================================
-- FRED/WEO/WDI series are revised after first release. The main macro_series /
-- macro_panel tables keep only the LATEST value, which is correct for monitoring
-- but injects look-ahead bias into backtests. These append-on-change history
-- tables record each distinct value together with the vintage_date on which our
-- ingest first observed it, so a backtest can ask "what was known as of date X".
-- A reader picks the row with the greatest vintage_date <= the as-of date.

-- run_id / change_type / prior_value: which run recorded this vintage row, and
-- whether it was a brand-new (date, key) observation or a revision of a value
-- already on record for that same date. NULL on rows written before this
-- tracking existed. change_type is 'new' | 'revised'.
--
-- Day granularity (deliberate): vintage_date is a DATE inside the PRIMARY
-- KEY, so each calendar day holds at most ONE row per key -- the day is the
-- vintage unit. A same-day re-observation with a different value REPLACES
-- that day's row, but record_vintage() merges the metadata: the surviving
-- row inherits the predecessor's change_type and prior_value, so it always
-- describes the day as a whole vs the previous day's knowledge. Intermediate
-- intraday values are not preserved (run_id reflects the last writer of the
-- day); as-of reads see end-of-day values, which is the intended backtest
-- semantics.
CREATE TABLE IF NOT EXISTS macro_series_vintage (
    date         DATE    NOT NULL,
    series_id    VARCHAR NOT NULL,
    value        DOUBLE,
    vintage_date DATE    NOT NULL,    -- ingest date this value was first seen
    source       VARCHAR,
    run_id       VARCHAR,
    change_type  VARCHAR,
    prior_value  DOUBLE,
    PRIMARY KEY (date, series_id, vintage_date)
);
CREATE INDEX IF NOT EXISTS idx_msv ON macro_series_vintage (series_id, date);
-- No index on run_id (deliberate): on duckdb 1.4.x a secondary index on a
-- column makes INSERT OR REPLACE keep the OLD value of that column on the
-- conflict path (fixed in 1.5.x, which no longer supports Python 3.9), so an
-- idx on run_id silently broke same-day vintage replacements. The report's
-- WHERE run_id = ? scan is milliseconds on this table size; do not re-add.

CREATE TABLE IF NOT EXISTS macro_panel_vintage (
    date          DATE    NOT NULL,
    country_iso3  VARCHAR NOT NULL,
    indicator_id  VARCHAR NOT NULL,
    value         DOUBLE,
    vintage_date  DATE    NOT NULL,
    source        VARCHAR,
    run_id        VARCHAR,
    change_type   VARCHAR,
    prior_value   DOUBLE,
    PRIMARY KEY (date, country_iso3, indicator_id, vintage_date)
);
CREATE INDEX IF NOT EXISTS idx_mpv ON macro_panel_vintage (indicator_id, country_iso3, date);
-- No index on run_id here either -- see the note on macro_series_vintage.


-- ============================================================================
-- IDENTITY & INGESTION LEDGER (plan v3.1 — issuer / instrument / listing)
-- ============================================================================
-- A CIK identifies an ISSUER (legal entity); a ticker identifies a LISTING
-- (symbol on a venue in a currency). They are distinct: one issuer can have
-- several share classes, ADRs and historic tickers. These tables layer that
-- identity model OVER the existing flat price tables: prices_daily.symbol
-- joins listings.symbol — prices_daily itself is untouched (its INSERT OR
-- REPLACE upsert path would NULL any extra column it does not write).
-- No secondary indexes here (duckdb 1.4.x INSERT OR REPLACE + index bug, see
-- the macro_series_vintage note); these tables are small, scans are fine.

CREATE TABLE IF NOT EXISTS issuers (
    issuer_id       VARCHAR PRIMARY KEY,   -- 'iss_' + stable slug/hash
    cik             VARCHAR UNIQUE,        -- SEC CIK, zero-padded 10 digits; NULL if none
    name            VARCHAR,
    sic             VARCHAR,
    fiscal_year_end VARCHAR,               -- 'MM-DD'
    created_at      TIMESTAMP,
    updated_at      TIMESTAMP
);

CREATE TABLE IF NOT EXISTS instruments (
    instrument_id VARCHAR PRIMARY KEY,     -- 'ins_' + stable slug/hash
    issuer_id     VARCHAR,                 -- NULL for indices, FX, commodities
    kind          VARCHAR NOT NULL,        -- EQUITY | ETF | INDEX | FX | CRYPTO | COMMODITY | FUND | OTHER
    name          VARCHAR,
    created_at    TIMESTAMP,
    updated_at    TIMESTAMP
);

CREATE TABLE IF NOT EXISTS listings (
    listing_id      VARCHAR PRIMARY KEY,   -- 'lst_' + stable slug/hash
    instrument_id   VARCHAR NOT NULL,
    symbol          VARCHAR NOT NULL,      -- as stored in prices_daily.symbol
    exchange        VARCHAR,               -- MIC or venue label ('XNAS', 'XMIL', ...)
    currency        VARCHAR,
    provider        VARCHAR,               -- default price provider ('yahoo', 'binance')
    provider_symbol VARCHAR,               -- provider-native symbol if it differs
    active_from     DATE,
    active_to       DATE,                  -- NULL = currently active
    created_at      TIMESTAMP,
    updated_at      TIMESTAMP
);

-- Curated-universe classification ("anagrafica"), keyed by symbol (joins
-- listings.symbol / prices_daily.symbol directly -- no synthetic id, since
-- there is exactly one classification row per config-universe symbol).
-- Materializes what catalog.py's `_classify()` previously only derived at
-- query time from the free-text `name` field in config/tickers.yaml, plus a
-- new `benchmark_proxy` slot for the top-down investment-process pillars.
-- Editorial/curated data, distinct from the identity tables above: a symbol
-- can exist in `listings` (identity) without ever being classified here, and
-- vice versa is not expected but not enforced by a FK (small reference table,
-- same rationale as issuers/instruments/listings above).
CREATE TABLE IF NOT EXISTS etf_classification (
    symbol          VARCHAR PRIMARY KEY,
    asset_class     VARCHAR,               -- EQUITY | FIXED_INCOME | COMMODITIES | ALTERNATIVES | FX | REAL_ESTATE
    area            VARCHAR,               -- config area, e.g. 'USA', 'Europe', 'Emerging Markets'
    category        VARCHAR,               -- name token 0 (mirrors catalog._classify's 'category')
    sub_group       VARCHAR,               -- name token 1 (mirrors catalog._classify's 'group')
    sector          VARCHAR,               -- GICS sector for sector ETFs, else NULL
    theme           VARCHAR,               -- for ALTERNATIVES sleeves
    benchmark_proxy VARCHAR,               -- pillar-proxy symbol, e.g. equity/fixed-income/commodity anchor; NULL until assigned
    priority        INTEGER,
    created_at      TIMESTAMP,
    updated_at      TIMESTAMP
);

-- ticker/ISIN/FIGI/CIK -> entity mapping with temporal validity. namespace is
-- e.g. 'ticker', 'ticker_historic', 'isin', 'figi', 'cik'; target_type is
-- 'issuer' | 'instrument' | 'listing'.
CREATE TABLE IF NOT EXISTS identifier_aliases (
    namespace   VARCHAR NOT NULL,
    value       VARCHAR NOT NULL,
    target_type VARCHAR NOT NULL,
    target_id   VARCHAR NOT NULL,
    valid_from  DATE,
    valid_to    DATE,                      -- NULL = still valid
    updated_at  TIMESTAMP,
    PRIMARY KEY (namespace, value, target_type, target_id)
);

-- One row per physical ingestion attempt. download_log stays as the legacy
-- per-symbol audit trail of the batch runner; ingestion_runs is the ledger for
-- on-demand ensure_* capabilities (plan v3.1 §4.1).
CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id          VARCHAR PRIMARY KEY,
    kind            VARCHAR NOT NULL,      -- 'price_history' | 'sec_facts' | ...
    input_json      VARCHAR,               -- normalized request input
    provider        VARCHAR,
    provider_reason VARCHAR,               -- why this provider (fallback is never silent)
    status          VARCHAR NOT NULL,      -- 'running' | 'completed' | 'error'
    attempts        INTEGER DEFAULT 1,
    error_msg       VARCHAR,
    payload_hash    VARCHAR,               -- hash of the written payload
    rows_written    INTEGER,
    started_at      TIMESTAMP,
    finished_at     TIMESTAMP
);

-- Idempotent job envelope: the same normalized request (request_hash) always
-- maps to the same job. Every ensure_* call creates or reuses one of these;
-- there are no long operations without a job_id.
CREATE TABLE IF NOT EXISTS ingestion_jobs (
    job_id       VARCHAR PRIMARY KEY,
    request_hash VARCHAR UNIQUE NOT NULL,
    kind         VARCHAR NOT NULL,
    request_json VARCHAR,
    status       VARCHAR NOT NULL,         -- 'queued' | 'running' | 'completed' | 'error'
    run_id       VARCHAR,                  -- last run serving this job
    requester    VARCHAR,                  -- 'llm:<session>' | 'cli' | 'notebook' | ...
    error_msg    VARCHAR,
    created_at   TIMESTAMP,
    updated_at   TIMESTAMP
);


-- ============================================================================
-- SEC / EDGAR (plan v3.1, Fase 3) — filings metadata and company facts
-- ============================================================================
-- Facts are APPEND-ONLY: a restated value arrives under a new accession (or a
-- different filed date) and lands as a NEW row; history is never overwritten.
-- fact_id is a deterministic hash of the full identity tuple (incl. value) so
-- re-ingestion is idempotent via anti-join, without UPDATE/REPLACE semantics.
-- No secondary indexes (duckdb 1.4.x INSERT OR REPLACE + index bug).

-- Filing metadata keeps run provenance across re-ingestions (audit CA-08):
-- first_seen_run_id never changes after the row is born; last_seen_run_id
-- records the most recent run that observed the filing. The write path is
-- UPDATE-existing + INSERT-new, never a blind INSERT OR REPLACE.
CREATE TABLE IF NOT EXISTS sec_filings (
    cik               VARCHAR NOT NULL,    -- zero-padded 10 digits
    accession         VARCHAR NOT NULL,    -- e.g. 0000320193-24-000123
    form              VARCHAR,             -- 10-K | 10-Q | 8-K | ...
    filed_date        DATE,
    report_date       DATE,
    primary_doc       VARCHAR,
    primary_doc_url   VARCHAR,
    issuer_id         VARCHAR,             -- FK issuers (soft)
    first_seen_run_id VARCHAR,
    last_seen_run_id  VARCHAR,
    updated_at        TIMESTAMP,
    PRIMARY KEY (cik, accession)
);

CREATE TABLE IF NOT EXISTS sec_company_facts (
    fact_id    VARCHAR PRIMARY KEY,        -- sha256[:24] of the identity tuple
    cik        VARCHAR NOT NULL,
    taxonomy   VARCHAR NOT NULL,           -- us-gaap | dei | ifrs-full
    concept    VARCHAR NOT NULL,           -- e.g. Assets, NetIncomeLoss
    unit       VARCHAR NOT NULL,           -- USD, shares, USD/shares
    start_date DATE,                       -- NULL for instant facts
    end_date   DATE NOT NULL,
    value      DOUBLE,
    fy         INTEGER,                    -- fiscal year of the REPORT it came from
    fp         VARCHAR,                    -- FY | Q1 | Q2 | Q3 | Q4
    form       VARCHAR,
    filed_date DATE,
    accession  VARCHAR,
    frame      VARCHAR,                    -- XBRL frame tag when present
    run_id     VARCHAR,
    created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sec_coverage (
    cik         VARCHAR PRIMARY KEY,
    issuer_id   VARCHAR,
    entity_name VARCHAR,
    n_filings   INTEGER,
    n_facts     INTEGER,
    forms       VARCHAR,                   -- distinct forms ingested, csv
    last_filed  DATE,
    lag_days    INTEGER,
    last_run_id VARCHAR,
    updated_at  TIMESTAMP
);


-- ============================================================================
-- VIEWS
-- ============================================================================

-- v_returns — daily log returns from adj_close, per LISTING (CA-01: dual
-- listings sharing a ticker must never be chained into one return series)
CREATE OR REPLACE VIEW v_returns AS
SELECT
    date,
    listing_id,
    symbol,
    adj_close,
    ln(adj_close / lag(adj_close) OVER (PARTITION BY listing_id ORDER BY date)) AS log_return
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

-- v_macro_panel_ext — the cross-country panel PLUS single-country FRED series
--   remapped into panel shape, so the Dalio layer can read both from one place.
--   The data itself stays stored/documented exactly like every other FRED
--   series (in macro_series); this view is a read-only access layer, it never
--   moves or duplicates storage. Mapping: macro_series.country holds the ISO3
--   for these cross-country series, so `country AS country_iso3` is direct.
--   New indicators land on the unweighted pillar 'markets' by design, so the
--   Dalio composite (weighted over the 8 scored pillars) is unchanged until
--   they are explicitly wired into the methodology.
--
--   Three added indicators:
--     bond_yield_10y        — from FRED single-country series (IRLTLT01*)
--     implied_interest_rate — DERIVED from panel rows already present (like
--                             v_returns derives log-returns from prices):
--                             interest_on_debt_gdp (IMF 'ie') ÷ gross debt %GDP
--                             × 100 = effective cost of the sovereign debt stock,
--                             the r in Dalio's nominal-growth-vs-r test. Uses the
--                             dedicated IMF interest series (homogeneous, one
--                             source) rather than differencing two balances.
--     reer_broad            — native BIS panel indicator (in macro_panel table)
CREATE OR REPLACE VIEW v_macro_panel_ext AS
SELECT date, country_iso3, indicator_id, value, indicator_name, pillar,
       orientation, source, provider_dataset, provider_code, unit, frequency,
       updated_at
FROM macro_panel
UNION ALL
SELECT date,
       country                                   AS country_iso3,
       'bond_yield_10y'                          AS indicator_id,
       value,
       '10Y government bond yield (OECD via FRED)' AS indicator_name,
       'markets'                                 AS pillar,
       0                                         AS orientation,
       source,
       'FRED'                                    AS provider_dataset,
       series_id                                 AS provider_code,
       'percent'                                 AS unit,
       'M'                                       AS frequency,
       updated_at
FROM macro_series
WHERE series_id LIKE 'IRLTLT01%' AND value IS NOT NULL
UNION ALL
-- implied_interest_rate — computed per (country, year) from IMF panel rows:
-- interest paid on debt (%GDP) ÷ gross debt (%GDP) × 100 = effective rate.
SELECT date,
       country_iso3,
       'implied_interest_rate'                   AS indicator_id,
       ie / debt * 100                           AS value,
       'Implied interest rate on govt debt (IMF interest %GDP ÷ debt %GDP)' AS indicator_name,
       'markets'                                 AS pillar,
       0                                         AS orientation,
       'derived'                                 AS source,
       'IMF(derived)'                            AS provider_dataset,
       'ie/GGXWDG_NGDP*100'                      AS provider_code,
       'percent'                                 AS unit,
       'A'                                       AS frequency,
       updated_at
FROM (
    SELECT date, country_iso3,
           max(CASE WHEN indicator_id = 'interest_on_debt_gdp' THEN value END) AS ie,
           max(CASE WHEN indicator_id = 'public_debt_gdp'      THEN value END) AS debt,
           max(updated_at)                                                     AS updated_at
    FROM macro_panel
    WHERE indicator_id IN ('interest_on_debt_gdp', 'public_debt_gdp')
      AND value IS NOT NULL
    GROUP BY date, country_iso3
)
WHERE ie IS NOT NULL AND debt > 0
UNION ALL
-- fx_debt_share — % of external (non-resident) debt denominated in FOREIGN
-- currency = Dalio's #1 "can they print their way out?" signal. A pure ratio
-- (no GDP needed), from the two IMF IIPCC series.
SELECT date,
       country_iso3,
       'fx_debt_share'                           AS indicator_id,
       fx / tot * 100                            AS value,
       'FX-denominated share of external debt (IMF IIPCC)' AS indicator_name,
       'markets'                                 AS pillar,
       0                                         AS orientation,
       'derived'                                 AS source,
       'IIPCC(derived)'                          AS provider_dataset,
       'DLNRES_DIC.FC/_T*100'                    AS provider_code,
       'percent'                                 AS unit,
       'A'                                       AS frequency,
       updated_at
FROM (
    SELECT date, country_iso3,
           max(CASE WHEN indicator_id = 'fx_debt_usd'         THEN value END) AS fx,
           max(CASE WHEN indicator_id = 'ext_debt_nonres_usd' THEN value END) AS tot,
           max(updated_at)                                                    AS updated_at
    FROM macro_panel
    WHERE indicator_id IN ('fx_debt_usd', 'ext_debt_nonres_usd')
      AND value IS NOT NULL
    GROUP BY date, country_iso3
)
WHERE fx IS NOT NULL AND tot > 0;


-- ---------------------------------------------------------------------------
-- Economic release calendar
--
-- macro_panel knows which PERIOD a figure refers to, not when that figure
-- became public: the *_vintage tables record when ingestion saw it, which
-- depends on when the job runs. These tables supply the missing axis -- the
-- moment of publication -- and with it the consensus, which the issuing
-- institutions do not publish.
--
-- The three provenances stay apart and never mix within one series:
--   macro_panel/macro_series  multilateral bodies (IMF, World Bank, BIS, ECB)
--   provenance='official'     issuing agencies (BLS, BEA, Census)
--   provenance='aggregator'   third-party calendars (MyFXBook, Tradays, ...)
-- The link to macro_panel is the v_macro_panel_asof view, never a write.
--
-- country_iso3 follows the hub convention; the aggregate euro area uses 'EMU'.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS calendar_indicators (
    indicator_key       VARCHAR PRIMARY KEY,   -- 'us_cpi_yy'
    country_iso3        VARCHAR NOT NULL,      -- 'USA'; aggregate euro area -> 'EMU'
    country_iso2        VARCHAR,               -- matching key against the sources
    area                VARCHAR NOT NULL,      -- reporting group: US, CN, EZ, ...
    name                VARCHAR NOT NULL,
    category            VARCHAR,
    archetype           VARCHAR,               -- shared description/methodology
    value_type          VARCHAR,
    frequency           VARCHAR,               -- M | Q | A | W | E (E = by calendar)
    criticality         VARCHAR,               -- T1 critical | T2 notable | T3 context
    nature              VARCHAR,               -- leading|coincident|lagging|policy
    rationale           VARCHAR,               -- why it matters in this country
    agency              VARCHAR,
    description         VARCHAR,
    methodology         VARCHAR,
    macro_indicator_id  VARCHAR,               -- bridge -> macro_panel.indicator_id
    macro_series_id     VARCHAR,               -- bridge -> macro_series.series_id
    match_rules         VARCHAR,               -- token rules to recognise it on the sources
    match_excludes      VARCHAR,
    active              BOOLEAN DEFAULT TRUE,
    updated_at          TIMESTAMP
);

CREATE TABLE IF NOT EXISTS calendar_events (
    event_id            VARCHAR PRIMARY KEY,   -- hash(indicator_key, release_utc)
    indicator_key       VARCHAR NOT NULL,
    country_iso3        VARCHAR NOT NULL,
    release_utc         TIMESTAMP NOT NULL,
    release_precision   VARCHAR,               -- 'minute' | 'day'
    reference_period    VARCHAR,               -- as the source writes it: 'Jul', 'Q2'
    reference_date      DATE,                  -- period end -> joins macro_panel.date
    -- Where reference_date came from: 'source' when a provider published the
    -- period, 'inferred' when it was derived from the release date and the
    -- indicator's usual lag.
    --
    -- The column exists because the two must never be indistinguishable.
    -- Nasdaq and Tradays carry two thirds of the observations and publish no
    -- period at all, so without inference the point-in-time bridge answers for
    -- one event in five; with it, a backtest would be joining on dates that
    -- nobody published. Both of those are acceptable -- silently mixing them
    -- is not.
    reference_date_origin VARCHAR,             -- 'source' | 'inferred'
    status              VARCHAR NOT NULL,      -- 'scheduled' | 'released'
    actual              VARCHAR,
    actual_num          DOUBLE,
    actual_source       VARCHAR,
    actual_provenance   VARCHAR,               -- 'official' beats 'aggregator'
    consensus           VARCHAR,
    consensus_num       DOUBLE,
    consensus_source    VARCHAR,               -- one source per series, never mixed
    consensus_disputed  BOOLEAN,               -- other sources give a different consensus
    consensus_low       DOUBLE,                -- range of expectations across sources: when
    consensus_high      DOUBLE,                -- they diverge the truth is the interval, not
    consensus_n         INTEGER,               -- one of the two picked by convention
    previous            VARCHAR,
    previous_num        DOUBLE,
    revised_from        VARCHAR,
    impact              VARCHAR,
    impact_source       VARCHAR,
    n_sources           INTEGER,
    values_agree        BOOLEAN,
    first_seen_at       TIMESTAMP,
    updated_at          TIMESTAMP
);

CREATE TABLE IF NOT EXISTS calendar_observations (
    event_id            VARCHAR NOT NULL,
    source              VARCHAR NOT NULL,
    provenance          VARCHAR NOT NULL,      -- 'official' | 'aggregator'
    vintage_date        DATE NOT NULL,         -- when the hub saw THIS version
    source_event_name   VARCHAR NOT NULL,      -- the exact name the source used
    release_utc         TIMESTAMP,
    -- 'minute' | 'day'. On the observation and not only on the event, because
    -- consolidation has to choose between sources: one collector may know the
    -- release to the minute while another publishes only a date, which arrives
    -- as midnight. Without this column the two are indistinguishable, taking
    -- the earliest timestamp records midnight, and v_macro_panel_asof then
    -- reports a value as public hours before it was.
    release_precision   VARCHAR,
    reference_period    VARCHAR,
    reference_date      DATE,                  -- period end, per source: it is the
                                               -- source that states which period
                                               -- the figure refers to
    actual              VARCHAR,
    consensus           VARCHAR,
    previous            VARCHAR,
    revised_from        VARCHAR,
    impact              VARCHAR,
    change_type         VARCHAR,               -- 'new' | 'revised' | 'unchanged'
    prior_actual        VARCHAR,
    run_id              VARCHAR,
    PRIMARY KEY (event_id, source, vintage_date)
);

CREATE INDEX IF NOT EXISTS idx_cal_events_release   ON calendar_events (release_utc);
CREATE INDEX IF NOT EXISTS idx_cal_events_indicator ON calendar_events (indicator_key, release_utc);
CREATE INDEX IF NOT EXISTS idx_cal_events_refdate   ON calendar_events (country_iso3, reference_date);
CREATE INDEX IF NOT EXISTS idx_cal_obs_source       ON calendar_observations (source, vintage_date);

-- ---------------------------------------------------------------------------
-- Which indicator a source means by a given name.
--
-- calendar_indicators.match_rules is a regex, and a regex generalises: it does
-- not decide about a name, it meets one and swallows it. 'hourly|earnings' was
-- never a judgement about BLS 'Real Earnings' -- an entirely different series,
-- published alongside the CPI because it is CPI-deflated earnings -- yet it
-- filed it as Average Hourly Earnings, T1 and all, and the report carried
-- 0.0% for July when the real print was 3.2%, out five days earlier. Twelve
-- more bindings of the same shape were found the same afternoon.
--
-- So the decision is recorded instead of being recomputed. One row per name a
-- source actually uses, with who decided it and when. An alias cannot
-- generalise: (source, country, name) is either in this table or it is not.
--
-- The key is the triple. Country is what makes it unambiguous: 'CPI y/y' on
-- Tradays means eleven different indicators, one per country, and over the
-- 411 bindings collected so far the triple leaves exactly zero ambiguity.
--
-- match_rules does not disappear, it steps down to proposing: when a name
-- arrives that is not in here, the regex suggests an indicator and the row
-- lands with status 'proposed', to be confirmed or rejected. Nothing enters
-- the calendar on a proposal alone.
--
-- The failure mode changes shape and that is the point: an unknown name now
-- yields no match instead of a wrong one. Silence is safer than a wrong
-- number, but it is still a failure, which is why unmapped names have to be
-- reported rather than dropped.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS calendar_indicator_aliases (
    source              VARCHAR NOT NULL,      -- 'tradays', 'nasdaq', ...
    country_iso3        VARCHAR NOT NULL,      -- normalised, never the source's own string
    source_name_norm    VARCHAR NOT NULL,      -- normalised name: the join key
    source_name_raw     VARCHAR,               -- as first seen, for a human reading this
    indicator_key       VARCHAR,               -- NULL = seen and deliberately not tracked
    status              VARCHAR NOT NULL DEFAULT 'confirmed',
                                               -- confirmed | proposed | rejected
    decided_by          VARCHAR,               -- who: a person, or the seeding run
    decided_at          TIMESTAMP,
    note                VARCHAR,               -- why, when the choice is not obvious
    PRIMARY KEY (source, country_iso3, source_name_norm)
);

-- No secondary index on this table, deliberately. On duckdb 1.4.x -- the last
-- line supporting Python 3.9 -- a secondary index on a column makes INSERT OR
-- REPLACE keep the OLD value of that column on the conflict path. An index on
-- `status` left a rejection reading 'confirmed'; one on `indicator_key` left
-- the rejected binding in place. Either way the decision never lands, which is
-- the exact silent drift this table exists to stop. The repo already paid for
-- this lesson once, with idx_msv_run / idx_mpv_run in the v3 -> v4 step.
-- The table is small and read whole by load_aliases(); it needs no index.

-- ---------------------------------------------------------------------------
-- Enrichment of a release: press commentary and technical detail.
--
-- A separate table for a reason of substance, not tidiness: this is content
-- GENERATED by a model from web pages, not a value published by an institute.
-- Keeping it in calendar_events next to 'actual' and 'consensus' would make
-- what was measured indistinguishable from what was written, which is exactly
-- the confusion the rest of the schema avoids.
--
-- generated_at is part of the key: a regeneration sits alongside rather than
-- overwriting, so what the report said on a given day stays reconstructible.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS calendar_event_notes (
    event_id            VARCHAR NOT NULL,
    generated_at        TIMESTAMP NOT NULL,
    model               VARCHAR,           -- who produced the enrichment
    reviewer            VARCHAR,           -- who checked its relevance
    review_attempts     INTEGER,           -- how many review rounds it took
    commentary_json     VARCHAR,           -- items: outlet, url, summary, evidence
    n_comments          INTEGER,
    drivers             VARCHAR,           -- what moved the figure
    components          VARCHAR,           -- how the indicator is built
    technical_source    VARCHAR,           -- the agency release, if cited
    not_found           VARCHAR,           -- what could not be found, and why
    run_id              VARCHAR,
    PRIMARY KEY (event_id, generated_at)
);

CREATE INDEX IF NOT EXISTS idx_cal_notes_event ON calendar_event_notes (event_id);

-- The surprise only means something if the consensus was captured BEFORE the
-- release: some sources overwrite it with the printed value. consensus_source
-- stays exposed so the reader knows which provider the expectation came from.
CREATE OR REPLACE VIEW v_calendar_surprise AS
SELECT  e.event_id, e.indicator_key, i.area, i.name AS indicator_name,
        i.criticality, e.release_utc, e.reference_date,
        e.actual_num, e.consensus_num,
        -- Null where the sources disagree on what was expected. The point
        -- surprise is the number this view exists to publish, and quoting one
        -- against a consensus that was never agreed is precisely the fabricated
        -- surprise consensus_disputed was added to prevent. The range is
        -- carried alongside so a caller can still say how wide the doubt was.
        CASE WHEN NOT e.consensus_disputed
             THEN e.actual_num - e.consensus_num END AS surprise,
        CASE WHEN NOT e.consensus_disputed
              AND e.consensus_num IS NOT NULL AND abs(e.consensus_num) > 1e-9
             THEN (e.actual_num - e.consensus_num) / abs(e.consensus_num)
        END AS surprise_rel,
        e.consensus_disputed, e.consensus_low, e.consensus_high, e.consensus_n,
        e.consensus_source, e.actual_provenance
FROM    calendar_events e
JOIN    calendar_indicators i USING (indicator_key)
WHERE   e.status = 'released'
  AND   e.actual_num IS NOT NULL AND e.consensus_num IS NOT NULL;

-- The "what to watch" for the Investment Committee: scheduled releases ordered
-- by criticality, with the reason the indicator matters in that country.
CREATE OR REPLACE VIEW v_calendar_upcoming AS
SELECT  e.release_utc, i.area, i.country_iso3, i.name, i.criticality, i.nature,
        e.reference_period, e.previous, e.consensus, i.rationale
FROM    calendar_events e
JOIN    calendar_indicators i USING (indicator_key)
WHERE   e.status = 'scheduled';

-- The point-in-time bridge: for every macro_panel value, when it became
-- public. Without it a backtest uses data not yet known at the time.
CREATE OR REPLACE VIEW v_macro_panel_asof AS
SELECT  m.date AS reference_date, m.country_iso3, m.indicator_id, m.value,
        e.release_utc AS known_from, i.indicator_key AS calendar_indicator_key
FROM    macro_panel m
LEFT JOIN calendar_indicators i
       ON i.macro_indicator_id = m.indicator_id AND i.country_iso3 = m.country_iso3
LEFT JOIN calendar_events e
       ON e.indicator_key = i.indicator_key AND e.reference_date = m.date
      AND e.status = 'released';
