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


class _WatchedCondition(threading.Condition):
    """The registry condition, recording which threads are blocked in it."""

    def __init__(self):
        super().__init__(threading.Lock())
        self.waiting = set()

    def wait(self, timeout=None):
        me = threading.get_ident()
        self.waiting.add(me)
        try:
            return super().wait(timeout)
        finally:
            self.waiting.discard(me)


@pytest.fixture
def watched(db, monkeypatch):
    cond = _WatchedCondition()
    monkeypatch.setattr(C, "_REGISTRY_COND", cond)
    return cond


@pytest.mark.parametrize("opener", ["open_existing", "get_conn_read_only"])
@pytest.mark.parametrize("attempt", range(5))
def test_a_reader_queued_with_a_writer_never_opens_read_write(
        db, watched, monkeypatch, opener, attempt):
    # A writer is queued behind a read-only instance and a reader is queued
    # behind the writer. When the instance drains, both race for the empty
    # slot: the reader must never open the (read-write) instance itself or
    # migrate it -- only the writer may -- and must then join it.
    holder = C.get_conn(db, read_only=True)  # the read-only instance
    opens, migrations = [], []
    real_open_root, real_migrate = C._open_root, C.migrate

    def open_root(path, *, read_only, create):
        opens.append((threading.current_thread().name, read_only))
        return real_open_root(path, read_only=read_only, create=create)

    def migrate(con):
        migrations.append(threading.current_thread().name)
        return real_migrate(con)

    monkeypatch.setattr(C, "_open_root", open_root)
    monkeypatch.setattr(C, "migrate", migrate)
    got: dict = {}

    def write():
        with C.get_conn(db) as con:
            con.execute("INSERT INTO stress VALUES (5, 5)")
            got["writer"] = True

    def read():
        con = (C.open_existing(db) if opener == "open_existing"
               else C.get_conn(db, read_only=True))
        with con:
            got["reader_ro"] = con.read_only
            got["instance_ro"] = C._REGISTRY[_key(db)].read_only

    monkeypatch.setattr(C, "_OPEN_EXISTING_WAIT_S", 30.0)
    w = threading.Thread(target=write, name="writer")
    r = threading.Thread(target=read, name="reader")
    w.start()
    _wait_until(lambda: w.ident in watched.waiting)
    r.start()
    _wait_until(lambda: r.ident in watched.waiting)  # queued, not sleeping on luck
    holder.close()
    w.join(timeout=30)
    r.join(timeout=30)
    assert not w.is_alive() and not r.is_alive()
    assert got["writer"] is True and got["reader_ro"] is True
    # Either the reader joined the writer's instance, or the writer had
    # already closed and the reader opened a fresh read-only one. Never may
    # the reader open read-write or migrate.
    if got["instance_ro"]:
        assert opens == [("writer", False), ("reader", True)]
    else:
        assert opens == [("writer", False)]
    assert migrations == ["writer"]


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


def test_from_query_duplicate_and_unknown_attributes_cannot_bypass_the_guard(db):
    with C.get_conn(db) as writer, C.get_conn(db, read_only=True) as reader:
        with pytest.raises(C.ReadOnlyViolation):
            reader.from_query("DELETE FROM stress")
        with pytest.raises(C.ReadOnlyViolation):
            reader.execute(duckdb.extract_statements("INSERT INTO stress VALUES (1, 1)")[0])
        with pytest.raises(C.ReadOnlyViolation):
            reader.execute("COPY stress FROM 'nowhere.csv'")
        with pytest.raises(C.ReadOnlyViolation):
            reader.execute("CHECKPOINT")
        with pytest.raises(C.ReadOnlyViolation):
            reader.checkpoint()
        with pytest.raises(C.ReadOnlyViolation):
            reader.sql("SELECT 1 AS a").create_view("v")
        dup = reader.duplicate()
        assert isinstance(dup, C.HubConnection) and dup.read_only
        assert C._REGISTRY[_key(db)].refs == 3
        with pytest.raises(C.ReadOnlyViolation):
            dup.execute("INSERT INTO stress VALUES (1, 1)")
        dup.close()
        for name in ("install_extension", "load_extension", "query_progress",
                     "register_filesystem", "no_such_method"):
            with pytest.raises(AttributeError, match="not exposed"):
                getattr(reader, name)
        with pytest.raises(AttributeError, match="not exposed"):
            reader.sql("SELECT 1 AS a").brand_new_relation_method
        # Reads still work through the allow-list.
        assert reader.from_query("SELECT count(*) FROM stress").fetchone() == (0,)
        assert reader.table("stress").count("*").fetchone() == (0,)
        assert reader.execute("SELECT 1").description[0][0] == "1"
        writer.execute("INSERT INTO stress VALUES (1, 1)")
        assert writer.execute("SELECT count(*) FROM stress").fetchone() == (1,)


