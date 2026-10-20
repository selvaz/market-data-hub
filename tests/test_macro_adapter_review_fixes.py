"""Offline regression coverage for the M5 review findings."""
from dataclasses import replace
import json
from pathlib import Path
import sqlite3

import pytest
import requests

from market_data_hub.macro_releases import open_reader, open_writer
from market_data_hub.macro_releases import bea, bls, collect, ons
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.catalogue import hicp_indicator
from market_data_hub.macro_releases.eurostat import EurostatAdapter
from market_data_hub.macro_releases.http import Document, FetchError
from market_data_hub.macro_releases.migrations import migrate
from market_data_hub.macro_releases.models import utc
from test_macro_official_adapters import FIXTURES, document


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network forbidden in offline review regressions")
    monkeypatch.setattr(requests.Session, "request", forbidden)


HEADLINE_CASES = json.loads((FIXTURES / "eurostat/headline-cases.json").read_text())["cases"]


@pytest.mark.parametrize("case", HEADLINE_CASES)
def test_headline_ea21_june_and_september(case, tmp_path):
    rows = EurostatAdapter.parse(document(f"eurostat/headline-{case['stage']}-rates.response.json"),
                                 stage=case["stage"])
    row = next(o for o in rows if o.period == case["period"] and o.indicator_id.endswith(case["basis"]))
    assert row.value == case["value"]
    assert row.indicator_id == "ea21_hicp_" + case["basis"]
    assert row.composition_id == "EA21"
    with open_writer(tmp_path / "headline.sqlite") as store:
        persist(store, [row])
        official = store.latest_official(row.indicator_id)[0]
        assert official["value"] == case["value"]
        assert official["composition_id"] == "EA21"
        assert store.conn.execute("SELECT composition_id FROM readings").fetchone()[0] == "EA21"


def test_evolving_composition_and_fixed_golden_identity(tmp_path):
    assert hicp_indicator("2025-12", "yy").geography == "EA20"
    assert hicp_indicator("2026-01", "yy").geography == "EA21"
    assert hicp_indicator("2027-01", "yy").geography == "EA21"
    assert hicp_indicator("2027-01", "yy", "EA22").geography == "EA_PENDING"
    rows = []
    for stage in ("flash", "final"):
        rows += EurostatAdapter.parse(document(f"eurostat/{stage}-rates.json"), stage=stage, geography="EA20")
        rows += EurostatAdapter.parse(document(f"eurostat/headline-{stage}-rates.response.json"), stage=stage)
    with open_writer(tmp_path / "compositions.sqlite") as store:
        persist(store, rows)
        june = [r for r in store.latest_official() if r["reference_period"] == "2026-06" and r["indicator_id"].endswith("yy")]
        assert {r["composition_id"]: r["value"] for r in june} == {"EA20": 2.7, "EA21": 2.8}
        ea21_june = store.conn.execute("SELECT value FROM value_vintages WHERE indicator_id='ea21_hicp_yy' AND reference_period='2026-06'").fetchall()
        assert [r[0] for r in ea21_june] == [2.8, 2.8]


def payroll_document(newest="2026-01", old_value="100"):
    year, month = newest.split("-")
    payload = {"status": "REQUEST_SUCCEEDED", "Results": {"series": [
        {"seriesID": "CES0000000001", "data": [
            {"year": year, "period": "M" + month, "value": "160000", "calculations": {"net_changes": {"1": "29"}}},
            {"year": "2021", "period": "M01", "value": "140000", "calculations": {"net_changes": {"1": old_value}}},
        ]}
    ]}}
    return Document(json.dumps(payload).encode(), "https://api.bls.gov/synthetic", "2026-10-17T19:00:00Z", "synthetic:" + newest + old_value)


