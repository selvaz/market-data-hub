"""Offline archive-to-card contracts, conservative skips and daily isolation."""

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest
import requests

from market_data_hub.macro_releases import open_reader, open_writer
from market_data_hub.macro_releases import consensus, migrations, run_daily
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.catalogue import INDICATORS
from market_data_hub.macro_releases.models import (
    ConsensusReading,
    Reading,
    ReleaseEvent,
    ScheduleVersion,
    SourceSeries,
)
from market_data_hub.macro_releases.resolve import Snapshot
from test_macro_read_api import observation


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("consensus ingestion must never use the network")

    monkeypatch.setattr(requests.Session, "request", forbidden)


@pytest.fixture
def store(tmp_path):
    with open_writer(tmp_path / "macro.sqlite") as store:
        for indicator in INDICATORS.values():
            store.put(indicator)
        persist(
            store,
            [
                observation(indicator=indicator)
                for indicator in INDICATORS
                if indicator.startswith("us_cpi_")
            ],
        )
        yield store


def event(**changes):
    return dict(
        title="CPI y/y",
        country="USD",
        date="2026-10-14T08:30:00-04:00",
        forecast="3.6%",
        previous="3.4%",
        impact="High",
        **changes,
    )


def archive(
    directory,
    events,
    *,
    captured="2026-10-13T21:30:05.123456Z",
    filename="2026-10-13.json",
    manifest=True,
    digest=None,
):
    directory.mkdir(exist_ok=True)
    path = directory / filename
    body = json.dumps(events).encode()
    path.write_bytes(body)
    if manifest:
        with (directory / "manifest.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    dict(
                        outcome="written",
                        file=str(path),
                        captured_at_utc=captured,
                        sha256=digest or hashlib.sha256(body).hexdigest(),
                    )
                )
                + "\n"
            )
    return path


def rows(store):
    return [
        dict(r)
        for r in store.conn.execute("SELECT * FROM consensus_readings ORDER BY id")
    ]


def test_manifest_capture_pre_post_release_separate_providers_and_cards(
    store, tmp_path
):
    persist(store, [observation()])
    raw = tmp_path / "raw"
    archive(raw, [event()])
    first = consensus.ingest(store, raw)
    assert first["readings_added"] == first["events_mapped"] == 1
    pre = rows(store)[0]
    assert pre["provider"] == "ff_forecast" and pre["survey_identity"] is None
    assert pre["captured_at"] == "2026-10-13T21:30:05.123456+00:00"
    assert pre["release_cutoff_at"] == "2026-10-14T12:30:00.000000+00:00"
    assert pre["cutoff_basis"] == "actual"
    assert (pre["pre_release_eligible"], pre["post_release"]) == (1, 0)
    archive(raw, [event()], filename="2026-10-14.json", captured="2026-10-14T12:30:00Z")
    assert consensus.ingest(store, raw)["readings_added"] == 1
    post = rows(store)[1]
    assert (post["pre_release_eligible"], post["post_release"]) == (0, 1)
    other = {k: pre[k] for k in ConsensusReading.model_fields}
    store.put(ConsensusReading(**dict(other, provider="survey_fixture", value=3.5)))
    card = store.release_cards(
        "2026-10-12", "2026-10-17", indicators=["us_cpi_headline_yy"]
    )["cards"][0]
    assert card["resolution"]["value"] == 3.7
    values = {r["provider"]: r for r in card["consensus"]}
    assert set(values) == {"ff_forecast", "survey_fixture"}
    assert values["ff_forecast"]["id"] == pre["id"]
    assert values["ff_forecast"]["kind"] == "ff_forecast"
    assert values["ff_forecast"]["surprise"] is None
    assert values["ff_forecast"]["surprise_eligible"] is False
    assert values["survey_fixture"]["surprise"] == 0.2
    assert [r["provider"] for r in card["surprise"]] == ["survey_fixture"]
    assert len(rows(store)) == 3
    # UNIQUE keys still deduplicate when the diagnostic ledger is bypassed.
    assert consensus.ingest(store, raw, reprocess=True)["readings_added"] == 0


@pytest.mark.parametrize(
    "provider", ["ff_forecast", "FF", "Forex Factory", "ForexFactory"]
)
def test_ff_only_card_has_no_surprise(store, tmp_path, provider):
    raw = tmp_path / "raw"
    archive(raw, [event()])
    consensus.ingest(store, raw)
    if provider != "ff_forecast":
        original = rows(store)[0]
        fields = {key: original[key] for key in ConsensusReading.model_fields}
        store.put(ConsensusReading(**dict(fields, provider=provider)))
    card = store.release_cards(
        "2026-10-12", "2026-10-17", indicators=["us_cpi_headline_yy"]
    )["cards"][0]
    assert card["surprise"] == []
    assert all(
        r["surprise"] is None and not r["surprise_eligible"] for r in card["consensus"]
    )


