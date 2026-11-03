"""EIA API v2 weekly observations. Never expose request URLs or exceptions."""

from __future__ import annotations

import http.client
import json
import os
from urllib.parse import urlencode

import pandas as pd

_UNITS = {
    "Thousand Barrels": {"thousand barrels", "mbbl"},
    "Bcf": {"billion cubic feet", "bcf"},
}


class EIARequestError(RuntimeError):
    """Sanitized request failure, containing only status and series identity."""


def api_key() -> str:
    """Read the credential only from the process environment."""
    try:
        key = os.environ["EIA_API_KEY"]
    except KeyError:
        raise ValueError("EIA_API_KEY is required") from None
    if not key.strip():
        raise ValueError("EIA_API_KEY is required")
    return key


def fetch(start: str, end: str, *, meta: dict, timeout: int = 30) -> pd.DataFrame:
    """Fetch all pages; reject unexpected series and units instead of guessing."""
    key = api_key()
    sid = meta["symbol"]
    rows = []
    offset = 0
    while True:
        params = {
            "api_key": key,
            "frequency": "weekly",
            "data[0]": "value",
            "facets[series][]": sid,
            "start": start,
            "end": end,
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
            "offset": offset,
            "length": 5000,
        }
        conn = http.client.HTTPSConnection("api.eia.gov", timeout=timeout)
        conn.set_debuglevel(0)
        status = None
        try:
            conn.request("GET", f"/v2/{meta['route']}/data/?{urlencode(params)}")
            response = conn.getresponse()
            status = response.status
            if status != 200:
                raise RuntimeError
            payload = json.loads(response.read())
        except Exception:
            # Do not propagate an HTTP/client exception: it may contain the URL.
            raise EIARequestError(
                f"HTTP {status or 'unavailable'}; series {sid}"
            ) from None
        finally:
            conn.close()
        try:
            page = payload["response"]["data"]
            total = int(payload["response"]["total"])
            if not isinstance(page, list) or (not page and offset < total):
                raise ValueError
            for item in page:
                if (
                    item["series"] != sid
                    or str(item["units"]).strip().lower() not in _UNITS[meta["unit"]]
                ):
                    raise ValueError
                if item["value"] is not None:
                    rows.append((item["period"], float(item["value"])))
        except (KeyError, TypeError, ValueError, OverflowError):
            raise ValueError(f"EIA response invalid for series {sid}") from None
        offset += len(page)
        if offset >= total:
            break
    df = pd.DataFrame(rows, columns=["date", "value"])
    try:
        df["date"] = pd.to_datetime(df["date"], format="%Y-%m-%d", errors="raise")
    except (ValueError, TypeError):
        raise ValueError(f"EIA response invalid for series {sid}") from None
    df = df.sort_values("date").reset_index(drop=True)
    df["series_id"] = sid
    df["series_name"] = meta["name"]
    df["unit"] = meta["unit"]
    df["frequency"] = meta["frequency"]
    df["source"] = "eia"
    df["country"] = meta["country"]
    return df[
        [
            "date",
            "series_id",
            "value",
            "series_name",
            "unit",
            "frequency",
            "source",
            "country",
        ]
    ]
