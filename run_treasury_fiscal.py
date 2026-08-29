# -*- coding: utf-8 -*-
"""
run_treasury_fiscal.py — collect U.S. Treasury Fiscal Data and ingest it.

Usage:
    python run_treasury_fiscal.py --db <path>                    # rolling window
    python run_treasury_fiscal.py --db <path> --lookback-days 30
    python run_treasury_fiscal.py --db <path> --start 2024-01-01 --end 2024-12-31
    python run_treasury_fiscal.py --db <path> --only debt

Three datasets, one run: the Daily Treasury Statement's operating cash
balance (the TGA and its neighbours), Debt to the Penny, and auction results.

**Why a rolling window and not "since yesterday".** The vendor revises: a
record_date already published can change, and auction rows land days after
the auction itself. The window therefore reaches back further than the
cadence it runs at, and the re-read costs nothing because every table is
keyed and upserted -- re-collecting a day already stored replaces it with
the vendor's current answer rather than duplicating it. Unlike the earnings
calendar, this source is a real archive: an old window can be re-read at any
time and returns the same rows, so a missed run is recoverable and a
backfill is just a wider --start/--end.

A partial failure is still a failure of the run (exit 1), but the datasets
that did land are already committed: they are independent tables and there
is nothing to be gained by discarding good rows because a sibling endpoint
was down.

--db is required and should be absolute: market-data-hub resolves a relative
path inside its own repository, so a runner invoked from elsewhere would
silently create an empty database there.
"""
import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from market_data_hub.db import connection as cx           # noqa: E402
from market_data_hub.db.upsert import upsert              # noqa: E402
from market_data_hub.sources import treasury_fiscal as tf  # noqa: E402

#: dataset name -> (fetch function, destination table)
DATASETS = {
    "cash": (tf.fetch_operating_cash_balance, "treasury_cash_balance"),
    "debt": (tf.fetch_debt_to_penny, "treasury_debt_outstanding"),
    "auctions": (tf.fetch_auctions, "treasury_auctions"),
}

#: Reaches back over a month by default. The DTS publishes on business days
#: with a one-day lag, auctions settle later still, and revisions land without
#: notice; a month of overlap makes a run that fails for a week self-healing
#: on the next success instead of leaving a hole nobody notices.
DEFAULT_LOOKBACK_DAYS = 35


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, type=Path,
                    help="Path to the market database. Absolute, please.")
    ap.add_argument("--start", help="Inclusive first record_date (YYYY-MM-DD).")
    ap.add_argument("--end", help="Inclusive last record_date (YYYY-MM-DD).")
    ap.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS,
                    help=f"Window size when --start is absent (default {DEFAULT_LOOKBACK_DAYS}).")
    ap.add_argument("--only", choices=sorted(DATASETS),
                    help="Collect one dataset instead of all three.")
    args = ap.parse_args()

    end = args.end or date.today().isoformat()
    start = args.start or (date.fromisoformat(end)
                           - timedelta(days=args.lookback_days)).isoformat()
    if start > end:
        print(f"ERROR: start {start} is after end {end}", file=sys.stderr)
        return 2

    scelti = [args.only] if args.only else list(DATASETS)
    print(f"db: {args.db}")
    print(f"window: {start} -> {end}  ({', '.join(scelti)})\n")

    con = cx.get_conn(str(args.db))
    fallite: list[str] = []
    try:
        for nome in scelti:
            fetch, tabella = DATASETS[nome]
            print(f"--- {nome} -> {tabella} ---")
            try:
                frame = fetch(start, end)
            except Exception as e:
                print(f"  FAILED: {type(e).__name__}: {str(e)[:160]}", file=sys.stderr)
                fallite.append(nome)
                continue
            if frame.empty:
                # Not an error: a window with no auctions, or a run over a
                # stretch of holidays, legitimately returns nothing.
                print("  0 rows (nothing published in this window)")
                continue
            upsert(con, tabella, frame)
            print(f"  {len(frame)} rows -> {tabella}")
    finally:
        con.close()

    print()
    _stampa_audit(args.db, scelti)
    if fallite:
        print(f"\nFAILED datasets: {', '.join(fallite)}", file=sys.stderr)
        return 1
    return 0


def _stampa_audit(db: Path, scelti: list[str]) -> None:
    """Row counts and the latest date now stored, per table."""
    con = cx.get_conn(str(db), read_only=True)
    print("=== audit ===")
    try:
        for nome in scelti:
            tabella = DATASETS[nome][1]
            colonna = "record_date"
            n = con.execute(f"SELECT COUNT(*) FROM {tabella}").fetchone()[0]
            ultimo = con.execute(
                f"SELECT MAX({colonna}) FROM {tabella}").fetchone()[0]
            print(f"  {tabella:26} {n:>8} rows   latest {ultimo}")
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
