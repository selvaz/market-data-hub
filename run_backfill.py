# -*- coding: utf-8 -*-
"""
run_backfill.py — initial historical load.

Forces the download from the backfill_start dates in settings.yaml, ignoring
the incremental logic. Start dates: Yahoo 2010, FRED 2000, Binance 2018.
Idempotent: the upsert replaces any rows already present, so it is
safe to re-run if interrupted.

Usage:
    python run_backfill.py                       # all sources
    python run_backfill.py --sources yahoo
    python run_backfill.py --sources binance --start 2020-01-01
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from market_data_hub.config_loader import get_settings  # noqa: E402
from market_data_hub.db.connection import get_conn  # noqa: E402
from market_data_hub.coverage.report import rebuild_coverage  # noqa: E402
from market_data_hub.runner import (  # noqa: E402
    run_yahoo, run_fred, run_binance, run_macro_panel, _log)
import uuid  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="market_data_hub historical backfill")
    p.add_argument("--sources", nargs="+",
                   choices=["yahoo", "fred", "binance", "macro_panel"],
                   default=["yahoo", "fred", "binance", "macro_panel"])
    p.add_argument("--start", help="override start date for ALL sources")
    p.add_argument("--db", help="DuckDB DB path")
    args = p.parse_args()

    cfg = get_settings()
    run_id = "backfill_" + uuid.uuid4().hex[:8]
    con = get_conn(args.db)
    try:
        if "yahoo" in args.sources:
            s = args.start or cfg["backfill_start"]["yahoo"]
            run_yahoo(con, cfg, run_id, start_override=s)
        if "fred" in args.sources:
            s = args.start or cfg["backfill_start"]["fred"]
            run_fred(con, cfg, run_id, start_override=s)
        if "binance" in args.sources:
            s = args.start or cfg["backfill_start"]["binance"]
            run_binance(con, cfg, run_id, start_override=s)
        if "macro_panel" in args.sources:
            sy = int((args.start or cfg["backfill_start"]["fred"])[:4])
            run_macro_panel(con, cfg, run_id, start_year=sy)

        _log("Rebuilding coverage_report...")
        n = rebuild_coverage(con, run_id)
        _log(f"coverage_report: {n} series")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