def test_post_release_only_has_no_consensus_or_surprise(store, tmp_path):
    persist(store, [observation()])
    archive(tmp_path / "raw", [event()], captured="2026-10-14T12:31:00Z")
    consensus.ingest(store, tmp_path / "raw")
    card = store.release_cards(
        "2026-10-12", "2026-10-17", indicators=["us_cpi_headline_yy"]
    )["cards"][0]
    assert rows(store)[0]["post_release"] == 1
    assert card["consensus"] == []
    assert card["surprise"] is None


def test_processed_snapshot_bypasses_parsing_and_new_capture_ingests(
    store, tmp_path, monkeypatch
):
    raw = tmp_path / "raw"
    archive(raw, [event()])
    consensus.ingest(store, raw)
    original = consensus.prepare
    monkeypatch.setattr(
        consensus, "prepare", lambda *a: pytest.fail("old snapshot parsed again")
    )
    result = consensus.ingest(store, raw)
    assert result["snapshots_processed"] == result["readings_added"] == 0
    assert result["snapshots_unchanged"] == 1
    monkeypatch.setattr(consensus, "prepare", original)
    archive(raw, [event()], filename="2026-10-14.json", captured="2026-10-14T11:30:00Z")
    result = consensus.ingest(store, raw)
    assert (
        result["snapshots_processed"]
        == result["snapshots_unchanged"]
        == result["readings_added"]
        == 1
    )


@pytest.mark.parametrize(
    "changes,reason,mapped",
    [
        ({"title": "CPI-ish y/y (2026-09)"}, "unmapped", 0),
        ({"date": "2026-10-15T08:30:00-04:00"}, "release_event_not_found", 1),
        ({"title": "CPI y/y (Sep)"}, "unmapped", 0),
        ({"country": "GBP"}, "release_event_not_found", 1),
        ({"country": "All"}, "unmapped", 0),
        ({"title": "German CPI y/y (2026-09)", "country": "EUR"}, "unmapped", 0),
        ({"title": "Final CPI y/y (2026-09)"}, "unmapped", 0),
        ({"forecast": ""}, "forecast_missing", 1),
        ({"forecast": "around 3.6%"}, "forecast_format_or_unit_unknown", 1),
        ({"forecast": "3.6"}, "forecast_format_or_unit_unknown", 1),
        ({"date": None}, "release_event_not_found", 1),
        ({"estimate_stage": "final"}, "stage_conflict", 1),
    ],
)
def test_no_guesses_and_diagnostic_counts(store, tmp_path, changes, reason, mapped):
    item = dict(event(), **changes)
    archive(tmp_path / "raw", [item])
    result = consensus.ingest(store, tmp_path / "raw")
    assert result["events_mapped"] == mapped
    assert result["events_skipped"] == 1 and result["readings_added"] == 0
    assert result["skip_reasons"] == {reason: 1}
    assert result["skipped_titles"][0]["title"] == item["title"]


def test_mtime_is_not_capture_time_and_later_manifest_evidence_can_retry(
    store, tmp_path
):
    raw = tmp_path / "raw"
    archive(raw, [event()], manifest=False)
    first = consensus.ingest(store, raw)
    assert first["skip_reasons"] == {"capture_time_not_in_manifest": 1}
    assert first["snapshots"][0]["captured_at"] is None and rows(store) == []
    archive(raw, [event()])
    assert consensus.ingest(store, raw)["readings_added"] == 1


@pytest.mark.parametrize(
    "captured,flags",
    [
        ("2026-10-13T22:59:59Z", (1, 0)),
        ("2026-10-13T23:00:00Z", (0, 0)),
        ("2026-10-15T04:00:00Z", (0, 0)),
    ],
)
def test_date_only_issuer_local_lower_bound(store, tmp_path, captured, flags):
    # A date-only official clock and no actual issuer publication for this
    # indicator retain an issuer-local midnight bound.
    item = observation(
        "uk_gdp_mm",
        "uk_gdp",
        period="2026-08",
        source="ONS",
        edition="2026-10-14",
        published="2026-10-14T00:00:00Z",
    )
    persist(store, [replace(item, precision="date_only")])
    store.put(
        ScheduleVersion(
            release_event_id="ONS:uk_gdp:regular:2026-10-14",
            expected_at="2026-10-13T23:00:00Z",
            issuer_timezone="Europe/London",
            precision="date_only",
            source_id="ONS",
            recorded_at="2026-10-01T00:00:00Z",
        )
    )
    archive(
        tmp_path / "raw",
        [dict(event(), title="GDP m/m", country="GBP", date="2026-10-14")],
        captured=captured,
    )
    consensus.ingest(store, tmp_path / "raw")
    reading = rows(store)[0]
    assert reading["release_cutoff_at"] == "2026-10-13T23:00:00.000000+00:00"
    assert reading["cutoff_precision"] == "date_only"
    assert (reading["pre_release_eligible"], reading["post_release"]) == flags