@pytest.mark.parametrize("edition,newest", [("2026-11-06", "2026-10"), ("2026-02-06", "2026-01")])
@pytest.mark.parametrize("provider", ["bls", "bea"])
def test_winter_release_local_time_and_utc(edition, newest, provider, monkeypatch, tmp_path):
    if provider == "bls":
        monkeypatch.setitem(bls.EDITIONS, ("us_payrolls", newest), edition)
        rows = bls.BLSAdapter(None).parse(payroll_document(newest), ["us_payrolls"], newest)
    else:
        doc = document("bea/nipa.json")
        payload = doc.json()
        for note in payload["BEAAPI"]["Results"]["Notes"]:
            if note.get("NoteRef") == bea.TABLE:
                note["NoteText"] = "LastRevised: " + ("November 6, 2026" if edition[5:7] == "11" else "February 6, 2026")
        monkeypatch.setitem(bea.EDITIONS, edition, ("2026-Q2", "third"))
        rows = bea.BEAAdapter.parse(replace(doc, body=json.dumps(payload).encode()), since="2026-06")
    assert rows
    assert all(o.published_at == edition + "T13:30:00+00:00" for o in rows)
    assert all(o.issuer_local_time == edition + "T08:30:00-05:00" for o in rows)
    assert all(o.issuer_timezone == "America/New_York" and o.precision == "exact" for o in rows)
    with open_writer(tmp_path / "winter.sqlite") as store:
        persist(store, rows)
        row = store.latest_official()[0]
        assert row["issuer_published_at"] == utc(edition + "T13:30:00Z")
        assert row["issuer_local_time"] == edition + "T08:30:00-05:00"
        assert row["issuer_timezone"] == "America/New_York"
        assert store.conn.execute("SELECT issuer_local_time FROM readings LIMIT 1").fetchone()[0] == row["issuer_local_time"]


def test_summer_release_stays_1230z():
    rows = bls.BLSAdapter(None).parse(document("bls/cpi-ces.json"), ["us_cpi", "us_payrolls"], "2026-09")
    assert all("T12:30:00+00:00" in o.published_at and "T08:30:00-04:00" in o.issuer_local_time for o in rows)


def test_february_benchmark_revises_five_complete_years(monkeypatch, tmp_path):
    monkeypatch.setitem(bls.EDITIONS, ("us_payrolls", "2026-01"), "2026-02-06")
    assert bls.revision_start("us_payrolls", "2026-01", "2026-01", "2026-02-06") == "2021-01"
    class Replay:
        def request(self, provider, url, **kwargs):
            assert int(kwargs["payload"]["startyear"]) <= 2021
            return payroll_document(old_value="75")
    monkeypatch.setattr(bls, "api_key", lambda _: "synthetic-key")
    rows = bls.BLSAdapter(Replay()).fetch(["us_payrolls"], "2026-01")
    old = next(o for o in rows if o.period == "2021-01")
    assert old.revision == "benchmark"
    with open_writer(tmp_path / "benchmark.sqlite") as store:
        persist(store, [replace(old, value=100, printed="100", edition="2026-01-09", revision="backfill")])
        persist(store, rows)
        revised = next(r for r in store.latest_official() if r["reference_period"] == "2021-01")
        assert revised["value"] == 75 and revised["revision_kind"] == "benchmark"
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages WHERE reference_period='2021-01'").fetchone()[0] == 2
        assert not [r for r in store.first_release() if r["reference_period"] == "2021-01"]


def test_backfill_not_first_print_and_changed_value_or_edition_only(tmp_path):
    rows = bls.BLSAdapter(None).parse(document("bls/cpi-ces.json"), ["us_payrolls"], "2026-07")
    history = next(o for o in rows if o.period == "2026-08")
    with open_writer(tmp_path / "backfill.sqlite") as store:
        persist(store, rows)
        assert {r["reference_period"] for r in store.first_release()} == {"2026-09"}
        row = store.conn.execute("SELECT revision_kind FROM value_vintages WHERE reference_period='2026-08'").fetchone()
        assert row[0] == "backfill"
        before = store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0]
        reread = replace(history, document=replace(history.document, document_hash="new-transport", retrieved_at="2026-10-18T19:00:00Z"))
        persist(store, [reread])
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == before
        changed = replace(reread, value=134, printed="134", document=replace(reread.document, document_hash="changed-value"))
        persist(store, [changed])
        persist(store, [replace(changed, edition="2026-11-06")])
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == before + 2
        assert not [r for r in store.first_release() if r["reference_period"] == "2026-08"]


def test_hicp_flash_then_final_is_routine_revision(tmp_path):
    rows = []
    for stage in ("flash", "final"):
        rows += [o for o in EurostatAdapter.parse(
            document(f"eurostat/headline-{stage}-rates.response.json"), stage=stage)
            if o.period == "2026-08" and o.indicator_id == "ea21_hicp_yy"]
    assert [(o.stage, o.value) for o in rows] == [("flash", 3.3), ("final", 3.2)]
    with open_writer(tmp_path / "hicp-revision.sqlite") as store:
        persist(store, rows[:1])
        persist(store, rows[1:])
        final = store.latest_official("ea21_hicp_yy")[0]
        assert final["estimate_stage"] == "final" and final["value"] == 3.2
        assert final["revision_kind"] == "routine"
        assert store.first_release("ea21_hicp_yy")[0]["estimate_stage"] == "flash"


