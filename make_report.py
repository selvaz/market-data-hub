# -*- coding: utf-8 -*-
"""
make_report.py — genera un report (HTML + Markdown) con i risultati dell'ultimo
download e le statistiche del database. Pensato per essere inviato via email.

Uso:
    python make_report.py                 # legge il DB di default
    python make_report.py --db <path>     # DB specifico
    python make_report.py --open          # apre l'HTML nel browser al termine

Output (in reports/):
    market_data_report_YYYYMMDD.html      # versione email-ready
    market_data_report_YYYYMMDD.md        # versione testo
"""
from __future__ import annotations

import argparse
import smtplib
import sys
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from market_data_hub.db.connection import get_conn  # noqa: E402
from market_data_hub.config_loader import get_settings  # noqa: E402

REPORT_DIR = Path(__file__).parent / "reports"


# ---------------------------------------------------------------- collectors
def collect(con) -> dict:
    d: dict = {}
    d["now"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # volumi per tabella
    tbl_stats = []
    for tbl, key, dcol in [("prices_daily", "symbol", "date"),
                           ("crypto_ohlcv", "symbol", "ts"),
                           ("macro_series", "series_id", "date"),
                           ("macro_panel", "indicator_id", "date")]:
        r = con.execute(
            f"SELECT count(*) AS n_rows, count(DISTINCT {key}) AS n_series, "
            f"min({dcol}) AS mn, max({dcol}) AS mx FROM {tbl}").fetch_df().iloc[0]
        tbl_stats.append({"table": tbl, "rows": int(r["n_rows"] or 0),
                          "series": int(r["n_series"] or 0),
                          "first": r["mn"], "last": r["mx"]})
    d["tables"] = tbl_stats
    d["total_rows"] = sum(t["rows"] for t in tbl_stats)

    # ultimo run
    last = con.execute(
        "SELECT run_id, min(started_at) AS run_start, max(ended_at) AS run_end, "
        "count(*) AS calls, sum(rows_added) AS added, sum(rows_updated) AS updated, "
        "sum(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errors, "
        "sum(CASE WHEN status='empty' THEN 1 ELSE 0 END) AS empty "
        "FROM download_log GROUP BY run_id ORDER BY run_start DESC LIMIT 1").fetch_df()
    d["last_run"] = last.iloc[0].to_dict() if not last.empty else {}

    # esito ultimo run per sorgente
    if not last.empty:
        rid = last.iloc[0]["run_id"]
        d["by_source"] = con.execute(
            "SELECT source, count(*) AS calls, sum(rows_added) AS added, "
            "sum(CASE WHEN status IN ('ok','fallback') THEN 1 ELSE 0 END) AS ok, "
            "sum(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errors, "
            "sum(CASE WHEN status='empty' THEN 1 ELSE 0 END) AS empty "
            "FROM download_log WHERE run_id = ? GROUP BY source ORDER BY source",
            [rid]).fetch_df()
        d["errors"] = con.execute(
            "SELECT source, symbol, error_msg FROM download_log "
            "WHERE run_id = ? AND status='error' ORDER BY source LIMIT 40",
            [rid]).fetch_df()
    else:
        d["by_source"] = pd.DataFrame()
        d["errors"] = pd.DataFrame()

    # coverage
    cov = con.execute("SELECT * FROM coverage_report").fetch_df()
    d["coverage_n"] = len(cov)
    d["stalled"] = cov[cov["stalled"] == True] if not cov.empty else pd.DataFrame()  # noqa: E712
    d["score_avg"] = round(cov["coverage_score"].mean(), 1) if not cov.empty else 0
    d["score_by_class"] = (cov.groupby("asset_class")
                           .agg(n=("symbol", "count"),
                                score=("coverage_score", "mean"),
                                stalled=("stalled", "sum")).reset_index()
                           if not cov.empty else pd.DataFrame())
    # worst 15 per score
    d["worst"] = (cov.sort_values("coverage_score")
                  [["symbol", "source", "asset_class", "freq_detected",
                    "last_date", "lag_days", "coverage_score", "status"]].head(15)
                  if not cov.empty else pd.DataFrame())

    # --- freschezza: serie aggiornate vs ferme, per sorgente, con range date ---
    if not cov.empty:
        c = cov.copy()
        c["last_date"] = pd.to_datetime(c["last_date"], errors="coerce")
        c["fresh"] = ~c["stalled"].astype(bool)
        fr = (c.groupby("source")
              .agg(n_serie=("symbol", "count"),
                   aggiornate=("fresh", "sum"),
                   ferme=("stalled", "sum"),
                   ultima_min=("last_date", "min"),
                   ultima_max=("last_date", "max")).reset_index())
        for col in ("ultima_min", "ultima_max"):
            fr[col] = fr[col].dt.strftime("%Y-%m-%d")
        d["freshness"] = fr
        # ripartizione per anno dell'ultima osservazione
        c["anno_ultimo"] = c["last_date"].dt.year
        byyear = (c.groupby("anno_ultimo")
                  .agg(n_serie=("symbol", "count")).reset_index()
                  .sort_values("anno_ultimo", ascending=False))
        byyear["anno_ultimo"] = byyear["anno_ultimo"].astype("Int64").astype(str)
        d["by_year"] = byyear
    else:
        d["freshness"] = pd.DataFrame()
        d["by_year"] = pd.DataFrame()
    return d


# ---------------------------------------------------------------- renderers
def _df_html(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return "<p style='color:#888'>—</p>"
    return df.to_html(index=False, border=0, classes="t", justify="left")


def render_html(d: dict) -> str:
    t = d["tables"]
    lr = d["last_run"]
    rows_tbl = "".join(
        f"<tr><td>{x['table']}</td><td class=n>{x['rows']:,}</td>"
        f"<td class=n>{x['series']}</td><td>{x['first']}</td><td>{x['last']}</td></tr>"
        for x in t)
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
 body{{font-family:-apple-system,Segoe UI,Arial,sans-serif;color:#1a1a2e;max-width:820px;margin:0 auto;padding:18px;font-size:14px}}
 h1{{font-size:20px;border-bottom:3px solid #1d4ed8;padding-bottom:8px}}
 h2{{font-size:15px;margin-top:24px;color:#1d4ed8;border-bottom:1px solid #e0e0e0;padding-bottom:4px}}
 table{{border-collapse:collapse;width:100%;font-size:12.5px;margin:8px 0}}
 .t th{{background:#f1f5f9;text-align:left;padding:6px 8px;border-bottom:2px solid #e2e8f0}}
 .t td{{padding:5px 8px;border-bottom:1px solid #f1f5f9}}
 td.n{{text-align:right;font-variant-numeric:tabular-nums}}
 .kpi{{display:inline-block;background:#eff6ff;border-radius:8px;padding:10px 16px;margin:4px 8px 4px 0}}
 .kpi b{{display:block;font-size:22px;color:#1d4ed8}}
 .kpi span{{font-size:11px;color:#555}}
 .ok{{color:#16a34a;font-weight:600}} .warn{{color:#d97706;font-weight:600}} .err{{color:#dc2626;font-weight:600}}
</style></head><body>
<h1>market_data_hub — Report download &amp; database</h1>
<p style="color:#666">Generato: {d['now']}</p>

<div>
 <div class="kpi"><b>{d['total_rows']:,}</b><span>righe totali nel DB</span></div>
 <div class="kpi"><b>{sum(x['series'] for x in t)}</b><span>serie/strumenti</span></div>
 <div class="kpi"><b>{d['score_avg']}</b><span>coverage score medio</span></div>
 <div class="kpi"><b>{len(d['stalled'])}</b><span>serie ferme (stalled)</span></div>
</div>

<h2>Volumi per tabella</h2>
<table class="t"><tr><th>Tabella</th><th>Righe</th><th>Serie</th><th>Da</th><th>A</th></tr>{rows_tbl}</table>

<h2>Ultimo download</h2>
<p>Run <code>{lr.get('run_id','—')}</code> &middot; {lr.get('calls',0)} chiamate &middot;
 <span class="ok">{int(lr.get('added',0) or 0):,} righe aggiunte</span> &middot;
 <span class="warn">{int(lr.get('empty',0) or 0)} vuote</span> &middot;
 <span class="err">{int(lr.get('errors',0) or 0)} errori</span></p>
<h3 style="font-size:13px">Per sorgente</h3>
{_df_html(d['by_source'])}

<h2>Freschezza per sorgente</h2>
<p style="color:#666;font-size:12px">Serie aggiornate vs ferme e intervallo di ultima osservazione per ciascuna sorgente.</p>
{_df_html(d.get('freshness', pd.DataFrame()))}

<h2>Serie per anno dell'ultima osservazione</h2>
{_df_html(d.get('by_year', pd.DataFrame()))}

<h2>Coverage per asset class</h2>
{_df_html(d['score_by_class'].round(1) if not d['score_by_class'].empty else d['score_by_class'])}

<h2>15 serie con coverage piu' basso</h2>
{_df_html(d['worst'])}

<h2>Serie ferme (stalled)</h2>
{_df_html(d['stalled'][['symbol','source','asset_class','freq_detected','last_date','lag_days','coverage_score']] if not d['stalled'].empty else d['stalled'])}

<h2>Errori ultimo run</h2>
{_df_html(d['errors'])}

<p style="color:#888;font-size:11px;margin-top:30px">market_data_hub &middot; report automatico</p>
</body></html>"""


def render_md(d: dict) -> str:
    lines = [f"# market_data_hub — Report download & DB", f"_Generato: {d['now']}_", ""]
    lines += [f"- **Righe totali:** {d['total_rows']:,}",
              f"- **Serie/strumenti:** {sum(x['series'] for x in d['tables'])}",
              f"- **Coverage score medio:** {d['score_avg']}",
              f"- **Serie ferme:** {len(d['stalled'])}", ""]
    lines.append("## Volumi per tabella\n")
    lines.append("| Tabella | Righe | Serie | Da | A |")
    lines.append("|---|---:|---:|---|---|")
    for x in d["tables"]:
        lines.append(f"| {x['table']} | {x['rows']:,} | {x['series']} | {x['first']} | {x['last']} |")
    lr = d["last_run"]
    lines += ["", "## Ultimo download",
              f"Run `{lr.get('run_id','—')}` — {lr.get('calls',0)} chiamate, "
              f"{int(lr.get('added',0) or 0):,} righe aggiunte, "
              f"{int(lr.get('empty',0) or 0)} vuote, {int(lr.get('errors',0) or 0)} errori.", ""]
    if not d["by_source"].empty:
        try:
            lines.append(d["by_source"].to_markdown(index=False))
        except Exception:
            lines.append(d["by_source"].to_string(index=False))
    return "\n".join(lines)


# ---------------------------------------------------------------- email sender

def send_email(html_content: str, d: dict) -> bool:
    """Invia il report HTML via SMTP. Ritorna True se inviato, False se saltato/errore."""
    cfg = get_settings().get("email", {})
    user = cfg.get("smtp_user", "").strip()
    pwd = cfg.get("smtp_password", "").strip()
    if not user or not pwd:
        print("Email: smtp_user/smtp_password non configurati in settings.yaml — invio saltato")
        return False

    to_list = cfg.get("to", [])
    if isinstance(to_list, str):
        to_list = [to_list]
    if not to_list:
        print("Email: nessun destinatario configurato — invio saltato")
        return False

    lr = d.get("last_run", {})
    added = int(lr.get("added", 0) or 0)
    errors = int(lr.get("errors", 0) or 0)
    status_tag = "OK" if errors == 0 else f"ATTENZIONE ({errors} errori)"
    subject = (
        f"[market_data_hub] Report {d['now'][:10]} — "
        f"{d['total_rows']:,} righe | {d['score_avg']} score | {status_tag}"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg.get("from", user)
    msg["To"] = ", ".join(to_list)
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    host = cfg.get("smtp_host", "smtp.gmail.com")
    port = int(cfg.get("smtp_port", 587))
    try:
        with smtplib.SMTP(host, port, timeout=30) as srv:
            srv.ehlo()
            srv.starttls()
            srv.login(user, pwd)
            srv.sendmail(user, to_list, msg.as_bytes())
        print(f"Email inviata a {', '.join(to_list)}")
        return True
    except Exception as e:
        print(f"Email ERRORE: {e}")
        return False


def main() -> int:
    p = argparse.ArgumentParser(description="Genera report download + statistiche DB")
    p.add_argument("--db")
    p.add_argument("--open", action="store_true")
    p.add_argument("--send-email", action="store_true",
                   help="Invia il report via email (config SMTP in settings.yaml)")
    args = p.parse_args()

    REPORT_DIR.mkdir(exist_ok=True)
    con = get_conn(args.db, read_only=True)
    try:
        d = collect(con)
    finally:
        con.close()

    stamp = datetime.now().strftime("%Y%m%d")
    html_path = REPORT_DIR / f"market_data_report_{stamp}.html"
    md_path = REPORT_DIR / f"market_data_report_{stamp}.md"
    html_content = render_html(d)
    html_path.write_text(html_content, encoding="utf-8")
    md_path.write_text(render_md(d), encoding="utf-8")

    print(f"Report HTML: {html_path}")
    print(f"Report MD:   {md_path}")
    print(f"Righe totali: {d['total_rows']:,} | Serie: "
          f"{sum(x['series'] for x in d['tables'])} | Score medio: {d['score_avg']}")
    if args.send_email:
        send_email(html_content, d)
    if args.open:
        import webbrowser
        webbrowser.open(html_path.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
