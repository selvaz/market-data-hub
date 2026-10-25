"""M7a: offline production-run lifecycle, recovery and official read API."""
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
import requests

from market_data_hub import __version__
from market_data_hub.macro_releases import open_reader, open_writer
from market_data_hub.macro_releases import migrations, run_daily, run_log
from market_data_hub.macro_releases.http import FetchError, HttpClient, RateLimiter
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.lease import LeaseBusy, LeaseLost, RunLease
from test_macro_official_adapters import document, observations as observations
from test_macro_read_api import observation, schedule
from test_macro_releases import legacy_calendar as legacy_calendar
from market_data_hub.macro_releases.legacy import import_calendar
from market_data_hub.macro_releases.store import Store

# Keep this historical five-family lifecycle fixture scoped to its saved data.
# The expanded default catalogue is covered by test_macro_us_monthly.
FAMILIES = ("us_cpi", "us_payrolls", "us_gdp", "ea_hicp", "uk_gdp")


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network forbidden in daily-runner tests")
    monkeypatch.setattr(requests.Session, "request", forbidden)
    monkeypatch.setattr(run_daily.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(run_daily, "FAMILIES", FAMILIES)


@pytest.fixture
def replay(monkeypatch, observations):
    calls = []
    class Adapter:
        def __init__(self, client):
            self.client = client

        def fetch(self, families, since):
            calls.append((self.client, families, since))
            return [o for o in observations if o.family in families]
    monkeypatch.setattr(run_daily, "ADAPTERS", dict.fromkeys(FAMILIES, Adapter))
    return calls


def invoke(db, capsys, *args):
    # Use a private empty archive: the machine's live FF captures must not
    # provide schedules or consensus to this saved-publication lifecycle test.
    raw = db.parent / "raw"
    raw.mkdir(exist_ok=True)
    code = run_daily.main(["--db", str(db), "--consensus-raw-dir", str(raw), *args])
    result = json.loads(capsys.readouterr().out)
    assert result["exit_code"] == code
    return code, result


def test_logged_official_first_run_and_idempotent_second(tmp_path, replay, capsys):
    db = tmp_path / "macro.sqlite"
    code, first = invoke(db, capsys)
    assert code == 0
    assert set(first["families"]) == set(FAMILIES)
    assert len({id(c[0]) for c in replay}) == 1  # all families share one client/limiter set
    assert first["families"]["ea_hicp"]["fetch_since"] == first["requested_since"]
    assert first["families"]["us_payrolls"]["fetch_since"] == first["requested_since"]
    with open_reader(db) as reader:
        log = [dict(r) for r in reader.conn.execute("SELECT * FROM collect_runs")]
        assert [r["status"] for r in log] == ["running", "ok"]
        assert log[0]["finished_at"] is None
        assert log[1]["finished_at"] >= log[0]["started_at"]
        assert all(r["code_version"] == __version__ and r["host"] for r in log)
        assert json.loads(log[0]["families"]) == list(FAMILIES)
        assert json.loads(log[0]["parameters"])["lookback_months"] == 24
        assert reader.conn.execute("SELECT COUNT(*) FROM collect_run_families").fetchone()[0] == 5
        cards = reader.release_cards("2026-10-01", "2026-10-17", indicators=["us_cpi_headline_yy"])
        assert cards["cards"][0]["resolution"]["status"] == "official"
    code, second = invoke(db, capsys)
    assert code == 3 and second["status"] == "no_change"
    assert all(r["vintages_added"] == r["readings_added"] == 0 and r["readings_skipped"] > 0
               for r in second["families"].values())
    with open_writer(db) as store:
        for table in ("collect_runs", "collect_run_families"):
            for op in (f"UPDATE {table} SET status='failed'", f"DELETE FROM {table}"):
                with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                    store.conn.execute(op)


@pytest.mark.parametrize("archive_exists", [False, True])
def test_partial_failure_isolated_even_between_bls_families(tmp_path, replay, monkeypatch, capsys, archive_exists):
    adapter = run_daily.ADAPTERS["us_cpi"]
    class Broken:
        def __init__(self, client):
            pass

        def fetch(self, *args):
            raise RuntimeError("https://issuer.example/?api_key=super-secret body=super-secret")
    monkeypatch.setitem(run_daily.ADAPTERS, "us_cpi", Broken)
    db = tmp_path / "macro.sqlite"
    raw = tmp_path / "consensus-archive"
    if archive_exists:
        raw.mkdir()
    code, result = invoke(db, capsys, "--consensus-raw-dir", str(raw))
    assert code == 1 and result["status"] == "failed"
    assert result["families"]["us_payrolls"]["vintages_added"] > 0
    assert result["families"]["us_cpi"]["error_type"] == "RuntimeError"
    assert result["consensus"]["status"] == ("no_change" if archive_exists else "failed")
    if not archive_exists:
        assert result["consensus"]["error_type"] == "FileNotFoundError"
        assert not raw.exists()
    assert "super-secret" not in json.dumps(result)
    with open_reader(db) as reader:
        failure = reader.explain_empty("us_cpi_headline_yy", "2026-09")
        assert failure["reason"] == "source_failed" and failure["family"] == "us_cpi"
        assert failure["time_basis"] == "collect_family_finished_at" and failure["time"]
        assert failure["run_id"] == result["run_id"]
        coverage = reader.coverage("2026-10-01", "2026-10-17")["families"]["us_cpi"]
        assert coverage["collection_failures"][0]["run_id"] == result["run_id"]
        # The independent reviewed calendar establishes the scheduled edition;
        # a failed fetch still supplies no captured actual.
        assert coverage["expected"] == 1
        assert coverage["captured"] == 0
        assert reader.explain_empty("us_cpi_headline_yy", "2010-01")["reason"] != "source_failed"
        assert reader.explain_empty("us_cpi_headline_yy", "2026-09",
                                    as_known_at="2026-10-01T00:00:00Z")["reason"] != "source_failed"
        assert reader.conn.execute("SELECT status FROM collect_runs WHERE finished_at IS NOT NULL").fetchone()[0] == "failed"
        assert "super-secret" not in repr([tuple(r) for r in reader.conn.execute("SELECT * FROM collect_run_families")])
    monkeypatch.setitem(run_daily.ADAPTERS, "us_cpi", adapter)
    assert invoke(db, capsys, "--families", "us_cpi", "--consensus-raw-dir", str(raw))[0] == 0
    with open_reader(db) as reader:
        assert reader.explain_empty("us_cpi_core_mm", "2026-09")["reason"] != "source_failed"
        assert reader.coverage("2026-10-01", "2026-10-17")["families"]["us_cpi"]["collection_failures"] == []


def test_family_write_rollback_and_failed_log(tmp_path, replay, monkeypatch, capsys):
    original = run_daily.persist
    def fail_after_first(store, rows):
        original(store, rows[:1])
        if rows[0].family == "us_cpi":
            raise ValueError("fixture failure after partial persistence")
        original(store, rows[1:])
    monkeypatch.setattr(run_daily, "persist", fail_after_first)
    db = tmp_path / "macro.sqlite"
    assert invoke(db, capsys, "--families", "us_cpi,us_payrolls")[0] == 1
    with open_reader(db) as reader:
        assert reader.conn.execute("SELECT COUNT(*) FROM readings WHERE indicator_id LIKE 'us_cpi_%' "
            "AND extraction_method<>'reviewed_calendar'").fetchone()[0] == 0
        rows = [dict(r) for r in reader.conn.execute("SELECT * FROM collect_run_families")]
        assert rows[0]["status"] == "failed" and rows[0]["readings_added"] == 0
        assert rows[1]["status"] == "ok" and rows[1]["vintages_added"] > 0


@pytest.mark.parametrize("exists", [False, True])
def test_dry_run_no_writes_and_exact_preview(tmp_path, replay, capsys, exists):
    db = tmp_path / "macro.sqlite"
    if exists:
        assert invoke(db, capsys)[0] == 0
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    code, preview = invoke(db, capsys, "--dry-run")
    assert code == (3 if exists else 0) and preview["dry_run"]
    assert preview["status"] == ("no_change" if exists else "ok")
    after = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    assert before == after
    code, actual = invoke(db, capsys)
    assert code == (3 if exists else 0)
    assert actual["families"] == preview["families"]


def test_dry_run_reads_committed_live_wal_without_touching_it(tmp_path, replay, capsys):
    db = tmp_path / "macro.sqlite"
    with open_writer(db) as store:
        persist(store, [observation()])
        assert Path(str(db) + "-wal").stat().st_size > 0
        # Windows denies reads of an actively held filelock byte. Verify its
        # metadata separately while comparing all SQLite files byte-for-byte.
        lock = Path(str(db) + ".writer.lock")
        lock_before = lock.stat()
        before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file() and p != lock}
        code, result = invoke(db, capsys, "--dry-run", "--families", "us_cpi")
        assert code == 0 and result["families"]["us_cpi"]["status"] == "ok"
        assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file() and p != lock}
        assert lock.stat().st_mtime_ns == lock_before.st_mtime_ns
        with run_daily.preview_store(db) as copied:
            assert copied.conn.execute("SELECT value FROM value_vintages").fetchone()[0] == 3.7


