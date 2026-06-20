# -*- coding: utf-8 -*-
"""Series contracts — the minimum shape a price/return series must have.

These types pin down *what columns a series exposes* and *what a return is*, so
the warehouse, LazyFin and LazyHMM stop disagreeing on the shape of a DataFrame.
Series values are ``float`` by policy (Decimal is only for :class:`Money`).

The DataFrame validators import pandas lazily so that ``lazydatacore`` stays a
leaf: importing the contract never pulls pandas unless you actually validate a
frame.
"""
from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Optional

from pydantic import AwareDatetime, BaseModel, ConfigDict

if TYPE_CHECKING:  # avoid importing pandas at module import time
    import pandas as pd

# The canonical OHLCV column set, in canonical order. Matches prices_daily.
OHLCV_COLUMNS = ("open", "high", "low", "close", "adj_close", "volume")


class Frequency(str, Enum):
    """Observation frequency. Single-letter codes match the warehouse."""

    D = "D"
    W = "W"
    M = "M"
    Q = "Q"
    A = "A"


class ReturnKind(str, Enum):
    """How a return was computed. Decimal fraction, never percent."""

    SIMPLE = "simple"  # p_t / p_{t-1} - 1
    LOG = "log"        # ln(p_t / p_{t-1})


class PriceBar(BaseModel):
    """A single OHLCV bar — the contract for one row of a price series.

    ``ts`` is timezone-aware UTC; prices are adjusted-or-raw ``float``; volume is
    ``float`` to cover both equity (integer) and crypto (fractional) volumes.
    """

    model_config = ConfigDict(extra="forbid")

    ts: AwareDatetime  # timezone-aware UTC bar open time
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    adj_close: Optional[float] = None
    volume: Optional[float] = None


def validate_wide_prices(df: "pd.DataFrame") -> "pd.DataFrame":
    """Validate a *wide* price frame: a DatetimeIndex, one column per symbol.

    This is the shape ``reader.read_prices(..., wide=True)`` returns. Returns the
    frame unchanged on success; raises ``ValueError`` otherwise.
    """
    import pandas as pd

    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("wide price frame must have a DatetimeIndex")
    if not df.index.is_monotonic_increasing:
        raise ValueError("wide price frame index must be sorted ascending")
    return df


def validate_long_prices(df: "pd.DataFrame") -> "pd.DataFrame":
    """Validate a *long* price frame: must carry ``date`` and ``symbol`` columns.

    This is the shape ``reader.read_prices(..., wide=False)`` returns. Returns the
    frame unchanged on success; raises ``ValueError`` otherwise.
    """
    required = {"date", "symbol"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"long price frame missing required columns: {sorted(missing)}"
        )
    return df
