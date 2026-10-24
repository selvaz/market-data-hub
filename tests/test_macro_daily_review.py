"""Round-one regressions: dynamic issuer editions, review routing and truthfulness."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest
import requests

from market_data_hub.macro_releases import open_reader, open_writer
from market_data_hub.macro_releases import discovery, run_daily
from market_data_hub.macro_releases import migrations
from market_data_hub.macro_releases.__main__ import main as read_main
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.catalogue import hicp_indicator
from market_data_hub.macro_releases.http import HttpClient, retry_delay
from market_data_hub.macro_releases.lease import LeaseBusy, RunLease
from market_data_hub.macro_releases.verification import SANITY_BANDS
from market_data_hub.macro_releases.verification import configured_bands
from test_macro_official_adapters import document, observations as observations

# This harness synthesizes next editions of the original certified pilot only.
# M9a families have their own saved-response daily replay in test_macro_us_monthly.
FAMILIES = ("us_cpi", "us_payrolls", "us_gdp", "ea_hicp", "uk_gdp")


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("Live network forbidden")
    monkeypatch.setattr(requests.Session, "request", forbidden)
    monkeypatch.setattr(run_daily.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(run_daily, "FAMILIES", FAMILIES)
    monkeypatch.setattr(discovery, "api_key", lambda _: "fake-key")


def changed(doc, body, url=None):
    if isinstance(body, dict) or isinstance(body, list):
        body = json.dumps(body).encode()
    elif isinstance(body, str):
        body = body.encode()
    return replace(doc, body=body, document_hash=hashlib.sha256(body).hexdigest(), url=url or doc.url,
                   retrieved_at="2026-12-01T12:00:00Z")


def next_documents(family, mode="ok"):
    """Synthetic next editions derived from saved bodies; no claimed live capture."""
    docs = {}
    if family in ("us_cpi", "us_payrolls"):
        doc = document("bls/cpi-ces.json")
        payload = doc.json()
        keys = [k for k, (indicator, _, _) in discovery.BLS_SERIES.items()
                if ("us_payrolls" if indicator.startswith("us_payrolls") else "us_cpi") == family]
        payload["Results"]["series"] = [s for s in payload["Results"]["series"] if s["seriesID"] in keys]
        for series in payload["Results"]["series"]:
            for row in series["data"]:
                original = row["year"] + "-" + row["period"][1:]
                if row["period"] == "M09" and row["year"] == "2026":
                    if mode == "band":
                        for values in row.get("calculations", {}).values():
                            for k in values:
                                values[k] = "50000"
                    if mode == "structure":
                        row["calculations"] = {}
                if re.fullmatch(r"M(0[1-9]|1[0-2])", row["period"]):
                    period = discovery.bls.shift_month(original, 1)
                    row["year"], row["period"] = period[:4], "M" + period[5:]
        docs["data"] = changed(doc, payload)
        title = "Consumer Price Index" if family == "us_cpi" else "Employment Situation"
        day = "12" if family == "us_cpi" else "06"
        docs["listing"] = changed(doc, f'<rss><channel><item><title>{title} for October 2026</title>'
            f'<pubDate>Thu, {day} Nov 2026 08:30:00 -0500</pubDate></item></channel></rss>',
            discovery.BLS_FEEDS[family])
    elif family == "us_gdp":
        doc = document("bea/nipa.json")
        payload = doc.json()
        results = payload["BEAAPI"]["Results"]
        for note in results["Notes"]:
            note["NoteText"] = note["NoteText"].replace("September 30, 2026", "October 29, 2026")
        for row in results["Data"]:
            if row["LineNumber"] == "1" and row["TimePeriod"] == "2026Q2":
                row["TimePeriod"] = "2026Q3"
                row["DataValue"] = "80" if mode == "band" else "2.4"
                if mode == "structure":
                    row["CL_UNIT"] = "Index"
        docs["data"] = changed(doc, payload)
        docs["listing"] = changed(doc, '<table><tr><td>GDP (Advance Estimate), 3rd Quarter 2026</td>'
            '<td><time datetime="2026-10-29T08:30:00-04:00">October 29, 2026</time></td></tr></table>', discovery.BEA_LISTING)
    elif family == "ea_hicp":
        for stage in ("flash", "final"):
            doc = document(f"eurostat/headline-{stage}-rates.response.json")
            payload = doc.json()
            times = payload["dimension"]["time"]["category"]["index"]
            payload["dimension"]["time"]["category"]["index"] = {
                discovery.bls.shift_month(p, 1): i for p, i in times.items()}
            if mode == "structure":
                payload["dimension"]["freq"]["category"]["index"] = {"Q": 0}
            if mode == "band":
                payload["value"] = {k: 80 for k in payload["value"]}
            docs[stage] = changed(doc, payload)
        docs["listing"] = document("discovery/eurostat-events.response.json")
    else:
        meta = document("ons/gdp69.json")
        payload = meta.json()
        payload["version"] = 70
        docs["meta"] = changed(meta, payload)
        edition = document("ons/current-edition.response.json")
        payload = edition.json()
        payload["links"]["latest_version"]["id"] = "70"
        docs["edition"] = changed(edition, payload)
        docs["listing"] = changed(meta, ''.join(f'<a href="{discovery.ons.BULLETIN_BASE}{slug}">{slug}</a>'
            for slug in ("july2026", "august2026", "september2026")), discovery.ons.BULLETIN_INDEX_URL)
        for slug in ("july", "august"):
            docs[slug] = document(f"ons/{slug}-content.html")
        doc = document("ons/august-content.html")
        body = doc.body.decode().replace("August 2026", "September 2026").replace("July 2026", "August 2026").replace("June 2026", "July 2026")
        body = body.replace('"2026/10/15"', '"2026/11/12"')
        if mode == "band":
            body = re.sub(r"(Monthly GDP grew by )[-\d.]+%", r"\g<1>80%", body)
        docs["september"] = changed(doc, body, discovery.ons.BULLETIN_BASE + "september2026")
        doc = document("ons/gdp69-csv.csv")
        # Keep a small valid CSV whose latest reference month advances to July.
        import csv
        from io import StringIO
        rows = list(csv.DictReader(StringIO(doc.body.decode("utf-8-sig"))))
        fields = list(rows[0])
        rows = [r for r in rows if r["mmm-yy"] in ("May-26", "Jun-26") and r["sic-unofficial"] == "A--T"]
        for row in rows:
            row["mmm-yy"] = {"May-26": "Jun-26", "Jun-26": "Jul-26"}[row["mmm-yy"]]
        output = StringIO()
        writer = csv.DictWriter(output, fields)
        writer.writeheader()
        writer.writerows(rows)
        docs["csv"] = changed(doc, output.getvalue().replace("v4_0", "v5_0") if mode == "structure" else output.getvalue())
    return docs


class Replay:
    def __init__(self, family, mode="ok"):
        self.family = family
        self.docs = next_documents(family, mode)

    def request(self, *args, **kwargs):
        return self.docs["data"]

    def get(self, provider, url, params=None):
        if provider in ("bls", "bea"):
            return self.docs["listing" if url in (discovery.BEA_LISTING, *discovery.BLS_FEEDS.values()) else "data"]
        if provider == "eurostat":
            return self.docs["listing" if url == discovery.EUROSTAT_CALENDAR else "flash" if url.endswith("prc_hicp_fpd") else "final"]
        if url == discovery.ons.EDITION_URL:
            return self.docs["edition"]
        if url == discovery.ons.BULLETIN_INDEX_URL:
            return self.docs["listing"]
        if url.endswith(".csv"):
            return self.docs["csv"]
        if "/versions/" in url:
            return self.docs["meta"]
        return self.docs[url.rsplit("/", 1)[-1].replace("2026", "")]


def seed(db, observations, family=None):
    with open_writer(db) as store:
        persist(store, [o for o in observations if family is None or o.family == family])
        if family in (None, "ea_hicp"):
            from market_data_hub.macro_releases.eurostat import EurostatAdapter
            for stage in ("flash", "final"):
                persist(store, EurostatAdapter.parse(document(f"eurostat/headline-{stage}-rates.response.json"), stage=stage))


def collect(db, client, family):
    # Use the same runner fetch/gate/persist path with a fixed synthetic window.
    return run_daily.collect_family(lambda: open_writer(db), client, "test-run", family,
        {family: {"fetch_since": "2026-06", "fetch_until": "2026-12-31"}}, dry_run=True)


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("mode", ["ok", "structure", "band"])
def test_next_edition_verification_and_review_queue(tmp_path, observations, family, mode):
    db = tmp_path / "macro.sqlite"
    seed(db, observations, family)
    result = collect(db, Replay(family, mode), family)[family]
    assert result["status"] == ("ok" if mode == "ok" else "needs_review"), result
    with open_reader(db) as reader:
        queue = reader.conn.execute("SELECT * FROM review_queue").fetchall()
        if mode == "ok":
            assert result["vintages_added"] > 0 and not queue
        else:
            assert result["vintages_added"] == 0 and queue
            assert reader.conn.execute("SELECT COUNT(*) FROM readings WHERE verification_status='unverified'").fetchone()[0] > 0
            if mode == "band":
                assert any("sanity" in reason for reason in result["review_reasons"])


def test_all_families_need_review_is_not_failed(tmp_path, observations, monkeypatch, capsys):
    db = tmp_path / "macro.sqlite"
    seed(db, observations)
    def fetch(client, family, since):
        return discovery.fetch_daily(Replay(family, "band"), family, "2026-06")
    monkeypatch.setattr(run_daily, "fetch_daily", fetch)
    assert run_daily.main(["--db", str(db)]) == 3
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "needs_review"
    assert all(r["status"] == "needs_review" for r in summary["families"].values())
    assert read_main(["review-queue", "--db", str(db), "--family", "us_cpi"]) == 0
    assert json.loads(capsys.readouterr().out)["review_queue"]


def test_ons_ray_replay_four_days_has_no_growth(tmp_path, observations, monkeypatch, capsys):
    original = [o for o in observations if o.family == "uk_gdp"]
    day = SimpleNamespace(value=0)
    class Adapter:
        def __init__(self, client):
            pass

        def fetch(self, families, since):
            out = []
            for obs in original:
                body = re.sub(rb"r:'[a-f0-9]+'", ("r:'" + f"{day.value:016x}" + "'").encode(), obs.document.body)
                out.append(replace(obs, document=changed(obs.document, body)))
            return out
    monkeypatch.setitem(run_daily.ADAPTERS, "uk_gdp", Adapter)
    db = tmp_path / "macro.sqlite"
    counts = None
    for i in range(4):
        day.value = i
        assert run_daily.main(["--db", str(db), "--families", "uk_gdp"]) == (0 if i == 0 else 3)
        result = json.loads(capsys.readouterr().out)["families"]["uk_gdp"]
        if i:
            assert result["readings_added"] == result["vintages_added"] == 0
        with open_reader(db) as reader:
            current = tuple(reader.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                            for table in ("readings", "value_vintages", "documents"))
            if counts:
                assert current == counts
            counts = current
            assert max(len(r[0]) for r in reader.conn.execute("SELECT raw_payload_json FROM readings")) < 300


@pytest.mark.parametrize("body", ["", '{"token":"cutoff"'])
def test_corrupt_lease_recovers_and_logs(tmp_path, observations, monkeypatch, capsys, body):
    db = tmp_path / "macro.sqlite"
    lease = Path(str(db) + ".daily.lease.json")
    lease.write_text(body)
    os.utime(lease, (0, 0))
    class Adapter:
        def __init__(self, client):
            pass

        def fetch(self, *args):
            return [o for o in observations if o.family == "us_cpi"]
    monkeypatch.setitem(run_daily.ADAPTERS, "us_cpi", Adapter)
    assert run_daily.main(["--db", str(db), "--families", "us_cpi"]) == 0
    capsys.readouterr()
    with open_reader(db) as reader:
        parameters = json.loads(reader.conn.execute("SELECT parameters FROM collect_runs LIMIT 1").fetchone()[0])
        assert parameters["lease_events"] == ["corrupt_lease_reclaimed_using_mtime"]


def test_fresh_corrupt_lease_respects_mtime_grace(tmp_path):
    db = tmp_path / "macro.sqlite"
    Path(str(db) + ".daily.lease.json").write_text("")
    with pytest.raises(LeaseBusy):
        with RunLease(db):
            pass


def test_renew_waits_for_writer_contention_and_fsync(tmp_path, monkeypatch):
    db = tmp_path / "macro.sqlite"
    fsync = []
    original = os.fsync
    monkeypatch.setattr("market_data_hub.macro_releases.lease.os.fsync", lambda fd: (fsync.append(fd), original(fd))[-1])
    errors = []
    with RunLease(db) as lease:
        def renew():
            try:
                lease.renew()
            except Exception as exc:
                errors.append(exc)
        with lease.guard():
            thread = threading.Thread(target=renew)
            thread.start()
            time.sleep(.05)
            assert thread.is_alive()
        thread.join(5)
        assert not thread.is_alive() and not errors and len(fsync) >= 2


def test_retry_after_and_exponential_backoff(tmp_path, monkeypatch):
    clock = SimpleNamespace(value=1000)
    sleeps, calls = [], []
    def sleep(seconds):
        sleeps.append(seconds)
        clock.value += seconds
    monkeypatch.setattr("market_data_hub.macro_releases.http.time.time", lambda: clock.value)
    monkeypatch.setattr("market_data_hub.macro_releases.http.time.sleep", sleep)
    statuses = [429, 503, 200]
    def request(*args, **kwargs):
        status = statuses[len(calls)]
        calls.append(clock.value)
        return SimpleNamespace(status_code=status, headers={"Retry-After": "5"} if status == 429 else {}, content=b"{}")
    client = HttpClient(session=SimpleNamespace(request=request), rate_directory=tmp_path)
    client.get("ons", "https://www.ons.gov.uk/example")
    assert sleeps == [5, 2] and calls == [1000, 1005, 1007]
    response = SimpleNamespace(headers={"Retry-After": "Thu, 01 Jan 1970 00:20:00 GMT"})
    assert retry_delay(response, 0, clock=lambda: 1000) == 200


def test_future_area_placeholder_and_band_configuration():
    assert hicp_indicator("2027-01", "yy").geography == "EA21"
    assert hicp_indicator("2028-01", "yy", "EA22").geography == "EA_PENDING"
    assert SANITY_BANDS["us_payrolls_change"] == 1500
    assert run_daily.DEFAULT_LOOKBACK_MONTHS == 24


@pytest.mark.parametrize("version", [3, 4, 5, 6])
def test_reader_range_forward_backward_and_read_api(tmp_path, version):
    db = tmp_path / "compatible.sqlite"
    module = Path(migrations.__file__).parent
    with sqlite3.connect(db) as conn:
        for script in (
            "schema.sql",
            "migration_003.sql",
            *(["migration_004.sql"] if version >= 4 else []),
            *(["migration_005.sql"] if version >= 5 else []),
            *(["migration_006.sql"] if version >= 6 else []),
        ):
            conn.executescript((module / script).read_text())
        conn.execute(f"PRAGMA user_version={version}")
    before = db.read_bytes()
    with open_reader(db) as reader:
        assert reader.conn.execute("PRAGMA user_version").fetchone()[0] == version
        assert reader.latest_official() == reader.first_release() == []
        assert reader.release_cards("2026-10-01", "2026-10-17")["cards"] == []
        assert reader.coverage("2026-10-01", "2026-10-17")["families"]["us_cpi"]["collection_failures"] == []
        assert reader.explain_empty("us_cpi_headline_yy", "2026-09")["reason"] == "schedule_unknown"
    assert db.read_bytes() == before  # readers neither migrate nor rewrite


def test_writer_remains_strict_and_incompatible_readers_reject(tmp_path, monkeypatch):
    db = tmp_path / "strict.sqlite"
    with open_writer(db):
        pass
    # This is the same newer-schema refusal as the existing v3 writer.
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "SCHEMA_VERSION", 3)
        with pytest.raises(RuntimeError, match="unsupported"):
            open_writer(db)
    with open_writer(db) as store:
        store.conn.execute("UPDATE schema_compatibility SET reader_floor=4")
    with pytest.raises(RuntimeError, match="not additive"):
        open_reader(db)
    with open_writer(db) as store:
        store.conn.execute(f"PRAGMA user_version={migrations.SCHEMA_VERSION + 1}")
    with pytest.raises(RuntimeError, match="compatibility range"):
        open_reader(db)
    with pytest.raises(RuntimeError, match="unsupported"):
        open_writer(db)


def test_migration_from_original_v4_keeps_run_log(tmp_path):
    db = tmp_path / "v4.sqlite"
    module = Path(migrations.__file__).parent
    with sqlite3.connect(db) as conn:
        for script in ("schema.sql", "migration_003.sql", "migration_004.sql"):
            conn.executescript((module / script).read_text())
        conn.execute("PRAGMA user_version=4")
        conn.execute("INSERT INTO collect_runs VALUES ('old','2026-10-01',NULL,'running','[]','{}','0.3.0','host')")
        conn.execute("INSERT INTO collect_runs VALUES ('old','2026-10-01','2026-10-02','no_change','[]','{}','0.3.0','host')")
        conn.execute("INSERT INTO collect_run_families VALUES ('old','running','us_cpi','no_change','2026-10-02',NULL,NULL,0,0,4,'2026-01','2026-10-02')")
        original = conn.execute("SELECT * FROM collect_runs ORDER BY status").fetchall()
        families = conn.execute("SELECT * FROM collect_run_families").fetchall()
    with open_writer(db) as store:
        assert [tuple(r) for r in store.conn.execute("SELECT * FROM collect_runs ORDER BY status")] == original
        assert [tuple(r) for r in store.conn.execute("SELECT * FROM collect_run_families")] == families
        assert store.conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_transport_only_changes_are_skipped_and_do_not_report_new_data(tmp_path, observations, monkeypatch, capsys):
    day = SimpleNamespace(value=0)
    class Adapter:
        def __init__(self, client):
            pass

        def fetch(self, *args):
            obs = next(o for o in observations if o.family == "us_cpi" and o.value is not None)
            # Changed transport bytes retain the same observation semantics.
            return [replace(obs, document=replace(obs.document, document_hash=f"new-raw-{day.value}"))]
    monkeypatch.setitem(run_daily.ADAPTERS, "us_cpi", Adapter)
    db = tmp_path / "macro.sqlite"
    assert run_daily.main(["--db", str(db), "--families", "us_cpi"]) == 0
    capsys.readouterr()
    day.value = 1
    assert run_daily.main(["--db", str(db), "--families", "us_cpi"]) == 3
    result = json.loads(capsys.readouterr().out)["families"]["us_cpi"]
    assert result["status"] == "no_change" and result["readings_added"] == 0 and result["vintages_added"] == 0
    assert result["readings_skipped"] == 1


def test_publication_calendar_mismatch_goes_to_review(tmp_path, observations):
    db = tmp_path / "macro.sqlite"
    seed(db, observations, "us_gdp")
    client = Replay("us_gdp")
    client.docs["listing"] = changed(client.docs["listing"], client.docs["listing"].body.replace(b"2026-10-29T", b"2026-10-30T"))
    result = collect(db, client, "us_gdp")["us_gdp"]
    assert result["status"] == "needs_review" and result["vintages_added"] == 0


def test_ons_listing_date_and_negative_growth(tmp_path, observations):
    db = tmp_path / "macro.sqlite"
    seed(db, observations, "uk_gdp")
    client = Replay("uk_gdp")
    doc = client.docs["september"]
    client.docs["september"] = changed(doc, doc.body.replace(b"Monthly GDP grew by 0.1%", b"Monthly GDP fell by 0.1%"))
    result = collect(db, client, "uk_gdp")["uk_gdp"]
    assert result["status"] == "ok", result
    with open_reader(db) as reader:
        assert reader.conn.execute("SELECT value FROM value_vintages WHERE indicator_id='uk_gdp_mm' AND reference_period='2026-09'").fetchone()[0] == -.1
    # The issuer index is an independent date check when that date is supplied.
    client.docs["listing"] = changed(client.docs["listing"], client.docs["listing"].body.replace(
        b'/september2026"', b'/september2026" data-gtm-search-result-release-date="20261113"'))
    assert collect(db, client, "uk_gdp")["uk_gdp"]["status"] == "needs_review"


def test_configurable_band_controls_verification(tmp_path, observations):
    db = tmp_path / "macro.sqlite"
    seed(db, observations, "us_gdp")
    result = run_daily.collect_family(lambda: open_writer(db), Replay("us_gdp", "band"), "preview", "us_gdp",
        {"us_gdp": {"fetch_since": "2026-06", "fetch_until": "2026-12-31"}}, dry_run=True,
        bands=configured_bands({"us_gdp_qq_saar": 100}))["us_gdp"]
    assert result["status"] == "ok" and result["vintages_added"] > 0
    for bad in ({"unknown": 5}, {"us_gdp_qq_saar": 0}, {"us_gdp_qq_saar": float("inf")}, [1]):
        with pytest.raises(ValueError):
            configured_bands(bad)


def test_future_composition_is_saved_for_review(tmp_path, observations):
    db = tmp_path / "macro.sqlite"
    seed(db, observations, "ea_hicp")
    client = Replay("ea_hicp")
    for stage in ("flash", "final"):
        payload = client.docs[stage].json()
        payload["dimension"]["geo"]["category"]["label"]["EA"] += ", EA22-2026"
        client.docs[stage] = changed(client.docs[stage], payload)
    result = collect(db, client, "ea_hicp")["ea_hicp"]
    assert result["status"] == "needs_review" and result["vintages_added"] == 0
    with open_reader(db) as reader:
        assert reader.conn.execute("SELECT COUNT(*) FROM readings WHERE composition_id='EA_PENDING' AND verification_status='unverified'").fetchone()[0] > 0


def test_document_only_structure_failure_is_reviewed(tmp_path, monkeypatch, capsys):
    class Broken:
        def request(self, *args, **kwargs):
            return changed(document("bls/cpi-ces.json"), b'not valid json')
    monkeypatch.setattr(run_daily, "HttpClient", lambda **kwargs: Broken())
    db = tmp_path / "macro.sqlite"
    assert run_daily.main(["--db", str(db), "--families", "us_cpi"]) == 3
    assert json.loads(capsys.readouterr().out)["families"]["us_cpi"]["status"] == "needs_review"
    with open_reader(db) as reader:
        assert reader.conn.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0] == 1
        assert reader.conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0] == 0


def test_reviewed_final_bootstraps_unpinned_historical_flash(tmp_path):
    db = tmp_path / "macro.sqlite"
    client = Replay("ea_hicp")
    for stage in ("flash", "final"):
        doc = document(f"eurostat/headline-{stage}-rates.response.json")
        payload = doc.json()
        periods = payload["dimension"]["time"]["category"]["index"]
        # Make the earliest flash May: its date comes from the issuer calendar,
        # while the current-final edition remains the reviewed September anchor.
        payload["dimension"]["time"]["category"]["index"] = {
            "2026-05" if p == "2026-06" else p: i for p, i in periods.items()}
        client.docs[stage] = changed(doc, payload)
    with open_writer(db):
        pass
    result = run_daily.collect_family(lambda: open_writer(db), client, "preview", "ea_hicp",
        {"ea_hicp": {"fetch_since": "2026-05", "fetch_until": "2026-12-31"}}, dry_run=True)["ea_hicp"]
    assert result["status"] == "ok" and result["vintages_added"] > 0, result


def test_payroll_missing_revision_month_and_bls_reference_year(tmp_path, observations):
    db = tmp_path / "macro.sqlite"
    seed(db, observations, "us_payrolls")
    client = Replay("us_payrolls")
    payload = client.docs["data"].json()
    for series in payload["Results"]["series"]:
        series["data"] = [r for r in series["data"] if (r["year"], r["period"]) != ("2026", "M09")]
    client.docs["data"] = changed(client.docs["data"], payload)
    result = collect(db, client, "us_payrolls")["us_payrolls"]
    assert result["status"] == "needs_review" and "payroll_revision_periods_missing" in result["review_reasons"]
    client = Replay("us_payrolls")
    client.docs["listing"] = changed(client.docs["listing"], client.docs["listing"].body.replace(b"October 2026", b"October 2025"))
    result = collect(db, client, "us_payrolls")["us_payrolls"]
    assert result["status"] == "needs_review" and "issuer_publication_unconfirmed" in result["review_reasons"]


def test_bea_estimate_stage_after_quarter_title(tmp_path, observations):
    db = tmp_path / "macro.sqlite"
    seed(db, observations, "us_gdp")
    client = Replay("us_gdp")
    doc = client.docs["listing"]
    client.docs["listing"] = changed(doc, doc.body.replace(b"GDP (Advance Estimate), 3rd Quarter 2026",
        b"Gross Domestic Product, 3rd Quarter 2026 (Advance Estimate)"))
    assert collect(db, client, "us_gdp")["us_gdp"]["status"] == "ok"


def test_ons_explicit_zero_growth_is_an_observation(tmp_path, observations):
    db = tmp_path / "macro.sqlite"
    seed(db, observations, "uk_gdp")
    client = Replay("uk_gdp")
    doc = client.docs["september"]
    client.docs["september"] = changed(doc, doc.body.replace(b"Monthly GDP grew by 0.1%", b"Monthly GDP showed no growth"))
    assert collect(db, client, "uk_gdp")["uk_gdp"]["status"] == "ok"
    with open_reader(db) as reader:
        assert reader.conn.execute("SELECT value FROM value_vintages WHERE indicator_id='uk_gdp_mm' AND reference_period='2026-09'").fetchone()[0] == 0


def test_new_hicp_flash_does_not_claim_estimated_final(tmp_path, observations):
    db = tmp_path / "macro.sqlite"
    seed(db, observations, "ea_hicp")
    client = Replay("ea_hicp")
    doc = client.docs["final"]
    payload = doc.json()
    times = payload["dimension"]["time"]["category"]["index"]
    payload["status"] = {str(k): "e" for k in payload["value"] if int(k) % len(times) == times["2026-10"]}
    client.docs["final"] = replace(changed(doc, payload), retrieved_at="2026-11-04T12:00:00Z")
    client.docs["flash"] = replace(client.docs["flash"], retrieved_at="2026-11-04T12:00:00Z")
    result = collect(db, client, "ea_hicp")["ea_hicp"]
    assert result["status"] == "ok", result
    with open_reader(db) as reader:
        assert reader.conn.execute("SELECT COUNT(*) FROM value_vintages WHERE reference_period='2026-10' AND estimate_stage='final'").fetchone()[0] == 0
        assert reader.conn.execute("SELECT COUNT(*) FROM value_vintages WHERE reference_period='2026-10' AND estimate_stage='flash'").fetchone()[0] > 0
