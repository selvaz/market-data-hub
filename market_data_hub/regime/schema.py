# -*- coding: utf-8 -*-
"""
schema.py — additive DDL for the HMM regime-monitor add-on.

Kept separate from market_data_hub/db/schema.sql and its SCHEMA_VERSION
ladder: this add-on is optional (requires ``lazyhmm``), so it must not force
a core schema migration on installations that don't use it. ensure_regime_schema()
is only called by the regime entrypoint (run_regime_daily.py), never by
db.connection.get_conn().
"""
from __future__ import annotations

import duckdb

_DDL = """
CREATE TABLE IF NOT EXISTS hmm_regime_estimates (
    symbol           VARCHAR NOT NULL,
    trading_date     DATE    NOT NULL,
    estimation_date  DATE    NOT NULL,
    n_states         INTEGER,
    state            INTEGER,
    is_high_vol      BOOLEAN,
    prob_high_vol    DOUBLE,
    state_probs_json VARCHAR,
    PRIMARY KEY (symbol, trading_date, estimation_date)
);
CREATE INDEX IF NOT EXISTS idx_hmm_regime_symbol_date
    ON hmm_regime_estimates (symbol, trading_date);

CREATE TABLE IF NOT EXISTS hmm_model_runs (
    symbol          VARCHAR NOT NULL,
    estimation_date DATE    NOT NULL,
    n_states        INTEGER,
    criterion       VARCHAR,
    bic             DOUBLE,
    loglik          DOUBLE,
    data_start      DATE,
    data_end        DATE,
    n_obs           INTEGER,
    transmat_json   VARCHAR,
    means_json      VARCHAR,
    covars_json     VARCHAR,
    labels_json     VARCHAR,
    fit_seconds     DOUBLE,
    status          VARCHAR,
    error_msg       VARCHAR,
    created_at      TIMESTAMP,
    PRIMARY KEY (symbol, estimation_date)
);
"""


def ensure_regime_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Idempotently create the regime-monitor tables (CREATE TABLE IF NOT EXISTS)."""
    con.execute(_DDL)
