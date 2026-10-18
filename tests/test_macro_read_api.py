"""M6 offline certification: authority, historical cards and shadow parity."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest
import requests

from market_data_hub.macro_releases import ReadAPI, open_reader, open_writer, resolve
from market_data_hub.macro_releases.__main__ import main
from market_data_hub.macro_releases.adapters import Observation, persist
from market_data_hub.macro_releases.catalogue import INDICATORS
from market_data_hub.macro_releases.http import Document
from market_data_hub.macro_releases.legacy import import_calendar
from market_data_hub.macro_releases.models import (
    ConsensusReading, ReleaseEvent, ScheduleVersion, SourceSeries, ValueVintage,
)
from market_data_hub.macro_releases.resolve import Snapshot
from test_macro_official_adapters import (
    GOLDEN, SLICE_IDS, golden_indicator, observations as observations,
)
from test_macro_releases import (
    add_legacy_observation, golden as golden, insert, legacy_calendar as legacy_calendar,
)

NOW = "2026-10-18T23:00:00Z"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("M6 tests must not use the network")
    monkeypatch.setattr(requests.Session, "request", forbidden)


@pytest.fixture
def store(tmp_path):
    with open_writer(tmp_path / "macro.sqlite") as store:
        yield store


def observation(indicator="us_cpi_headline_yy", family="us_cpi", period="2026-09",
                value=3.7, edition="2026-10-14", stage="regular", source="BLS",
                seen="2026-10-14T12:31:00Z", published=None, **kwargs):
    payload = repr((indicator, period, value, edition, stage, source, kwargs)).encode()
    document = Document(payload, "https://issuer.example/publication", seen,
                        hashlib.sha256(payload).hexdigest())
    return Observation(indicator, family, period, value, str(value), indicator + ":series",
                       source, document, edition, published or edition + "T12:30:00Z",
                       stage=stage, precision="exact", **kwargs)


def evidence(store, *, source="BLS", role="issuer", value=3.7, verified=True,
             method="api", edition="2026-10-14", seen="2026-10-14T12:31:00Z",
             unit="%", scale=1, indicator="us_cpi_headline_yy", published=None,
             composition=None, stage="regular", **kwargs):
    info = INDICATORS[indicator]
    store.put(info)
    event = f"{info.issuer_id}:test:{stage}:{edition}"
    store.put(ReleaseEvent(id=event, issuer_id=info.issuer_id, family="us_cpi",
                           estimate_stage=stage, edition=edition))
    series = f"{source}:{indicator}"
    store.put(SourceSeries(id=series, indicator_id=indicator, source_id=source,
                           issuer_id=info.issuer_id, distributor_id=source,
                           source_key=indicator, evidence_role=role))
    vintage_id = store.put(ValueVintage(
        indicator_id=indicator, reference_period="2026-09", release_event_id=event,
        source_series_id=series, value=value, published_as=str(value), unit=unit, scale=scale,
        estimate_stage=stage, revision_kind="initial", issuer_published_at=published or edition + "T12:30:00Z",
        publication_precision="exact", first_seen_at=seen, retrieved_at=seen,
        document_hash=hashlib.sha256(repr((source, value, seen)).encode()).hexdigest(),
        parser_version="m6-fixture", extraction_method=method,
        verification_status="verified" if verified else "unverified", composition_id=composition,
        **kwargs,
    ))
    return event, vintage_id


def schedule(store, edition="2026-10-14", expected="2026-10-14T12:30:00Z", recorded="2026-10-01T00:00:00Z"):
    event = "BLS:us_cpi:regular:" + edition
    store.put(ReleaseEvent(id=event, issuer_id="BLS", family="us_cpi", estimate_stage="regular", edition=edition))
    store.put(ScheduleVersion(release_event_id=event, expected_at=expected, issuer_timezone="America/New_York",
                              precision="exact", source_id="BLS", recorded_at=recorded))
    return event


def cards(store, since="2026-10-10", until="2026-10-17", **kwargs):
    return store.release_cards(since, until, as_known_at=kwargs.pop("as_known_at", NOW), **kwargs)


def test_all_32_golden_contract_resolutions(store, golden):
    assert len(golden.cases) == 32
    for case in golden.cases:
        stage = case["vintage"] if case["vintage"] not in {"first", "revised"} else "regular"
        result = resolve(store, golden.indicators[case["id"]], case["reference_period"], stage,
                         as_known_at=case["publication_date"] + "T23:59:59Z")
        assert result.status == "official", case["id"]
        assert result.value == pytest.approx(case["value"]), case["id"]
        assert result.edition == case["publication_date"]
        assert result.evidence_ids


@pytest.mark.parametrize("case_id", SLICE_IDS)
def test_golden_issuer_adapter_resolutions(store, observations, case_id):
    case = next(c for c in GOLDEN if c["id"] == case_id)
    indicator = golden_indicator(case)
    edition = "2026-10-16" if case_id == "ea_hicp_yy_final_2026-09" else case["publication_date"]
    matched = [o for o in observations if o.indicator_id == indicator and o.period == case["reference_period"] and o.edition == edition]
    persist(store, observations)
    assert matched
    event = ":".join((INDICATORS[indicator].issuer_id, matched[0].family,
                      matched[0].event_stage or matched[0].stage, edition))
    result = resolve(store, indicator, case["reference_period"], matched[0].stage,
                     as_known_at=NOW, release_event_id=event)
    assert result.value * result.scale == pytest.approx(case["value"])
    assert result.status == ("provisional" if all(o.method == "archive_text" for o in matched) else "official")


def test_payroll_card_new_month_two_revisions_and_previous(store):
    old = [observation("us_payrolls_change", "us_payrolls", period, value, "2026-09-04",
                       seen="2026-09-04T12:31:00Z") for period, value in [("2026-07", 30), ("2026-08", 162)]]
    new = [observation("us_payrolls_change", "us_payrolls", period, value, "2026-10-02",
                       seen="2026-10-02T12:31:00Z", revision=revision)
           for period, value, revision in [("2026-07", -10, "routine"), ("2026-08", 133, "routine"), ("2026-09", 29, "initial")]]
    persist(store, old + new)
    first = cards(store, "2026-10-02", "2026-10-02", indicators=["us_payrolls_change"])
    assert not first["empty"]
    assert len(first["cards"]) == 1
    card = first["cards"][0]
    assert card["reference_period"] == "2026-09"
    assert card["resolution"]["value"] == 29
    assert card["resolution"]["scale"] == 1000
    assert card["previous"]["reference_period"] == "2026-08"
    assert card["previous"]["value"] == 162
    assert [(r["reference_period"], r["old"]["value"], r["new"]["value"]) for r in card["revisions"]] == [
        ("2026-07", 30, -10), ("2026-08", 162, 133)]
    assert card["surprise"] is None
    # A transport reread adds evidence, never an extra release or revision.
    persist(store, [replace(o, document=replace(o.document, document_hash="reread:" + o.document.document_hash)) for o in new])
    again = cards(store, "2026-10-02", "2026-10-02", indicators=["us_payrolls_change"])["cards"][0]
    assert len(again["revisions"]) == 2
    assert again["resolution"]["value"] == 29


@pytest.mark.parametrize("final_value", [3.8, 3.9])
def test_hicp_two_stages_and_flash_to_final_change(store, final_value):
    persist(store, [observation("ea_hicp_yy", "ea_hicp", value=3.8, source="Eurostat", stage="flash", edition="2026-09-30",
                               seen="2026-09-30T12:31:00Z", composition_id="EA20"),
                    observation("ea_hicp_yy", "ea_hicp", value=final_value, source="Eurostat", stage="final", edition="2026-10-16",
                                seen="2026-10-16T12:31:00Z", composition_id="EA20")])
    results = cards(store, "2026-09-30", "2026-10-17", indicators=["ea_hicp_yy"])["cards"]
    assert [c["stage"] for c in results] == ["flash", "final"]
    assert results[0]["stage_change"] is None
    change = results[1]["stage_change"]
    assert change["old"]["stage"] == "flash" and change["new"]["stage"] == "final"
    assert change["old"]["value"] == 3.8 and change["new"]["value"] == final_value
    assert change["changed"] is (final_value != 3.8)
    assert results[1]["revisions"] == []


def test_mirror_newer_issuer_pending_in_resolver_and_card(store):
    persist(store, [observation(edition="2026-10-13", value=3.6),
                    observation(edition="2026-10-14", value=3.7, source="FRED")])
    result = resolve(store, "us_cpi_headline_yy", "2026-09")
    assert result.status == "issuer_pending" and result.value == 3.7
    assert "issuer not yet captured" in result.reason
    assert result.discrepancies[0]["reason"] == "other_edition"
    card = cards(store, "2026-10-14", "2026-10-14")["cards"][0]
    assert card["resolution"]["status"] == "issuer_pending"
    assert store.coverage("2026-10-14", "2026-10-14", as_known_at=NOW)["families"]["us_cpi"]["issuer_pending"] == 1


@pytest.mark.parametrize("mirror_first", [True, False])
def test_older_or_same_edition_mirror_discrepancy_is_not_dispute(store, mirror_first):
    issuer = observation(value=3.7)
    mirror = observation(value=99, source="FRED")
    persist(store, [mirror, issuer] if mirror_first else [issuer, mirror])
    persist(store, [observation(value=98, source="FRED", edition="2026-10-13")])
    result = resolve(store, "us_cpi_headline_yy", "2026-09")
    assert result.status == "official" and result.value == 3.7
    assert {d["value"] for d in result.discrepancies} == {98, 99}


def test_equal_authority_conflict_is_disputed_and_tie_is_stable(store):
    evidence(store, source="Issuer-copy-B", value=3.8)
    evidence(store, source="Issuer-copy-A", value=3.7)
    result = resolve(store, "us_cpi_headline_yy", "2026-09")
    assert result.status == "disputed" and result.value == 3.7
    assert result.source["source_id"] == "Issuer-copy-A"
    assert result.discrepancies[0]["reason"] == "same_edition_conflict"
    assert result == resolve(store, "us_cpi_headline_yy", "2026-09")


@pytest.mark.parametrize("method", ["llm", "html", "llm_html"])
def test_unverified_issuer_extraction_against_structured_source_is_disputed(store, method):
    evidence(store, verified=False, method=method, value=3.8)
    evidence(store, source="FRED", role="mirror", verified=False, value=3.7)
    result = resolve(store, "us_cpi_headline_yy", "2026-09")
    assert result.status == "disputed" and result.value == 3.8


def test_scale_aliases_semantics_corrections_and_derived_exclusion(store):
    evidence(store, value=3.7)
    evidence(store, source="copy", unit="percent", value=.037, scale=100)
    evidence(store, source="wrong-geo", composition="EA21", value=99)
    evidence(store, source="wrong-unit", unit="persons", value=99)
    evidence(store, source="derived", value=99, formula_version="derive:v1")
    evidence(store, source="other-transform", indicator="us_cpi_headline_mm", value=99)
    result = resolve(store, "us_cpi_headline_yy", "2026-09")
    assert result.status == "official" and not result.discrepancies
    assert len(result.evidence_ids) == 2
    # A correction in one stream supersedes that stream's older figure.
    evidence(store, value=3.8, seen="2026-10-14T13:00:00Z")
    evidence(store, source="copy", unit="percent", value=.038, scale=100, seen="2026-10-14T13:00:00Z")
    assert resolve(store, "us_cpi_headline_yy", "2026-09").value == 3.8


def consensus(store, event, provider, value, captured="2026-10-14T12:00:00Z", **changes):
    data = dict(provider=provider, indicator_id="us_cpi_headline_yy", release_event_id=event,
                measure=INDICATORS["us_cpi_headline_yy"].measure, reference_period="2026-09", estimate_stage="regular",
                value=value, unit="%", captured_at=captured, release_cutoff_at="2026-10-14T12:30:00Z",
                cutoff_precision="exact", cutoff_basis="actual", content_hash=repr((provider, value, captured)))
    data.update(changes)
    store.put(ConsensusReading(**data))


def test_consensus_provider_freeze_no_averaging_ff_label_and_surprise(store):
    obs = observation()
    persist(store, [obs])
    event = "BLS:us_cpi:regular:2026-10-14"
    consensus(store, event, "Reuters", 3.5, captured="2026-10-14T11:00:00Z")
    consensus(store, event, "Reuters", 3.6)
    consensus(store, event, "Reuters", 99, captured="2026-10-14T12:31:00Z")
    consensus(store, event, "Forex Factory", 3.4)
    consensus(store, event, "Wrong unit", 100, unit="persons")
    consensus(store, event, "Wrong measure", 100, measure="core")
    consensus(store, event, "Wrong stage", 100, estimate_stage="flash")
    consensus(store, event, "At release", 100, captured="2026-10-14T12:30:00Z")
    card = cards(store, indicators=["us_cpi_headline_yy"])["cards"][0]
    assert [(c["provider"], c["value"]) for c in card["consensus"]] == [("Forex Factory", 3.4), ("Reuters", 3.6)]
    assert card["consensus"][0]["kind"] == "ff_forecast"
    assert [s["value"] for s in card["surprise"]] == pytest.approx([.3, .1])
    assert all(s["unit"] == "percentage_points" for s in card["surprise"])


def test_consensus_reconciles_new_exact_publication_time(store):
    persist(store, [observation()])
    event = "BLS:us_cpi:regular:2026-10-14"
    consensus(store, event, "Reuters", 3.6, release_cutoff_at="2026-10-14T00:00:00Z",
              cutoff_precision="date_only", cutoff_basis="scheduled")
    assert not store.conn.execute("SELECT pre_release_eligible FROM consensus_readings").fetchone()[0]
    assert cards(store)["cards"][0]["consensus"][0]["value"] == 3.6


def test_date_only_release_excludes_uncertain_same_day_forecast(store):
    persist(store, [replace(observation(), published_at="2026-10-14T00:00:00Z", precision="date_only")])
    consensus(store, "BLS:us_cpi:regular:2026-10-14", "Reuters", 3.6)
    card = cards(store)["cards"][0]
    assert card["consensus"] == [] and card["surprise"] is None


def test_as_known_at_filters_before_ranking_and_hides_late_archive(store):
    persist(store, [observation(seen="2026-10-16T00:00:00Z"),
                    observation(value=3.6, edition="2026-10-13", seen="2026-10-17T00:00:00Z")])
    assert resolve(store, "us_cpi_headline_yy", "2026-09", as_known_at="2026-10-15T00:00:00Z") is None
    early = cards(store, indicators=["us_cpi_headline_yy"], as_known_at="2026-10-15T00:00:00Z")
    assert not early["cards"] and early["empty"]
    assert resolve(store, "us_cpi_headline_yy", "2026-09", as_known_at=NOW).value == 3.7
    assert all(e.first_seen_at <= "2026-10-16T23:00:00.000000+00:00" for e in Snapshot.load(store, "2026-10-16T23:00:00Z").evidence)


def test_new_issuer_edition_cannot_inherit_old_verification(store):
    evidence(store, edition="2026-10-13", value=3.6)
    evidence(store, edition="2026-10-14", value=3.8, verified=False, method="llm")
    assert resolve(store, "us_cpi_headline_yy", "2026-09").status == "provisional"
    evidence(store, source="FRED", role="mirror", edition="2026-10-14", value=3.7, verified=False)
    result = resolve(store, "us_cpi_headline_yy", "2026-09")
    assert result.edition == "2026-10-14" and result.status == "disputed"
    assert any(d["edition"] == "2026-10-13" and d["reason"] == "other_edition" for d in result.discrepancies)


def test_unavailable_headline_does_not_promote_an_older_revision(store):
    persist(store, [observation(period="2026-09", value=None),
                    observation(period="2026-08", value=3.6, revision="routine")])
    result = cards(store, indicators=["us_cpi_headline_yy"])
    assert not result["cards"] and result["empty"][0]["reason"] == "published_not_resolved"
    count = store.coverage("2026-10-10", "2026-10-17", as_known_at=NOW)["families"]["us_cpi"]
    assert count["expected"] == count["missing"] == 1


def test_previous_does_not_backdate_late_capture_or_skip_period_gaps(store):
    persist(store, [observation(period="2026-08", value=3.6, edition="2026-09-14", seen="2026-10-15T00:00:00Z"),
                    observation(period="2026-07", value=3.5, edition="2026-08-14", seen="2026-08-14T12:31:00Z"),
                    observation()])
    card = cards(store)["cards"][-1]
    assert card["previous"] is None and card["previous_period"] == "2026-08"
    assert card["previous_missing_reason"]


def test_consensus_scale_conversion_and_missing_provider_snapshot(store):
    persist(store, [observation("us_payrolls_change", "us_payrolls", value=29)])
    store.put(ConsensusReading(provider="Survey", indicator_id="us_payrolls_change",
        release_event_id="BLS:us_payrolls:regular:2026-10-14", measure="us_payrolls_change",
        reference_period="2026-09", estimate_stage="regular", value=28000, unit="persons", scale=1,
        captured_at="2026-10-14T12:00:00Z", release_cutoff_at="2026-10-14T12:30:00Z",
        cutoff_precision="exact", cutoff_basis="actual", content_hash="test-scale"))
    card = cards(store)["cards"][0]
    assert card["consensus"][0]["normalized_value"] == 28
    assert card["surprise"][0]["value"] == 1 and card["surprise"][0]["scale"] == 1000


def test_schedule_versions_knowledge_and_future_missing_reason(store):
    schedule(store, expected="2026-10-16T12:30:00Z")
    schedule(store, expected="2026-10-20T12:30:00Z", recorded="2026-10-15T00:00:00Z")
    early = store.coverage("2026-10-10", "2026-10-17", as_known_at="2026-10-14T00:00:00Z")
    counts = early["families"]["us_cpi"]
    assert counts["expected"] == 1 and counts["missing_reasons"][0]["reason"] == "not_yet_released"
    later = store.coverage("2026-10-10", "2026-10-17", as_known_at=NOW)
    assert later["families"]["us_cpi"]["expected"] == 0


def test_geography_and_timestamp_window_bounds(store):
    persist(store, [observation()])
    result = cards(store, indicators=["us_cpi_headline_yy"], geography="GBR")
    assert not result["cards"] and "geography" in result["empty"][0]["detail"]
    result = cards(store, "2026-10-14T12:00:00Z", "2026-10-14T12:30:00Z", indicators=["us_cpi_headline_yy"])
    assert not result["cards"]  # until is exclusive for timestamps
    with pytest.raises(ValueError):
        cards(store, "2026-10-18", "2026-10-10")
    empty_selection = cards(store, indicators=[])
    assert not empty_selection["cards"] and empty_selection["empty"][0]["detail"] == "empty indicator selection"


def test_each_empty_reason_and_requested_indicator_attachment(store, legacy_calendar):
    api = ReadAPI(store)
    query = ("2026-10-10", "2026-10-17")
    assert api.explain_empty("not-in-catalogue", query, as_known_at=NOW)["reason"] == "not_covered"
    assert api.explain_empty("us_cpi_headline_yy", query, as_known_at=NOW)["reason"] == "no_release_scheduled"
    schedule(store, "2026-11-14", "2026-11-14T13:30:00Z")
    assert api.explain_empty("us_cpi_headline_yy", ("2026-11-01", "2026-11-30"), as_known_at=NOW)["reason"] == "not_yet_released"
    assert api.explain_empty("us_cpi_headline_yy", "2026-11", as_known_at=NOW)["reason"] == "not_yet_released"
    persist(store, [observation(value=None)])
    assert api.explain_empty("us_cpi_headline_yy", "2026-09", as_known_at=NOW)["reason"] == "published_not_resolved"
    with sqlite3.connect(legacy_calendar) as legacy:
        legacy.execute("UPDATE runs SET status='failed' WHERE run_id='cal_z'")
    import_calendar(store, legacy_calendar, captured_at="2026-10-18T01:00:00Z")
    indicator = store.conn.execute("SELECT indicator_id FROM legacy_candidates WHERE legacy_code='final'").fetchone()[0]
    failure = api.explain_empty(indicator, query, as_known_at=NOW)
    assert failure["reason"] == "source_failed"
    assert failure["family"].startswith("Eurostat:")
    assert failure["time"].startswith("2026-10-17") and failure["time_basis"] == "legacy_run_started_at"
    assert "not persisted" in failure["limitations"][0]
    empty = cards(store, indicators=["us_cpi_headline_yy", "unknown"])["empty"]
    assert {e["indicator_id"] for e in empty} == {"us_cpi_headline_yy", "unknown"}


def test_coverage_counts_events_missing_reasons_and_freshness(store):
    persist(store, [observation(), observation(indicator="us_cpi_core_yy", value=3.0)])
    schedule(store, "2026-10-15", "2026-10-15T12:30:00Z")
    schedule(store, "2026-10-16", "2026-10-16T12:30:00Z")
    persist(store, [observation(edition="2026-10-16", value=None)])
    result = store.coverage("2026-10-10", "2026-10-17", as_known_at=NOW)
    counts = result["families"]["us_cpi"]
    assert (counts["expected"], counts["captured"], counts["official"], counts["missing"]) == (3, 1, 1, 2)
    assert {r["reason"] for r in counts["missing_reasons"]} == {"published_not_resolved"}
    assert counts["latest_first_seen_at"].startswith("2026-10-14")
    assert counts["latest_published_at"].startswith("2026-10-14")


def test_cli_readonly_json_and_missing_db(store, capsys, tmp_path):
    persist(store, [observation()])
    db = Path(store.conn.execute("PRAGMA database_list").fetchone()[2])
    before = store.conn.total_changes
    args = ["--since", "2026-10-10", "--until", "2026-10-17", "--db", str(db), "--as-known-at", NOW, "--json"]
    assert main(["cards", *args, "--indicator", "us_cpi_headline_yy"]) == 0
    assert len(json.loads(capsys.readouterr().out)["cards"]) == 1
    assert main(["coverage", *args]) == 0
    assert json.loads(capsys.readouterr().out)["families"]["us_cpi"]["official"] == 1
    assert store.conn.total_changes == before
    with open_reader(db) as reader:
        assert reader.conn.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            reader.conn.execute("DELETE FROM indicators")
    missing = tmp_path / "missing.sqlite"
    with pytest.raises(SystemExit):
        main(["cards", *args[:4], "--db", str(missing)])
    assert not missing.exists()


def test_shadow_legacy_week_parity_report(store, legacy_calendar, tmp_path):
    # Extend the importer's synthetic schema-v4 fixture, never read a live file.
    with sqlite3.connect(legacy_calendar) as legacy:
        insert(legacy, "indicators", dict(code="cpi", jurisdiction="USA", name="CPI US y/y", agency="BLS",
               tier="T1", unit_code="percent", transform="pct_change", basis="yoy", frequency="M", scale=1,
               created_at="2026-01-01T00:00:00Z", created_by_run="seed"))
        insert(legacy, "occurrences", dict(id=3, indicator_code="cpi", period_norm="2026-09",
               release_date="2026-10-17", scheduled_at_utc="2026-10-17T12:30:00Z", created_by_run="cal_z"))
        add_legacy_observation(legacy, 3, "cal_z", status="released", value=3.7)
    source_bytes = legacy_calendar.read_bytes()
    import_calendar(store, legacy_calendar, captured_at="2026-10-18T01:00:00Z")
    result = cards(store)
    legacy_cards = [c for c in result["cards"] if c["release_event_id"].startswith("legacy:")]
    assert len(legacy_cards) == 2
    cpi = next(c for c in legacy_cards if c["indicator_name"] == "CPI US y/y")
    hicp = next(c for c in legacy_cards if c["stage"] == "final")
    assert cpi["resolution"]["value"] == 3.7
    assert hicp["resolution"]["value"] == 3.8 and hicp["indicator_name"] == "HICP y/y"
    assert cpi["legacy_consensus_evidence"][0]["value"] == 3.6
    assert all(c["resolution"]["status"] == "provisional" and c["resolution"]["published_at"] is None for c in legacy_cards)
    assert cpi["consensus"] == [] and cpi["surprise"] is None
    assert legacy_calendar.read_bytes() == source_bytes
    report = {"fixture": "legacy_calendar_v4.sql + synthetic CPI observation", "window": ["2026-10-10", "2026-10-17"],
              "cards": legacy_cards, "limitations": result["limitations"]}
    (tmp_path / "shadow-parity.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    # Frozen mappings learned after the cutoff must not leak backwards.
    assert not cards(store, as_known_at="2026-10-17T23:59:59Z")["cards"]
