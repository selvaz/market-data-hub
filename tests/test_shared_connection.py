"""In-process sharing of one DuckDB instance per file (defect D52).

DuckDB refuses a second connection to the same file in one process when its
configuration differs (``read_only=True`` next to a read-write connection).
A server running several agents in one process hit exactly that; these tests
pin the registry in ``db.connection`` that prevents it.
"""
from __future__ import annotations

import gc
import threading
import time

import duckdb
import pytest

from market_data_hub.db import connection as C


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "hub.duckdb")
    with C.get_conn(path) as con:
        con.execute("CREATE TABLE stress (thread INTEGER, i INTEGER)")
    yield path
    assert C._REGISTRY == {}, "a handle leaked the shared instance"


def test_raw_duckdb_rejects_mixed_configuration(db):
    # The defect itself, so the registry below is known to be needed.
    rw = duckdb.connect(db)
    try:
        with pytest.raises(duckdb.ConnectionException, match="different configuration"):
            duckdb.connect(db, read_only=True)
    finally:
        rw.close()


def test_concurrent_readers_and_writers_never_hit_the_configuration_error(db):
    readers, writers, rounds = 6, 3, 25
    errors: list[BaseException] = []
    start = threading.Barrier(readers + writers)

    def reader():
        start.wait()
        for _ in range(rounds):
            try:
                with C.get_conn(db, read_only=True) as con:
                    con.execute("SELECT count(*) FROM stress").fetchone()
            except BaseException as exc:  # noqa: BLE001 - collected for the assert
                errors.append(exc)

    def writer(n):
        start.wait()
        for i in range(rounds):
            try:
                with C.get_conn(db) as con:
                    con.execute("INSERT INTO stress VALUES (?, ?)", [n, i])
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(readers)]
    threads += [threading.Thread(target=writer, args=(n,)) for n in range(writers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not any(t.is_alive() for t in threads), "a thread hung"
    assert errors == []
    with C.get_conn(db, read_only=True) as con:
        assert con.execute("SELECT count(*) FROM stress").fetchone()[0] == writers * rounds


def test_read_only_request_shares_a_read_write_instance_and_refuses_writes(db):
    with C.get_conn(db) as writer:
        with C.get_conn(db, read_only=True) as reader:
            assert reader.read_only
            assert reader.execute("SELECT count(*) FROM stress").fetchone() == (0,)
            with pytest.raises(C.ReadOnlyViolation):
                reader.execute("INSERT INTO stress VALUES (1, 1)")
            with pytest.raises(C.ReadOnlyViolation):
                reader.execute("SELECT 1; DROP TABLE stress")
            with pytest.raises(C.ReadOnlyViolation):
                reader.append("stress", None)
            # A read-only database allows temp objects; so does the guard.
            reader.execute("CREATE TEMP TABLE scratch AS SELECT 1 AS x")
        writer.execute("INSERT INTO stress VALUES (1, 1)")


def test_closing_one_handle_leaves_the_others_open(db):
    first = C.get_conn(db, read_only=True)
    second = C.get_conn(db, read_only=True)
    first.close()
    first.close()  # idempotent
    assert second.execute("SELECT count(*) FROM stress").fetchone() == (0,)
    second.close()
    assert C._REGISTRY == {}


def test_last_close_releases_the_file(db):
    con = C.get_conn(db)
    con.close()
    # Nothing in this process keeps the file open: a raw connection of either
    # configuration succeeds, as it did before the registry existed.
    duckdb.connect(db, read_only=True).close()
    duckdb.connect(db).close()


def test_garbage_collected_handle_releases_the_instance(db):
    C.get_conn(db).execute("SELECT 1")
    gc.collect()
    assert C._REGISTRY == {}


def test_writer_waits_for_read_only_handles_to_drain(db):
    reader = C.get_conn(db, read_only=True)
    got: dict = {}

    def write():
        with C.get_conn(db) as con:
            con.execute("INSERT INTO stress VALUES (9, 9)")
            got["ok"] = True

    t = threading.Thread(target=write)
    t.start()
    time.sleep(0.3)
    assert "ok" not in got  # still queued behind the read-only instance
    reader.close()
    t.join(timeout=30)
    assert got == {"ok": True}


def test_same_thread_upgrade_fails_fast_instead_of_deadlocking(db):
    with C.get_conn(db, read_only=True):
        t0 = time.monotonic()
        with pytest.raises(C.SharedDatabaseBusy):
            C.get_conn(db)
        assert time.monotonic() - t0 < 5


def test_writer_upgrade_is_bounded_by_the_writer_budget(db, monkeypatch):
    monkeypatch.setattr(C, "_WRITER_LOCK_WAIT_S", 0.3)
    reader = C.get_conn(db, read_only=True)
    errors: list[BaseException] = []

    def write():
        try:
            C.get_conn(db).close()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=write)
    t.start()
    t.join(timeout=30)
    reader.close()
    assert len(errors) == 1 and isinstance(errors[0], C.SharedDatabaseBusy)


def test_open_existing_does_not_create(tmp_path):
    with pytest.raises(FileNotFoundError):
        C.open_existing(str(tmp_path / "absent.duckdb"))
    assert not (tmp_path / "absent.duckdb").exists()


def _wait_until(predicate, timeout=10.0):
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > end:
            raise AssertionError("condition not reached")
        time.sleep(0.01)


def _key(path):
    return C._registry_key(path)


def test_timed_out_writer_wakes_readers_queued_behind_it(db, monkeypatch):
    # A reader queued behind a writer must re-check when that writer gives up;
    # without the wake-up it would sleep for its whole budget.
    monkeypatch.setattr(C, "_WRITER_LOCK_WAIT_S", 0.5)
    monkeypatch.setattr(C, "_READER_LOCK_WAIT_S", 60.0)
    holder = C.get_conn(db, read_only=True)
    outcome: dict = {}

    def write():
        try:
            C.get_conn(db).close()
        except C.SharedDatabaseBusy:
            outcome["writer"] = "timed out"

    def read():
        _wait_until(lambda: C._WRITERS_WAITING.get(_key(db)))
        with C.get_conn(db, read_only=True) as con:
            outcome["reader"] = con.execute("SELECT count(*) FROM stress").fetchone()
        outcome["reader_at"] = time.monotonic()

    w = threading.Thread(target=write)
    r = threading.Thread(target=read)
    t0 = time.monotonic()
    w.start()
    r.start()
    w.join(timeout=30)
    r.join(timeout=30)
    holder.close()
    assert not w.is_alive() and not r.is_alive()
    assert outcome["writer"] == "timed out"
    assert outcome["reader"] == (0,)
    assert outcome["reader_at"] - t0 < 10, "queued reader was not woken"


def test_chained_calls_stay_guarded_and_keep_the_handle_alive(db):
    with C.get_conn(db, read_only=True) as reader:
        result = reader.execute("SELECT 1")
        assert result is reader
        with pytest.raises(C.ReadOnlyViolation):
            result.execute("DELETE FROM stress")
        rel = reader.sql("SELECT * FROM stress")
        with pytest.raises(C.ReadOnlyViolation):
            rel.insert_into("stress")
        with pytest.raises(C.ReadOnlyViolation):
            reader.table("stress").filter("i > 0").insert([1, 1])
        with pytest.raises(C.ReadOnlyViolation):
            rel.query("v", "DROP TABLE stress")
        assert rel.filter("i > 0").fetchall() == []
    # No name keeps the handle: the chain alone must keep its cursor open.
    for _ in range(20):
        assert C.get_conn(db).execute("SELECT 42").fetchone() == (42,)
        assert C.get_conn(db, read_only=True).sql("SELECT 7").fetchall() == [(7,)]
        gc.collect()
    gc.collect()


def test_cursor_returns_a_tracked_handle(db):
    parent = C.get_conn(db, read_only=True)
    child = parent.cursor()
    assert isinstance(child, C.HubConnection) and child.read_only
    with pytest.raises(C.ReadOnlyViolation):
        child.execute("INSERT INTO stress VALUES (1, 1)")
    parent.close()
    # The child is counted: the instance stays open for it, then goes.
    assert child.execute("SELECT count(*) FROM stress").fetchone() == (0,)
    assert C._REGISTRY[_key(db)].refs == 1
    child.close()
    assert C._REGISTRY == {}
    with pytest.raises(duckdb.ConnectionException):
        parent.cursor()


def test_open_existing_never_opens_the_instance_read_write(db, monkeypatch):
    # A writer is queued behind a read-only instance; when it drains, an
    # open_existing() reader must not open the next (read-write) instance
    # itself, because that would hand the writer an unmigrated database.
    migrations = []
    real_migrate = C.migrate
    monkeypatch.setattr(C, "migrate", lambda con: migrations.append(1) or real_migrate(con))
    holder = C.get_conn(db, read_only=True)
    got: dict = {}

    def write():
        with C.get_conn(db) as con:
            con.execute("INSERT INTO stress VALUES (5, 5)")
            got["writer"] = True

    def peek():
        with C.open_existing(db) as con:
            got["peek_ro"] = con.read_only
            got["peek_mode_ro"] = C._REGISTRY[_key(db)].read_only

    w = threading.Thread(target=write)
    w.start()
    _wait_until(lambda: C._WRITERS_WAITING.get(_key(db)))
    p = threading.Thread(target=peek)
    p.start()
    time.sleep(0.2)
    holder.close()
    w.join(timeout=30)
    p.join(timeout=30)
    assert not w.is_alive() and not p.is_alive()
    assert got["writer"] is True
    assert got["peek_ro"] is True
    assert got["peek_mode_ro"] is False  # it joined the writer's instance
    assert migrations == [1]  # the read-write instance was migrated once


def test_open_existing_does_not_wait_long_for_another_opener(db, monkeypatch):
    monkeypatch.setattr(C, "_OPEN_EXISTING_WAIT_S", 0.3)
    release = threading.Event()
    real_open_root = C._open_root

    def slow_open_root(*args, **kwargs):
        release.wait(30)
        return real_open_root(*args, **kwargs)

    monkeypatch.setattr(C, "_open_root", slow_open_root)
    opener = threading.Thread(target=lambda: C.get_conn(db).close())
    opener.start()
    _wait_until(lambda: _key(db) in C._REGISTRY)
    t0 = time.monotonic()
    with pytest.raises(C.SharedDatabaseBusy):
        C.open_existing(db)
    assert time.monotonic() - t0 < 5
    release.set()
    opener.join(timeout=30)
    assert not opener.is_alive()


def test_reader_taking_the_writer_lock_fails_fast_instead_of_deadlocking(db, monkeypatch):
    from market_data_hub.lock import db_write_lock

    monkeypatch.setattr(C, "_WRITER_LOCK_WAIT_S", 30.0)
    reader_has_handle = threading.Event()
    writer_has_lock = threading.Event()
    outcome: dict = {}

    def reader():
        with C.get_conn(db, read_only=True):
            reader_has_handle.set()
            writer_has_lock.wait(10)
            _wait_until(lambda: C._WRITERS_WAITING.get(_key(db)))
            t0 = time.monotonic()
            try:
                with db_write_lock(db, timeout=20):
                    outcome["reader"] = "got the lock"
            except C.SharedDatabaseBusy:
                outcome["reader"] = "refused"
            outcome["reader_s"] = time.monotonic() - t0

    def writer():
        reader_has_handle.wait(10)
        with db_write_lock(db, timeout=20):
            writer_has_lock.set()
            with C.get_conn(db) as con:
                con.execute("INSERT INTO stress VALUES (7, 7)")
                outcome["writer"] = "wrote"

    threads = [threading.Thread(target=reader), threading.Thread(target=writer)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads), "lock-ordering deadlock"
    assert outcome["reader"] == "refused" and outcome["reader_s"] < 5
    assert outcome["writer"] == "wrote"