def test_failure_diagnostic_on_missing_event_keeps_future_pending(tmp_path, replay, monkeypatch, capsys):
    db = tmp_path / "macro.sqlite"
    with open_writer(db) as store:
        due = schedule(store)
        future = schedule(store, edition="2027-10-14", expected="2027-10-14T12:30:00Z")
    class Broken:
        def __init__(self, client):
            pass

        def fetch(self, *args):
            raise FetchError("BLS publication metadata needs review: fixture")
    monkeypatch.setitem(run_daily.ADAPTERS, "us_cpi", Broken)
    assert invoke(db, capsys, "--families", "us_cpi")[0] == 1
    with open_reader(db) as reader:
        counts = reader.coverage("2026-10-01", "2027-10-17")["families"]["us_cpi"]
        reasons = {r["release_event_id"]: r for r in counts["missing_reasons"]}
        assert reasons[due]["reason"] == "source_failed" and reasons[due]["time"]
        assert "metadata/version needs review" in reasons[due]["detail"]
        assert reasons[future]["reason"] == "not_yet_released"


def test_failed_dry_run_no_target_created(tmp_path, monkeypatch, capsys):
    class Broken:
        def __init__(self, client):
            pass

        def fetch(self, *args):
            raise FetchError("Missing credential: BLS_API_KEY")
    monkeypatch.setitem(run_daily.ADAPTERS, "us_cpi", Broken)
    db = tmp_path / "missing.sqlite"
    code, result = invoke(db, capsys, "--dry-run", "--families", "us_cpi")
    assert code == 1 and result["families"]["us_cpi"]["error_message"] == "Missing credential: BLS_API_KEY"
    assert not db.exists() and not Path(str(db) + ".writer.lock").exists()


