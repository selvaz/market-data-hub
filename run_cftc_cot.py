# -*- coding: utf-8 -*-
"""
run_cftc_cot.py — collect CFTC Commitments of Traders positioning and ingest it.

Usage:
    python run_cftc_cot.py --db <path>                     # rolling window
    python run_cftc_cot.py --db <path> --lookback-days 120
    python run_cftc_cot.py --db <path> --start 2020-01-01 --end 2024-12-31
    python run_cftc_cot.py --db <path> --only tff
    python run_cftc_cot.py --db <path> --backfill-years 20 --dry-run
    python run_cftc_cot.py --db <path> --backfill-years 20

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

The routine run reads a bounded Socrata API date window. ``--backfill-years``
uses CFTC's official historical compressed ZIP archives instead, validates
them before writes, and upserts bounded CSV batches on the existing
(report_date, cftc_contract_market_code) keys.

--db is required and should be absolute: market-data-hub resolves a relative
path inside its own repository, so a runner invoked from elsewhere would
silently create an empty database there.
"""
import argparse
import sys
import tempfile
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from market_data_hub.db import connection as cx      # noqa: E402
from market_data_hub.db.upsert import log_run, upsert  # noqa: E402
from market_data_hub.lock import db_write_lock       # noqa: E402
from market_data_hub.sources import cftc_cot as cot  # noqa: E402
from market_data_hub.sources import cftc_history as history  # noqa: E402

#: report name -> (fetch function, destination table)
REPORTS = {
    "tff": (cot.fetch_tff_futures, "cftc_tff_positioning"),
    "legacy": (cot.fetch_legacy_futures, "cftc_legacy_positioning"),
}

#: Eight weeks. Weekly releases, so this spans roughly eight of them: enough
#: that a month of failed runs still heals itself on the next success, and
#: short enough that the routine weekly run stays small.
DEFAULT_LOOKBACK_DAYS = 56


def _today() -> date:
    """Today's date, kept injectable for deterministic window tests."""
    return date.today()


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
    ap.add_argument("--backfill-years", type=int,
                    help=("Backfill official CFTC compressed history from "
                          "January 1 of the year N years ago through today."))
    ap.add_argument("--dry-run", action="store_true",
                    help="With --backfill-years, validate archives and print "
                         "upsert candidates by table/year without opening the DB.")
    args = ap.parse_args()

    if args.backfill_years is not None:
        if args.backfill_years <= 0:
            print("ERROR: --backfill-years must be positive", file=sys.stderr)
            return 2
        if args.start or args.end:
            print("ERROR: --backfill-years cannot be combined with --start/--end",
                  file=sys.stderr)
            return 2
        return _run_historical_backfill(args)

    if args.dry_run:
        print("ERROR: --dry-run requires --backfill-years", file=sys.stderr)
        return 2

    end = args.end or _today().isoformat()
    start = args.start or (date.fromisoformat(end)
                           - timedelta(days=args.lookback_days)).isoformat()
    if start > end:
        print(f"ERROR: start {start} is after end {end}", file=sys.stderr)
        return 2

    scelti = [args.only] if args.only else list(REPORTS)
    print(f"db: {args.db}")
    print(f"window: {start} -> {end}  ({', '.join(scelti)})\n")

    fallite: list[str] = []
    # Fetch BEFORE taking the writer lock: a slow or unavailable Socrata
    # endpoint must not hold the shared advisory lock and make unrelated
    # scheduled writers time out.
    frames: list[tuple[str, str, object]] = []
    for nome in scelti:
        fetch, tabella = REPORTS[nome]
        print(f"--- {nome} -> {tabella} ---")
        try:
            frame = fetch(start, end)
        except Exception as e:
            print(f"  FAILED: {type(e).__name__}: {str(e)[:160]}",
                  file=sys.stderr)
            fallite.append(nome)
            continue
        if frame.empty:
            # A window shorter than the weekly cadence, or one that lands
            # entirely between releases, legitimately returns nothing.
            print("  0 rows (no release in this window)")
            continue
        frames.append((nome, tabella, frame))

    if frames:
        try:
            # Cooperate with the same DB lock as the historical writer so a daily
            # refresh cannot enter while the backfill is committing a batch.
            with db_write_lock(str(args.db)):
                con = cx.get_conn(str(args.db))
                try:
                    for nome, tabella, frame in frames:
                        upsert(con, tabella, frame)
                        print(f"  {nome}: {len(frame)} rows -> {tabella}")
                finally:
                    # Close the DuckDB handle BEFORE the advisory lock is
                    # released, so a waiting writer never finds the file
                    # still owned by this process.
                    con.close()
        except Exception as e:
            print(f"FAILED CFTC database write: {type(e).__name__}: {e}",
                  file=sys.stderr)
            return 1

    print()
    _stampa_audit(args.db, scelti)
    if fallite:
        print(f"\nFAILED reports: {', '.join(fallite)}", file=sys.stderr)
        return 1
    return 0