def test_first_actual_clock_wins_over_schedule_and_ff_time(store, tmp_path):
    persist(store, [observation()])
    release = "BLS:us_cpi:regular:2026-10-14"
    for recorded, expected in [
        ("2026-10-01T00:00:00Z", "2026-10-14T13:00:00Z"),
        ("2026-10-14T00:00:00Z", "2026-10-14T14:00:00Z"),
    ]:
        store.put(
            ScheduleVersion(
                release_event_id=release,
                expected_at=expected,
                issuer_timezone="America/New_York",
                precision="exact",
                source_id="BLS",
                recorded_at=recorded,
            )
        )
    archive(tmp_path / "raw", [event()])
    consensus.ingest(store, tmp_path / "raw")
    assert rows(store)[0]["release_cutoff_at"] == "2026-10-14T12:30:00.000000+00:00"


def test_explicit_ff_period_cannot_replace_missing_release_day(store, tmp_path):
    persist(store, [observation()])
    archive(tmp_path / "raw", [dict(event(), date=None)])
    result = consensus.ingest(store, tmp_path / "raw")
    assert result["readings_added"] == 0
    assert result["skip_reasons"] == {"release_event_not_found": 1}


def test_ff_clock_uses_eastern_and_converts_to_issuer_local_day():
    from zoneinfo import ZoneInfo

    for raw in ("2026-10-14T23:30:00-04:00", "2026-10-14T23:30:00"):
        assert (
            consensus.event_clock({"date": raw}, ZoneInfo("Europe/London"))
            == "2026-10-15"
        )


def test_unique_issuer_event_supplies_period_and_stage_without_ff_period(
    store, tmp_path
):
    archive(tmp_path / "raw", [dict(event(), period="1999-01")])
    assert consensus.ingest(store, tmp_path / "raw")["readings_added"] == 1
    row = rows(store)[0]
    assert row["reference_period"] == "2026-09"
    assert row["estimate_stage"] == "regular"
    assert row["release_event_id"] == "BLS:us_cpi:regular:2026-10-14"


def test_two_same_day_issuer_events_are_ambiguous(store, tmp_path):
    persist(
        store,
        [
            observation(
                edition="second-edition-same-day", published="2026-10-14T14:00:00Z"
            )
        ],
    )
    archive(tmp_path / "raw", [event()])
    result = consensus.ingest(store, tmp_path / "raw")
    assert result["readings_added"] == 0
    assert result["skip_reasons"] == {"release_event_ambiguous": 1}


def test_final_title_on_flash_issuer_event_is_stage_conflict(store, tmp_path):
    persist(
        store,
        [observation("ea21_hicp_yy", "ea_hicp", source="Eurostat", stage="flash")],
    )
    archive(tmp_path / "raw", [dict(event(), title="Final CPI y/y", country="EUR")])
    result = consensus.ingest(store, tmp_path / "raw")
    assert result["readings_added"] == 0
    assert result["skip_reasons"] == {"stage_conflict": 1}


def pending_release(store, edition="2026-10-22", *, membership=True):
    release_id = "BLS:us_cpi:regular:" + edition
    store.put(
        ReleaseEvent(
            id=release_id,
            issuer_id="BLS",
            family="us_cpi",
            estimate_stage="regular",
            edition=edition,
        )
    )
    store.put(
        ScheduleVersion(
            release_event_id=release_id,
            expected_at=edition + "T12:30:00Z",
            issuer_timezone="America/New_York",
            precision="exact",
            source_id="BLS",
            recorded_at="2026-10-18T00:00:00Z",
        )
    )
    if membership:
        store.put(
            SourceSeries(
                id="planned:cpi",
                indicator_id="us_cpi_headline_yy",
                source_id="BLS",
                issuer_id="BLS",
                distributor_id="BLS",
                source_key="planned:cpi",
                evidence_role="issuer",
            )
        )
        store.put(
            Reading(
                indicator_id="us_cpi_headline_yy",
                release_event_id=release_id,
                source_id="BLS",
                series_key="planned:cpi",
                period_key="2026-09",
                reference_period="2026-09",
                edition=edition,
                estimate_stage="regular",
                reading_kind="unavailable",
                issuer_id="BLS",
                distributor_id="BLS",
                evidence_origin_id=release_id,
                first_seen_at="2026-10-18T00:00:00Z",
                retrieved_at="2026-10-18T00:00:00Z",
                content_hash=release_id,
                parser_version="fixture",
                extraction_method="json",
                verification_status="unverified",
                raw_payload_json="{}",
                issuer_timezone="America/New_York",
            )
        )
    return release_id


def test_issuer_schedule_supplies_period_and_forecast_only_card(store, tmp_path):
    release_id = pending_release(store)
    raw = tmp_path / "raw"
    archive(
        raw,
        [dict(event(), date="2026-10-22T08:30:00")],
        filename="2026-10-21.json",
        captured="2026-10-21T21:30:00Z",
    )
    assert consensus.ingest(store, raw)["readings_added"] == 1
    reading = rows(store)[0]
    assert reading["release_event_id"] == release_id
    assert reading["reference_period"] == "2026-09"
    assert (
        reading["cutoff_basis"] == "scheduled" and reading["pre_release_eligible"] == 1
    )
    card = store.release_cards(
        "2026-10-19",
        "2026-10-23",
        indicators=["us_cpi_headline_yy"],
        as_known_at="2026-10-21T22:00:00Z",
    )["cards"][0]
    assert (
        card["status"] == "scheduled"
        and card["resolution"] is card["actual"] is card["surprise"] is None
    )
    assert card["consensus"][0]["kind"] == "ff_forecast"
    assert card["consensus"][0]["normalized_value"] == 3.6
    assert card["consensus"][0]["surprise"] is None
    coverage = store.coverage(
        "2026-10-19", "2026-10-23", as_known_at="2026-10-21T22:00:00Z"
    )
    assert coverage["families"]["us_cpi"]["captured"] == 0