def test_live_stale_lease_and_old_owner_fenced(tmp_path):
    db = tmp_path / "macro.sqlite"
    clock = SimpleNamespace(value=1000)
    old = RunLease(db, timeout=10, clock=lambda: clock.value)
    with old:
        with pytest.raises(LeaseBusy):
            with RunLease(db, timeout=10, clock=lambda: clock.value):
                pass
        clock.value += 11
        with RunLease(db, timeout=10, clock=lambda: clock.value) as new:
            with new.guard():
                with pytest.raises(LeaseLost):
                    old.validate()
                new.validate()
        assert not old.path.exists()


def test_blocked_runner_makes_no_fetch(tmp_path, replay, capsys):
    db = tmp_path / "macro.sqlite"
    with RunLease(db):
        code, result = invoke(db, capsys)
        assert code == 1 and result["error_type"] == "LeaseBusy"
        assert not replay and not db.exists()


def test_stale_run_recovery_preserves_committed_families(tmp_path, replay, capsys):
    db = tmp_path / "macro.sqlite"
    _, windows = run_daily.fetch_windows(["us_cpi", "us_payrolls"], 72,
                                         run_daily.datetime.now(run_daily.timezone.utc).date())
    with open_writer(db) as store:
        run_log.start(store, "crashed", ["us_cpi", "us_payrolls"], {"fetch_windows": windows})
        run_log.family_result(store, "crashed", "us_cpi", dict(windows["us_cpi"], status="no_change",
                              vintages_added=0, readings_added=0, readings_skipped=1))
    Path(str(db) + ".daily.lease.json").write_text('{"token":"crashed", "expires_at":0}')
    assert invoke(db, capsys)[0] == 0
    with open_reader(db) as reader:
        assert reader.conn.execute("SELECT status FROM collect_runs WHERE run_id='crashed' AND finished_at IS NOT NULL").fetchone()[0] == "failed"
        rows = reader.conn.execute("SELECT family,status,error_type FROM collect_run_families WHERE run_id='crashed' ORDER BY family").fetchall()
        assert [tuple(r) for r in rows] == [("us_cpi", "no_change", None), ("us_payrolls", "failed", "InterruptedRun")]


