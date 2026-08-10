#!/usr/bin/env python
"""Compare each symbol's full-history regime fit against a shorter-window
fit (e.g. the last 8 years) already persisted by
``run_regime_daily.py --lookback-years N``.

Pure read against ``lazystats_depot`` -- no market_data.duckdb access, no
re-fitting, no db_write_lock needed. Saves the comparison as its own
depot row (JSON-reproducible, see window_comparison_render.py) and
optionally sends it via Telegram.

Usage:
    python run_regime_window_comparison.py --variant 8y --dry-run
    python run_regime_window_comparison.py --variant 8y --send
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import lazytools.registry as lazytools_registry  # noqa: E402
from lazystats.io.depot import ResultDepot  # noqa: E402

from market_data_hub.config_loader import get_settings  # noqa: E402
from market_data_hub.regime.estimate import priority_symbols  # noqa: E402
from market_data_hub.regime.window_comparison import (  # noqa: E402
    WINDOW_COMPARISON_KIND, WINDOW_COMPARISON_SERIES_KEY, build_comparison_payload,
)
from market_data_hub.regime.window_comparison_render import render_html  # noqa: E402


def _report_dir() -> Path:
    cfg = get_settings().get("reports", {})
    base = Path(cfg.get("dir") or "reports")
    if not base.is_absolute():
        base = Path(__file__).parent / base
    return base / "regime"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", help="DuckDB path; defaults to market_data_hub settings")
    p.add_argument("--priority", type=int, default=1, help="tickers.yaml priority tier (default: 1)")
    p.add_argument("--tickers", help="comma-separated symbol override (testing)")
    p.add_argument("--variant", required=True, help="the run_regime_daily.py --lookback-years variant tag, e.g. '8y'")
    p.add_argument("--asof", help="override the estimation_date (YYYY-MM-DD); default: today")
    p.add_argument("--dry-run", action="store_true", help="build + save + render, but do not send Telegram")
    p.add_argument("--send", action="store_true", help="send the Telegram report")
    args = p.parse_args()

    symbols = [s.strip() for s in args.tickers.split(",")] if args.tickers else None
    asof = datetime.strptime(args.asof, "%Y-%m-%d").date() if args.asof else datetime.now().date()
    if symbols is None:
        symbols = priority_symbols(args.priority, db_path=args.db)
    if not symbols:
        print(f"No symbols to compare (priority={args.priority} universe is empty); nothing to do.")
        return 0

    depot = ResultDepot(lazytools_registry.resolve_db("lazystats_depot"))
    try:
        payload = build_comparison_payload(depot, symbols, variant=args.variant, asof=asof, db_path=args.db)
        result_id = depot.save(
            kind=WINDOW_COMPARISON_KIND,
            produced_by="scheduled:run_regime_window_comparison",
            instruments=sorted(symbols),
            payload=payload,
            provenance=payload["provenance"],
            cadence="stable",
            series_key=f"{WINDOW_COMPARISON_SERIES_KEY}:{args.variant}",
        )
        row = depot.load(result_id)
    finally:
        depot.close()

    s = payload["summary"]
    print(f"Compared {s['n_symbols']} symbols (full-history vs {args.variant}): "
          f"{s['n_disagree']} disagree, {s['n_single_state']} single-state, {s['n_missing']} missing.")

    out_dir = _report_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"regime_window_comparison_{args.variant}_{asof.isoformat()}_{result_id}.html"
    out_path.write_text(render_html(row), encoding="utf-8")
    print(f"Report: {out_path} (result_id={result_id})")

    from market_data_hub.artifact_registry import register_report_artifact
    register_report_artifact(
        title=f"Regime window comparison ({args.variant}) {asof.isoformat()}",
        summary=f"{s['n_symbols']} symbols | {s['n_disagree']} disagree | "
                f"{s['n_single_state']} single-state | {s['n_missing']} missing",
        tags=["regime", "comparison", args.variant],
        content_uri=str(out_path),
    )

    if args.dry_run or not args.send:
        return 0

    import os
    from lazytools.connectors.telegram import TelegramClient

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram not configured: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.", file=sys.stderr)
        return 2

    text = (
        f"Regime window comparison (full-history vs {args.variant}) — {asof.isoformat()}\n"
        f"{s['n_symbols']} symbols compared\n"
        f"Structural disagreements: {s['n_disagree']}\n"
        f"Single-state windows: {s['n_single_state']}\n"
        f"Missing data: {s['n_missing']}"
    )
    with TelegramClient.from_token(token) as client:
        client.send_message(chat_id=chat_id, text=text)
        client.send_document(chat_id=chat_id, document=out_path.read_bytes(),
                             filename=out_path.name, caption="Regime window comparison")
    print("Sent Telegram summary + report attachment.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