def test_schedule_without_indicator_period_membership_cannot_link(store, tmp_path):
    pending_release(store, membership=False)
    archive(tmp_path / "raw", [dict(event(), date="2026-10-22T08:30:00-04:00")])
    assert consensus.ingest(store, tmp_path / "raw")["skip_reasons"] == {
        "release_event_not_found": 1
    }


def test_pending_card_cannot_revive_post_release_forecast_with_later_schedule(
    store, tmp_path
):
    release_id = pending_release(store)
    raw_row = dict(
        store.conn.execute(
            "SELECT * FROM readings WHERE release_event_id=?", (release_id,)
        ).fetchone()
    )
    reading = {key: raw_row[key] for key in Reading.model_fields}
    reading.update(
        published_at="2026-10-22T10:00:00Z",
        publication_precision="exact",
        content_hash="issuer-published-without-number",
    )
    store.put(Reading(**reading))
    archive(
        tmp_path / "raw",
        [dict(event(), date="2026-10-22T08:30:00-04:00")],
        captured="2026-10-22T11:00:00Z",
    )
    assert consensus.ingest(store, tmp_path / "raw")["readings_added"] == 1
    assert rows(store)[0]["post_release"] == 1
    assert (
        store.release_cards(
            "2026-10-19",
            "2026-10-23",
            indicators=["us_cpi_headline_yy"],
            as_known_at="2026-10-22T12:00:00Z",
        )["cards"]
        == []
    )


def test_new_issuer_metadata_revisits_not_found_snapshot_automatically(store, tmp_path):
    raw = tmp_path / "raw"
    archive(raw, [dict(event(), date="2026-10-22T08:30:00-04:00")])
    assert consensus.ingest(store, raw)["skip_reasons"] == {
        "release_event_not_found": 1
    }
    pending_release(store)
    assert consensus.ingest(store, raw)["readings_added"] == 1
    assert consensus.ingest(store, raw)["snapshots_unchanged"] == 1


def test_new_catalogue_alias_revisits_unmapped_snapshot_automatically(store, tmp_path):
    raw = tmp_path / "raw"
    archive(raw, [dict(event(), title="Fixture CPI annual rate")])
    first = consensus.ingest(store, raw)
    assert first["skip_reasons"] == {"unmapped": 1}
    assert rows(store) == []
    assert consensus.ingest(store, raw)["snapshots_unchanged"] == 1
    store.conn.execute(
        "INSERT INTO indicator_aliases VALUES (?,?,?,?,?,?,?)",
        (
            "fixture",
            "Fixture CPI annual rate",
            "USA",
            "fixture-cpi",
            "us_cpi_headline_yy",
            "regular",
            "candidate",
        ),
    )
    result = consensus.ingest(store, raw)
    assert result["snapshots_processed"] == result["readings_added"] == 1
    assert result["skip_reasons"] == {}
    reading = rows(store)[0]
    assert reading["indicator_id"] == "us_cpi_headline_yy"
    assert reading["release_event_id"] == "BLS:us_cpi:regular:2026-10-14"
    assert consensus.ingest(store, raw)["snapshots_unchanged"] == 1
    assert len(rows(store)) == 1
    assert (
        store.conn.execute("SELECT COUNT(*) FROM consensus_snapshots").fetchone()[0]
        == 2
    )
    # A duplicate source alias with equivalent normalized text does not alter
    # match() or invalidate a checkpoint.
    store.conn.execute(
        "INSERT INTO indicator_aliases VALUES (?,?,?,?,?,?,?)",
        (
            "another-source",
            "  FIXTURE CPI   annual rate ",
            "USA",
            "another-code",
            "us_cpi_headline_yy",
            "regular",
            "candidate",
        ),
    )
    assert consensus.ingest(store, raw)["snapshots_unchanged"] == 1


def test_catalogue_alias_fingerprint_is_order_independent_and_tracks_mapping(store):
    aliases = [
        (
            "fixture",
            "Fixture CPI annual rate",
            "USA",
            "yy",
            "us_cpi_headline_yy",
            "regular",
            "candidate",
        ),
        (
            "fixture",
            "Fixture CPI monthly rate",
            "USA",
            "mm",
            "us_cpi_headline_mm",
            "regular",
            "candidate",
        ),
    ]
    sql = "INSERT INTO indicator_aliases VALUES (?,?,?,?,?,?,?)"
    store.conn.executemany(sql, aliases)
    version = consensus.Catalogue(store).version
    store.conn.execute("DELETE FROM indicator_aliases WHERE source_id='fixture'")
    store.conn.executemany(sql, reversed(aliases))
    assert consensus.Catalogue(store).version == version
    store.conn.execute(
        "UPDATE indicator_aliases SET estimate_stage='final' WHERE legacy_code='yy'"
    )
    assert consensus.Catalogue(store).version != version


