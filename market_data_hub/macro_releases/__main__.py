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
    args = parser.parse_args(argv)
    try:
        with open_reader(args.db) as store:
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
