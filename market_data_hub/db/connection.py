# -*- coding: utf-8 -*-
"""
connection.py — centralized access to the DuckDB database.

The DB path is configurable via settings.yaml or the MARKET_DATA_DB environment
variable. The schema is applied (idempotently) on first open.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import duckdb

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _default_db() -> str:
    """Last-resort DB path when neither db_path, MARKET_DATA_DB nor settings.yaml
    provide one. Windows keeps the historical D:\\market_data location; other
    platforms fall back to a portable path under the user's home."""
    if os.name == "nt":
        return r"D:\market_data\market_data.duckdb"
    return str(Path.home() / ".market_data" / "market_data.duckdb")


_DEFAULT_DB = _default_db()


def _resolve_db_path(db_path: Optional[str] = None) -> str:
    if db_path:
        return db_path
    env = os.environ.get("MARKET_DATA_DB")
    if env:
        return env
    # settings.yaml takes precedence over the hard-coded default
    try:
        from market_data_hub.config_loader import get_settings
        s = get_settings()
        if s.get("db_path"):
            return s["db_path"]
    except Exception:
        pass
    return _DEFAULT_DB


def apply_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Apply the SQL schema (idempotent)."""
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    con.execute(sql)


def get_conn(db_path: Optional[str] = None, *, read_only: bool = False
             ) -> duckdb.DuckDBPyConnection:
    """
    Open (creating if absent) the DuckDB database and ensure the schema.

    read_only=True for readers (reader.py, diagnose.py) so multiple processes
    can read in parallel without locking.
    """
    path = _resolve_db_path(db_path)
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    if read_only and not os.path.exists(path):
        # a reader on a nonexistent DB: create it once in write mode
        tmp = duckdb.connect(path)
        apply_schema(tmp)
        tmp.close()

    con = duckdb.connect(path, read_only=read_only)
    if not read_only:
        apply_schema(con)
    return con