def move_schedule(store, release_id, day, recorded):
    store.put(
        ScheduleVersion(
            release_event_id=release_id,
            expected_at=day + "T12:30:00Z",
            issuer_timezone="America/New_York",
            precision="exact",
            source_id="BLS",
            recorded_at=recorded,
        )
    )


@pytest.mark.parametrize("reprocess", [False, True])
@pytest.mark.parametrize(
    "reason", ["release_event_ambiguous", "release_event_not_found"]
)
def test_rejected_release_link_is_retired_and_restorable(
    store, tmp_path, reason, reprocess
):
    first_id = pending_release(store)
    raw = tmp_path / "raw"
    archive(raw, [dict(event(), date="2026-10-22T08:30:00-04:00")])
    assert consensus.ingest(store, raw)["readings_added"] == 1
    original = rows(store)[0]

    def cards():
        return store.release_cards(
            "2026-10-19",
            "2026-10-24",
            indicators=["us_cpi_headline_yy"],
            as_known_at="2026-10-30T00:00:00Z",
        )["cards"]

    assert cards()[0]["consensus"][0]["id"] == original["id"]
    if reason == "release_event_ambiguous":
        second_id = pending_release(store, edition="2026-10-23")
        move_schedule(store, second_id, "2026-10-22", "2026-10-19T00:00:00Z")
    else:
        move_schedule(store, first_id, "2026-10-23", "2026-10-19T00:00:00Z")
    result = consensus.ingest(store, raw, reprocess=reprocess)
    assert result["skip_reasons"] == {reason: 1}
    assert result["readings_added"] == 0
    assert Snapshot.load(store).consensus == []
    assert cards() == []
    assert rows(store) == [original]
    journal = store.conn.execute("SELECT * FROM consensus_link_versions").fetchall()
    assert len(journal) == 1
    assert journal[0]["consensus_reading_id"] == original["id"]
    assert journal[0]["retired"] == 1
    assert Snapshot.load(store, "2026-10-19T23:59:00Z").consensus == []
    consensus.ingest(store, raw, reprocess=True)
    assert (
        store.conn.execute("SELECT COUNT(*) FROM consensus_link_versions").fetchone()[0]
        == 1
    )

    if reason == "release_event_ambiguous":
        move_schedule(store, second_id, "2026-10-23", "2026-10-20T00:00:00Z")
    else:
        move_schedule(store, first_id, "2026-10-22", "2026-10-20T00:00:00Z")
    assert consensus.ingest(store, raw, reprocess=reprocess)["readings_added"] == 0
    assert Snapshot.load(store).consensus[0]["id"] == original["id"]
    assert cards()[0]["consensus"][0]["id"] == original["id"]
    assert rows(store) == [original]
    journal = store.conn.execute(
        "SELECT * FROM consensus_link_versions ORDER BY id"
    ).fetchall()
    assert [r["retired"] for r in journal] == [1, 0]
    assert store.conn.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize(
    "reason", ["release_event_ambiguous", "release_event_not_found"]
)
def test_retirement_and_checkpoint_roll_back_together(store, tmp_path, reason):
    first_id = pending_release(store)
    raw = tmp_path / "raw"
    archive(raw, [dict(event(), date="2026-10-22T08:30:00-04:00")])
    consensus.ingest(store, raw)
    if reason == "release_event_ambiguous":
        second_id = pending_release(store, edition="2026-10-23")
        move_schedule(store, second_id, "2026-10-22", "2026-10-19T00:00:00Z")
    else:
        move_schedule(store, first_id, "2026-10-23", "2026-10-19T00:00:00Z")
    store.conn.execute(
        "CREATE TRIGGER reject_checkpoint BEFORE INSERT ON consensus_snapshots "
        "BEGIN SELECT RAISE(ABORT, 'fixture checkpoint failure'); END"
    )
    assert consensus.ingest(store, raw)["status"] == "failed"
    assert len(Snapshot.load(store).consensus) == len(rows(store)) == 1
    assert (
        store.conn.execute("SELECT COUNT(*) FROM consensus_link_versions").fetchone()[0]
        == 0
    )
    assert (
        store.conn.execute("SELECT COUNT(*) FROM consensus_snapshots").fetchone()[0]
        == 1
    )
    store.conn.execute("DROP TRIGGER reject_checkpoint")
    assert consensus.ingest(store, raw)["skip_reasons"] == {reason: 1}
    assert Snapshot.load(store).consensus == []
    assert (
        store.conn.execute("SELECT COUNT(*) FROM consensus_link_versions").fetchone()[0]
        == 1
    )


