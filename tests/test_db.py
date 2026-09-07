# -*- coding: utf-8 -*-
"""DB-path resolution and upsert behavior."""
from __future__ import annotations

import datetime as dt

import pandas as pd

from market_data_hub.db import connection as C
from market_data_hub.db.upsert import upsert


def test_resolve_prefers_explicit_then_env(monkeypatch, tmp_path):
    explicit = str(tmp_path / "explicit.duckdb")
    monkeypatch.setenv("MARKET_DATA_DB", str(tmp_path / "env.duckdb"))
    assert C.resolve_db_path(explicit) == explicit
    assert C.resolve_db_path() == str(tmp_path / "env.duckdb")


def test_default_db_is_repo_local():
    assert C._default_db().endswith("market_data.duckdb")
    assert C.resolve_db_path("market_data.duckdb").endswith("market_data.duckdb")


def test_upsert_is_idempotent(tmp_db):
    con = C.get_conn()
    rows = pd.DataFrame([{
        "date": dt.date(2024, 1, 1), "symbol": "SPY", "open": 1, "high": 2,
        "low": 0.5, "close": 1.5, "adj_close": 1.4, "volume": 100,
        "source": "yahoo", "is_live": False,
    }])
    added, updated = upsert(con, "prices_daily", rows)
    assert (added, updated) == (1, 0)
    added2, updated2 = upsert(con, "prices_daily", rows)   # same PK
    assert (added2, updated2) == (0, 1)
    assert con.execute("SELECT count(*) FROM prices_daily").fetchone()[0] == 1
    con.close()


def test_upsert_defaults_is_live_false_when_column_absent(tmp_db):
    # A provider fetch (Yahoo daily bars) yields no is_live column. It must be
    # written as FALSE, not NULL — read_prices / extract_series filter
    # ``is_live = FALSE``, and in SQL ``NULL = FALSE`` is NULL, so NULL bars are
    # silently invisible and a freshly ingested ticker would never appear.
    con = C.get_conn()
    rows = pd.DataFrame([{
        "date": dt.date(2024, 1, 1), "symbol": "NEWTKR", "open": 1, "high": 2,
        "low": 0.5, "close": 1.5, "adj_close": 1.4, "volume": 100, "source": "yahoo",
    }])  # deliberately NO is_live column
    upsert(con, "prices_daily", rows)
    live = con.execute("SELECT is_live FROM prices_daily WHERE symbol = 'NEWTKR'").fetchall()
    assert live and all(v[0] is False for v in live), live
    con.close()
    # end-to-end: the reader (which filters is_live = FALSE) now sees the bar
    from market_data_hub.reader import read_prices
    px = read_prices("NEWTKR", field="adj_close")
    assert not px.empty and "NEWTKR" in px.columns


def test_upsert_counts_are_truthful_under_intra_batch_pk_duplicates(tmp_db):
    # A source batch can repeat a primary key (e.g. the BIS euro-aggregate
    # broadcast used to duplicate (date, country) pairs): INSERT OR REPLACE
    # collapses those to one stored row, so the reported (added, updated)
    # must count distinct keys, not raw batch rows — this used to report a
    # constant phantom rows_added on every run.
    con = C.get_conn()
    base = {"open": 1, "high": 2, "low": 0.5, "close": 1.5, "adj_close": 1.4,
            "volume": 100, "source": "yahoo", "is_live": False}
    batch = pd.DataFrame([
        {"date": dt.date(2024, 1, 1), "symbol": "SPY", **base},
        {"date": dt.date(2024, 1, 1), "symbol": "SPY", **base, "close": 9.9},  # dup PK
        {"date": dt.date(2024, 1, 2), "symbol": "SPY", **base},
    ])
    added, updated = upsert(con, "prices_daily", batch)
    assert (added, updated) == (2, 0)   # 2 distinct keys, not 3 raw rows
    assert con.execute("SELECT count(*) FROM prices_daily").fetchone()[0] == 2
    # keep='last' matches INSERT OR REPLACE semantics: the later row wins
    stored = con.execute("SELECT close FROM prices_daily WHERE date = DATE '2024-01-01'").fetchone()[0]
    assert stored == 9.9
    added2, updated2 = upsert(con, "prices_daily", batch)   # replay: all existing
    assert (added2, updated2) == (0, 2)
    con.close()



def test_reader_waits_for_a_writer_holding_the_file(tmp_path, monkeypatch):
    # An ingestion run holds an exclusive lock for the length of its
    # transaction and DuckDB fails a reader outright instead of queueing it.
    # Backtests and reports are scheduled independently of ingestion, so a
    # lock that clears in seconds used to cost the whole run.
    import subprocess
    import sys
    import time

    import duckdb

    path = str(tmp_path / "locked.duckdb")
    duckdb.connect(path).close()
    holder = subprocess.Popen([
        sys.executable, "-c",
        f"import duckdb, time; c = duckdb.connect(r'{path}'); "
        "c.execute('create table x(a int)'); time.sleep(4); c.close()",
    ])
    try:
        time.sleep(1.0)
        monkeypatch.setattr(C, "_READER_LOCK_WAIT_S", 30.0)
        monkeypatch.setattr(C, "_READER_LOCK_POLL_S", 0.5)
        con = C._connect_read_only_waiting(path)     # would raise before
        con.close()
    finally:
        holder.wait()


