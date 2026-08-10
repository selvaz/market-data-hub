# -*- coding: utf-8 -*-
"""
run_regime_daily.py — daily HMM regime-monitor entry point (optional add-on,
requires the `lazystats` package's `regimes` extra).

Fits a 1-3 state Gaussian HMM per priority-1 symbol on its whole daily-return
history, persists every day's estimate as-of that day (never overwriting past
estimates — see market_data_hub/regime/estimate.py), builds a single
self-contained HTML report, and optionally sends a Telegram summary + report
attachment.

Usage:
    python run_regime_daily.py --dry-run
    python run_regime_daily.py --tickers SPY,TLT --dry-run
    python run_regime_daily.py --send
    python run_regime_daily.py --lookback-years 8 --tickers SPY,TLT

``--lookback-years N`` fits the SAME pipeline restricted to the last N years
of history instead of full history -- identical persistence (revision
tracking, upsert-in-place), just a different start date and a
differently-namespaced series (see market_data_hub/regime/estimate.py's
``variant``) so it never collides with the full-history production series.
This is a diagnostic/comparison fit, not itself a day-to-day monitor, so it
skips the HTML reports and Telegram send -- see
market_data_hub/regime/window_comparison.py for the report that reads both
this and the full-history series back.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from market_data_hub.config_loader import get_settings  # noqa: E402
from market_data_hub.lock import DBLockTimeout, db_write_lock  # noqa: E402
from market_data_hub.regime.estimate import (  # noqa: E402
    DEFAULT_N_STARTS, DEFAULT_RETRO_DAYS, DEFAULT_S_MAX,
    priority_symbols, run_daily_regime_estimation, summary_dataframe,
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
    p.add_argument("--lookback-years", type=int,
                   help="restrict the fit to the last N years instead of full history; "
                        "writes to a separate series, skips reports/Telegram")
    p.add_argument("--dry-run", action="store_true",
                   help="fit + write DB + build report, but do not send Telegram")
    p.add_argument("--send", action="store_true", help="send the Telegram report")
    args = p.parse_args()

    symbols = [s.strip() for s in args.tickers.split(",")] if args.tickers else None
    asof = datetime.strptime(args.asof, "%Y-%m-%d").date() if args.asof else datetime.now().date()
    windowed = args.lookback_years is not None
    start = (asof - timedelta(days=365 * args.lookback_years)).isoformat() if windowed else None
    variant = f"{args.lookback_years}y" if windowed else None

    # Resolve the universe up front: an empty one would otherwise reach
    # summary_dataframe({}) whose frame has no 'status' column (KeyError).
    if symbols is None:
        symbols = priority_symbols(args.priority, db_path=args.db)
    if not symbols:
        print(f"No symbols to fit (priority={args.priority} universe is empty); "
              "nothing to do.")
        return 0

    window_desc = f", last {args.lookback_years}y (from {start})" if windowed else ""
    print(f"Fitting regimes as of {asof.isoformat()} "
          f"({'custom tickers' if args.tickers else f'priority={args.priority}'}){window_desc}...")
    # This job is scheduled (MarketData_HMMRegime, 30 min after US close) and can
    # overlap the USClose/EU18 refresh or a manual backfill/import, all of which
    # take the same single-writer lock. Mirror runner.py: if another writer holds
    # it past the timeout, skip this run cleanly (exit 0) instead of letting an
    # unhandled DBLockTimeout crash the task with a traceback — a red Scheduler
    # task and a silently-missed regime report/Telegram on a benign, expected
    # contention. A skipped day is recovered on the next scheduled run.
    try:
        with db_write_lock(args.db):
            results = run_daily_regime_estimation(
                symbols=symbols, priority=args.priority, S_max=args.s_max,
                n_starts=args.n_starts, retro_days=args.retro_days, asof=asof,
                db_path=args.db, start=start, variant=variant,
            )
    except DBLockTimeout as ex:
        print(f"SKIP: {ex}")
        return 0

    if windowed:
        # Diagnostic/comparison fit, not a day-to-day monitor: fit + persist
        # only. window_comparison.py reads this series and the full-history
        # one back together -- no report/Telegram here would just duplicate
        # (and desync from) that comparison.
        summary = summary_dataframe(results)
        ok = summary[summary["status"] == "ok"]
        errors = summary[summary["status"] != "ok"]
        print(f"Done ({variant} window): {len(ok)} ok, {len(errors)} errors.")
        if not errors.empty:
            print(errors[["symbol", "error_msg"]].to_string(index=False))
        return 0

    summary = summary_dataframe(results)
    ok = summary[summary["status"] == "ok"]
    errors = summary[summary["status"] != "ok"]
    changed = ok[ok["changed_today"]]
    revised = ok[ok["revised_last_n_days"] > 0]
    print(f"Done: {len(ok)} ok, {len(errors)} errors, "
          f"{len(changed)} regime changes today, {len(revised)} with revisions.")

    import lazytools.registry as lazytools_registry
    from lazystats.io.depot import ResultDepot

    from market_data_hub.regime.daily_payload import (
        REGIME_REPORT_KIND, REGIME_REPORT_SERIES_KEY, build_daily_payload,
    )
    from market_data_hub.regime.daily_render import render_html as render_daily_json_report

    depot = ResultDepot(lazytools_registry.resolve_db("lazystats_depot"))
    try:
        out_path = generate_html_report(depot, results, out_dir=_report_dir(), asof=asof,
                                        db_path=args.db)

        # The consolidated, JSON-first daily report: names, EVERY state's
        # annualized mean/vol (not just the current one), and the full
        # current state-probability vector -- saved once as its own depot
        # row so the HTML can always be reconstructed from that JSON alone
        # (see render_regime_report.py), independent of the chart-based
        # report above.
        daily_payload = build_daily_payload(depot, results, asof=asof)
        daily_result_id = depot.save(
            kind=REGIME_REPORT_KIND,
            produced_by="scheduled:run_regime_daily",
            instruments=sorted(results),
            payload=daily_payload,
            provenance=daily_payload["provenance"],
            cadence="stable",
            series_key=REGIME_REPORT_SERIES_KEY,
        )
        daily_row = depot.load(daily_result_id)
    finally:
        depot.close()
    print(f"Report: {out_path}")

    daily_json_path = _report_dir() / f"regime_daily_{asof.isoformat()}_{daily_result_id}.html"
    daily_json_path.write_text(render_daily_json_report(daily_row), encoding="utf-8")
    print(f"JSON-reproducible report: {daily_json_path} (result_id={daily_result_id})")

    from market_data_hub.artifact_registry import register_report_artifact
    register_report_artifact(
        title=f"Regime monitor report {asof.isoformat()}",
        summary=f"{len(ok)} symbols fitted | {len(errors)} errors | "
                f"{len(changed)} regime changes today | {len(revised)} revisions",
        tags=["regime", "daily"],
        content_uri=str(out_path),
    )
    register_report_artifact(
        title=f"Regime monitor (all states, JSON-reproducible) {asof.isoformat()}",
        summary=f"{len(ok)} symbols fitted | {len(errors)} errors | "
                f"{len(changed)} regime changes today | {len(revised)} revisions",
        tags=["regime", "daily", "json-reproducible"],
        content_uri=str(daily_json_path),
    )

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
                             filename=out_path.name, caption="HMM regime report (charts)")
        client.send_document(chat_id=chat_id, document=daily_json_path.read_bytes(),
                             filename=daily_json_path.name,
                             caption="HMM regime report (all states, JSON-reproducible)")
    print("Sent Telegram summary + both report attachments.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
