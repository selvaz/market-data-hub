"""Remove wrong-date live rows from one known incident date (dry run by default)."""

from __future__ import annotations

import argparse
from datetime import date

from market_data_hub.db.connection import get_conn
from market_data_hub.db.live_cleanup import (
    remove_non_trading_day_live_rows,
    remove_wrong_dated_live_duplicates,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="DuckDB database path")
    parser.add_argument("--date", required=True, type=date.fromisoformat,
                        help="wrong live-row date (YYYY-MM-DD)")
    parser.add_argument("--apply", action="store_true",
                        help="delete candidates (default: dry-run count only)")
    parser.add_argument(
        "--non-trading-day", action="store_true",
        help="confirm the date was a non-trading day for the affected listings; "
             "select live rows with earlier settled history and no same-day "
             "settled row, without requiring exact duplicate prices",
    )
    args = parser.parse_args()

    con = get_conn(args.db, read_only=not args.apply)
    try:
        cleanup = (remove_non_trading_day_live_rows if args.non_trading_day
                   else remove_wrong_dated_live_duplicates)
        count = cleanup(con, args.date, dry_run=not args.apply)
    finally:
        con.close()
    action = "deleted" if args.apply else "would delete"
    kind = "non-trading-day live row(s)" if args.non_trading_day else "exact live duplicate(s)"
    print(f"{action} {count} {kind} dated {args.date}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
