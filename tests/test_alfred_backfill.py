"""Offline HTTP fixtures and real, temporary DuckDB integration tests."""
from __future__ import annotations

from contextlib import closing
from datetime import date
import json
from pathlib import Path

import duckdb
from filelock import FileLock
import pytest
import requests

from market_data_hub import alfred_backfill as job
from market_data_hub.db.connection import get_conn, migrate, SCHEMA_VERSION
from market_data_hub.db.upsert import upsert
from market_data_hub.reader import alfred_as_known_at, read_alfred_vintage


FIXTURES = Path(__file__).parent / "fixtures" / "alfred_backfill"


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class Response:
    def __init__(self, payload=None, status=200, headers=None):
        self.payload = payload
        self.status_code = status
        self.headers = headers or {}

    def json(self):
        return self.payload


class Session:
    def __init__(self, fixture="payroll.json", db_path=None):
        self.rows = json.loads((FIXTURES / fixture).read_text())["observations"]
        self.vintages = sorted({row["realtime_start"] for row in self.rows})
        self.calls = []
        self.db_path = db_path

    def get(self, url, *, params, **kwargs):
        # Transport must run without the advisory lock OR any open DB writer.
        if self.db_path:
            with FileLock(str(Path(self.db_path).with_suffix(".lock")), timeout=0):
                if Path(self.db_path).exists():
                    with closing(duckdb.connect(self.db_path, read_only=True)):
                        pass
        self.calls.append((url, params.copy()))
        start, end = params["realtime_start"], params["realtime_end"]
        if "vintagedates" in url:
            dates = [v for v in self.vintages if start <= v <= end]
            offset, limit = params["offset"], params["limit"]
            return Response({"vintage_dates": dates[offset:offset + limit], "count": len(dates)})
        rows = [{**r, "realtime_start": max(start, r["realtime_start"]),
                 "realtime_end": min(end, r["realtime_end"])} for r in self.rows
                if r["realtime_start"] <= end and r["realtime_end"] >= start]
        offset, limit = params["offset"], params["limit"]
        return Response({"observations": rows[offset:offset + limit], "count": len(rows)})


def client(session, max_requests=None):
    clock = Clock()
    return job.Client(api_key="secret-must-not-appear", session=session,
                      limiter=job.RateLimiter(clock=clock.time, sleep=clock.sleep),
                      sleep=clock.sleep, max_requests=max_requests)


def run(db, session, **kwargs):
    summaries = []
    code = job.run(db, ["PAYEMS"], client=client(session, kwargs.pop("max_requests", None)),
                   emit=lambda s: summaries.append(json.loads(s)), **kwargs)
    return code, summaries[0]


def test_rate_limiter_fake_clock():
    clock = Clock()
    limiter = job.RateLimiter(clock=clock.time, sleep=clock.sleep)
    starts = []
    for _ in range(181):
        limiter.wait()
        starts.append(clock.now)
    assert starts[90] == pytest.approx(60)
    assert starts[180] == pytest.approx(120)
    clock.now += 100
    limiter.wait()
    assert clock.now == pytest.approx(220)


def test_revised_payroll_and_reader(tmp_db):
    session = Session(db_path=tmp_db)
    code, summary = run(tmp_db, session)
    assert code == 0, summary
    assert summary["rows_added"] == len(session.rows)
    assert summary["vintages_added"] == len(session.vintages)
    observation_date = session.rows[0]["date"]
    revisions = [r for r in session.rows if r["date"] == observation_date and r["value"] != "."]
    assert len({r["value"] for r in revisions}) > 1
    for revision in revisions:
        as_of = revision["realtime_start"]
        frame = alfred_as_known_at("PAYEMS", as_of, db_path=tmp_db)
        actual = frame.loc[frame.date == observation_date, "value"].iloc[0]
        assert actual == float(revision["value"])
        assert (frame.as_of <= as_of).all()
    assert alfred_as_known_at("PAYEMS", "2023-01-01", db_path=tmp_db).empty
    assert not read_alfred_vintage("PAYEMS", as_of=session.vintages[0], db_path=tmp_db).empty


def test_resume_after_partial_page_and_idempotence(tmp_db, monkeypatch):
    monkeypatch.setattr(job, "PAGE_SIZE", 2)
    session = Session(db_path=tmp_db)
    code, partial = run(tmp_db, session, max_requests=2)
    assert code == 1
    assert partial["status"] == "incomplete"
    assert partial["rows_added"] == 2
    with closing(get_conn(tmp_db, read_only=True)) as con:
        assert con.execute("SELECT count(*) FROM alfred_backfill_progress").fetchone()[0] == 0
    second = Session(db_path=tmp_db)
    code, resumed = run(tmp_db, second)
    assert code == 0
    assert resumed["rows_added"] == len(session.rows) - 2
    assert second.calls[0][1]["offset"] == 2
    code, repeated = run(tmp_db, Session(db_path=tmp_db))
    assert code == 3
    assert repeated["rows_added"] == repeated["vintages_added"] == 0
    with closing(get_conn(tmp_db, read_only=True)) as con:
        assert con.execute("SELECT count(*) FROM alfred_vintage_observations").fetchone()[0] == len(session.rows)


