"""Read literal ForexFactory forecasts with verified, pre-release provenance.

These provider forecasts are never a surprise baseline. Reading an archive
does not download data, open a database, or change any files.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Union

from market_data_hub.econ_calendar.collect.forexfactory import paese_iso
from market_data_hub.macro_releases.consensus import _snapshot_bytes, manifest_hashes
from market_data_hub.macro_releases.read_times import timestamp

__all__ = ["ArchivedForecast", "read_archived_forecasts"]

_ISO3 = {
    "US": "USA", "GB": "GBR", "CA": "CAN", "JP": "JPN", "CN": "CHN",
    "AU": "AUS", "NZ": "NZL", "CH": "CHE", "EU": "EMU", "DE": "DEU",
    "FR": "FRA", "IT": "ITA", "ES": "ESP",
}


@dataclass(frozen=True)
class ArchivedForecast:
    """One exact source release and its latest qualifying literal forecast."""

    jurisdiction: str
    title: str
    release_at: datetime
    forecast_raw: str
    captured_at: datetime
    snapshot_path: str
    snapshot_sha256: str
    provider: str = "ff_forecast"


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def read_archived_forecasts(
    raw_dir: Union[str, Path], *, known_at: datetime,
    release_from: datetime, release_to: datetime,
) -> list[ArchivedForecast]:
    """Return latest verified captures in the inclusive UTC release window.

    All datetime arguments must be timezone-aware. Missing archives/manifests
    yield an empty list; unreadable, hash-mismatched or malformed snapshots
    and rows without an explicit release offset are ignored. Both ``written``
    and ``unchanged`` manifest entries prove captures. Filenames and mtimes
    never prove a capture time. Results sort by release, jurisdiction, title;
    equal capture times prefer the lexicographically first snapshot filename.
    """
    known_at = _utc(known_at, "known_at")
    release_from = _utc(release_from, "release_from")
    release_to = _utc(release_to, "release_to")
    if release_from > release_to:
        raise ValueError("release_from must be <= release_to")

    directory = Path(raw_dir)
    hashes = manifest_hashes(directory)
    latest = {}
    # Unlike load_snapshots(), keep each recorded capture of unchanged bytes.
    for filename, capture in sorted(hashes, key=lambda key: (key[1], key[0])):
        captured_at = datetime.fromisoformat(capture)
        if captured_at > known_at:
            continue
        path = directory / filename
        try:
            body, digest = _snapshot_bytes(path, captured_at=capture, hashes=hashes)
            events = json.loads(body)
        except (OSError, ValueError):
            continue
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict):
                continue
            title, forecast = event.get("title"), event.get("forecast")
            country = event.get("country")
            if (
                not isinstance(title, str)
                or not isinstance(forecast, str) or not forecast.strip()
                or not isinstance(country, str)
            ):
                continue
            try:
                release, precision = timestamp(event.get("date"))
            except (ValueError, OverflowError):
                continue
            if precision != "exact":
                continue
            release_at = datetime.fromisoformat(release)
            if not (release_from <= release_at <= release_to and captured_at < release_at):
                continue
            if country == "EUR" and title.startswith("Belgian "):
                jurisdiction = "BEL"
            elif country == "All":
                jurisdiction = "GLOBAL"
            else:
                jurisdiction = _ISO3.get(paese_iso(country, title))
            if jurisdiction is None:
                continue
            key = (jurisdiction, title, release_at)
            previous = latest.get(key)
            if previous is None or previous.captured_at < captured_at:
                latest[key] = ArchivedForecast(
                    jurisdiction=jurisdiction, title=title, release_at=release_at,
                    forecast_raw=forecast, captured_at=captured_at,
                    snapshot_path=str(path), snapshot_sha256=digest,
                )
    return sorted(latest.values(), key=lambda row: (row.release_at, row.jurisdiction, row.title))