@pytest.mark.parametrize("path", ["api", "pdf"])
def test_bea_stage_revisions_have_same_kind_on_api_and_pdf(path, tmp_path):
    advance = bea.BEAAdapter.parse_archived_release(document("bea/advance-release.pdf"))
    if path == "pdf":
        second = bea.BEAAdapter.parse_archived_release(document("bea/second-release.pdf"))
    else:
        # Exercise the API parser for the certified second edition with synthetic
        # metadata/value edits to the saved current response, not a live API call.
        doc = document("bea/nipa.json")
        payload = doc.json()
        for note in payload["BEAAPI"]["Results"]["Notes"]:
            if note.get("NoteRef") == bea.TABLE:
                note["NoteText"] = "LastRevised: August 26, 2026"
        for row in payload["BEAAPI"]["Results"]["Data"]:
            if row["LineNumber"] == "1" and row["TimePeriod"] == "2026Q2":
                row["DataValue"] = "1.5"
        second = bea.BEAAdapter.parse(replace(doc, body=json.dumps(payload).encode()), since="2026-06")
    third = bea.BEAAdapter.parse(document("bea/nipa.json"), since="2026-06")
    with open_writer(tmp_path / "bea-stages.sqlite") as store:
        for rows in (advance, second, third):
            persist(store, rows)
        vintages = store.conn.execute(
            "SELECT estimate_stage,value,revision_kind FROM value_vintages ORDER BY issuer_published_at").fetchall()
        assert [tuple(r) for r in vintages] == [
            ("advance", 1.5, "initial"), ("second", 1.5, "routine"), ("third", 2.2, "routine")]
        assert store.first_release()[0]["estimate_stage"] == "advance"


@pytest.mark.parametrize("source", ["hicp", "bea_api", "bea_pdf", "ons"])
@pytest.mark.parametrize("mirror_first", [False, True])
def test_first_issuer_history_load_is_backfill_even_after_fred(source, mirror_first, tmp_path):
    if source == "hicp":
        rows = EurostatAdapter.parse(document("eurostat/headline-final-rates.response.json"), stage="final")
    elif source == "bea_api":
        rows = bea.BEAAdapter.parse(document("bea/nipa.json"), since="2026-06")
    elif source == "bea_pdf":
        rows = bea.BEAAdapter.parse_archived_release(document("bea/second-release.pdf"))
    else:
        rows = ons.ONSAdapter.parse_levels(document("ons/gdp69-csv.csv"), document("ons/gdp69.json").json(),
                                           "2026-06", publication=document("ons/june-content.response.json"))
    with open_writer(tmp_path / "first-load.sqlite") as store:
        if mirror_first:
            # Synthetic prior mirror for the exact measure/period/stage. It must
            # not supply issuer history, even though its issuer_id is the issuer.
            persist(store, [replace(o, source="FRED", series_key="synthetic-mirror:" + o.series_key,
                                    edition="2026-07-01", published_at="2026-07-01T00:00:00Z",
                                    revision="provider_history") for o in rows])
        ids = persist(store, rows)
        assert ids
        assert {r[0] for r in store.conn.execute(
            "SELECT v.revision_kind FROM value_vintages v JOIN source_series s ON s.id=v.source_series_id "
            "WHERE s.evidence_role='issuer'")} == {"backfill"}
        assert store.latest_official() and not store.first_release()


def test_later_publication_does_not_revise_an_older_archive(tmp_path):
    third = bea.BEAAdapter.parse(document("bea/nipa.json"), since="2026-06")
    advance = bea.BEAAdapter.parse_archived_release(document("bea/advance-release.pdf"))
    with open_writer(tmp_path / "reverse-editions.sqlite") as store:
        persist(store, third)
        persist(store, advance)
        assert store.latest_official()[0]["revision_kind"] == "backfill"
        assert store.first_release()[0]["revision_kind"] == "initial"
        assert store.first_release()[0]["estimate_stage"] == "advance"


@pytest.mark.parametrize("changed", ["version", "bulletin", "empty"])
def test_ons_new_publications_fail_before_values(changed):
    calls = []
    class Replay:
        def get(self, provider, url):
            calls.append(url)
            if url == ons.EDITION_URL:
                doc = document("ons/current-edition.response.json")
                if changed == "version":
                    payload = doc.json()
                    payload["links"]["latest_version"]["id"] = "70"
                    doc = replace(doc, body=json.dumps(payload).encode())
                return doc
            assert url == ons.BULLETIN_INDEX_URL
            doc = document("ons/current-bulletins.response.json")
            if changed == "bulletin":
                doc = replace(doc, body=doc.body + b'<a href="/gdpmonthlyestimateuk/september2026">New bulletin</a>')
            elif changed == "empty":
                doc = replace(doc, body=b"<html>Unexpected index</html>")
            return doc
    with pytest.raises(FetchError, match="needs review"):
        ons.ONSAdapter(Replay()).fetch(["uk_gdp"], "2026-06")
    assert calls == [ons.EDITION_URL, ons.BULLETIN_INDEX_URL]


