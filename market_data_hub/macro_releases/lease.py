"""Expiring run ownership, fenced by the store's existing writer file lock."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import time
from uuid import uuid4

from filelock import FileLock


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

    @contextmanager
    def guard(self):
        with FileLock(str(self.db) + ".writer.lock", timeout=0):
            yield

    def _read(self):
        # Malformed metadata fails closed; never guess whether a run is live.
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else None

    def _write(self):
        temporary = Path(str(self.path) + ".tmp")
        temporary.write_text(json.dumps({"token": self.token,
                                         "expires_at": self.clock() + self.timeout}), encoding="utf-8")
        temporary.replace(self.path)

    def __enter__(self):
        self.db.parent.mkdir(parents=True, exist_ok=True)
        with self.guard():
            state = self._read()
            if state and state["expires_at"] > self.clock():
                raise LeaseBusy("daily collector lease is live")
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
