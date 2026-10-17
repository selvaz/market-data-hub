"""Atomic, versioned migrations. Call only from the single writer."""

from pathlib import Path
import sqlite3

SCHEMA_VERSION = 3
APPEND_ONLY = (
    "value_vintages",
    "readings",
    "release_events",
    "schedule_versions",
    "resolution_versions",
    "consensus_readings",
    "claims",
    "claim_evidence",
    "discrepancies",
)


def migrate(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current > SCHEMA_VERSION:
        raise RuntimeError(f"unsupported macro releases schema {current}")
    if current == SCHEMA_VERSION:
        return
    # Do not use executescript: it commits any pending transaction implicitly.
    conn.execute("BEGIN IMMEDIATE")
    try:
        scripts = (["schema.sql"] if current == 0 else
                   (["migration_002.sql"] if current == 1 else []))
        scripts.append("migration_003.sql")
        for script in scripts:
            statement = ""
            for line in Path(__file__).with_name(script).read_text(encoding="utf-8").splitlines(True):
                statement += line
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ""
        for table in APPEND_ONLY if current == 0 else (("consensus_readings",) if current == 1 else ()):
            for operation in ("UPDATE", "DELETE"):
                conn.execute(
                    f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
                    f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"
                )
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
