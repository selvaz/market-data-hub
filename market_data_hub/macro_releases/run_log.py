"""Append-only daily collection lifecycle and family outcomes."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import socket

from market_data_hub import __version__

from .models import utc


def now():
    return utc(datetime.now(timezone.utc).isoformat())


def start(store, run_id, families, parameters):
    with store.transaction():
        store.conn.execute("INSERT INTO collect_runs VALUES (?,?,NULL,'running',?,?,?,?)",
                           (run_id, now(), json.dumps(families), json.dumps(parameters),
                            __version__, socket.gethostname()))


def finish(store, run_id, status):
    with store.transaction():
        store.conn.execute(
            "INSERT INTO collect_runs SELECT run_id,started_at,?,?,families,parameters,code_version,host "
            "FROM collect_runs WHERE run_id=? AND status='running'",
            (now(), status, run_id))


def family_result(store, run_id, family, result):
    with store.transaction():
        store.conn.execute(
            "INSERT INTO collect_run_families "
            "(run_id,family,status,finished_at,error_type,error_message,vintages_added,"
            "readings_added,readings_skipped,fetch_since,fetch_until) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, family, result["status"], now(), result.get("error_type"), result.get("error_message"),
             result["vintages_added"], result["readings_added"], result["readings_skipped"],
             result["fetch_since"], result["fetch_until"]))


def recover(store):
    """Called only by a new fenced owner; retain committed family successes."""
    rows = store.conn.execute(
        "SELECT * FROM collect_runs r WHERE status='running' AND NOT EXISTS "
        "(SELECT 1 FROM collect_runs t WHERE t.run_id=r.run_id AND t.status!='running')").fetchall()
    with store.batch():
        for row in rows:
            parameters = json.loads(row["parameters"])
            done = {r[0] for r in store.conn.execute(
                "SELECT family FROM collect_run_families WHERE run_id=?", (row["run_id"],))}
            for family in json.loads(row["families"]):
                if family not in done:
                    family_result(store, row["run_id"], family, dict(
                        status="failed", error_type="InterruptedRun",
                        error_message="Previous run ended without a family outcome; recovered by next lease owner",
                        vintages_added=0, readings_added=0, readings_skipped=0,
                        **parameters["fetch_windows"][family]))
            finish(store, row["run_id"], "failed")
