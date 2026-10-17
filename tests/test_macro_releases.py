"""Publication/knowledge history, evidence retention and read-only migration."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3

from filelock import Timeout
import pytest
from pydantic import ValidationError

from market_data_hub.macro_releases import open_reader, open_writer
from market_data_hub.macro_releases.legacy import import_calendar
from market_data_hub.macro_releases.migrations import migrate
from market_data_hub.macro_releases.models import (
    ConsensusReading,
    Discrepancy,
    Indicator,
    Reading,
    ReleaseEvent,
    ScheduleVersion,
    SourceSeries,
    ValueVintage,
)
from market_data_hub.macro_releases.numbers import parse_number
from market_data_hub.macro_releases.store import connect_readonly

FIXTURES = Path(__file__).parent / "fixtures"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@pytest.fixture
def store(tmp_path):
    with open_writer(tmp_path / "macro.sqlite") as db:
        yield db


def add_measure(
    store, indicator="payroll", issuer="BLS", transform="change", unit="persons"
):
    store.put(
        Indicator(
            id=indicator,
            measure=indicator,
            geography="USA",
            seasonal_adjustment="SA",
            transform=transform,
            unit=unit,
            frequency="M",
            issuer_id=issuer,
            tier="T1",
            definition="Test measure",
            catalogue_status="supported",
            active=True,
        )
    )
    store.put(
        SourceSeries(
            id=indicator + ":issuer",
            indicator_id=indicator,
            source_id=issuer,
            issuer_id=issuer,
            distributor_id=issuer,
            source_key=indicator,
            evidence_role="issuer",
        )
    )


def event(store, edition="2026-10", stage="regular", family="employment", issuer="BLS"):
    event_id = ":".join((issuer, family, stage, edition))
    store.put(
        ReleaseEvent(
            id=event_id,
            issuer_id=issuer,
            family=family,
            estimate_stage=stage,
            edition=edition,
        )
    )
    return event_id


def vintage(
    store,
    release,
    period="2026-09",
    value=100000,
    indicator="payroll",
    published="2026-10-02T12:30:00Z",
    seen="2026-10-03T01:00:00Z",
    stage="regular",
    revision="initial",
    **changes,
):
    data = dict(
        indicator_id=indicator,
        reference_period=period,
        release_event_id=release,
        source_series_id=indicator + ":issuer",
        value=value,
        published_as=str(value),
        unit="persons",
        estimate_stage=stage,
        revision_kind=revision,
        issuer_published_at=published,
        publication_precision="exact",
        first_seen_at=seen,
        retrieved_at=seen,
        document_hash=digest((release, period, value)),
        parser_version="fixture-v1",
        extraction_method="structured",
        verification_status="verified",
    )
    data.update(changes)
    return store.put(ValueVintage(**data))


def reading(
    store,
    *,
    indicator="payroll",
    value=99999,
    origin="wire:reuters:article-1",
    **changes,
):
    data = dict(
        indicator_id=indicator,
        source_id="web",
        series_key=indicator,
        period_key="2026-09",
        reference_period="2026-09",
        edition="web-edition-1",
        estimate_stage="regular",
        value=value,
        published_as=str(value),
        unit="persons",
        issuer_id="BLS",
        distributor_id="web",
        evidence_origin_id=origin,
        first_seen_at="2026-10-03T01:00:00Z",
        retrieved_at="2026-10-03T01:00:00Z",
        content_hash=digest(value),
        parser_version="fixture-v1",
        extraction_method="web_search",
        verification_status="unverified",
        raw_payload_json="{}",
    )
    data.update(changes)
    return store.put(Reading(**data))


def test_payroll_one_event_carries_new_month_and_two_revisions(store):
    add_measure(store)
    release = event(store)
    for period, value, revision in [
        ("2026-09", 100000, "initial"),
        ("2026-08", 133000, "routine"),
        ("2026-07", 142000, "routine"),
    ]:
        vintage(store, release, period, value, revision=revision)
    assert store.conn.execute("SELECT COUNT(*) FROM release_events").fetchone()[0] == 1
    assert len(store.latest_official()) == 3
    assert {r["release_event_id"] for r in store.latest_official()} == {release}


def test_revisions_knowledge_and_old_publication_download(store):
    add_measure(store)
    first = event(store, "august")
    revised = event(store, "september")
    vintage(
        store,
        first,
        "2026-08",
        162000,
        published="2026-09-04T12:30:00Z",
        seen="2026-09-05T01:00:00Z",
    )
    vintage(
        store,
        revised,
        "2026-08",
        133000,
        revision="routine",
        seen="2026-10-05T01:00:00Z",
    )
    assert store.as_known_at("2026-09-04T22:00:00Z") == []
    assert store.as_known_at("2026-10-04T01:00:00Z")[0]["value"] == 162000
    assert store.as_known_at("2026-10-05T01:00:00Z")[0]["value"] == 133000
    assert store.first_release()[0]["value"] == 162000
    # Downloading an OLD edition later never makes it latest by publication.
    older = event(store, "old-backfill")
    vintage(
        store,
        older,
        "2026-08",
        170000,
        published="2026-09-01T12:30:00Z",
        seen="2026-10-10T01:00:00Z",
    )
    assert store.as_known_at("2026-10-04T01:00:00Z")[0]["value"] == 162000
    assert store.as_known_at("2026-10-11T01:00:00Z")[0]["value"] == 133000


def test_idempotency_unchanged_edition_and_changed_value(store):
    add_measure(store)
    release = event(store)
    original = vintage(store, release)
    assert vintage(store, release) == original
    with pytest.raises(ValueError, match="conflicting value semantics"):
        vintage(
            store,
            release,
            value=999,
            document_hash=digest((release, "2026-09", 100000)),
        )
    assert (
        vintage(
            store,
            release,
            document_hash="new-bytes-same-print",
            retrieved_at="2026-10-04T00:00:00Z",
        )
        == original
    )
    correction = vintage(
        store, release, value=110000, revision="correction", seen="2026-10-05T00:00:00Z"
    )
    assert correction != original
    next_edition = event(store, "2026-11")
    vintage(
        store,
        next_edition,
        value=110000,
        published="2026-11-06T13:30:00Z",
        seen="2026-11-07T01:00:00Z",
        revision="unchanged",
    )
    assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 3
    assert store.latest_official()[0]["release_event_id"] == next_edition
    assert (
        store.latest_official()[0]["selection_rule_version"]
        == "placeholder:m4-verified-issuer-v1"
    )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.conn.execute("UPDATE value_vintages SET value=0")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.conn.execute("DELETE FROM value_vintages")


def test_hicp_stages_and_rescheduling_keep_identity(store):
    add_measure(store, "hicp", issuer="Eurostat", transform="yy", unit="%")
    flash = event(store, "2026-09-flash", "flash", "HICP", "Eurostat")
    final = event(store, "2026-09-final", "final", "HICP", "Eurostat")
    vintage(store, flash, indicator="hicp", value=3.8, unit="%", stage="flash")
    vintage(
        store,
        final,
        indicator="hicp",
        value=3.8,
        unit="%",
        stage="final",
        published="2026-10-17T09:00:00Z",
        seen="2026-10-18T01:00:00Z",
    )
    for expected, recorded in [
        ("2026-10-16T09:00:00Z", "2026-10-01T01:00:00Z"),
        ("2026-10-17T09:00:00Z", "2026-10-10T01:00:00Z"),
    ]:
        store.put(
            ScheduleVersion(
                release_event_id=final,
                expected_at=expected,
                recorded_at=recorded,
                precision="exact",
                issuer_timezone="Europe/Luxembourg",
                source_id="Eurostat",
            )
        )
    assert store.conn.execute("SELECT COUNT(*) FROM indicators").fetchone()[0] == 1
    assert store.first_release()[0]["estimate_stage"] == "flash"
    assert store.latest_official()[0]["estimate_stage"] == "final"
    assert (
        store.conn.execute("SELECT COUNT(*) FROM schedule_versions").fetchone()[0] == 2
    )


@pytest.mark.parametrize(
    "captured,eligible,post",
    [
        ("2026-10-02T12:29:59Z", 1, 0),
        ("2026-10-02T12:30:00Z", 0, 1),
        ("2026-10-02T12:30:01Z", 0, 1),
        ("2026-10-02T14:30:01+02:00", 0, 1),
    ],
)
def test_consensus_strictly_pre_release_and_provider_separate(
    store, captured, eligible, post
):
    add_measure(store)
    release = event(store)
    for provider, value in [("FF forecast", 90000), ("Provider B", 80000)]:
        record = ConsensusReading(
            provider=provider,
            indicator_id="payroll",
            release_event_id=release,
            measure="payroll",
            reference_period="2026-09",
            estimate_stage="regular",
            value=value,
            unit="persons",
            captured_at=captured,
            release_cutoff_at="2026-10-02T12:30:00Z",
            cutoff_precision="exact",
            cutoff_basis="actual",
            content_hash=provider,
        )
        assert store.put(record) == store.put(record)
    rows = list(store.conn.execute("SELECT * FROM consensus_readings"))
    assert len(rows) == 2
    assert {r["value"] for r in rows} == {80000, 90000}
    assert {(r["pre_release_eligible"], r["post_release"]) for r in rows} == {
        (eligible, post)
    }


def test_unknown_consensus_cutoff_is_ineligible(store):
    add_measure(store)
    store.put(
        ConsensusReading(
            provider="FF forecast",
            indicator_id="payroll",
            release_event_id=event(store),
            measure="payroll",
            reference_period="2026-09",
            estimate_stage="regular",
            value=1,
            unit="persons",
            captured_at="2026-10-01T00:00:00Z",
            content_hash="x",
        )
    )
    row = store.conn.execute("SELECT * FROM consensus_readings").fetchone()
    assert not row["pre_release_eligible"] and not row["post_release"]


def test_discrepancy_retains_official_and_evidence_origins(store):
    add_measure(store)
    official = vintage(store, event(store))
    ids = [reading(store, source_id=f"searcher-{n}") for n in range(3)]
    for reading_id in ids:
        store.put(
            Discrepancy(
                reading_id=reading_id,
                selected_vintage_id=official,
                reason="disagrees with verified issuer",
                recorded_at="2026-10-03T02:00:00Z",
            )
        )
    assert store.latest_official()[0]["id"] == official
    assert store.conn.execute("SELECT COUNT(*) FROM discrepancies").fetchone()[0] == 3
    assert (
        store.conn.execute(
            "SELECT COUNT(DISTINCT evidence_origin_id) FROM readings"
        ).fetchone()[0]
        == 1
    )


def test_mirror_and_unverified_extraction_cannot_displace_issuer(store):
    add_measure(store)
    release = event(store)
    official = vintage(store, release)
    store.put(
        SourceSeries(
            id="mirror",
            indicator_id="payroll",
            source_id="FRED",
            issuer_id="BLS",
            distributor_id="FRED",
            source_key="PAYEMS",
            evidence_role="mirror",
        )
    )
    vintage(
        store,
        event(store, "newer-mirror"),
        value=123,
        source_series_id="mirror",
        published="2026-10-10T12:30:00Z",
        seen="2026-10-11T00:00:00Z",
    )
    vintage(
        store,
        event(store, "unverified"),
        value=456,
        verification_status="unverified",
        published="2026-10-11T12:30:00Z",
        seen="2026-10-12T00:00:00Z",
    )
    assert store.latest_official()[0]["id"] == official


def test_reader_migrations_lock_and_atomic_failure(tmp_path):
    path = tmp_path / "macro.sqlite"
    with open_writer(path) as db:
        migrate(db.conn)
        assert db.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert db.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.conn.execute("PRAGMA busy_timeout").fetchone()[0] == 60000
        with pytest.raises(Timeout):
            open_writer(path, lock_timeout=0)
        with open_reader(path) as reader:
            assert reader.latest_official() == []
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                reader.conn.execute(
                    "INSERT INTO release_events VALUES ('x','x','x','x','x')"
                )
        with pytest.raises(sqlite3.IntegrityError):
            vintage(db, "nonexistent")
        assert not db.conn.in_transaction
    missing = tmp_path / "does-not-exist.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        open_reader(missing)
    assert not missing.exists()
    with open_writer(path) as db:
        db.conn.execute("PRAGMA user_version=999")
    with pytest.raises(RuntimeError, match="unsupported"):
        open_writer(path)


def test_contracts_reject_fake_period_and_naive_time():
    with pytest.raises(ValidationError, match="timezone-aware"):
        ScheduleVersion(
            release_event_id="x",
            expected_at="2026-10-17",
            issuer_timezone="UTC",
            precision="date_only",
            source_id="x",
            recorded_at="2026-10-01T00:00:00Z",
        )
    with pytest.raises(ValidationError, match="known reference period"):
        Reading(
            source_id="x",
            series_key="x",
            period_key="d:2026-10-17",
            reference_period="d:2026-10-17",
            edition="x",
            estimate_stage="unknown",
            issuer_id="x",
            distributor_id="x",
            evidence_origin_id="unknown",
            first_seen_at="2026-10-17T00:00:00Z",
            retrieved_at="2026-10-17T00:00:00Z",
            content_hash="x",
            parser_version="x",
            extraction_method="web_search",
            verification_status="unverified",
            raw_payload_json="{}",
        )


@pytest.mark.parametrize(
    "printed,expected",
    [
        ("+3,3 %", 3.3),
        ("162,000", 162000),
        ("unchanged (0.0 percent)", 0),
        ("0.1% drop", -0.1),
        ("106,2 Mrd €", 106200000000),
        ("1710K", 1710000),
        ("−0,2%", -0.2),
        ("1\u202f234,5", 1234.5),
    ],
)
def test_numeric_formats(printed, expected):
    assert parse_number(printed) == pytest.approx(expected)


def test_numeric_ambiguity_requires_explicit_decimal_and_multiple_numbers_rejected():
    assert parse_number("0,123", decimal_separator=",") == 0.123
    assert parse_number("162,000", target_scale=1000) == 162
    with pytest.raises(ValueError):
        parse_number("3.7% vs 3.6%")


@dataclass
class Golden:
    cases: list
    indicators: dict


@pytest.fixture
def golden(store):
    cases = json.loads(
        (FIXTURES / "macro_releases_golden.json").read_text(encoding="utf-8")
    )["cases"]
    indicators = {}
    for case in sorted(cases, key=lambda c: (c["publication_date"], c["id"])):
        semantics = tuple(
            case[k]
            for k in (
                "country",
                "issuer",
                "indicator",
                "change_basis",
                "seasonal_adjustment",
                "unit",
            )
        )
        indicator = digest(semantics)
        indicators[case["id"]] = indicator
        transform = (
            "change"
            if case["change_basis"] == "mm_level_change"
            else case["change_basis"]
        )
        if not store.conn.execute(
            "SELECT 1 FROM indicators WHERE id=?", (indicator,)
        ).fetchone():
            store.put(
                Indicator(
                    id=indicator,
                    measure=case["indicator"],
                    geography=case["country"],
                    seasonal_adjustment=case["seasonal_adjustment"],
                    transform=transform,
                    unit=case["unit"],
                    frequency="Q" if "Q" in case["reference_period"] else "M",
                    issuer_id=case["issuer"],
                    definition="Golden regression fixture only",
                )
            )
        distributor = (
            "FRASER"
            if "fraser.stlouisfed.org" in case["source_url"]
            else case["issuer"]
        )
        series = digest((indicator, distributor, case["series_id"]))
        store.put(
            SourceSeries(
                id=series,
                indicator_id=indicator,
                source_id=distributor,
                issuer_id=case["issuer"],
                distributor_id=distributor,
                source_key=case["series_id"],
                evidence_role="issuer",
            )
        )
        stage = (
            case["vintage"]
            if case["vintage"] not in ("first", "revised")
            else "regular"
        )
        release = event(
            store, case["publication_date"], stage, case["indicator"], case["issuer"]
        )
        stamp = case["publication_date"] + "T00:00:00Z"
        store.put(
            ValueVintage(
                indicator_id=indicator,
                reference_period=case["reference_period"],
                release_event_id=release,
                source_series_id=series,
                value=case["value"],
                published_as=case["printed_as"],
                unit=case["unit"],
                estimate_stage=stage,
                revision_kind="routine" if case["vintage"] == "revised" else "initial",
                issuer_published_at=stamp,
                publication_precision="date_only",
                first_seen_at=stamp,
                retrieved_at=stamp,
                document_hash=digest(case),
                parser_version="golden-fixture-v1",
                extraction_method="hand_verified_fixture",
                verification_status="verified",
            )
        )
    return Golden(cases, indicators)


def test_all_golden_formats_and_vintages(store, golden):
    assert len(golden.cases) == 32
    assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 32
    for case in golden.cases:
        assert parse_number(case["printed_as"]) == pytest.approx(case["value"]), case[
            "id"
        ]
    for case in golden.cases:
        if case["vintage"] == "revised":
            indicator = golden.indicators[case["id"]]
            first = store.first_release(indicator)
            latest = store.latest_official(indicator)
            first = next(
                r for r in first if r["reference_period"] == case["reference_period"]
            )
            latest = next(
                r for r in latest if r["reference_period"] == case["reference_period"]
            )
            assert first["id"] != latest["id"]
            assert first["value"] != latest["value"]


def test_golden_flash_final_and_as_known(store, golden):
    flash, final = "ea_hicp_yy_flash_2026-09", "ea_hicp_yy_final_2026-09"
    assert golden.indicators[flash] == golden.indicators[final]
    indicator = golden.indicators[flash]
    assert store.first_release(indicator)[0]["estimate_stage"] == "flash"
    assert store.latest_official(indicator)[0]["estimate_stage"] == "final"
    payroll = golden.indicators["us_nfp_change_2026-08_first"]
    assert store.as_known_at("2026-10-01T23:59:59Z", payroll)[0]["value"] == 162000
    assert store.as_known_at("2026-10-03T00:00:00Z", payroll)[0]["value"] == 133000
    gdp = golden.indicators["us_gdp_q2_2026_advance"]
    assert (
        store.conn.execute(
            "SELECT COUNT(*) FROM value_vintages WHERE indicator_id=?", (gdp,)
        ).fetchone()[0]
        == 3
    )
    assert (
        golden.indicators["us_cpi_headline_yy_nsa_2026-09"]
        != golden.indicators["us_cpi_headline_mm_sa_2026-09"]
    )


def insert(conn, table, data):
    conn.execute(
        f"INSERT INTO {table} ({', '.join(data)}) VALUES ({', '.join('?' for _ in data)})",
        tuple(data.values()),
    )


@pytest.fixture
def legacy_calendar(tmp_path):
    path = tmp_path / "calendar.sqlite"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            (FIXTURES / "legacy_calendar_v4.sql").read_text(encoding="utf-8")
        )
        for run in ("cal_z", "cal_a"):
            insert(
                conn,
                "runs",
                dict(
                    run_id=run,
                    as_of="2026-10-17",
                    knowledge_cutoff="2026-10-17T23:00:00Z",
                    started_at="2026-10-17T23:00:00Z",
                    status="published",
                    config_fingerprint="test",
                ),
            )
        for code, name, unit in [
            ("flash", "HICP y/y (flash)", "percent"),
            ("final", "HICP y/y (Final)", "percent"),
            ("uncertain_flash", "Other (flash)", "percent"),
            ("uncertain_final", "Other (final)", "index_points"),
        ]:
            insert(
                conn,
                "indicators",
                dict(
                    code=code,
                    jurisdiction="EMU",
                    name=name,
                    agency="Eurostat",
                    tier="T1",
                    unit_code=unit,
                    transform="pct_change",
                    basis="yoy",
                    frequency="M",
                    scale=1,
                    created_at="2026-01-01T00:00:00Z",
                    created_by_run="seed",
                ),
            )
            insert(
                conn,
                "indicator_aliases",
                dict(
                    alias_norm=name.lower(),
                    jurisdiction="EMU",
                    code=code,
                    created_by_run="seed",
                ),
            )
        for index, period in enumerate(("2026-09", "d:2026-10-17"), start=1):
            insert(
                conn,
                "occurrences",
                dict(
                    id=index,
                    indicator_code="final",
                    period_norm=period,
                    release_date="2026-10-17",
                    scheduled_at_utc="2026-10-17T09:00:00Z",
                    created_by_run="cal_z",
                ),
            )
        for index, searcher in enumerate(
            ("claude_web", "codex_web", "crawler"), start=1
        ):
            insert(
                conn,
                "sightings",
                dict(
                    id=index,
                    run_id="cal_z",
                    searcher=searcher,
                    region="europe",
                    jurisdiction="EMU",
                    raw_name="HICP y/y (Final)",
                    raw_period="2026-09",
                    release_at_utc="2026-10-17T09:00:00Z",
                    release_date="2026-10-17",
                    raw_actual="3.8%",
                    sources_json=json.dumps(
                        [
                            {
                                "url": f"https://wire.example/story?utm_source={searcher}",
                                "wire_id": "same-wire-story",
                            }
                        ]
                    ),
                    indicator_code="final",
                    resolution="alias",
                    occurrence_id=1,
                ),
            )
        add_legacy_observation(conn, 1, "cal_z", status="released", value=3.8)
        add_legacy_observation(conn, 2, "cal_z", status="scheduled", value=None)
    return path


def add_legacy_observation(
    conn, occurrence, run, status, value, observed_at="2026-10-17T23:00:00Z"
):
    insert(
        conn,
        "observations",
        dict(
            occurrence_id=occurrence,
            run_id=run,
            observed_at=observed_at,
            release_date="2026-10-17",
            actual_raw="3.8%" if value else None,
            actual_value=value,
            consensus_raw="3.6%",
            consensus_value=3.6,
            previous_raw="3.2%",
            previous_value=3.2,
            unit_code="percent",
            scale=1,
            status=status,
            n_searchers=3,
            n_domains=1,
            official_source=1,
            sources_json='[{"url":"https://wire.example/story","wire_id":"same-wire-story"}]',
        ),
    )


def test_legacy_candidates_stages_raw_evidence_idempotency_and_readonly(
    store, legacy_calendar
):
    before = legacy_calendar.read_bytes()
    first = import_calendar(store, legacy_calendar, captured_at="2026-10-18T01:00:00Z")
    assert (
        first["new_readings"] == 9
    )  # three sightings + actual/consensus/previous on two observations
    assert legacy_calendar.read_bytes() == before
    assert store.conn.execute("SELECT COUNT(*) FROM indicators").fetchone()[0] == 3
    assert (
        store.conn.execute("SELECT COUNT(*) FROM legacy_candidates").fetchone()[0] == 4
    )
    assert (
        store.conn.execute("SELECT COUNT(*) FROM indicator_aliases").fetchone()[0] == 4
    )
    assert store.conn.execute("SELECT SUM(active) FROM indicators").fetchone()[0] == 0
    candidates = {
        r["legacy_code"]: dict(r)
        for r in store.conn.execute("SELECT * FROM legacy_candidates")
    }
    assert candidates["flash"]["indicator_id"] == candidates["final"]["indicator_id"]
    assert candidates["flash"]["estimate_stage"] == "flash"
    assert candidates["final"]["estimate_stage"] == "final"
    assert (
        candidates["uncertain_flash"]["indicator_id"]
        != candidates["uncertain_final"]["indicator_id"]
    )
    assert store.latest_official() == []
    assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 0
    assert {
        tuple(r)
        for r in store.conn.execute(
            "SELECT DISTINCT extraction_method, verification_status FROM readings"
        )
    } == {("web_search", "unverified")}
    assert (
        store.conn.execute(
            "SELECT COUNT(DISTINCT evidence_origin_id) FROM readings"
        ).fetchone()[0]
        == 1
    )
    unknown = store.conn.execute(
        "SELECT * FROM readings WHERE period_key LIKE 'unknown:%'"
    ).fetchall()
    assert len(unknown) == 3
    assert all("release date" in r["period_unknown_reason"] for r in unknown)
    assert all(r["first_seen_at"].startswith("2026-10-18") for r in unknown)
    previous = store.conn.execute(
        "SELECT * FROM readings WHERE reading_kind='previous'"
    ).fetchall()
    assert all(
        r["reference_period"] is None and r["estimate_stage"] == "unknown"
        for r in previous
    )
    # A later import preserves original first_seen_at and adds no rows.
    counts = {
        t: store.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in (
            "readings",
            "indicators",
            "legacy_candidates",
            "indicator_aliases",
            "import_worklist",
        )
    }
    assert (
        import_calendar(store, legacy_calendar, captured_at="2026-10-19T01:00:00Z")[
            "new_readings"
        ]
        == 0
    )
    assert all(
        store.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == count
        for t, count in counts.items()
    )
    with connect_readonly(legacy_calendar) as conn:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("UPDATE runs SET status='failed'")


def test_worklist_revisits_old_run_late_value_without_watermark(store, legacy_calendar):
    # Remove no rows: a third occurrence has no observation at the first pull.
    with sqlite3.connect(legacy_calendar) as conn:
        insert(
            conn,
            "occurrences",
            dict(
                id=3,
                indicator_code="flash",
                period_norm="2026-08",
                release_date="2026-10-17",
                created_by_run="cal_a",
            ),
        )
    import_calendar(store, legacy_calendar, captured_at="2026-10-18T01:00:00Z")
    pending = store.conn.execute(
        "SELECT * FROM import_worklist WHERE occurrence_key='3'"
    ).fetchone()
    assert pending["status"] == "pending" and pending["reason"] == "overdue"
    with sqlite3.connect(legacy_calendar) as conn:
        add_legacy_observation(conn, 3, "cal_a", status="released", value=3.8)
    result = import_calendar(store, legacy_calendar, captured_at="2026-10-19T01:00:00Z")
    assert result["new_readings"] == 3
    assert (
        store.conn.execute(
            "SELECT status FROM import_worklist WHERE occurrence_key='3'"
        ).fetchone()[0]
        == "complete"
    )
    assert (
        store.conn.execute(
            "SELECT first_seen_at FROM readings WHERE reference_period='2026-08'"
        )
        .fetchone()[0]
        .startswith("2026-10-19")
    )


def test_unlinked_sightings_and_late_observations_stay_in_worklist(
    store, legacy_calendar
):
    with sqlite3.connect(legacy_calendar) as conn:
        insert(
            conn,
            "sightings",
            dict(
                id=20,
                run_id="cal_a",
                searcher="crawler",
                region="europe",
                jurisdiction="EMU",
                raw_name="Unknown HICP",
                raw_period="2026-09",
                release_at_utc="2026-10-17T09:00:00Z",
                release_date="2026-10-17",
                sources_json="[]",
                resolution="unresolved",
            ),
        )
        add_legacy_observation(
            conn,
            1,
            "cal_a",
            status="released",
            value=3.8,
            observed_at="2026-10-18T23:00:00Z",
        )
    import_calendar(store, legacy_calendar, captured_at="2026-10-19T01:00:00Z")
    rows = {
        r["occurrence_key"]: dict(r)
        for r in store.conn.execute("SELECT * FROM import_worklist")
    }
    assert rows["sighting:20"]["reason"] == "unlinked_sighting"
    assert rows["1"]["reason"] == "late_value_needs_review"
    assert rows["1"]["status"] == "pending"
    with sqlite3.connect(legacy_calendar) as conn:
        conn.execute(
            "UPDATE sightings SET indicator_code='final', occurrence_id=1, resolution='alias' WHERE id=20"
        )
    import_calendar(store, legacy_calendar, captured_at="2026-10-20T01:00:00Z")
    assert (
        store.conn.execute(
            "SELECT status FROM import_worklist WHERE occurrence_key='sighting:20'"
        ).fetchone()[0]
        == "complete"
    )
