# -*- coding: utf-8 -*-
"""
connection.py — accesso centralizzato al database DuckDB.

Il path del DB e' configurabile via settings.yaml o variabile d'ambiente
MARKET_DATA_DB. Lo schema viene applicato (idempotente) alla prima apertura.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import duckdb

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"
_DEFAULT_DB = r"D:\market_data\market_data.duckdb"


def _resolve_db_path(db_path: Optional[str] = None) -> str:
    if db_path:
        return db_path
    env = os.environ.get("MARKET_DATA_DB")
    if env:
        return env
    # settings.yaml ha la precedenza sul default hard-coded
    try:
        from market_data_hub.config_loader import get_settings
        s = get_settings()
        if s.get("db_path"):
            return s["db_path"]
    except Exception:
        pass
    return _DEFAULT_DB


def apply_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Applica lo schema SQL (idempotente)."""
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    con.execute(sql)


def get_conn(db_path: Optional[str] = None, *, read_only: bool = False
             ) -> duckdb.DuckDBPyConnection:
    """
    Apre (creando se assente) il database DuckDB e garantisce lo schema.

    read_only=True per i lettori (reader.py, diagnose.py) cosi' piu' processi
    possono leggere in parallelo senza lock.
    """
    path = _resolve_db_path(db_path)
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    if read_only and not os.path.exists(path):
        # un reader su DB inesistente: crealo una volta in scrittura
        tmp = duckdb.connect(path)
        apply_schema(tmp)
        tmp.close()

    con = duckdb.connect(path, read_only=read_only)
    if not read_only:
        apply_schema(con)
    return con
