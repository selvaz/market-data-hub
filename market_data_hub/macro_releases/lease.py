"""Expiring run ownership, fenced by the store's existing writer file lock."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
import math
from pathlib import Path
import time
from uuid import uuid4

from filelock import FileLock, Timeout


class LeaseBusy(RuntimeError):
    pass


class LeaseLost(RuntimeError):
    pass


class RunLease:
    """Renew between bounded requests; validate under the writer lock before writes.

    Expiration permits takeover, but never permits concurrent writes: a previous
    owner must still present its token while holding the same writer file lock.
    """

    def __init__(self, db, *, timeout=1800, clock=time.time):
        self.db = Path(db).resolve()
        self.path = Path(str(self.db) + ".daily.lease.json")
        self.token = uuid4().hex
        self.timeout = timeout
        self.clock = clock
        self.events = []

    @contextmanager
    def guard(self):
        lock = FileLock(str(self.db) + ".writer.lock", timeout=5)
        # Writer contention is not loss of the run lease. Short waits keep a
        # concurrent start/importer from spuriously killing the live owner.
        while True:
            try:
                lock.acquire()
                break
            except Timeout:
                continue
        try:
            yield
        finally:
            lock.release()

    def _read(self):
        if not self.path.exists():
            return None
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(state.get("token"), str) or not math.isfinite(float(state["expires_at"])):
                raise ValueError("Invalid lease fields")
            state["expires_at"] = float(state["expires_at"])
            return state
        except (ValueError, KeyError, TypeError, AttributeError):
            return {"token": None, "expires_at": self.path.stat().st_mtime + self.timeout,
                    "corrupt": True}

    def _write(self):
        temporary = Path(str(self.path) + ".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump({"token": self.token, "expires_at": self.clock() + self.timeout}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.path)

    def __enter__(self):
        self.db.parent.mkdir(parents=True, exist_ok=True)
        with self.guard():
            state = self._read()
            if state and state["expires_at"] > self.clock():
                raise LeaseBusy("daily collector lease is live")
            if state and state.get("corrupt"):
                self.events.append("corrupt_lease_reclaimed_using_mtime")
            elif state:
                self.events.append("expired_lease_reclaimed")
            self._write()
        return self

    def validate(self):
        """Caller holds the writer lock, including for token renewal."""
        state = self._read()
        if not state or state["token"] != self.token or state["expires_at"] <= self.clock():
            raise LeaseLost("daily collector lease expired or changed owner")
        self._write()

    def renew(self):
        with self.guard():
            self.validate()

    def __exit__(self, *_):
        with self.guard():
            state = self._read()
            if state and state["token"] == self.token:
                self.path.unlink()