def test_reader_still_raises_when_the_lock_outlives_the_budget(tmp_path, monkeypatch):
    # Waiting is bounded: a lock that never clears must surface, not hang.
    import subprocess
    import sys
    import time

    import duckdb
    import pytest

    path = str(tmp_path / "stuck.duckdb")
    duckdb.connect(path).close()
    holder = subprocess.Popen([
        sys.executable, "-c",
        f"import duckdb, time; c = duckdb.connect(r'{path}'); time.sleep(5); c.close()",
    ])
    try:
        time.sleep(1.0)
        monkeypatch.setattr(C, "_READER_LOCK_WAIT_S", 1.0)
        monkeypatch.setattr(C, "_READER_LOCK_POLL_S", 0.2)
        with pytest.raises(duckdb.IOException):
            C._connect_read_only_waiting(path)
    finally:
        holder.wait()


def test_writer_waits_then_connects_when_the_lock_clears(tmp_path, monkeypatch):
    import subprocess
    import sys
    import time

    import duckdb

    path = str(tmp_path / "writer-waits.duckdb")
    duckdb.connect(path).close()
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import duckdb, sys, time; "
            "c = duckdb.connect(sys.argv[1]); "
            "print('locked', flush=True); "
            "time.sleep(1.0); c.close()",
            path,
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "locked"
        monkeypatch.setattr(C, "_WRITER_LOCK_WAIT_S", 2.0)
        monkeypatch.setattr(C, "_WRITER_LOCK_POLL_S", 0.05)
        started = time.perf_counter()
        con = C._connect_read_write_waiting(path)
        waited = time.perf_counter() - started
        con.close()
        # Without this the test would also pass if the writer had connected
        # at once -- which is the very thing it exists to rule out.
        assert waited >= 0.3, f"the writer did not wait for the lock ({waited:.3f}s)"
    finally:
        holder.wait(timeout=5)


def test_writer_reraises_unrelated_io_error_immediately(monkeypatch):
    import time

    import duckdb
    import pytest

    def fail_immediately(*args, **kwargs):
        raise duckdb.IOException("IO Error: Permission denied")

    def unexpected_sleep(seconds):
        pytest.fail(f"non-lock IOException slept for {seconds} seconds")

    monkeypatch.setattr(C.duckdb, "connect", fail_immediately)
    monkeypatch.setattr(C.time, "sleep", unexpected_sleep)
    monkeypatch.setattr(C, "_WRITER_LOCK_WAIT_S", 30.0)

    started = time.perf_counter()
    with pytest.raises(duckdb.IOException, match="Permission denied"):
        C._connect_read_write_waiting("unreachable.duckdb")
    assert time.perf_counter() - started < 0.2


def test_writer_raises_when_the_lock_outlives_the_budget(tmp_path, monkeypatch):
    import subprocess
    import sys

    import duckdb
    import pytest

    path = str(tmp_path / "writer-times-out.duckdb")
    duckdb.connect(path).close()
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import duckdb, sys; "
            "c = duckdb.connect(sys.argv[1]); "
            "print('locked', flush=True); "
            "sys.stdin.readline(); c.close()",
            path,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "locked"
        monkeypatch.setattr(C, "_WRITER_LOCK_WAIT_S", 0.15)
        monkeypatch.setattr(C, "_WRITER_LOCK_POLL_S", 0.02)
        with pytest.raises(duckdb.IOException):
            C._connect_read_write_waiting(path)
    finally:
        assert holder.stdin is not None
        holder.stdin.write("\n")
        holder.stdin.flush()
        holder.wait(timeout=5)


def test_the_lock_message_of_every_platform_is_recognised():
    """The waiter must recognise a held lock on Linux as well as Windows.

    DuckDB reports the operating system's own wording, and the two share no
    substring: matching only Windows' phrasing made the waiter re-raise
    immediately on Linux, so the feature did nothing on the platform CI runs
    on -- caught by its own test failing there, not here.

    Both strings below were taken from a real conflict: the Linux one from
    the CI failure, the Windows one by holding the file from a second
    process on this machine.
    """
    windows = (
        r'IO Error: Cannot open file "C:\tmp\locked.duckdb": The process '
        "cannot access the file because it is being used by another process."
    )
    linux = (
        'IO Error: Could not set lock on file "/tmp/locked.duckdb": '
        "Conflicting lock is held in /opt/hostedtoolcache/Python/3.11.16/"
        "x64/bin/python3.11 (PID 2484)."
    )
    for msg in (windows, linux):
        assert any(m in msg for m in C._LOCK_HELD_MARKERS), msg


