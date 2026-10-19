"""Read-only shadow cards and coverage CLI; explicit database path required."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3

from .read_api import ReadAPI
from .store import open_reader


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("cards", "coverage"):
        command = commands.add_parser(name)
        command.add_argument("--db", required=True, type=Path)
        command.add_argument("--since", required=True)
        command.add_argument("--until", required=True)
        command.add_argument("--as-known-at")
        command.add_argument("--json", action="store_true", help="Emit JSON (also the default)")
        if name == "cards":
            command.add_argument("--indicator", action="append", dest="indicators")
            command.add_argument("--geography")
    review = commands.add_parser("review-queue", help="List provisional issuer releases needing review")
    review.add_argument("--db", required=True, type=Path)
    review.add_argument("--family")
    resolve = commands.add_parser("resolve-worklist", help="Resolve imported evidence on an explicit local copy")
    resolve.add_argument("--db", required=True, type=Path)
    resolve.add_argument("--dry-run", action="store_true")
    resolve.add_argument("--as-of", help="UTC evaluation cutoff (default: now)")
    resolve.add_argument("--lateness-days", type=int, default=14, help="Retry window, at least 1 day (default: 14)")
    resolve.add_argument("--revert", metavar="RUN_ID", help="Restore one run's original worklist state atomically")
    args = parser.parse_args(argv)
    try:
        if args.command == "resolve-worklist":
            from .worklist import execute
            result = execute(args.db, dry_run=args.dry_run, as_of=args.as_of,
                             lateness_days=args.lateness_days, revert=args.revert)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        with open_reader(args.db) as store:
            if args.command == "review-queue":
                tables = {r[0] for r in store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                rows = [] if "review_queue" not in tables else [dict(r) for r in store.conn.execute(
                    "SELECT * FROM review_queue" + (" WHERE family=?" if args.family else "") +
                    " ORDER BY recorded_at,id", (args.family,) if args.family else ())]
                for row in rows:
                    row["reasons"] = json.loads(row["reasons"])
                print(json.dumps({"review_queue": rows}, indent=2))
                return 0
            api = ReadAPI(store)
            kwargs = {"as_known_at": args.as_known_at}
            if args.command == "cards":
                kwargs.update(indicators=args.indicators, geography=args.geography)
                result = api.release_cards(args.since, args.until, **kwargs)
            else:
                result = api.coverage(args.since, args.until, **kwargs)
    except (ValueError, RuntimeError, sqlite3.Error) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
