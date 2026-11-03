"""Ingest weekly EIA inventories into a hub database with ingestion vintages."""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from market_data_hub.config_loader import get_eia_series
from market_data_hub.db.connection import get_conn
from market_data_hub.db.upsert import log_run, record_vintage, upsert
from market_data_hub.lock import DBLockTimeout, db_write_lock
from market_data_hub.sources import eia


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", required=True, type=Path, help="Absolute hub database path"
    )
    parser.add_argument(
        "--start",
        help="First observation date; defaults to initial history or a 95-day refresh",
    )
    parser.add_argument("--end", default=date.today().isoformat())
    args = parser.parse_args()
    try:
        eia.api_key()
    except ValueError:
        print("ERROR: EIA_API_KEY is required", file=sys.stderr)
        return 1
    if not args.db.is_absolute():
        print("ERROR: --db must be an absolute path", file=sys.stderr)
        return 2
    try:
        end = date.fromisoformat(args.end)
        if args.start and date.fromisoformat(args.start) > end:
            raise ValueError
    except ValueError:
        print("ERROR: invalid date range", file=sys.stderr)
        return 2
    run_id = "eia_" + uuid.uuid4().hex[:12]
    failed = False
    try:
        with db_write_lock(str(args.db)):
            con = get_conn(str(args.db))
            try:
                for meta in get_eia_series():
                    sid = meta["symbol"]
                    latest = con.execute(
                        "SELECT max(date) FROM macro_series WHERE series_id = ?", [sid]
                    ).fetchone()[0]
                    start = args.start or (
                        (latest - timedelta(days=95)).isoformat()
                        if latest
                        else "1982-01-01"
                    )
                    started = datetime.now(timezone.utc)
                    timer = time.perf_counter()
                    try:
                        frame = eia.fetch(start, end.isoformat(), meta=meta)
                        con.execute("BEGIN TRANSACTION")
                        try:
                            added, updated = (
                                (0, 0)
                                if frame.empty
                                else upsert(con, "macro_series", frame, outer_txn=True)
                            )
                            if not frame.empty:
                                record_vintage(
                                    con,
                                    "macro_series",
                                    frame,
                                    date.today().isoformat(),
                                    run_id=run_id,
                                )
                            log_run(
                                con,
                                run_id=run_id,
                                started_at=started,
                                source="eia",
                                symbol=sid,
                                rows_added=added,
                                rows_updated=updated,
                                status="empty" if frame.empty else "ok",
                                error_msg=None,
                                duration_sec=time.perf_counter() - timer,
                            )
                            con.execute("COMMIT")
                        except Exception:
                            con.execute("ROLLBACK")
                            raise
                        print(f"{sid}: {len(frame)} observations")
                    except Exception as exc:
                        # Only a status and series id may appear in request failure logs.
                        failed = True
                        error = (
                            str(exc)
                            if isinstance(exc, eia.EIARequestError)
                            else f"EIA collection failed; series {sid}"
                        )
                        log_run(
                            con,
                            run_id=run_id,
                            started_at=started,
                            source="eia",
                            symbol=sid,
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
            "Another writer holds the hub lock; skipping EIA collection",
            file=sys.stderr,
        )
        return 9
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