def test_an_unrelated_io_error_is_not_waited_out():
    """Waiting is only ever for a lock. A missing file, a permission error
    or a corrupt database are IOExceptions too, and spending the budget
    before re-raising them would be worse than failing at once."""
    for msg in (
        'IO Error: No files found that match the pattern "/tmp/absent.duckdb"',
        'IO Error: Cannot open file "/tmp/x.duckdb": Permission denied',
        "IO Error: The file is not a valid DuckDB database file",
    ):
        assert not any(m in msg for m in C._LOCK_HELD_MARKERS), msg


def test_writer_waits_out_a_reader_which_is_the_real_production_case(tmp_path,
                                                                     monkeypatch):
    """The holder that actually blocks writers here is a READER.

    Ten long-lived MCP servers hold the hub open read-only and take no
    advisory lock. A test that puts two writers in contention exercises a
    case that barely happens, and would not notice if DuckDB worded the
    reader-held lock differently.
    """
    import subprocess
    import sys
    import time

    import duckdb

    path = str(tmp_path / "reader-holds.duckdb")
    duckdb.connect(path).close()
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import duckdb, sys, time; "
            "c = duckdb.connect(sys.argv[1], read_only=True); "
            "print('locked', flush=True); "
            "time.sleep(1.0); c.close()",
            path,
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "locked"
        monkeypatch.setattr(C, "_WRITER_LOCK_WAIT_S", 5.0)
        monkeypatch.setattr(C, "_WRITER_LOCK_POLL_S", 0.05)
        started = time.perf_counter()
        con = C._connect_read_write_waiting(path)
        waited = time.perf_counter() - started
        con.close()
        assert waited >= 0.3, f"the writer did not wait for the reader ({waited:.3f}s)"
    finally:
        holder.wait(timeout=10)


def test_the_budget_is_an_upper_bound_even_with_a_longer_poll():
    """A poll longer than what is left must not outlive the budget.

    Checking the deadline and then sleeping a fixed interval lets the call
    return after the budget has expired, or raise a whole poll late. Either
    way the number the caller set would not be the bound it claims to be.
    """
    import time

    import duckdb
    import pytest

    def always_locked(*args, **kwargs):
        raise duckdb.IOException(
            'IO Error: Cannot open file "x.duckdb": The process cannot access '
            "the file because it is being used by another process."
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(C.duckdb, "connect", always_locked)
        started = time.perf_counter()
        with pytest.raises(duckdb.IOException):
            C._connect_waiting("x.duckdb", read_only=False,
                               budget_s=0.2, poll_s=30.0)
        elapsed = time.perf_counter() - started
    assert elapsed < 1.0, f"slept past the budget ({elapsed:.3f}s of 0.2s)"


def test_a_path_that_reads_like_a_lock_message_is_not_a_lock():
    """The path is inside the message, so it must not be matched.

    A database under a directory named like the wording we look for would
    otherwise make every IO error on it -- a missing file, a permission
    problem -- look like a held lock, and be retried for five minutes before
    raising anyway.
    """
    import duckdb

    lock = duckdb.IOException(
        'IO Error: Cannot open file "/data/hub.duckdb": The process cannot '
        "access the file because it is being used by another process."
    )
    impostor = duckdb.IOException(
        'IO Error: Cannot open file "/srv/being used by another process/'
        'hub.duckdb": Permission denied'
    )
    assert C._is_lock_held(lock)
    assert not C._is_lock_held(impostor)


def test_a_nonsense_budget_falls_back_instead_of_waiting_forever():
    """``monotonic() >= nan`` is always false: the loop would never end."""
    import pytest

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MARKET_DATA_WRITER_LOCK_WAIT_S", "nan")
        assert C._budget_from_env("MARKET_DATA_WRITER_LOCK_WAIT_S", "300") == 300.0
        mp.setenv("MARKET_DATA_WRITER_LOCK_WAIT_S", "inf")
        assert C._budget_from_env("MARKET_DATA_WRITER_LOCK_WAIT_S", "300") == 300.0
        mp.setenv("MARKET_DATA_WRITER_LOCK_WAIT_S", "-1")
        assert C._budget_from_env("MARKET_DATA_WRITER_LOCK_WAIT_S", "300") == 300.0
        mp.setenv("MARKET_DATA_WRITER_LOCK_WAIT_S", "not a number")
        assert C._budget_from_env("MARKET_DATA_WRITER_LOCK_WAIT_S", "300") == 300.0
        mp.setenv("MARKET_DATA_WRITER_LOCK_WAIT_S", "12.5")
        assert C._budget_from_env("MARKET_DATA_WRITER_LOCK_WAIT_S", "300") == 12.5