def test_completed_batch_resumes_at_next_vintage(tmp_db, monkeypatch):
    monkeypatch.setattr(job, "VINTAGE_BATCH", 1)
    first = Session(db_path=tmp_db)
    assert run(tmp_db, first, max_requests=2)[0] == 1
    with closing(get_conn(tmp_db, read_only=True)) as con:
        checkpoint = con.execute("SELECT last_vintage FROM alfred_backfill_progress").fetchone()[0]
    assert checkpoint.isoformat() == first.vintages[0]
    second = Session(db_path=tmp_db)
    assert run(tmp_db, second)[0] == 0
    assert second.calls[0][1]["realtime_start"] > checkpoint.isoformat()
    assert second.calls[1][1]["realtime_start"] == checkpoint.isoformat()


def test_no_revisions_and_incremental_withdrawal(tmp_db):
    session = Session("no_revisions.json", tmp_db)
    assert len({r['realtime_start'] for r in session.rows}) == 1
    assert run(tmp_db, session)[0] == 0
    assert run(tmp_db, Session("no_revisions.json", tmp_db))[0] == 3
    # A previously open observation is withdrawn without a replacement value.
    first = session.vintages[0]
    session.vintages.append("2024-03-01")
    session.rows[0]["realtime_end"] = "2024-02-29"
    assert run(tmp_db, session)[0] == 0  # new vintage and changed validity interval
    assert job._checkpoint(tmp_db, "PAYEMS", job.EARLIEST) == "2024-03-01"
    before = alfred_as_known_at("PAYEMS", first, db_path=tmp_db)
    after = alfred_as_known_at("PAYEMS", "2024-03-01", db_path=tmp_db)
    assert len(after) == len(before) - 1


def test_dry_run_does_not_create_database_or_lock(tmp_path):
    db = str(tmp_path / "new" / "hub.duckdb")
    code, summary = run(db, Session(), dry_run=True)
    assert code == 3
    assert summary["status"] == "dry_run"
    assert summary["vintages_pending"] > 0
    assert not Path(db).parent.exists()


def test_interruption_between_writes_resumes_exact_offset(tmp_db, monkeypatch):
    monkeypatch.setattr(job, "WRITE_BATCH", 2)
    original = job._write
    writes = []
    def interrupt(*args, **kwargs):
        writes.append(1)
        if len(writes) == 2:
            raise KeyboardInterrupt
        return original(*args, **kwargs)
    monkeypatch.setattr(job, "_write", interrupt)
    code, summary = run(tmp_db, Session(db_path=tmp_db))
    assert code == 1 and summary["rows_added"] == 2
    assert job._pending(tmp_db, "PAYEMS", job.EARLIEST)["offset"] == 2
    monkeypatch.setattr(job, "_write", original)
    resumed = Session(db_path=tmp_db)
    assert run(tmp_db, resumed)[0] == 0
    assert resumed.calls[0][1]["offset"] == 2
    assert job._pending(tmp_db, "PAYEMS", job.EARLIEST) is None


def test_missing_value_does_not_resurrect_prior_revision(tmp_db):
    session = Session()
    row = session.rows[0]
    session.rows = [dict(row, realtime_end="2024-01-31"),
                    dict(row, realtime_start="2024-02-01", realtime_end=job.LATEST, value=".")]
    session.vintages = ["2024-01-01", "2024-02-01"]
    assert run(tmp_db, session)[0] == 0
    assert len(alfred_as_known_at("PAYEMS", "2024-01-31", db_path=tmp_db)) == 1
    assert alfred_as_known_at("PAYEMS", "2024-02-01", db_path=tmp_db).empty


def test_since_scope_does_not_skip_older_history(tmp_db):
    session = Session()
    floor = session.vintages[-1]
    assert run(tmp_db, session, since=floor)[0] == 0
    assert run(tmp_db, Session())[0] == 0
    with closing(get_conn(tmp_db, read_only=True)) as con:
        assert con.execute("SELECT count(*) FROM alfred_backfill_progress").fetchone()[0] == 2
    assert not alfred_as_known_at("PAYEMS", session.vintages[0], db_path=tmp_db).empty


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_auth_and_other_4xx_never_retried(status, tmp_path, capsys):
    session = Session()
    calls = []
    def get(*args, **kwargs):
        calls.append(1)
        return Response(status=status)
    session.get = get
    assert job.run(str(tmp_path / "x.duckdb"), ["PAYEMS"], client=client(session)) == 1
    assert len(calls) == 1
    assert "secret-must-not-appear" not in capsys.readouterr().out


