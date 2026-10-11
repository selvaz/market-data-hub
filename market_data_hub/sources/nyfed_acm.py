"""Official NY Fed ACM ten-year monthly chart CSV, in native percent units.

The small public CSV is monthly (last business day), not the daily XLS dataset.
Emit the existing macro_series shape, with source='nyfed_acm'.
"""
from __future__ import annotations

from io import StringIO

import pandas as pd

from market_data_hub.sources.fred import _http_get

_CSV_URL = "https://www.newyorkfed.org/medialibrary/media/research/data_indicators/acmPlot_data.csv"


def fetch_acm(start: str, end: str, *, meta: dict, timeout: int = 30,
              retries: int = 3, base_sleep: float = 1.0) -> pd.DataFrame:
    """Fetch/filter monthly ten-year term premia, preserving the provider dates."""
    response = _http_get(_CSV_URL, {}, timeout, retries, base_sleep)
    raw = pd.read_csv(StringIO(response.text))
    if not {"RunDates", "TERMYld"} <= set(raw.columns):
        raise ValueError("NY Fed ACM CSV requires RunDates and TERMYld columns")
    dates = pd.to_datetime(raw["RunDates"], format="%d-%b-%Y", errors="raise")
    values = pd.to_numeric(raw["TERMYld"], errors="raise")
    df = pd.DataFrame({"date": dates, "value": values})
    df = df[df["date"].between(pd.Timestamp(start), pd.Timestamp(end))]
    df = df.dropna(subset=["value"]).sort_values("date").reset_index(drop=True)
    df["series_id"] = meta["symbol"]
    df["series_name"] = meta["name"]
    df["unit"] = meta["unit"]
    df["frequency"] = meta["frequency"]
    df["country"] = meta["country"]
    df["source"] = "nyfed_acm"
    return df[["date", "series_id", "value", "series_name", "unit",
               "frequency", "source", "country"]]
