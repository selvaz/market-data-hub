"""Inventory and safely retire an explicitly selected empty listing duplicate.

Dry run (the default):
    python scripts/resolve_duplicate_listings.py --db DB_PATH --symbol BCI

Apply only after reviewing the listing IDs and price counts:
    python scripts/resolve_duplicate_listings.py --db DB_PATH --symbol BCI \
        --keep-listing-id lst_... --deactivate-listing-id lst_... --apply

The default path opens DuckDB read-only. Applying uses the hub's writer lock
and the transactional listing-management API in db.identity.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from market_data_hub.db.connection import get_conn, resolve_db_path  # noqa: E402
from market_data_hub.db.identity import (  # noqa: E402
    duplicate_active_listings,
    resolve_empty_duplicate_listing,
)
from market_data_hub.lock import db_write_lock  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inventory or retire an explicitly selected empty duplicate listing")
    parser.add_argument("--db", help="DuckDB path (defaults to hub settings)")
    parser.add_argument("--symbol", help="limit the inventory to this exact symbol")
    parser.add_argument("--keep-listing-id",
                        help="active listing ID to retain (required with --apply)")
    parser.add_argument("--deactivate-listing-id",
                        help="empty listing ID to retire (required with --apply)")
    parser.add_argument("--apply", action="store_true",
                        help="apply the reviewed resolution; default is read-only dry run")
    return parser


def _format_day(value) -> str:
    return value.isoformat() if value is not None else "-"


def main(argv: Optional[List[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)

    if args.apply:
        missing = [name for name, value in (
            ("--symbol", args.symbol),
            ("--keep-listing-id", args.keep_listing_id),
            ("--deactivate-listing-id", args.deactivate_listing_id),
        ) if not value]
        if missing:
            parser.error("--apply requires " + ", ".join(missing))
    elif args.keep_listing_id or args.deactivate_listing_id:
        parser.error("listing IDs are accepted only with --apply")

    if not args.apply:
        db_path = resolve_db_path(args.db)
        if not Path(db_path).is_file():
            parser.error(f"database file does not exist: {db_path}")
        print("DRY RUN (read_only=True); no database changes will be made.")
        # Do not use get_conn(read_only=True) here: that convenience helper
        # creates and initializes a missing database before opening it.
        # DuckDB's direct read_only connection also protects against the file
        # disappearing between the existence check and connection.
        try:
            con = duckdb.connect(db_path, read_only=True)
        except duckdb.Error as exc:
            parser.error(f"could not open database read-only at {db_path}: {exc}")
        try:
            rows = duplicate_active_listings(con, symbol=args.symbol)
        finally:
            con.close()
        if not rows:
            print("No active duplicate listings found.")
            return 0
        print("Active duplicate listings (review IDs and price counts before apply):")
        for row in rows:
            print(
                f"  {row['symbol']}: listing_id={row['listing_id']} "
                f"exchange={row['exchange']!r} provider={row['provider']!r} "
                f"provider_symbol={row['provider_symbol']!r} "
                f"instrument_id={row['instrument_id']} "
                f"price_rows={row['price_rows']} "
                f"range={_format_day(row['first_date'])}.."
                f"{_format_day(row['last_date'])}"
            )
        return 0

    with db_write_lock(args.db):
        con = get_conn(args.db)
        try:
            result = resolve_empty_duplicate_listing(
                con,
                symbol=args.symbol,
                keep_listing_id=args.keep_listing_id,
                deactivate_listing_id=args.deactivate_listing_id,
            )
        finally:
            con.close()
    action = "updated" if result["changed"] else "already resolved"
    print(
        f"{action}: {result['symbol']} keeps {result['keep_listing_id']}; "
        f"deactivated {result['deactivated_listing_id']}; "
        f"duplicate price rows={result['price_rows']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
