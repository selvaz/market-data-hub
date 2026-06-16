# -*- coding: utf-8 -*-
"""Shared pytest fixtures and import-path setup."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Point the package's DB resolution at a throwaway DuckDB file."""
    db = tmp_path / "test.duckdb"
    monkeypatch.setenv("MARKET_DATA_DB", str(db))
    return str(db)