def test_fetch_occurs_without_writer_lock_or_transaction(tmp_path, replay, monkeypatch, capsys):
    db = tmp_path / "macro.sqlite"
    adapter = run_daily.ADAPTERS["us_cpi"]
    class Check(adapter):
        def fetch(self, families, since):
            with open_writer(db, lock_timeout=0) as probe:
                assert not probe.conn.in_transaction
            return super().fetch(families, since)
    monkeypatch.setitem(run_daily.ADAPTERS, "us_cpi", Check)
    assert invoke(db, capsys, "--families", "us_cpi")[0] == 0


def test_real_adapters_saved_responses_share_client(tmp_path, monkeypatch, capsys):
    class Replay:
        def __init__(self, **kwargs):
            pass

        def request(self, provider, url, **kwargs):
            from dataclasses import replace
            import hashlib
            doc = document("bls/cpi-ces.json")
            payload = doc.json()
            payload["Results"]["series"] = [s for s in payload["Results"]["series"] if s["seriesID"] in kwargs["payload"]["seriesid"]]
            body = json.dumps(payload).encode()
            return replace(doc, body=body, document_hash=hashlib.sha256(body).hexdigest())

        def get(self, provider, url, params=None):
            if provider == "bea":
                return document("bea/" + ("advance-release.pdf" if "adv.pdf" in url else
                                          "second-release.pdf" if "2nd.pdf" in url else "nipa.json"))
            if provider == "ons":
                if url.endswith("/time-series"):
                    return document("ons/current-edition.response.json")
                if url.endswith("previousreleases"):
                    return document("ons/current-bulletins.response.json")
                if url.endswith("june2026"):
                    return document("ons/june-content.response.json")
                if "gdp-to-four" in url:
                    return document("ons/gdp69-csv.csv" if url.endswith(".csv") else "ons/gdp69.json")
                return document("ons/" + ("july" if url.endswith("july2026") else "august") + "-content.html")
            return document("eurostat/headline-" + ("flash" if url.endswith("prc_hicp_fpd") else "final") + "-rates.response.json")
    monkeypatch.setattr(run_daily, "HttpClient", Replay)
    monkeypatch.setattr("market_data_hub.macro_releases.bls.api_key", lambda _: "fake-key")
    monkeypatch.setattr("market_data_hub.macro_releases.bea.api_key", lambda _: "fake-key")
    monkeypatch.setattr("market_data_hub.macro_releases.discovery.api_key", lambda _: "fake-key")
    code, result = invoke(tmp_path / "macro.sqlite", capsys)
    assert code == 0, result
    assert all(r["readings_added"] > 0 for r in result["families"].values())


def test_rate_spacing_retry_and_provider_independence(tmp_path, monkeypatch):
    clock = SimpleNamespace(value=1_000_000.0)
    sleeps, calls = [], []
    def sleep(delay):
        sleeps.append(delay)
        clock.value += delay
    monkeypatch.setattr("market_data_hub.macro_releases.http.time.time", lambda: clock.value)
    monkeypatch.setattr("market_data_hub.macro_releases.http.time.sleep", sleep)
    RateLimiter("bls", tmp_path).acquire()
    RateLimiter("bls", tmp_path).acquire()
    RateLimiter("bea", tmp_path).acquire()
    assert sleeps == [1.0]
    def request(method, url, **kwargs):
        calls.append(clock.value)
        return SimpleNamespace(status_code=503 if len(calls) == 1 else 200, content=b"{}")
    client = HttpClient(session=SimpleNamespace(request=request), rate_directory=tmp_path)
    limiter = client.limiters["bls"]
    client.get("bls", "https://api.bls.gov/example")
    client.get("bls", "https://api.bls.gov/example")
    assert client.limiters["bls"] is limiter
    assert len(calls) == 3 and all(b - a >= 1 for a, b in zip(calls, calls[1:]))


