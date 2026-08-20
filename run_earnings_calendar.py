# -*- coding: utf-8 -*-
"""
run_earnings_calendar.py — collect the global earnings calendar and ingest it.

Usage:
    python run_earnings_calendar.py --db <path> --work-dir <dir>            # next week
    python run_earnings_calendar.py --db <path> --work-dir <dir> --mode last
    python run_earnings_calendar.py --db <path> --work-dir <dir> --no-collect
    python run_earnings_calendar.py --db <path> --audit-only

Collection writes one CSV per source into --work-dir; ingestion reads those
CSVs. --no-collect re-runs ingestion alone over what is already on disk.

Two modes because the source keeps two dates per company and no history:
--mode next lists what is expected (the weekly watchlist), --mode last lists
what has just been published (the recap, and the one-off backfill that gives a
new database roughly a quarter of past releases to start from).

--db is required and should be absolute: market-data-hub resolves a relative
path inside its own repository, so a runner invoked from elsewhere would
silently create an empty database there.
"""
import argparse
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from market_data_hub.db import connection as cx                        # noqa: E402
from market_data_hub.earnings_calendar import audit, ingest_observations  # noqa: E402

CSV = "tradingview.csv"


def _prossima_settimana(oggi: date) -> tuple[date, date]:
    """Monday to Monday of the week after the one containing `oggi`."""
    lunedi = oggi - timedelta(days=oggi.weekday()) + timedelta(days=7)
    return lunedi, lunedi + timedelta(days=7)


def _settimana_passata(oggi: date) -> tuple[date, date]:
    lunedi = oggi - timedelta(days=oggi.weekday()) - timedelta(days=7)
    return lunedi, lunedi + timedelta(days=7)


def collect(uscita: Path, da: date, a: date, mode: str, min_market_cap: float) -> int:
    """Download the window into its CSV. Returns the number of rows written."""
    from market_data_hub.earnings_calendar.collect.tradingview import scarica

    tabella = scarica(datetime.combine(da, time.min), datetime.combine(a, time.min),
                      mode=mode, min_market_cap=min_market_cap)
    if tabella.empty:
        # A stale CSV from a previous run would otherwise be ingested under
        # today's vintage, recording an old reading as if seen today.
        if uscita.exists():
            uscita.unlink()
            print(f"  removed stale {uscita.name}", flush=True)
        return 0
    tabella.to_csv(uscita, index=False, encoding="utf-8-sig")
    return len(tabella)


def main() -> int:
    p = argparse.ArgumentParser(description="global earnings calendar: collect and ingest")
    p.add_argument("--db", required=True,
                   help="DuckDB path. Absolute: a relative one resolves inside the repo.")
    p.add_argument("--work-dir", default=".", help="where the CSV is written and read")
    p.add_argument("--mode", choices=("next", "last"), default="next",
                   help="next: expected releases; last: what has just been published")
    p.add_argument("--window-start", default=None, help="YYYY-MM-DD (default: per --mode)")
    p.add_argument("--window-end", default=None, help="YYYY-MM-DD, exclusive")
    p.add_argument("--min-market-cap", type=float, default=1e9,
                   help="skip anything smaller, in USD (default: 1e9)")
    p.add_argument("--no-collect", action="store_true",
                   help="skip downloading; ingest the CSV already in --work-dir")
    p.add_argument("--audit-only", action="store_true", help="only check the database")
    p.add_argument("--run-id", default=None)
    args = p.parse_args()

    db = Path(args.db)
    if not db.is_absolute():
        print(f"WARNING: --db {args.db} is relative; resolving to {db.resolve()}",
              file=sys.stderr)
    con = cx.get_conn(str(db.resolve()))
    print(f"db: {db.resolve()}  (schema {cx.get_schema_version(con)})")

    if args.audit_only:
        _stampa_audit(con)
        con.close()
        return 0

    oggi = date.today()
    predefinita = _settimana_passata(oggi) if args.mode == "last" else _prossima_settimana(oggi)
    da = date.fromisoformat(args.window_start) if args.window_start else predefinita[0]
    a = date.fromisoformat(args.window_end) if args.window_end else predefinita[1]

    work_dir = Path(args.work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    uscita = work_dir / CSV

    print(f"\n=== {args.mode}: {da} -> {a} ===")
    if args.no_collect:
        print("  collection: not attempted (--no-collect)")
    else:
        try:
            n = collect(uscita, da, a, args.mode, args.min_market_cap)
            print(f"  collected {n} rows -> {uscita}")
        except Exception as e:
            # One source, so a failed collection is a failed run: ingesting the
            # CSV left on disk would re-date an older reading as today's.
            print(f"  FAILED: {type(e).__name__}: {str(e)[:160]}", file=sys.stderr)
            con.close()
            return 1

    if not uscita.exists():
        print("\nnothing to ingest.", file=sys.stderr)
        con.close()
        return 1

    from market_data_hub.earnings_calendar.collect.tradingview import leggi
    osservazioni = leggi(uscita)
    if not osservazioni:
        print("\nnothing to ingest.", file=sys.stderr)
        con.close()
        return 1

    esito = ingest_observations(
        con, osservazioni, run_id=args.run_id or f"earnings-{args.mode}-{oggi}")
    print(f"\ningested: {esito}")
    _stampa_audit(con)
    con.close()
    return 0


def _stampa_audit(con) -> None:
    print("\n=== audit ===")
    for chiave, valore in audit(con).items():
        print(f"  {chiave:26} {valore}")


if __name__ == "__main__":
    raise SystemExit(main())