def test_ons_publication_date_and_csv_no_redirect_capture(tmp_path):
    ons.check_freshness(document("ons/current-edition.response.json"), document("ons/current-bulletins.response.json"))
    rows = ons.ONSAdapter.parse_levels(document("ons/gdp69-csv.csv"), document("ons/gdp69.json").json(),
                                     "2026-06", publication=document("ons/june-content.response.json"))
    assert {o.published_at for o in rows} == {utc("2026-08-24T00:00:00Z")}
    assert {o.edition for o in rows} == {"2026-08-24"}
    meta = json.loads((FIXTURES / "ons/gdp69-csv.meta.json").read_text())
    assert meta["url"] == ons.CSV_URL
    assert meta["status_code"] == 200 and meta["location"] is None
    original = json.loads((FIXTURES / "ons/csv-original-url.response.json").read_text())["metadata"]
    assert original["status_code"] == 301 and original["location"] == ons.CSV_URL
    with open_writer(tmp_path / "levels.sqlite") as store:
        persist(store, rows)
        assert not store.first_release()
        assert store.latest_official()[0]["issuer_published_at"] == utc("2026-08-24T00:00:00Z")


@pytest.mark.parametrize("failure_point", ["fetch", "persist"])
def test_cli_unexpected_exception_is_atomic_and_other_family_runs(failure_point, monkeypatch, tmp_path, capsys):
    bls_rows = bls.BLSAdapter(None).parse(document("bls/cpi-ces.json"), ["us_cpi"], "2026-09")
    bea_rows = bea.BEAAdapter.parse(document("bea/nipa.json"), since="2026-06")
    def fetch(self, families, since):
        if failure_point == "fetch":
            raise RuntimeError("Unexpected synthetic failure")
        return bls_rows
    monkeypatch.setattr(bls.BLSAdapter, "fetch", fetch)
    monkeypatch.setattr(bea.BEAAdapter, "fetch", lambda *_: bea_rows)
    def interrupted_persist(store, rows):
        if rows[0].family == "us_cpi":
            persist(store, rows[:1])  # Fault after real inserts in several tables.
            raise RuntimeError("Unexpected synthetic write failure")
        return persist(store, rows)
    monkeypatch.setattr(collect, "persist", interrupted_persist)
    target = tmp_path / "isolated.sqlite"
    assert collect.main(["--db", str(target), "--families", "us_cpi,us_gdp", "--since", "2026-06"]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["failures"] == {"BLSAdapter": "RuntimeError"}
    assert output["family_failures"] == {"us_cpi": ["BLSAdapter"]}
    assert set(output["families"]) == {"BEAAdapter"}
    with open_reader(target) as store:
        assert {r["indicator_id"] for r in store.latest_official()} == {"us_gdp_qq_saar"}
        for table in ("indicators", "release_events", "source_series", "readings"):
            assert store.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE " +
                                      ("issuer_id" if table in ("indicators", "release_events", "source_series") else "source_id") + "='BLS'").fetchone()[0] == 0


def test_v2_migration_adds_provenance_and_filters_legacy_archive(tmp_path):
    from market_data_hub.macro_releases import migrations
    path = tmp_path / "v2.sqlite"
    conn = sqlite3.connect(path, isolation_level=None)
    conn.executescript(Path(migrations.__file__).with_name("schema.sql").read_text())
    conn.execute("PRAGMA user_version=2")
    migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
    assert {"composition_id", "issuer_local_time", "issuer_timezone"}.issubset(
        {r[1] for r in conn.execute("PRAGMA table_info(value_vintages)")})
    conn.close()
    row = bls.BLSAdapter.parse_payroll_publication(document("bls/august-web.json"), "2026-09-04", "2026-08")[0]
    with open_writer(path) as store:
        # Reproduce a v2 archive classification without changing captured bytes.
        persist(store, [replace(row, series_key="CES0000000001:archive-change", method="html")])
        assert store.conn.execute("SELECT verification_status FROM value_vintages").fetchone()[0] == "verified"
        assert not store.latest_official() and not store.first_release()
