"""Write a daily macro release report, optionally sending the HTML via Telegram."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from market_data_hub.macro_releases.report import collect_report, write_report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=os.environ.get("MACRO_RELEASES_DB"),
                        help="Existing macro SQLite database (default: MACRO_RELEASES_DB)")
    parser.add_argument("--out-dir", type=Path, help="Output directory (default: configured reports/macro_releases)")
    parser.add_argument("--since-hours", type=float, default=24, help="First-seen window in hours (default: 24)")
    parser.add_argument("--telegram", action="store_true", help="Send the saved HTML with the hub's Telegram helper")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 1
    try:
        if args.db is None:
            raise ValueError("Specify --db or set MACRO_RELEASES_DB to an existing macro releases SQLite database")
        out_dir = args.out_dir
        if out_dir is None:
            from market_data_hub.config_loader import get_settings
            out_dir = Path(get_settings().get("reports", {}).get("dir") or "reports")
            if not out_dir.is_absolute():
                out_dir = Path(__file__).resolve().parent / out_dir
            out_dir /= "macro_releases"
        report = collect_report(args.db, since_hours=args.since_hours)
        html_path, json_path = write_report(report, out_dir)
        print(report["message"])
        print(f"Saved report: {html_path}\nSaved JSON: {json_path}")
        if args.telegram:
            token = os.environ.get("TELEGRAM_BOT_TOKEN")
            chat_id = os.environ.get("TELEGRAM_CHAT_ID")
            if not token or not chat_id:
                raise ValueError("Telegram not configured: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
            # Keep the optional connector (and all network access) behind the flag.
            from send_telegram_run_report import send_report_document
            send_report_document(html_path, token=token, chat_id=chat_id,
                                 caption=f'{report["title"]} | {report["message"]}')
            print(f"Sent Telegram report attachment: {html_path.name}")
        return 0 if report["release_count"] else 3
    except Exception as exc:
        # A failed send still leaves the HTML and JSON for inspection/retry.
        print(f"Macro release report failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
