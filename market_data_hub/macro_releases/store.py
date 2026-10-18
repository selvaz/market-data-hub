"""Small explicit SQLite API; every call completes its short transaction.

The old read views and as_known_at retain M4 placeholder selection. The M6
consumer methods delegate to the pure resolver/read API, without writing decisions.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Optional, Union

from filelock import FileLock

from . import models as m
from .migrations import READER_CEILING, READER_FLOOR, migrate

TABLES = {
    m.Indicator: "indicators",
    m.SourceSeries: "source_series",
    m.ReleaseEvent: "release_events",
    m.ScheduleVersion: "schedule_versions",
    m.ValueVintage: "value_vintages",
    m.Reading: "readings",
    m.ResolutionVersion: "resolution_versions",
    m.ConsensusReading: "consensus_readings",
    m.Claim: "claims",
    m.ClaimEvidence: "claim_evidence",
    m.Discrepancy: "discrepancies",
}


def connect_readonly(path: Union[str, Path]) -> sqlite3.Connection:
    """No migrations, mkdir, fallback database, or immutable=1 on a live WAL."""
    conn = sqlite3.connect(
        Path(path).resolve().as_uri() + "?mode=ro",
        uri=True,
        timeout=60,
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.execute("PRAGMA query_only = ON")
    return conn


class Store:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        writable: bool = False,
        lock: Optional[FileLock] = None,
    ):
        self.conn = conn
        self.writable = writable
        self._lock = lock
        self._batch = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        try:
            self.conn.close()
        finally:
            if self._lock is not None:
                self._lock.release()
                self._lock = None

    @contextmanager
    def transaction(self):
        if not self.writable:
            raise PermissionError("read-only macro releases handle")
        if self.conn.in_transaction:
            if self._batch:
                yield
                return
            raise RuntimeError("nested or long-lived transactions are not supported")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    @contextmanager
    def batch(self):
        """Atomically persist a fully fetched family; never perform I/O here."""
        if self._batch:
            raise RuntimeError("nested batches are not supported")
        with self.transaction():
            self._batch = True
            try:
                yield
            finally:
                self._batch = False

    def put(self, record: m.Contract) -> Union[int, str]:
        """Idempotent typed insert. Readings retain distinct raw-observation hashes.

        Vintage document hashes deduplicate transport retries; an unchanged value
        in the same publication is also a re-read, even if its bytes changed.
        An unchanged value in a NEW edition still creates a vintage.
        """
        table = TABLES[type(record)]
        data = record.model_dump()
        if isinstance(record, m.ConsensusReading):
            cutoff = record.release_cutoff_at
            data["pre_release_eligible"] = bool(cutoff and record.captured_at < cutoff)
            data["post_release"] = bool(
                cutoff
                and record.cutoff_precision == "exact"
                and record.captured_at >= cutoff
            )
        with self.transaction():
            if isinstance(record, m.ValueVintage):
                semantics = ("indicator_id", "value", "unit", "scale", "estimate_stage")
                provenance = ("verification_status", "extraction_method")
                same_document = self.conn.execute(
                    "SELECT * FROM value_vintages WHERE source_series_id=? AND reference_period=? "
                    "AND release_event_id=? AND document_hash=?",
                    (
                        record.source_series_id,
                        record.reference_period,
                        record.release_event_id,
                        record.document_hash,
                    ),
                ).fetchone()
                if same_document:
                    if any(same_document[key] != data[key] for key in semantics):
                        raise ValueError(
                            "same publication hash has conflicting value semantics"
                        )
                    if any(same_document[key] != data[key] for key in provenance):
                        raise ValueError(
                            "publication re-read has conflicting verification/extraction provenance"
                        )
                    return same_document["id"]
                previous = self.conn.execute(
                    "SELECT * FROM value_vintages WHERE source_series_id=? AND reference_period=? "
                    "AND release_event_id=? ORDER BY id DESC LIMIT 1",
                    (
                        record.source_series_id,
                        record.reference_period,
                        record.release_event_id,
                    ),
                ).fetchone()
                if previous and all(previous[key] == data[key] for key in semantics):
                    if any(previous[key] != data[key] for key in provenance):
                        raise ValueError(
                            "publication re-read has conflicting verification/extraction provenance"
                        )
                    return previous["id"]
            columns = ", ".join(data)
            placeholders = ", ".join("?" for _ in data)
            cursor = self.conn.execute(
                f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) ON CONFLICT DO NOTHING",
                tuple(data.values()),
            )
            if cursor.rowcount:
                return data.get("id", cursor.lastrowid)
            # Return the original record id on a retry, never the stale lastrowid.
            if "id" in data:
                row = self.conn.execute(
                    f"SELECT * FROM {table} WHERE id=?", (data["id"],)
                ).fetchone()
                if row is None or any(row[key] != value for key, value in data.items()):
                    raise ValueError(f"conflicting {table} identity")
                return row["id"]
            if isinstance(record, m.Reading):
                keys = (
                    "source_id",
                    "series_key",
                    "period_key",
                    "edition",
                    "content_hash",
                )
            elif isinstance(record, m.Discrepancy):
                keys = ("reading_id", "selected_vintage_id")
            else:
                keys = tuple(data)
            where = " AND ".join(f"{key} IS ?" for key in keys)
            row = self.conn.execute(
                f"SELECT id FROM {table} WHERE {where}",
                tuple(data[key] for key in keys),
            ).fetchone()
            if row is None:
                raise ValueError(f"conflicting {table} identity")
            return row["id"]

    def first_release(self, indicator_id: Optional[str] = None) -> list[dict]:
        return self._view("first_release", indicator_id)

    def release_cards(self, since, until, indicators=None, geography=None, as_known_at=None):
        from .read_api import ReadAPI
        return ReadAPI(self).release_cards(since, until, indicators, geography, as_known_at)

    def explain_empty(self, indicator, period_or_window, *, as_known_at=None):
        from .read_api import ReadAPI
        return ReadAPI(self).explain_empty(indicator, period_or_window, as_known_at=as_known_at)

    def coverage(self, since, until, *, as_known_at=None):
        from .read_api import ReadAPI
        return ReadAPI(self).coverage(since, until, as_known_at=as_known_at)

    def latest_official(self, indicator_id: Optional[str] = None) -> list[dict]:
        return self._view("latest_official", indicator_id)

    def _view(self, name: str, indicator_id: Optional[str]) -> list[dict]:
        where, params = (
            (" WHERE indicator_id=?", (indicator_id,)) if indicator_id else ("", ())
        )
        return [
            dict(row)
            for row in self.conn.execute(
                f"SELECT * FROM {name}{where} ORDER BY indicator_id, reference_period",
                params,
            )
        ]

    def as_known_at(
        self, timestamp: str, indicator_id: Optional[str] = None
    ) -> list[dict]:
        """Latest publication among values first available to us at/before t.

        Filter BEFORE ranking: a later download of an older issuer publication
        cannot leak into the historical result or supersede a newer edition.
        """
        where = " AND indicator_id=?" if indicator_id else ""
        params = (
            (m.utc(timestamp), indicator_id) if indicator_id else (m.utc(timestamp),)
        )
        return [
            dict(row)
            for row in self.conn.execute(
                "SELECT * FROM (SELECT v.*, ROW_NUMBER() OVER ("
                "PARTITION BY indicator_id, reference_period ORDER BY issuer_published_at DESC, "
                "first_seen_at DESC, id DESC) AS selection_rank FROM official_candidates v "
                f"WHERE first_seen_at <= ?{where}) WHERE selection_rank=1 "
                "ORDER BY indicator_id, reference_period",
                params,
            )
        ]


def open_reader(path: Union[str, Path]) -> Store:
    conn = connect_readonly(path)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if not READER_FLOOR <= version <= READER_CEILING:
        conn.close()
        raise RuntimeError("macro releases schema is outside the declared reader compatibility range")
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='schema_compatibility'").fetchone():
        compatibility = conn.execute("SELECT reader_floor,reader_ceiling FROM schema_compatibility WHERE schema_version=?", (version,)).fetchone()
        if compatibility is None or not compatibility[0] <= READER_FLOOR <= compatibility[1]:
            conn.close()
            raise RuntimeError("database changes are not additive for this reader contract")
    return Store(conn)


def open_writer(path: Union[str, Path], *, lock_timeout: float = 60) -> Store:
    """One cooperating writer per DB, across threads/processes; no default live path."""
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(path) + ".writer.lock", timeout=lock_timeout)
    lock.acquire()
    conn = None
    try:
        conn = sqlite3.connect(str(path), timeout=60, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 60000")
        conn.execute("PRAGMA journal_mode = WAL")
        migrate(conn)
        return Store(conn, writable=True, lock=lock)
    except BaseException:
        if conn is not None:
            conn.close()
        lock.release()
        raise
