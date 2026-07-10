"""Generate the neutral market_data_hub country dashboard."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from market_data_hub.country_dashboard import write_dashboard  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate neutral country data dashboard")
    parser.add_argument("--db", help="DuckDB path; defaults to settings")
    parser.add_argument("--open", action="store_true", help="Open the generated HTML")
    args = parser.parse_args()

    path = write_dashboard(args.db)
    print(f"Country dashboard: {path}")
    if args.open:
        import webbrowser
        webbrowser.open(path.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