def test_retry_backoff_and_budget():
    responses = [Response(status=429, headers={"Retry-After": "3"}),
                 Response(status=503), Response({"count": 0})]
    session = Session()
    session.get = lambda *a, **kw: responses.pop(0)
    transport = client(session)
    assert transport.get("https://example.test") == {"count": 0}
    assert transport.requests == 3
    assert transport.sleep.__self__.sleeps[:2] == [3, 2]
    limited = client(session, max_requests=0)
    with pytest.raises(job.RequestLimit):
        limited.get("https://example.test")


def test_transport_failure_redacts_secret():
    session = Session()
    def get(*args, **kwargs):
        raise requests.ConnectionError("https://example.test?api_key=secret-must-not-appear")
    session.get = get
    with pytest.raises(RuntimeError, match="transport failed") as exc:
        client(session).get("https://example.test")
    assert "secret-must-not-appear" not in str(exc.value)


def test_vintage_pagination_not_truncated():
    session = Session()
    session.vintages = [date.fromordinal(730120 + i).isoformat() for i in range(10001)]
    dates = client(session).vintages("PAYEMS", job.EARLIEST, job.LATEST)
    assert len(dates) == 10001
    assert [params["offset"] for _, params in session.calls] == [0, 10000]


def test_write_failure_rolls_back_checkpoint(tmp_db, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("simulated disk error")
    monkeypatch.setattr(job, "upsert", broken)
    assert run(tmp_db, Session())[0] == 1
    with closing(get_conn(tmp_db, read_only=True)) as con:
        assert con.execute("SELECT count(*) FROM alfred_backfill_progress").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM alfred_vintage_observations").fetchone()[0] == 0


def test_v25_migration_and_legacy_snapshot(tmp_db):
    with closing(get_conn(tmp_db)) as con:
        con.execute("DROP INDEX idx_alfred_series_date")
        con.execute("ALTER TABLE alfred_vintage_observations DROP COLUMN realtime_end")
        con.execute("DROP TABLE alfred_backfill_progress")
        con.execute("UPDATE schema_meta SET value = '25' WHERE key = 'schema_version'")
        assert migrate(con) == SCHEMA_VERSION
        upsert(con, "alfred_vintage_observations", job._frame("LEGACY", [{
            "date": "2020-01-01", "realtime_start": "2020-02-01",
            "realtime_end": job.LATEST, "value": "100"}]).drop(columns="realtime_end"))
    assert alfred_as_known_at("LEGACY", "2020-03-01", db_path=tmp_db).value.tolist() == [100]


def test_cli_and_config(monkeypatch, tmp_path):
    entries = job.configured_series()
    assert len({e['series_id'] for e in entries}) == len(entries)
    assert all(e['sources'] and e['reason'] for e in entries)
    fake = client(Session())
    monkeypatch.setattr(job, "Client", lambda **kwargs: fake)
    assert job.main(["--db", str(tmp_path / 'hub.duckdb'), "--series", "PAYEMS,UNRATE",
                     "--dry-run"]) == 3
    with pytest.raises(SystemExit):
        job.main(["--db", "unused", "--series", "UNKNOWN"])


@pytest.mark.parametrize("incremental,empty_code", [(False, 3), (True, 0)])
def test_incremental_exit_codes(tmp_db, incremental, empty_code):
    assert run(tmp_db, Session(), incremental=incremental)[0] == 0
    assert run(tmp_db, Session(), incremental=incremental)[0] == empty_code
    assert run(tmp_db, Session(), incremental=incremental, max_requests=0)[0] == 1


def test_incremental_cli_noop_success(monkeypatch, tmp_path):
    fake = client(Session())
    monkeypatch.setattr(job, "Client", lambda **kwargs: fake)
    args = ["--db", str(tmp_path / "hub.duckdb"), "--series", "PAYEMS", "--incremental"]
    assert job.main(args) == 0
    assert job.main(args) == 0


def test_vendor_error_on_one_series_does_not_stop_the_rest(tmp_db):
    # 31/10/2026: a transient HTTP 500 on the first series stopped the whole run.
    session = Session(db_path=tmp_db)
    good = session.get
    def get(url, *, params, **kwargs):
        if params.get("series_id") == "AWHMAN":
            return Response(status=500)
        return good(url, params=params, **kwargs)
    session.get = get
    summaries = []
    code = job.run(tmp_db, ["AWHMAN", "PAYEMS"], client=client(session),
                   emit=lambda s: summaries.append(json.loads(s)))
    assert code == 1
    assert [s["status"] for s in summaries] == ["error", "ok"]
    assert summaries[0]["error"] == "ALFRED HTTP 500 after 5 attempts"
    assert summaries[1]["rows_added"] > 0


def test_write_failure_still_stops_the_run(tmp_db, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("simulated disk error")
    monkeypatch.setattr(job, "upsert", broken)
    summaries = []
    code = job.run(tmp_db, ["PAYEMS", "UNRATE"], client=client(Session()),
                   emit=lambda s: summaries.append(json.loads(s)))
    assert code == 1
    assert [s["series_id"] for s in summaries] == ["PAYEMS"]
