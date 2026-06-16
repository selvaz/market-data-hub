# -*- coding: utf-8 -*-
"""
base.py — common contract for the source modules.

Each source exposes functions that return DataFrames in the canonical format
of the destination table, plus a SourceResult for logging.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


@dataclass
class SourceResult:
    """Standardized outcome of downloading a single symbol/series."""
    symbol: str
    source: str
    status: str = "ok"            # 'ok' | 'error' | 'empty' | 'skipped'
    df: Optional[pd.DataFrame] = None
    rows_added: int = 0
    rows_updated: int = 0
    error: Optional[str] = None
    duration_sec: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == "ok"
