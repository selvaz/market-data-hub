# -*- coding: utf-8 -*-
"""
run_daily.py — entry point del download giornaliero incrementale.

Uso:
    python run_daily.py                          # full: yahoo + fred + binance + macro_panel + live
    python run_daily.py --report --open          # full + report HTML + apre nel browser
    python run_daily.py --report --send-email    # full + report + invio email
    python run_daily.py --live-only              # solo live price injection intraday
    python run_daily.py --sources yahoo fred     # sorgenti specifiche
    python run_daily.py --end 2024-12-31

Nota: senza --sources scarica TUTTE le sorgenti (yahoo, fred, binance,
macro_panel) + live injection.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from market_data_hub.runner import run  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Download giornaliero market_data_hub")
    p.add_argument("--live-only", action="store_true",
                   help="solo live price injection intraday")
    p.add_argument("--full", action="store_true",
                   help="forza modalita' full (default)")
    p.add_argument("--sources", nargs="+",
                   choices=["yahoo", "fred", "binance", "macro_panel"],
                   help="limita alle sorgenti indicate")
    p.add_argument("--end", help="data finale (default: oggi UTC)")
    p.add_argument("--db", help="path DB DuckDB (override settings)")
    p.add_argument("--report", action="store_true",
                   help="genera report HTML/MD al termine del download")
    p.add_argument("--send-email", action="store_true",
                   help="invia il report via email (implica --report)")
    p.add_argument("--open", dest="open_browser", action="store_true",
                   help="apre il report HTML nel browser al termine (implica --report)")
    args = p.parse_args()

    # --send-email e --open implicano --report
    if args.send_email or args.open_browser:
        args.report = True

    mode = "live-only" if args.live_only else "full"

    try:
        run(mode=mode, sources=args.sources, end=args.end, db_path=args.db)
    except Exception as e:
        print(f"ERRORE durante il download: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        # continua comunque verso il report se richiesto
        if not args.report:
            return 1

    if args.report and not args.live_only:
        print("\n--- Generazione report ---")
        try:
            # import inline per non rallentare i run live-only
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
            print(f"Righe: {d['total_rows']:,} | Serie: "
                  f"{sum(x['series'] for x in d['tables'])} | "
                  f"Score: {d['score_avg']} | Ferme: {len(d['stalled'])}")

            if args.send_email:
                send_email(html_content, d)

            # report Ray Dalio (fasi ciclo debito + regime) — il calcolo Dalio
            # e' gia' stato fatto dentro run(); qui generiamo solo l'HTML.
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
                print(f"Report Dalio: {dalio_path} "
                      f"(fasi: {dd['phase_counts']})")
            except Exception as e:
                print(f"(report Dalio saltato: {e})")

            if args.open_browser:
                import webbrowser
                webbrowser.open(html_path.as_uri())
                if dalio_path:
                    webbrowser.open(dalio_path.as_uri())
                print(f"Aperto nel browser: {html_path}")

        except Exception as e:
            print(f"ERRORE generazione report: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()

    return 0


if __name__ == "__main__":
    sys.exit(main())
