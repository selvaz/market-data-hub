"""Collect versioned FRED series descriptions with a persistent freshness cache."""

from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

from market_data_hub.config_loader import get_fred_series
from market_data_hub.db.connection import get_conn
from market_data_hub.db.upsert import upsert

FIELDS = (
    "title",
    "units",
    "units_short",
    "seasonal_adjustment",
    "seasonal_adjustment_short",
    "frequency",
    "frequency_short",
    "last_updated",
    "observation_start",
    "observation_end",
)
URL = "https://api.stlouisfed.org/fred/series"


def refresh(con, api_key, *, max_age_days=30, interval=1.0, session=None):
    """Fetch each stale configured series once; failures do not erase cached versions.

    Returns failed IDs. An interval of at least one second limits this process to
    60 requests/minute, below FRED's 120/minute limit. Coordinate other clients.
    """
    if not api_key:
        raise ValueError("FRED_API_KEY is required")
    if max_age_days < 0 or interval < 1:
        raise ValueError(
            "max_age_days must be nonnegative and interval at least one second"
        )
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        days=max_age_days
    )
    cached = dict(
        con.execute(
            "SELECT series_id, CAST(max(retrieved_at) AS VARCHAR) "
            "FROM fred_series_metadata GROUP BY series_id"
        ).fetchall()
    )
    client = session or requests.Session()
    failures = []
    requested = False
    for series_id in dict.fromkeys(entry["symbol"] for entry in get_fred_series()):
        timestamp = cached.get(series_id)
        if timestamp:
            parsed = datetime.fromisoformat(timestamp)
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        if timestamp and parsed >= cutoff:
            continue
        if requested:
            time.sleep(interval)
        requested = True
        try:
            response = client.get(
                URL,
                params={
                    "series_id": series_id,
                    "api_key": api_key,
                    "file_type": "json",
                },
                timeout=30,
            )
            response.raise_for_status()
            items = response.json()["seriess"]
            if len(items) != 1 or items[0].get("id") != series_id:
                raise ValueError("unexpected FRED series response")
            item = items[0]
            if any(
                not isinstance(item.get(field), str) or not item[field]
                for field in FIELDS
            ):
                raise ValueError("incomplete FRED metadata")
            row = {field: item[field] for field in FIELDS}
            row.update(
                series_id=series_id,
                retrieved_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            upsert(con, "fred_series_metadata", pd.DataFrame([row]))
        except (requests.RequestException, KeyError, TypeError, ValueError):
            # Do not print request exceptions: URLs can contain the API key.
            failures.append(series_id)
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", required=True, help="Hub database to update")
    parser.add_argument("--max-age-days", type=float, default=30)
    args = parser.parse_args()
    key = os.environ.get("FRED_API_KEY")
    if not key:
        parser.error("FRED_API_KEY is required")
    con = get_conn(args.db_path)
    try:
        failures = refresh(con, key, max_age_days=args.max_age_days)
    finally:
        con.close()
    if failures:
        print("Failed series: " + ", ".join(failures))
        return 1
    print("FRED metadata refresh complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
