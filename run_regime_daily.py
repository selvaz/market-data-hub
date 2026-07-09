# -*- coding: utf-8 -*-
"""
run_regime_daily.py — daily HMM regime-monitor entry point (optional add-on,
requires the sibling `lazyhmm` package).

Fits a 1-3 state Gaussian HMM per priority-1 symbol on its whole daily-return
history, persists every day's estimate as-of that day (never overwriting past
estimates — see market_data_hub/regime/estimate.py), builds a single
self-contained HTML report, and optionally sends a Telegram summary + report
attachment.

Usage:
    python run_regime_daily.py --dry-run
    python run_regime_daily.py --tickers SPY,TLT --dry-run
    python run_regime_daily.py --send
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from market_data_hub.config_loader import get_settings  # noqa: E402
from market_data_hub.db.connection import get_conn  # noqa: E402
from market_data_hub.lock import db_write_lock  # noqa: E402
from market_data_hub.regime.estimate import (  # noqa: E402
    DEFAULT_N_STARTS, DEFAULT_RETRO_DAYS, DEFAULT_S_MAX,
    run_daily_regime_estimation, summary_dataframe,
)
from market_data_hub.regime.report import generate_html_report  # noqa: E402


def _report_dir() -> Path:
    cfg = get_settings().get("reports", {})
    base = Path(cfg.get("dir") or "reports")
    if not base.is_absolute():
        base = Path(__file__).parent / base
    return base / "regime"


def main() -> int:
    p = argparse.ArgumentParser(description="Daily HMM regime monitor")
    p.add_argument("--db", help="DuckDB path; defaults to market_data_hub settings")
    p.add_argument("--priority", type=int, default=1,
                   help="tickers.yaml priority tier to fit (default: 1)")
    p.add_argument("--tickers", help="comma-separated symbol override (testing)")
    p.add_argument("--s-max", type=int, default=DEFAULT_S_MAX)
    p.add_argument("--n-starts", type=int, default=DEFAULT_N_STARTS)
    p.add_argument("--retro-days", type=int, default=DEFAULT_RETRO_DAYS)
    p.add_argument("--asof", help="override the estimation_date (YYYY-MM-DD); default: today")
    p.add_argument("--dry-run", action="store_true",
                   help="fit + write DB + build report, but do not send Telegram")
    p.add_argument("--send", action="store_true", help="send the Telegram report")
    args = p.parse_args()

    symbols = [s.strip() for s in args.tickers.split(",")] if args.tickers else None
    asof = datetime.strptime(args.asof, "%Y-%m-%d").date() if args.asof else datetime.now().date()

    print(f"Fitting regimes as of {asof.isoformat()} "
          f"({'custom tickers' if symbols else f'priority={args.priority}'})...")
    with db_write_lock(args.db):
        results = run_daily_regime_estimation(
            symbols=symbols, priority=args.priority, S_max=args.s_max,
            n_starts=args.n_starts, retro_days=args.retro_days, asof=asof,
            db_path=args.db,
        )

    summary = summary_dataframe(results)
    ok = summary[summary["status"] == "ok"]
    errors = summary[summary["status"] != "ok"]
    changed = ok[ok["changed_today"]]
    revised = ok[ok["revised_last_n_days"] > 0]
    print(f"Done: {len(ok)} ok, {len(errors)} errors, "
          f"{len(changed)} regime changes today, {len(revised)} with revisions.")

    con = get_conn(args.db, read_only=True)
    try:
        out_path = generate_html_report(con, results, out_dir=_report_dir(), asof=asof)
    finally:
        con.close()
    print(f"Report: {out_path}")

    if args.dry_run or not args.send:
        if not errors.empty:
            print(errors[["symbol", "error_msg"]].to_string(index=False))
        return 0

    import os
    from lazytools.connectors.telegram import TelegramClient

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram not configured: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.",
              file=sys.stderr)
        return 2

    lines = [
        f"HMM regime monitor — {asof.isoformat()}",
        f"{len(ok)} symbols fitted, {len(errors)} errors",
        f"Regime changes today: {len(changed)}"
        + (": " + ", ".join(changed['symbol'].tolist()[:15]) if len(changed) else ""),
        f"Retroactive revisions (30d): {len(revised)}"
        + (": " + ", ".join(revised['symbol'].tolist()[:15]) if len(revised) else ""),
    ]
    text = "\n".join(lines)

    with TelegramClient.from_token(token) as client:
        client.send_message(chat_id=chat_id, text=text)
        client.send_document(chat_id=chat_id, document=out_path.read_bytes(),
                             filename=out_path.name, caption="HMM regime report")
    print("Sent Telegram summary + report attachment.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
