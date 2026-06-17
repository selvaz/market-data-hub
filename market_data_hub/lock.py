# -*- coding: utf-8 -*-
"""
lock.py — cross-process write lock for the DuckDB file.

DuckDB allows only a single writer per database file; a second writer crashes
with an IO error. The EOD task (22:00) and the hourly live task can overlap at
the day boundary, so writers serialize on an advisory file lock placed next to
the database. Readers (read_only=True) are unaffected and never take the lock.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from market_data_hub.db.connection import _resolve_db_path

# Default seconds to wait for a concurrent writer before giving up. Kept short:
# if another writer holds the DB, the right move is to skip this run cleanly
# rather than queue behind a potentially long EOD download.
DEFAULT_TIMEOUT = 30.0


def db_lock_path(db_path: Optional[str] = None) -> str:
    """Path of the advisory lock file for a given (or resolved) DB path."""
    return str(Path(_resolve_db_path(db_path)).with_suffix(".lock"))


class DBLockTimeout(RuntimeError):
    """Raised when the writer lock cannot be acquired within the timeout."""


@contextmanager
def db_write_lock(db_path: Optional[str] = None,
                  timeout: float = DEFAULT_TIMEOUT) -> Iterator[None]:
    """Hold the writer lock for the database file for the duration of the block.

    Raises DBLockTimeout if another writer holds it past ``timeout``. If
    ``filelock`` is not installed the lock is a no-op (best effort), so the
    pipeline still runs in minimal environments / the offline test suite.
    """
    try:
        from filelock import FileLock, Timeout
    except ImportError:  # pragma: no cover - filelock listed in requirements
        yield
        return

    lock = FileLock(db_lock_path(db_path), timeout=timeout)
    try:
        lock.acquire()
    except Timeout as exc:  # pragma: no cover - timing dependent
        raise DBLockTimeout(
            f"Another writer holds the DB lock ({db_lock_path(db_path)}); "
            f"skipping this run.") from exc
    try:
        yield
    finally:
        lock.release()
