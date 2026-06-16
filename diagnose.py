# -*- coding: utf-8 -*-
"""
diagnose.py — report diagnostico di coverage e qualita' dati.

Uso:
    python diagnose.py                 # tabella coverage completa
    python diagnose.py --stalled       # solo serie ferme
    python diagnose.py --symbol SPY    # dettaglio singolo simbolo
    python diagnose.py --runs          # ultimi run dal download_log
    python diagnose.py --summary       # statistiche aggregate
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from market_data_hub.db.connection import get_conn  # noqa: E402

pd.set_option("display.max_rows", 400)
pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 30)


def _con(db):
    return get_conn(db, read_only=True)


def cmd_coverage(con, only_stalled: bool):
    cols = ("symbol, source, asset_class, freq_detected, first_date, last_date, "
            "obs_count, lag_days, stalled, gap_count, missing_pct, "
            "coverage_score, status")
    where = "WHERE stalled = TRUE" if only_stalled else ""
    df = con.execute(
        f"SELECT {cols} FROM coverage_report {where} "
        f"ORDER BY stalled DESC, coverage_score ASC").fetch_df()
    if df.empty:
        print("Nessun dato in coverage_report. Esegui prima run_daily.py o run_backfill.py.")
        return
    print(f"\n=== COVERAGE REPORT {'(solo stalled)' if only_stalled else ''} "
          f"— {len(df)} serie ===\n")
    print(df.to_string(index=False))
    print(f"\nStalled: {int(df['stalled'].sum())} | "
          f"Score medio: {df['coverage_score'].mean():.1f} | "
          f"Obs totali: {int(df['obs_count'].sum()):,}")


def cmd_symbol(con, symbol: str):
    cov = con.execute("SELECT * FROM coverage_report WHERE symbol = ?",
                      [symbol]).fetch_df()
    print(f"\n=== {symbol} ===\n")
    if cov.empty:
        print("Non presente in coverage_report.")
    else:
        for k, v in cov.iloc[0].items():
            print(f"  {k:16s}: {v}")

    # storia download recente
    log = con.execute(
        "SELECT started_at, source, rows_added, rows_updated, status, error_msg "
        "FROM download_log WHERE symbol = ? OR symbol = ? "
        "ORDER BY started_at DESC LIMIT 10",
        [symbol, symbol + ":1h"]).fetch_df()
    if not log.empty:
        print("\n  Ultimi download:")
        print(log.to_string(index=False))


def cmd_runs(con):
    df = con.execute(
        "SELECT run_id, min(started_at) AS start, count(*) AS n_symbols, "
        "sum(rows_added) AS added, sum(rows_updated) AS updated, "
        "sum(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errors "
        "FROM download_log GROUP BY run_id ORDER BY start DESC LIMIT 15").fetch_df()
    print("\n=== ULTIMI RUN ===\n")
    print(df.to_string(index=False) if not df.empty else "Nessun run registrato.")


def cmd_summary(con):
    print("\n=== SUMMARY ===\n")
    for tbl, key in [("prices_daily", "symbol"), ("macro_series", "series_id"),
                     ("crypto_ohlcv", "symbol")]:
        r = con.execute(
            f"SELECT count(*) rows, count(DISTINCT {key}) syms, "
            f"min(" + ("date" if tbl != "crypto_ohlcv" else "ts") + ") mn, "
            f"max(" + ("date" if tbl != "crypto_ohlcv" else "ts") + ") mx "
            f"FROM {tbl}").fetch_df()
        if not r.empty and r.iloc[0]["rows"]:
            x = r.iloc[0]
            print(f"  {tbl:16s}: {int(x['rows']):>10,} righe | "
                  f"{int(x['syms']):>4} serie | {x['mn']} -> {x['mx']}")
        else:
            print(f"  {tbl:16s}: vuoto")


def main() -> int:
    p = argparse.ArgumentParser(description="Diagnostica market_data_hub")
    p.add_argument("--stalled", action="store_true")
    p.add_argument("--symbol")
    p.add_argument("--runs", action="store_true")
    p.add_argument("--summary", action="store_true")
    p.add_argument("--db")
    args = p.parse_args()

    con = _con(args.db)
    try:
        if args.symbol:
            cmd_symbol(con, args.symbol)
        elif args.runs:
            cmd_runs(con)
        elif args.summary:
            cmd_summary(con)
        else:
            cmd_coverage(con, only_stalled=args.stalled)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