def test_relinked_observation_has_one_live_release_and_can_restore_old_link(
    store, tmp_path
):
    first_id = pending_release(store)
    second_id = pending_release(store, edition="2026-10-23")
    raw = tmp_path / "raw"
    archive(raw, [dict(event(), date="2026-10-22T08:30:00-04:00")])
    assert consensus.ingest(store, raw)["readings_added"] == 1
    original_id = rows(store)[0]["id"]
    move_schedule(store, first_id, "2026-10-23", "2026-10-19T00:00:00Z")
    move_schedule(store, second_id, "2026-10-22", "2026-10-19T00:00:00Z")
    assert consensus.ingest(store, raw)["readings_added"] == 1
    live = Snapshot.load(store).consensus
    assert len(rows(store)) == 2  # immutable raw evidence is retained
    assert len(live) == 1 and live[0]["release_event_id"] == second_id
    assert Snapshot.load(store, "2026-10-19T23:59:00Z").consensus == []
    cards = store.release_cards(
        "2026-10-19",
        "2026-10-24",
        indicators=["us_cpi_headline_yy"],
        as_known_at="2026-10-30T00:00:00Z",
    )["cards"]
    assert [c["release_event_id"] for c in cards] == [second_id]
    move_schedule(store, first_id, "2026-10-22", "2026-10-20T00:00:00Z")
    move_schedule(store, second_id, "2026-10-23", "2026-10-20T00:00:00Z")
    assert consensus.ingest(store, raw, reprocess=True)["readings_added"] == 0
    live = Snapshot.load(store).consensus
    assert len(live) == 1 and live[0]["id"] == original_id
    assert consensus.ingest(store, raw, reprocess=True)["readings_added"] == 0
    assert (
        store.conn.execute("SELECT COUNT(*) FROM consensus_link_versions").fetchone()[0]
        == 2
    )
    for operation in (
        "DELETE FROM consensus_link_versions",
        "UPDATE consensus_link_versions SET recorded_at='bad'",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store.conn.execute(operation)
    assert store.conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_relinked_observation_supersedes_old_reference_period(store, tmp_path):
    raw = tmp_path / "raw"
    archive(raw, [event()])
    consensus.ingest(store, raw)
    persist(store, [observation(period="2026-10")])
    assert consensus.ingest(store, raw)["readings_added"] == 1
    live = Snapshot.load(store).consensus
    assert len(rows(store)) == 2
    assert len(live) == 1 and live[0]["reference_period"] == "2026-10"


def test_relink_and_checkpoint_roll_back_together(store, tmp_path):
    raw = tmp_path / "raw"
    archive(raw, [event()])
    consensus.ingest(store, raw)
    persist(store, [observation(period="2026-10")])
    store.conn.execute(
        "CREATE TRIGGER reject_checkpoint BEFORE INSERT ON consensus_snapshots "
        "BEGIN SELECT RAISE(ABORT, 'fixture checkpoint failure'); END"
    )
    assert consensus.ingest(store, raw)["status"] == "failed"
    assert len(rows(store)) == len(Snapshot.load(store).consensus) == 1
    assert (
        store.conn.execute("SELECT COUNT(*) FROM consensus_link_versions").fetchone()[0]
        == 0
    )
    assert (
        store.conn.execute("SELECT COUNT(*) FROM consensus_snapshots").fetchone()[0]
        == 1
    )
    store.conn.execute("DROP TRIGGER reject_checkpoint")
    assert consensus.ingest(store, raw)["readings_added"] == 1
    assert len(rows(store)) == 2 and len(Snapshot.load(store).consensus) == 1


def test_conflicting_issuer_zones_refuse_link_even_with_schedule(store, tmp_path):
    persist(store, [observation(issuer_timezone="America/New_York")])
    persist(store, [observation(period="2026-08", issuer_timezone="Europe/London")])
    move_schedule(
        store, "BLS:us_cpi:regular:2026-10-14", "2026-10-14", "2026-10-01T00:00:00Z"
    )
    archive(tmp_path / "raw", [event()])
    assert consensus.ingest(store, tmp_path / "raw")["skip_reasons"] == {
        "release_event_not_found": 1
    }
    assert rows(store) == []


def test_issuer_zone_fallback_without_stored_timezone(store, tmp_path):
    # No fixture CPI reading, vintage or schedule carries a timezone.
    assert not any(e.issuer_timezone for e in Snapshot.load(store).evidence)
    archive(tmp_path / "raw", [event()])
    assert consensus.ingest(store, tmp_path / "raw")["readings_added"] == 1
    linked = consensus.Catalogue(store).releases.by_indicator["us_cpi_headline_yy"]
    assert linked[0]["zone"] == consensus.ISSUER_ZONES["BLS"]


def test_issuer_revisions_do_not_supply_the_forecast_period(store, tmp_path):
    persist(store, [observation(period="2026-08", revision="routine")])
    archive(tmp_path / "raw", [event()])
    consensus.ingest(store, tmp_path / "raw")
    assert rows(store)[0]["reference_period"] == "2026-09"


def test_bad_snapshot_hash_retries_and_other_snapshots_continue(store, tmp_path):
    raw = tmp_path / "raw"
    archive(raw, [event()], digest="incorrect")
    archive(raw, [event()], filename="2026-10-14.json", captured="2026-10-14T11:00:00Z")
    result = consensus.ingest(store, raw)
    assert result["status"] == "failed" and result["readings_added"] == 1
    assert result["failures"] == [
        {"snapshot_file": "2026-10-13.json", "error_type": "ValueError"}
    ]
    assert (
        store.conn.execute("SELECT COUNT(*) FROM consensus_snapshots").fetchone()[0]
        == 1
    )


def test_snapshot_rows_and_ledger_roll_back_together(store, tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    archive(raw, [event(), dict(event(), title="CPI m/m (2026-09)", forecast="0.6%")])
    original = store.put

    def fail(record):
        if (
            isinstance(record, ConsensusReading)
            and record.indicator_id == "us_cpi_headline_mm"
        ):
            raise ValueError("fixture failure")
        return original(record)

    monkeypatch.setattr(store, "put", fail)
    assert consensus.ingest(store, raw)["status"] == "failed"
    assert rows(store) == []
    assert (
        store.conn.execute("SELECT COUNT(*) FROM consensus_snapshots").fetchone()[0]
        == 0
    )
    monkeypatch.setattr(store, "put", original)
    assert consensus.ingest(store, raw)["readings_added"] == 2


def test_reviewed_aliases_are_limited_exact_and_justified(store, tmp_path):
    catalogue = consensus.Catalogue(store)
    # Coverage does not justify inventing an FF alias: M9f explicitly prefers
    # a missing alias over a different measure. Every existing alias must still
    # name a supported family, be unique, exact and justified below.
    families = {alias["family"] for alias in catalogue.aliases}
    assert families <= set(run_daily.FAMILIES)
    assert "us_fhfa_hpi" not in families
    identities = [(alias["currency"], consensus.name(alias["title"])) for alias in catalogue.aliases]
    assert len(identities) == len(set(identities))
    for alias in catalogue.aliases:
        indicator = INDICATORS[alias["indicator_id"]]
        assert indicator.tier == "T1" and indicator.geography == alias["geography"]
        assert alias["title"] in alias["justification"]
        item = dict(
            event(), title=alias["title"] + " (2026-09)", country=alias["currency"]
        )
        mapped, reason = catalogue.match(item, consensus.mapping_title(item))
        assert reason is None and mapped == alias


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("broken", [False, True])
def test_runner_after_official_same_lease_independent_status(
    tmp_path, monkeypatch, dry_run, broken
):
    db = tmp_path / "runner.sqlite"
    raw = tmp_path / "raw"
    archive(raw, [event()])

    class Replay:
        def __init__(self, client):
            pass

        def fetch(self, *args):
            return [replace(observation(), revision="initial")]

    monkeypatch.setitem(run_daily.ADAPTERS, "us_cpi", Replay)
    original = run_daily.ingest
    seen = []

    def checked(store, directory, **kwargs):
        assert (
            store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 1
        )
        if not dry_run:
            assert Path(str(db) + ".daily.lease.json").exists()
        seen.append(1)
        if broken:
            raise RuntimeError("fixture failure")
        return original(store, directory, **kwargs)

    monkeypatch.setattr(run_daily, "ingest", checked)
    result = run_daily.execute(db, ["us_cpi"], 24, dry_run, raw_dir=raw)
    assert (
        seen == [1]
        and result["status"] == result["families"]["us_cpi"]["status"] == "ok"
    )
    assert result["exit_code"] == 0
    assert result["consensus"]["status"] == ("failed" if broken else "ok")
    if dry_run:
        assert not db.exists()
    else:
        with open_reader(db) as reader:
            log = reader.conn.execute("SELECT * FROM collect_run_consensus").fetchone()
            assert log["status"] == result["consensus"]["status"]
            assert json.loads(log["result_json"]) == result["consensus"]
            assert (
                reader.conn.execute(
                    "SELECT status FROM collect_runs WHERE finished_at IS NOT NULL"
                ).fetchone()[0]
                == "ok"
            )


@pytest.mark.parametrize("version", [5, 6])
def test_prior_migration_is_additive_and_ledgers_append_only(tmp_path, version):
    db = tmp_path / f"v{version}.sqlite"
    with sqlite3.connect(db) as conn:
        for filename in (
            "schema.sql",
            "migration_003.sql",
            "migration_004.sql",
            "migration_005.sql",
        ):
            conn.executescript(
                Path(migrations.__file__).with_name(filename).read_text()
            )
        if version == 6:
            conn.executescript(
                Path(migrations.__file__).with_name("migration_006.sql").read_text()
            )
        conn.execute(f"PRAGMA user_version={version}")
        conn.execute(
            "INSERT INTO collect_runs VALUES ('old','2026-10-01',NULL,'running','[]','{}','v5','host')"
        )
    with open_writer(db) as store:
        assert (
            store.conn.execute("PRAGMA user_version").fetchone()[0]
            == migrations.SCHEMA_VERSION
        )
        assert (
            store.conn.execute("SELECT code_version FROM collect_runs").fetchone()[0]
            == "v5"
        )
        assert store.conn.execute("PRAGMA foreign_key_check").fetchall() == []
        store.conn.execute(
            "INSERT INTO consensus_snapshots VALUES ('file','time','hash','rule','now','{}')"
        )
        store.conn.execute(
            "INSERT INTO collect_run_consensus VALUES ('old','running','no_change','now','{}')"
        )
        for table in ("consensus_snapshots", "collect_run_consensus"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                store.conn.execute(f"DELETE FROM {table}")


def test_recovery_of_consensus_interruption_preserves_completed_official_status(store):
    from market_data_hub.macro_releases import run_log

    parameters = {
        "consensus_raw_dir": "archive",
        "fetch_windows": {
            "us_cpi": {"fetch_since": "2024-10", "fetch_until": "2026-10-20"}
        },
    }
    run_log.start(store, "interrupted-consensus", ["us_cpi"], parameters)
    run_log.family_result(
        store,
        "interrupted-consensus",
        "us_cpi",
        dict(
            parameters["fetch_windows"]["us_cpi"],
            status="ok",
            vintages_added=1,
            readings_added=1,
            readings_skipped=0,
        ),
    )
    run_log.recover(store)
    terminal = store.conn.execute(
        "SELECT status FROM collect_runs WHERE run_id='interrupted-consensus' AND finished_at IS NOT NULL"
    ).fetchone()
    assert terminal[0] == "ok"
    failed = store.conn.execute("SELECT * FROM collect_run_consensus").fetchone()
    assert failed["status"] == "failed"
    assert json.loads(failed["result_json"])["error_type"] == "InterruptedRun"
    run_log.recover(store)  # append-only recovery is idempotent
    assert (
        store.conn.execute("SELECT COUNT(*) FROM collect_run_consensus").fetchone()[0]
        == 1
    )


def test_existing_catalogue_alias_requires_exact_stage_event_and_respects_scale(
    store, tmp_path
):
    persist(
        store,
        [
            observation(
                "us_payrolls_change",
                "us_payrolls",
                value=29,
                edition="2026-10-02",
                published="2026-10-02T12:30:00Z",
            )
        ],
    )
    store.conn.execute(
        "INSERT INTO indicator_aliases VALUES ('test','Non-Farm Employment Change','USA','fixture',"
        "'us_payrolls_change','regular','candidate')"
    )
    item = dict(
        event(),
        title="Non-Farm Employment Change",
        reference_period="2026-09",
        date="2026-10-02T08:30:00-04:00",
        forecast="50K",
    )
    archive(tmp_path / "raw", [item], captured="2026-10-01T21:30:00Z")
    assert consensus.ingest(store, tmp_path / "raw")["readings_added"] == 1
    reading = rows(store)[0]
    assert (reading["value"], reading["scale"], reading["unit"]) == (
        50,
        1000,
        "persons",
    )
    assert reading["release_event_id"] == "BLS:us_payrolls:regular:2026-10-02"


def test_removed_catalogue_alias_retires_the_link_and_reports_a_change(store, tmp_path):
    # Review P2s on #117: a mapping rejection retires an existing link, and a run
    # whose only effect is a journal change is not "no_change".
    raw = tmp_path / "raw"
    archive(raw, [dict(event(), title="Fixture CPI annual rate")])
    store.conn.execute(
        "INSERT INTO indicator_aliases VALUES (?,?,?,?,?,?,?)",
        (
            "fixture",
            "Fixture CPI annual rate",
            "USA",
            "fixture-cpi",
            "us_cpi_headline_yy",
            "regular",
            "candidate",
        ),
    )
    assert consensus.ingest(store, raw)["readings_added"] == 1
    assert len(Snapshot.load(store).consensus) == 1
    store.conn.execute("DELETE FROM indicator_aliases WHERE source_id='fixture'")
    result = consensus.ingest(store, raw)
    assert result["skip_reasons"] == {"unmapped": 1}
    assert result["readings_added"] == 0 and result["links_changed"] == 1
    assert result["status"] == "ok"
    assert Snapshot.load(store).consensus == []
    assert len(rows(store)) == 1


def test_stage_change_on_stored_alias_retires_the_link(store, tmp_path):
    # Review P2 on #117: an alias whose stage changes to a conflicting one (stage_conflict)
    # invalidates the association like a lost mapping does.
    raw = tmp_path / "raw"
    archive(raw, [dict(event(), title="Fixture CPI annual rate")])
    store.conn.execute(
        "INSERT INTO indicator_aliases VALUES (?,?,?,?,?,?,?)",
        (
            "fixture",
            "Fixture CPI annual rate",
            "USA",
            "fixture-cpi",
            "us_cpi_headline_yy",
            "regular",
            "candidate",
        ),
    )
    assert consensus.ingest(store, raw)["readings_added"] == 1
    store.conn.execute(
        "UPDATE indicator_aliases SET estimate_stage='final' WHERE source_id='fixture'"
    )
    result = consensus.ingest(store, raw)
    assert result["skip_reasons"] == {"stage_conflict": 1}
    assert result["links_changed"] == 1 and result["status"] == "ok"
    assert Snapshot.load(store).consensus == []
