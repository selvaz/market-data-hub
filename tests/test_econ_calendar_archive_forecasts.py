"""Archive contract fixtures: five rows copied from the 2026-10-28 FF feed.

The sample retains source fields/literals; manifest hashes are generated for
these reduced bytes. Temporal/country negative cases mutate copies in tmp_path.
No test reads a production archive or uses its capture time for this sample.
"""

from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from market_data_hub.econ_calendar.archive_forecasts import (
    ArchivedForecast,
    read_archived_forecasts,
)

UTC = timezone.utc
CAPTURE = "2026-10-28T21:30:06.405923Z"
RELEASE = datetime(2026, 10, 29, 12, 30, tzinfo=UTC)


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@pytest.fixture
def feed():
    return json.loads((Path(__file__).parent / "fixtures" / "ff_archive_sample.json").read_text())


@pytest.fixture
def event(feed):
    return feed[3].copy()


def archive(directory, events, *, filename="2026-10-28.json", captured=CAPTURE,
            manifest=True, sha256=None, outcome="written", file=None):
    directory.mkdir(exist_ok=True)
    path = directory / filename
    body = json.dumps(events).encode()
    path.write_bytes(body)
    if manifest:
        record = dict(
            captured_at_utc=captured, local_date=captured[:10], outcome=outcome,
            file=file or str(path), sha256=sha256 or hashlib.sha256(body).hexdigest(),
            bytes=len(body), http_status=200, error=None,
            feed_week_start="2026-10-25", feed_week_end="2026-10-30",
        )
        with (directory / "manifest.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record) + "\n")
    return path


def read(directory, **kwargs):
    options = dict(known_at=instant("2026-10-29T12:00:00Z"),
                   release_from=instant("2026-10-25T00:00:00Z"),
                   release_to=instant("2026-10-31T23:59:59Z"))
    return read_archived_forecasts(directory, **dict(options, **kwargs))


def test_manifest_missing_file_ignored(tmp_path, event):
    archive(tmp_path, [event], manifest=False)
    assert read(tmp_path) == []
    (tmp_path / "manifest.jsonl").write_text("\n{}\nnot-json\n[]\n")
    assert read(tmp_path) == []


def test_sha_mismatch_ignored_without_replacing_valid_capture(tmp_path, event):
    original = archive(tmp_path, [event])
    archive(tmp_path, [dict(event, forecast="999K")], filename="2026-10-29.json",
            captured="2026-10-29T10:00:00Z", sha256="0" * 64)
    result, = read(tmp_path)
    assert result.forecast_raw == "198K"
    assert result.snapshot_path == str(original)
    original.write_bytes(b"[]")
    assert read(tmp_path) == []


@pytest.mark.parametrize("captured", ["2026-10-29T12:30:00Z", "2026-10-29T12:30:00.000001Z"])
def test_capture_at_or_after_release_excluded(tmp_path, event, captured):
    archive(tmp_path, [event])
    archive(tmp_path, [dict(event, forecast="999K")], filename="2026-10-29.json",
            captured=captured)
    result, = read(tmp_path, known_at=instant("2026-10-30T00:00:00Z"))
    assert result.forecast_raw == "198K"
    assert result.captured_at == instant(CAPTURE)


def test_capture_after_known_at_excluded_and_equality_included(tmp_path, event):
    archive(tmp_path, [event])
    cutoff = instant("2026-10-29T11:00:00.123456Z")
    archive(tmp_path, [dict(event, forecast="200K")], filename="2026-10-29.json",
            captured=cutoff.isoformat().replace("+00:00", "Z"))
    assert read(tmp_path, known_at=cutoff - timedelta(microseconds=1))[0].forecast_raw == "198K"
    result, = read(tmp_path, known_at=cutoff)
    assert result.forecast_raw == "200K"
    assert result.captured_at == cutoff


def test_latest_capture_chosen_independent_of_manifest_and_filename_order(tmp_path, event):
    latest = archive(tmp_path, [dict(event, forecast="200K")],
                     filename="2026-10-27.json", captured="2026-10-29T11:00:00Z")
    archive(tmp_path, [event], filename="2026-10-29.json")
    result, = read(tmp_path)
    assert result.forecast_raw == "200K"
    assert result.snapshot_path == str(latest)


def test_unchanged_manifest_entry_is_a_later_qualifying_capture(tmp_path, event):
    archive(tmp_path, [event])
    archive(tmp_path, [event], captured="2026-10-29T11:00:00Z", outcome="unchanged")
    assert read(tmp_path)[0].captured_at == instant("2026-10-29T11:00:00Z")
    assert read(tmp_path, known_at=instant(CAPTURE))[0].captured_at == instant(CAPTURE)


@pytest.mark.parametrize("forecast", ["", " \t\n", None, 0])
def test_blank_or_nonliteral_forecast_dropped(tmp_path, event, forecast):
    archive(tmp_path, [dict(event, forecast=forecast)])
    assert read(tmp_path) == []


def test_literal_values_titles_and_full_provenance_preserved(tmp_path, feed):
    path = archive(tmp_path, feed)
    results = read(tmp_path, known_at=instant("2026-10-25T00:00:00Z"))
    assert results == []
    # The first two source releases precede this capture and the statement is blank.
    claims, boj = read(str(tmp_path))
    assert claims == ArchivedForecast(
        jurisdiction="USA", title="Unemployment Claims", release_at=RELEASE,
        forecast_raw="198K", captured_at=instant(CAPTURE), snapshot_path=str(path),
        snapshot_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    assert boj.forecast_raw == "<1.25%"
    assert boj.release_at == instant("2026-10-30T02:30:00Z")
    assert claims.provider == boj.provider == "ff_forecast"
    assert {field.name for field in fields(claims)} == {
        "provider", "jurisdiction", "title", "release_at", "forecast_raw",
        "captured_at", "snapshot_path", "snapshot_sha256",
    }
    with pytest.raises(FrozenInstanceError):
        claims.forecast_raw = "999K"


@pytest.mark.parametrize("forecast", ["0", "0.3%", "<0.50%", "215K", " 215K "])
def test_forecast_is_not_normalized(tmp_path, event, forecast):
    archive(tmp_path, [dict(event, title=" Claims (Oct) ", forecast=forecast)])
    result, = read(tmp_path)
    assert result.forecast_raw == forecast
    assert result.title == " Claims (Oct) "


def test_empty_source_title_is_preserved_without_inference(tmp_path, event):
    archive(tmp_path, [dict(event, title="")])
    result, = read(tmp_path)
    assert result.title == ""


@pytest.mark.parametrize("country,title,jurisdiction", [
    ("USD", "German CPI", "USA"), ("GBP", "CPI", "GBR"), ("CAD", "CPI", "CAN"),
    ("JPY", "CPI", "JPN"), ("CNY", "CPI", "CHN"), ("AUD", "CPI", "AUS"),
    ("NZD", "CPI", "NZL"), ("CHF", "CPI", "CHE"), ("All", "Meetings", "GLOBAL"),
    ("EUR", "German ifo Business Climate", "DEU"), ("EUR", "French CPI", "FRA"),
    ("EUR", "Italian CPI", "ITA"), ("EUR", "Spanish CPI", "ESP"),
    ("EUR", "Belgian NBB Business Climate", "BEL"),
    ("EUR", "M3 Money Supply y/y", "EMU"), ("EUR", "CPI German", "EMU"),
])
def test_jurisdiction_rules(tmp_path, event, country, title, jurisdiction):
    archive(tmp_path, [dict(event, country=country, title=title)])
    result, = read(tmp_path)
    assert result.jurisdiction == jurisdiction
    assert result.title == title


@pytest.mark.parametrize("source,expected", [
    ("2026-10-29T23:30:00-04:00", "2026-10-30T03:30:00Z"),
    ("2026-10-30T00:30:00+09:00", "2026-10-29T15:30:00Z"),
    ("2026-11-01T01:30:00-04:00", "2026-11-01T05:30:00Z"),
    ("2026-11-01T01:30:00-05:00", "2026-11-01T06:30:00Z"),
])
def test_release_offset_conversion_to_utc(tmp_path, event, source, expected):
    archive(tmp_path, [dict(event, date=source)])
    result, = read(tmp_path, release_to=instant("2026-11-02T00:00:00Z"))
    assert result.release_at == instant(expected)
    assert result.release_at.tzinfo == result.captured_at.tzinfo == UTC


@pytest.mark.parametrize("date", ["2026-10-29", "2026-10-29T08:30:00", None, "bad"])
def test_release_without_explicit_offset_is_not_inferred(tmp_path, event, date):
    archive(tmp_path, [dict(event, date=date)])
    assert read(tmp_path) == []


def test_release_window_inclusive_and_compared_in_utc(tmp_path, event):
    archive(tmp_path, [event])
    eastern = timezone(timedelta(hours=-4))
    assert len(read(tmp_path, release_from=RELEASE.astimezone(eastern), release_to=RELEASE)) == 1
    assert read(tmp_path, release_from=RELEASE + timedelta(microseconds=1)) == []
    assert read(tmp_path, release_to=RELEASE - timedelta(microseconds=1)) == []


def test_release_identity_keeps_distinct_times_titles_and_countries(tmp_path, event):
    archive(tmp_path, [event, dict(event, date="2026-10-30T08:30:00-04:00"),
                       dict(event, title="Other Claims"), dict(event, country="CAD")])
    assert len(read(tmp_path)) == 4


@pytest.mark.parametrize("argument", ["known_at", "release_from", "release_to"])
def test_naive_query_timestamp_rejected(tmp_path, argument):
    with pytest.raises(ValueError, match="timezone-aware"):
        read(tmp_path, **{argument: datetime(2026, 10, 29)})


def test_reversed_window_rejected(tmp_path):
    with pytest.raises(ValueError, match="release_from"):
        read(tmp_path, release_from=RELEASE, release_to=RELEASE - timedelta(seconds=1))


def test_missing_directory_returns_empty_without_creating_it(tmp_path):
    missing = tmp_path / "missing"
    assert read(missing) == []
    assert not missing.exists()


@pytest.mark.parametrize("body", [b"not-json", b"{}", b"[null, 42, {}]", b"\xff"])
def test_malformed_snapshot_ignored(tmp_path, body):
    path = archive(tmp_path, [], sha256=hashlib.sha256(body).hexdigest())
    path.write_bytes(body)
    assert read(tmp_path) == []


def test_failed_missing_and_windows_manifest_paths(tmp_path, event):
    archive(tmp_path, [event], outcome="failed")
    assert read(tmp_path) == []
    path = archive(tmp_path, [event], file=r"C:\copied\archive\2026-10-28.json")
    assert read(tmp_path)[0].snapshot_path == str(path)
    path.unlink()
    assert read(tmp_path) == []


def test_reader_does_not_write_open_database_or_use_network(tmp_path, event, monkeypatch):
    import duckdb
    import requests
    import sqlite3

    archive(tmp_path, [event])
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()}

    def forbidden(*args, **kwargs):
        pytest.fail("archive reader attempted a write, database connection or network call")

    monkeypatch.setattr(duckdb, "connect", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    assert len(read(tmp_path)) == 1
    assert before == {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()}
