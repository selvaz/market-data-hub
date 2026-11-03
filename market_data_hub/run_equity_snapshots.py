"""Shared CLI implementation for the two independent snapshot jobs."""
import argparse
import time
import uuid
from datetime import datetime, timezone

from market_data_hub import equity_snapshots as snapshots
from market_data_hub.db.connection import get_conn
from market_data_hub.db.upsert import log_run
from market_data_hub.lock import DBLockTimeout, db_write_lock


def main(kind):
    parser = argparse.ArgumentParser(description=f"Collect {kind} snapshots")
    parser.add_argument("--db", required=True, help="Absolute DuckDB path")
    args = parser.parse_args()
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    error = None
    partial = None
    rows = None
    try:
        rows = (snapshots.fetch_valuations(snapshots.symbols()) if kind == "valuation"
                else snapshots.fetch_breadth())
        if not rows:
            raise ValueError("source returned no observations")
        if kind == "valuation":
            missing = sorted(set(snapshots.symbols()) - {row[0] for row in rows})
            if missing:
                partial = "missing ETF quotes: " + ", ".join(missing)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    fetched = snapshots.now()
    try:
        with db_write_lock(args.db):
            con = get_conn(args.db)
            try:
                table = "etf_valuation_snapshots" if kind == "valuation" else "market_breadth_snapshots"
                before = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                if error is None:
                    if kind == "valuation":
                        snapshots.store_valuations(con, rows, fetched)
                    else:
                        snapshots.store_breadth(con, *rows, fetched)
                added = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] - before
                log_run(con, run_id=uuid.uuid4().hex, started_at=started,
                        source=f"equity_{kind}", symbol="*", rows_added=added,
                        rows_updated=0, status="error" if error else "degraded" if partial else "ok",
                        error_msg=error or partial,
                        duration_sec=time.monotonic() - t0)
                print(f"{kind}: {added} new rows; {error or partial or 'ok'}")
            finally:
                con.close()
    except DBLockTimeout as exc:
        print(exc)
        return 9
    return 1 if error else 2 if partial else 0