def _run_historical_backfill(args: argparse.Namespace) -> int:
    """Download, validate, then stream CFTC archive rows into the existing tables."""
    end_date = _today()
    # The number names full calendar years of history before the current year;
    # e.g. in 2026, --backfill-years 20 starts on 2006-01-01.
    start_date = date(end_date.year - args.backfill_years, 1, 1)
    selected = [args.only] if args.only else list(REPORTS)
    print(f"db: {args.db}")
    print(f"archive window: {start_date} -> {end_date}  "
          f"({', '.join(selected)})")

    # Stage and fully validate all source archives before acquiring the writer
    # lock or making the first database write. ZIPs are downloaded to disk and
    # parsed one row at a time, so a bundle never has to fit in RAM.
    staged = []
    with tempfile.TemporaryDirectory(prefix="cftc-history-") as temp_dir:
        temp_root = Path(temp_dir)
        try:
            for report in selected:
                for index, spec in enumerate(
                        history.historical_archive_plan(report, start_date,
                                                        end_date)):
                    archive_path = temp_root / f"{report}-{index}-{spec.filename}"
                    print(f"preflight: {report} {spec.filename}", flush=True)
                    history.download_archive(spec.url, archive_path)
                    counts = history.count_archive_rows(
                        archive_path, report, start_date, end_date)
                    staged.append((report, spec, archive_path, counts))
        except Exception as exc:
            print(f"FAILED archive preflight: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            return 1

        if args.dry_run:
            _print_backfill_counts(staged, start_date, end_date,
                                   label="would upsert", dry_run=True)
            return 0

        run_id = f"cftc-history-{uuid.uuid4().hex[:12]}"
        try:
            # This is the same DB-level advisory lock used by the hub writers.
            # Downloads and archive validation happen before taking it.
            with db_write_lock(str(args.db)):
                con = cx.get_conn(str(args.db))
                try:
                    for report, spec, archive_path, counts in staged:
                        table = REPORTS[report][1]
                        print(f"upsert: {report} {spec.filename}", flush=True)
                        archive_added = 0
                        archive_updated = 0
                        archive_started = datetime.now(timezone.utc)
                        started = time.monotonic()
                        for frame in history.iter_archive_batches(
                                archive_path, report, start_date, end_date):
                            for year in sorted(frame["report_date"].dt.year.unique()):
                                year_frame = frame.loc[
                                    frame["report_date"].dt.year == year]
                                preserved_metadata = (
                                    ("commodity_name", "commodity_subgroup_name")
                                    if report == "tff" else
                                    ("commodity_name",)
                                )
                                added, updated = upsert(
                                    con, table, year_frame,
                                    preserve_non_null_columns=preserved_metadata,
                                    prefer_existing_non_null_columns=(
                                        "contract_market_name",))
                                archive_added += added
                                archive_updated += updated
                        log_run(
                            con,
                            run_id=f"{run_id}:{report}:{spec.filename}",
                            started_at=archive_started,
                            source="cftc_cot_history",
                            symbol=f"{table}:{spec.first_year}-{spec.last_year}",
                            rows_added=archive_added,
                            rows_updated=archive_updated,
                            status="ok" if counts else "empty",
                            error_msg=None,
                            duration_sec=time.monotonic() - started,
                        )
                finally:
                    con.close()
        except Exception as exc:
            print(f"FAILED historical upsert: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            return 1

        _print_backfill_counts(staged, start_date, end_date,
                               label="upserted", dry_run=False)
        print(f"history run_id: {run_id}")
    _stampa_audit(args.db, selected)
    return 0


def _print_backfill_counts(staged, start_date: date, end_date: date, *,
                           label: str, dry_run: bool) -> None:
    totals = {report: 0 for report, _ in REPORTS.items()}
    print("=== CFTC historical archive rows ===")
    for report, spec, _archive_path, counts in staged:
        table = REPORTS[report][1]
        first = max(start_date.year, spec.first_year)
        last = min(end_date.year, spec.last_year)
        print(f"--- {table} / {spec.filename} ---")
        for year in range(first, last + 1):
            n = counts.get(year, 0)
            totals[report] += n
            print(f"  {year}: {n:,} {label} rows")
    for report in totals:
        if any(staged_report == report
               for staged_report, _spec, _path, _counts in staged):
            print(f"  {REPORTS[report][1]} total: {totals[report]:,}")
    if dry_run:
        print("dry-run: no database connection opened and no rows written")


def _stampa_audit(db: Path, scelti: list[str]) -> None:
    """Row counts, distinct contracts and the latest report_date now stored."""
    con = cx.get_conn(str(db), read_only=True)
    print("=== audit ===")
    try:
        for nome in scelti:
            tabella = REPORTS[nome][1]
            n, contratti, primo, ultimo = con.execute(
                f"SELECT COUNT(*), COUNT(DISTINCT contract_market_name), "
                f"MIN(report_date), MAX(report_date) FROM {tabella}").fetchone()
            print(f"  {tabella:26} {n:>8} rows  "
                  f"{contratti:>4} contracts   {primo} -> {ultimo}")
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
