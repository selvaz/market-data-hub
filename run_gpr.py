"""Ingest the authors' daily and monthly GPR workbooks into macro_series."""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from market_data_hub.config_loader import get_gpr_series
from market_data_hub.db.connection import get_conn
from market_data_hub.db.upsert import log_run, record_vintage, upsert
from market_data_hub.lock import DBLockTimeout, db_write_lock
from market_data_hub.sources import gpr


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", required=True, type=Path, help="Absolute hub database path"
    )
    args = parser.parse_args()
    if not args.db.is_absolute():
        print("ERROR: --db must be an absolute path", file=sys.stderr)
        return 2
    run_id = "gpr_" + uuid.uuid4().hex[:12]
    failed = False
    try:
        with db_write_lock(str(args.db)):
            con = get_conn(str(args.db))
            try:
                for frequency in ("D", "M"):
                    started = datetime.now(timezone.utc)
                    timer = time.perf_counter()
                    try:
                        frame = gpr.fetch(frequency, get_gpr_series())
                        fetched = datetime.now(timezone.utc)
                        frame["updated_at"] = fetched
                        con.execute("BEGIN TRANSACTION")
                        try:
                            added, updated = upsert(
                                con, "macro_series", frame, outer_txn=True
                            )
                            record_vintage(
                                con,
                                "macro_series",
                                frame,
                                fetched.date().isoformat(),
                                run_id=run_id,
                            )
                            log_run(
                                con,
                                run_id=run_id,
                                started_at=started,
                                source="gpr",
                                symbol=frequency,
                                rows_added=added,
                                rows_updated=updated,
                                status="ok",
                                error_msg=None,
                                duration_sec=time.perf_counter() - timer,
                            )
                            con.execute("COMMIT")
                        except Exception:
                            con.execute("ROLLBACK")
                            raise
                        print(f"GPR {frequency}: {len(frame)} observations")
                    except Exception as exc:
                        failed = True
                        error = f"GPR {frequency} collection failed: {exc}"
                        log_run(
                            con,
                            run_id=run_id,
                            started_at=started,
                            source="gpr",
                            symbol=frequency,
                            rows_added=0,
                            rows_updated=0,
                            status="error",
                            error_msg=error,
                            duration_sec=time.perf_counter() - timer,
                        )
                        print(error, file=sys.stderr)
            finally:
                con.close()
    except DBLockTimeout:
        print(
            "Another writer holds the hub lock; skipping GPR collection",
            file=sys.stderr,
        )
        return 9
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
