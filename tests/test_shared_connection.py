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
