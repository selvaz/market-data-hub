# -*- coding: utf-8 -*-
"""
run_backfill.py — initial historical load.

Thin CLI over ``runner.run(mode="backfill")``: forces the download from the
backfill_start dates in settings.yaml (Yahoo 2010, FRED 2000, Binance 2018)
instead of the incremental logic. Running through the runner also means the
backfill takes the writer lock (it cannot corrupt a concurrent scheduled run)
and rebuilds both coverage tables. Idempotent: the upsert replaces any rows
already present, so it is safe to re-run if interrupted.

Usage:
    python run_backfill.py                       # all sources
    python run_backfill.py --sources yahoo
    python run_backfill.py --sources binance --start 2020-01-01
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from market_data_hub.runner import run  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="market_data_hub historical backfill")
    p.add_argument("--sources", nargs="+",
                   choices=["yahoo", "fred", "binance", "macro_panel", "factors"],
                   default=["yahoo", "fred", "binance", "macro_panel","factors"])
    p.add_argument("--start", help="override start date for ALL sources")
    p.add_argument("--db", help="DuckDB DB path")
    args = p.parse_args()

    run(mode="backfill", sources=args.sources, start_override=args.start,
        db_path=args.db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
