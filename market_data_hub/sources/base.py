# -*- coding: utf-8 -*-
"""
base.py — contratto comune dei moduli sorgente.

Ogni sorgente espone funzioni che ritornano DataFrame nel formato canonico
della tabella di destinazione, piu' una SourceResult per il logging.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


@dataclass
class SourceResult:
    """Esito standardizzato del download di un singolo simbolo/serie."""
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
