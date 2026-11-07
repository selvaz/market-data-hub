"""Official NY Fed ACM ten-year monthly CSV and daily workbook, in percent."""
from __future__ import annotations

from io import BytesIO, StringIO

import pandas as pd

from market_data_hub.sources.fred import _http_get

_XLS_URL = "https://www.newyorkfed.org/medialibrary/media/research/data_indicators/ACMTermPremium.xls"
_CSV_URL = "https://www.newyorkfed.org/medialibrary/media/research/data_indicators/acmPlot_data.csv"


def fetch_acm(start: str, end: str, *, meta: dict, timeout: int = 30,
              retries: int = 3, base_sleep: float = 1.0) -> pd.DataFrame:
    """Fetch/filter ten-year term premia, preserving provider dates and units."""
    if meta["symbol"] == "NYFED_ACM_TP10_DAILY":
        response = _http_get(_XLS_URL, {}, timeout, retries, base_sleep)
        raw = pd.read_excel(BytesIO(response.content), sheet_name="ACM Daily")
        date_col, value_col = "DATE", "ACMTP10"
    else:
        response = _http_get(_CSV_URL, {}, timeout, retries, base_sleep)
        raw = pd.read_csv(StringIO(response.text))
        date_col, value_col = "RunDates", "TERMYld"
    if not {date_col, value_col} <= set(raw.columns):
        raise ValueError(f"NY Fed ACM data requires {date_col} and {value_col} columns")
    dates = pd.to_datetime(raw[date_col], format="%d-%b-%Y", errors="raise")
    values = pd.to_numeric(raw[value_col], errors="raise")
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
