# -*- coding: utf-8 -*-
"""
run_daily.py — entry point for the incremental daily download.

Usage:
    python run_daily.py                          # full: yahoo + fred + binance + macro_panel + live
    python run_daily.py --report --open          # full + HTML report + open in browser
    python run_daily.py --report --send-email    # full + report + send email
    python run_daily.py --live-only              # live intraday price injection only
    python run_daily.py --sources yahoo fred     # specific sources
    python run_daily.py --end 2024-12-31

Note: without --sources it downloads ALL sources (yahoo, fred, binance,
macro_panel) + live injection.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from market_data_hub.runner import run  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="market_data_hub daily download")
    p.add_argument("--live-only", action="store_true",
                   help="live intraday price injection only")
    p.add_argument("--full", action="store_true",
                   help="force full mode (default)")
    p.add_argument("--sources", nargs="+",
                   choices=["yahoo", "fred", "binance", "macro_panel"],
                   help="limit to the given sources")
    p.add_argument("--end", help="end date (default: today UTC)")
    p.add_argument("--db", help="DuckDB DB path (override settings)")
    p.add_argument("--report", action="store_true",
                   help="generate HTML/MD report when the download finishes")
    p.add_argument("--send-email", action="store_true",
                   help="send the report by email (implies --report)")
    p.add_argument("--open", dest="open_browser", action="store_true",
                   help="open the HTML report in the browser when finished (implies --report)")
    args = p.parse_args()

    # --send-email and --open imply --report
    if args.send_email or args.open_browser:
        args.report = True

    mode = "live-only" if args.live_only else "full"

    try:
        run(mode=mode, sources=args.sources, end=args.end, db_path=args.db)
    except Exception as e:
        print(f"ERROR during download: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        # continue to the report anyway if requested
        if not args.report:
            return 1

    if args.report and not args.live_only:
        print("\n--- Generating report ---")
        try:
            # inline import so live-only runs are not slowed down
            from make_report import main as _report_main, \
                collect, render_html, render_md, send_email, REPORT_DIR
            from market_data_hub.db.connection import get_conn
            from datetime import datetime

            REPORT_DIR.mkdir(exist_ok=True)
            con = get_conn(args.db, read_only=True)
            try:
                d = collect(con)
            finally:
                con.close()

            stamp = datetime.now().strftime("%Y%m%d_%H%M")
            html_path = REPORT_DIR / f"market_data_report_{stamp}.html"
            md_path = REPORT_DIR / f"market_data_report_{stamp}.md"
            html_content = render_html(d)
            html_path.write_text(html_content, encoding="utf-8")
            md_path.write_text(render_md(d), encoding="utf-8")
            print(f"Report: {html_path}")
            print(f"Rows: {d['total_rows']:,} | Series: "
                  f"{sum(x['series'] for x in d['tables'])} | "
                  f"Score: {d['score_avg']} | Stalled: {len(d['stalled'])}")

            if args.send_email:
                send_email(html_content, d)

            # Ray Dalio report (debt-cycle phases + regime) — the Dalio
            # computation was already done inside run(); here we only generate the HTML.
            dalio_path = None
            try:
                from make_dalio_report import collect as dcollect, render_html as drender
                con2 = get_conn(args.db, read_only=True)
                try:
                    dd = dcollect(con2)
                finally:
                    con2.close()
                dalio_path = REPORT_DIR / f"dalio_report_{stamp}.html"
                dalio_path.write_text(drender(dd), encoding="utf-8")
                print(f"Dalio report: {dalio_path} "
                      f"(phases: {dd['phase_counts']})")
            except Exception as e:
                print(f"(Dalio report skipped: {e})")

            if args.open_browser:
                import webbrowser
                webbrowser.open(html_path.as_uri())
                if dalio_path:
                    webbrowser.open(dalio_path.as_uri())
                print(f"Opened in browser: {html_path}")

        except Exception as e:
            print(f"ERROR generating report: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()

    return 0


if __name__ == "__main__":
    sys.exit(main())
