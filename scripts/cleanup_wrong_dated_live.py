"""Remove exact wrong-date live duplicates from one known incident date."""

from __future__ import annotations

import argparse
from datetime import date

from market_data_hub.db.connection import get_conn
from market_data_hub.db.live_cleanup import remove_wrong_dated_live_duplicates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="DuckDB database path")
    parser.add_argument("--date", required=True, type=date.fromisoformat,
                        help="wrong live-row date (YYYY-MM-DD)")
    parser.add_argument("--apply", action="store_true",
                        help="delete candidates (default: dry-run count only)")
    args = parser.parse_args()

    con = get_conn(args.db, read_only=not args.apply)
    try:
        count = remove_wrong_dated_live_duplicates(
            con, args.date, dry_run=not args.apply
        )
    finally:
        con.close()
    action = "deleted" if args.apply else "would delete"
    print(f"{action} {count} exact live duplicate(s) dated {args.date}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