@pytest.mark.parametrize("error,attempts", [(requests.Timeout(), 3), (requests.ConnectionError(), 3),
                                           (requests.exceptions.SSLError(), 1), (requests.RequestException(), 1)])
def test_only_transient_transport_retried(monkeypatch, error, attempts):
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise error
    monkeypatch.setattr("market_data_hub.macro_releases.http.time.sleep", lambda _: None)
    client = HttpClient(session=SimpleNamespace(request=fail), persistent_rates=False)
    client.limiters["bls"].interval = 0
    with pytest.raises(FetchError):
        client.get("bls", "https://api.bls.gov/example")
    assert len(calls) == attempts


def test_migration_v3_preserves_20k_legacy_readings(tmp_path, legacy_calendar):
    db = tmp_path / "v3.sqlite"
    conn = sqlite3.connect(db, isolation_level=None)
    conn.row_factory = sqlite3.Row
    module = Path(migrations.__file__).parent
    conn.executescript((module / "schema.sql").read_text() + (module / "migration_003.sql").read_text())
    for table in migrations.APPEND_ONLY:
        for operation in ("UPDATE", "DELETE"):
            conn.execute(f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
                         f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END")
    conn.execute("PRAGMA user_version=3")
    old = Store(conn, writable=True)
    import_calendar(old, legacy_calendar, captured_at="2026-10-18T01:00:00Z")
    template = dict(conn.execute("SELECT * FROM readings LIMIT 1").fetchone())
    template.pop("id")
    columns = list(template)
    with old.batch():
        count = conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
        conn.executemany(f"INSERT INTO readings ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                         [tuple(dict(template, content_hash=f"legacy-{i}").values()) for i in range(20_000 - count)])
    def snapshot(connection):
        return {row[0]: connection.execute(f'SELECT * FROM "{row[0]}" ORDER BY rowid').fetchall()
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    before = {name: [tuple(r) for r in rows] for name, rows in snapshot(conn).items()}
    old.close()
    original_bytes = db.read_bytes()
    with run_daily.preview_store(db) as preview:
        assert (
            preview.conn.execute("PRAGMA user_version").fetchone()[0]
            == migrations.SCHEMA_VERSION
        )
        assert preview.conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0] == 20_000
    assert db.read_bytes() == original_bytes
    with sqlite3.connect(db) as unchanged:
        assert unchanged.execute("PRAGMA user_version").fetchone()[0] == 3
    with open_writer(db) as upgraded:
        assert (
            upgraded.conn.execute("PRAGMA user_version").fetchone()[0]
            == migrations.SCHEMA_VERSION
        )
        for name, rows in before.items():
            assert [tuple(r) for r in upgraded.conn.execute(f'SELECT * FROM "{name}" ORDER BY rowid')] == rows
        assert upgraded.conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0] == 20_000
        assert upgraded.conn.execute("PRAGMA foreign_key_check").fetchall() == []
        migrations.migrate(upgraded.conn)  # retry must not reapply v3's ALTER TABLEs


def test_cli_requires_explicit_db_even_with_production_environment(monkeypatch):
    monkeypatch.setenv("MACRO_RELEASES_DB", "C:/ProgramData/InvestmentCommittee/live/db/macro_releases.sqlite")
    for args in ([], ["C:/ProgramData/InvestmentCommittee/live/db/macro_releases.sqlite"],
                 ["--db", "unused.sqlite", "--families", "unknown"],
                 ["--db", "unused.sqlite", "--lookback-months", "0"]):
        with pytest.raises(SystemExit) as exc:
            run_daily.main(args)
        assert exc.value.code == 2
    seen = []
    def execute(target, *args, **kwargs):
        seen.append(target)
        return {"exit_code": 3}
    monkeypatch.setattr(run_daily, "execute", execute)
    assert run_daily.main(["--db", "C:/ProgramData/InvestmentCommittee/live/db/macro_releases.sqlite"]) == 3
    assert "ProgramData" in str(seen[0])  # no real filesystem access to that path
