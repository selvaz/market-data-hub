# -*- coding: utf-8 -*-
"""
run_cftc_cot.py — collect CFTC Commitments of Traders positioning and ingest it.

Usage:
    python run_cftc_cot.py --db <path>                     # rolling window
    python run_cftc_cot.py --db <path> --lookback-days 120
    python run_cftc_cot.py --db <path> --start 2020-01-01 --end 2024-12-31
    python run_cftc_cot.py --db <path> --only tff

Two reports, one run. **They are not two views of the same thing.** TFF
(Traders in Financial Futures) covers financial contracts -- rates, FX,
equity indices, credit -- and breaks positioning down by dealer, asset
manager and leveraged money. Legacy covers commodities with a coarser
commercial/non-commercial split and no leveraged-money category. A contract
appears in one or the other, not both, which is why they land in separate
tables and are read by separate tools.

**Cadence.** The CFTC publishes Friday afternoon (US Eastern) for the
position snapshot taken the preceding Tuesday, so the data is three days
stale the moment it exists, and a run before Friday's release simply
re-reads what it already has. Weekly is the natural rhythm; the window
deliberately spans several releases so one missed Friday heals on the next
run rather than leaving a gap.

Re-reading is safe and cheap: both tables are keyed on
(report_date, contract_market_name) and upserted, and unlike the earnings
calendar this source is a genuine archive -- an old window returns the same
rows years later, so a backfill is just a wider --start/--end.

--db is required and should be absolute: market-data-hub resolves a relative
path inside its own repository, so a runner invoked from elsewhere would
silently create an empty database there.
"""
import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from market_data_hub.db import connection as cx      # noqa: E402
from market_data_hub.db.upsert import upsert         # noqa: E402
from market_data_hub.sources import cftc_cot as cot  # noqa: E402

#: report name -> (fetch function, destination table)
REPORTS = {
    "tff": (cot.fetch_tff_futures, "cftc_tff_positioning"),
    "legacy": (cot.fetch_legacy_futures, "cftc_legacy_positioning"),
}

#: Eight weeks. Weekly releases, so this spans roughly eight of them: enough
#: that a month of failed runs still heals itself on the next success, and
#: short enough that the routine weekly run stays small.
DEFAULT_LOOKBACK_DAYS = 56


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, type=Path,
                    help="Path to the market database. Absolute, please.")
    ap.add_argument("--start", help="Inclusive first report_date (YYYY-MM-DD).")
    ap.add_argument("--end", help="Inclusive last report_date (YYYY-MM-DD).")
    ap.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS,
                    help=f"Window size when --start is absent (default {DEFAULT_LOOKBACK_DAYS}).")
    ap.add_argument("--only", choices=sorted(REPORTS),
                    help="Collect one report instead of both.")
    args = ap.parse_args()

    end = args.end or date.today().isoformat()
    start = args.start or (date.fromisoformat(end)
                           - timedelta(days=args.lookback_days)).isoformat()
    if start > end:
        print(f"ERROR: start {start} is after end {end}", file=sys.stderr)
        return 2

    scelti = [args.only] if args.only else list(REPORTS)
    print(f"db: {args.db}")
    print(f"window: {start} -> {end}  ({', '.join(scelti)})\n")

    con = cx.get_conn(str(args.db))
    fallite: list[str] = []
    try:
        for nome in scelti:
            fetch, tabella = REPORTS[nome]
            print(f"--- {nome} -> {tabella} ---")
            try:
                frame = fetch(start, end)
            except Exception as e:
                print(f"  FAILED: {type(e).__name__}: {str(e)[:160]}", file=sys.stderr)
                fallite.append(nome)
                continue
            if frame.empty:
                # A window shorter than the weekly cadence, or one that lands
                # entirely between releases, legitimately returns nothing.
                print("  0 rows (no release in this window)")
                continue
            upsert(con, tabella, frame)
            print(f"  {len(frame)} rows -> {tabella}")
    finally:
        con.close()

    print()
    _stampa_audit(args.db, scelti)
    if fallite:
        print(f"\nFAILED reports: {', '.join(fallite)}", file=sys.stderr)
        return 1
    return 0


def _stampa_audit(db: Path, scelti: list[str]) -> None:
    """Row counts, distinct contracts and the latest report_date now stored."""
    con = cx.get_conn(str(db), read_only=True)
    print("=== audit ===")
    try:
        for nome in scelti:
            tabella = REPORTS[nome][1]
            n, contratti, ultimo = con.execute(
                f"SELECT COUNT(*), COUNT(DISTINCT contract_market_name), "
                f"MAX(report_date) FROM {tabella}").fetchone()
            print(f"  {tabella:26} {n:>8} rows  "
                  f"{contratti:>4} contracts   latest {ultimo}")
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