def test_handle_passed_to_another_thread_still_breaks_the_lock_cycle(db, watched):
    # Ownership follows use: thread A creates a read-only handle and hands it
    # to B; B uses it, so B (not A) is the thread that must not wait for the
    # advisory writer lock while a writer holding it waits for that handle.
    from market_data_hub.lock import db_write_lock

    handoff, a_checked = [], {}
    created = threading.Event()
    writer_waiting = threading.Event()
    outcome: dict = {}

    def thread_a():
        handoff.append(C.get_conn(db, read_only=True))
        created.set()
        writer_waiting.wait(30)
        a_checked["holds"] = C.thread_holds_read_only_instance(db)

    def thread_b():
        created.wait(30)
        con = handoff[0]
        con.execute("SELECT count(*) FROM stress").fetchone()
        writer_waiting.wait(30)
        t0 = time.monotonic()
        try:
            with db_write_lock(db, timeout=20):
                outcome["b"] = "got the lock"
        except C.SharedDatabaseBusy:
            outcome["b"] = "refused"
        outcome["b_s"] = time.monotonic() - t0
        con.close()

    def writer():
        created.wait(30)
        with db_write_lock(db, timeout=20):
            w = threading.current_thread().ident
            threading.Thread(
                target=lambda: (_wait_until(lambda: w in watched.waiting),
                                writer_waiting.set()),
                daemon=True).start()
            with C.get_conn(db) as con:
                con.execute("INSERT INTO stress VALUES (8, 8)")
                outcome["writer"] = "wrote"

    threads = [threading.Thread(target=f) for f in (thread_a, thread_b, writer)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads), "lock-ordering deadlock"
    assert outcome["b"] == "refused" and outcome["b_s"] < 5
    assert outcome["writer"] == "wrote"
    assert a_checked["holds"] is False  # the creator handed it over


def test_finalising_a_handle_while_the_registry_lock_is_held_does_not_deadlock(db):
    # A finaliser can run on a thread that holds the registry lock (an
    # allocation under the lock triggers garbage collection, or the scan in
    # used_by() drops the last reference). It must queue the release, not
    # wait for the lock it is already holding.
    done = threading.Event()

    def run():
        handle = C.get_conn(db, read_only=True)
        with C._REGISTRY_COND:
            del handle  # last reference: __del__ runs here, lock held
            gc.collect()
            assert C._REGISTRY[_key(db)].refs == 1  # release is queued, not lost
        C._drain_pending_releases()
        done.set()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=30)
    assert done.is_set(), "finaliser deadlocked on the registry lock"
    assert C._REGISTRY == {}


def test_handles_dropped_during_ownership_scans_never_deadlock(db):
    # Stress the same race without forcing it: threads drop handles without
    # closing them (some in reference cycles, finalised by gc on whichever
    # thread runs it) while others scan ownership under the lock.
    stop = threading.Event()
    errors: list[BaseException] = []

    def dropper():
        while not stop.is_set():
            try:
                h = C.get_conn(db, read_only=True)
                h.execute("SELECT 1")
                cycle: list = [h]
                cycle.append(cycle)
                del h, cycle
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

    def scanner():
        while not stop.is_set():
            try:
                C.thread_holds_read_only_instance(db)
                gc.collect()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

    threads = [threading.Thread(target=dropper, daemon=True) for _ in range(3)]
    threads += [threading.Thread(target=scanner, daemon=True) for _ in range(2)]
    for t in threads:
        t.start()
    time.sleep(3)
    stop.set()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "deadlock under finalisation"
    assert errors == []
    gc.collect()
    C._drain_pending_releases()
    assert C._REGISTRY == {}


def test_relation_query_moves_ownership_to_the_using_thread(db):
    handle = C.get_conn(db, read_only=True)
    rel = handle.sql("SELECT * FROM stress")
    seen = {}

    def other():
        rel.query("v", "SELECT count(*) FROM v").fetchall()
        seen["holds"] = C.thread_holds_read_only_instance(db)

    t = threading.Thread(target=other)
    t.start()
    t.join(timeout=30)
    assert seen["holds"] is True
    assert C.thread_holds_read_only_instance(db) is False
    handle.close()


def test_read_only_guard_refuses_state_changing_functions(db):
    with C.get_conn(db) as writer:
        writer.execute("CREATE SEQUENCE seq START 1")
        with C.get_conn(db, read_only=True) as reader:
            for sql in ("SELECT nextval('seq')",
                        "SELECT main.setval('seq', 10)",
                        "SELECT * FROM (SELECT nextval('seq') AS n) t",
                        "SELECT * FROM checkpoint()",
                        "SELECT * FROM force_checkpoint()"):
                with pytest.raises(C.ReadOnlyViolation):
                    reader.execute(sql)
            # Mentioning the names is not calling them; currval only reads.
            assert reader.execute("SELECT 'nextval(' AS x").fetchone() == ("nextval(",)
        assert writer.execute("SELECT nextval('seq')").fetchone() == (1,)
        with C.get_conn(db, read_only=True) as reader:
            assert reader.execute("SELECT currval('seq')").fetchone() == (1,)
