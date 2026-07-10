# -*- coding: utf-8 -*-
"""
report.py — single self-contained HTML report for the daily regime run.

Mirrors the "single file, no external assets" approach already used by
make_dalio_report.py so the output stays directly attachable to a Telegram
message (send_document). Charts are lazyhmm's own matplotlib plots, rendered
under the Agg backend and embedded as base64 PNGs.
"""
from __future__ import annotations

import base64
import html
import io
from datetime import date, datetime
from pathlib import Path
from typing import Dict

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from market_data_hub import catalog  # noqa: E402
from market_data_hub.regime.estimate import SymbolRunResult  # noqa: E402

_CSS = """
body { font-family: -apple-system, Segoe UI, Arial, sans-serif; margin: 0;
       background: #0B0F14; color: #D7E1EA; }
header { padding: 16px 24px; background: #10161d; border-bottom: 1px solid #2B3440; }
h1 { font-size: 20px; margin: 0 0 4px; }
.sub { color: #9AA7B4; font-size: 13px; }
nav { padding: 10px 24px; background: #10161d; border-bottom: 1px solid #2B3440;
      display: flex; flex-wrap: wrap; gap: 6px; }
nav a { color: #7BDFF2; text-decoration: none; font-size: 12px; padding: 3px 8px;
        border: 1px solid #2B3440; border-radius: 10px; }
nav a.flag { border-color: #FAA916; color: #FAA916; }
main { padding: 20px 24px; }
table { border-collapse: collapse; width: 100%; margin-bottom: 24px; font-size: 13px; }
th, td { border-bottom: 1px solid #2B3440; padding: 6px 10px; text-align: left; }
th { color: #9AA7B4; font-weight: 600; }
tr.flag td { color: #FAA916; }
tr.err td { color: #EE6352; }
section.symbol { border-top: 2px solid #2B3440; padding-top: 18px; margin-top: 18px; }
section.symbol h2 { margin-bottom: 4px; }
img { max-width: 100%; border-radius: 4px; }
.badge { display: inline-block; padding: 1px 7px; border-radius: 8px; font-size: 11px;
         margin-left: 6px; }
.badge.hv { background: #EE6352; color: #0B0F14; }
.badge.calm { background: #2D7DD2; color: #0B0F14; }
.badge.rev { background: #FAA916; color: #0B0F14; }
"""

_JS = """
function show(id) {
  document.querySelectorAll('section.symbol').forEach(s => s.style.display = 'none');
  document.getElementById('landing').style.display = id ? 'none' : 'block';
  if (id) document.getElementById(id).style.display = 'block';
}
"""


def _fig_to_base64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _display_names() -> Dict[str, str]:
    """symbol -> human-readable name from tickers.yaml (best-effort, one lookup)."""
    try:
        df = catalog.list_symbols(with_coverage=False)
        return {s: str(n) for s, n in zip(df["symbol"], df["name"]) if n}
    except Exception:
        return {}


def _chart_img(run, symbol: str) -> str:
    # The fit is daily: lazyhmm's default points_per_year=52 (weekly) would
    # slice ~1 year while the chart claims 5.
    run.plot_series_with_regimes(symbol, last_years=5, points_per_year=252)
    fig = plt.gcf()
    b64 = _fig_to_base64(fig)
    return f'<img alt="{html.escape(symbol)} regimes" src="data:image/png;base64,{b64}"/>'


def _revision_table(con: duckdb.DuckDBPyConnection, symbol: str, dates: list) -> str:
    if not dates:
        return ""
    rows_html = []
    for d in dates:
        hist = con.execute(
            """
            SELECT estimation_date, state, prob_high_vol FROM hmm_regime_estimates
            WHERE symbol = ? AND trading_date = ? ORDER BY estimation_date
            """,
            [symbol, d],
        ).fetch_df()
        if len(hist) < 2:
            continue
        old, new = hist.iloc[-2], hist.iloc[-1]
        rows_html.append(
            f"<tr><td>{d}</td><td>{int(old['state'])} &rarr; {int(new['state'])}</td>"
            f"<td>{old['prob_high_vol']:.3f} &rarr; {new['prob_high_vol']:.3f}</td>"
            f"<td>{old['estimation_date']} &rarr; {new['estimation_date']}</td></tr>"
        )
    if not rows_html:
        return ""
    return (
        "<h3>Retroactive revisions (last 30d)</h3>"
        "<table><tr><th>Trading date</th><th>State</th><th>P(high-vol)</th>"
        "<th>Estimation date</th></tr>" + "".join(rows_html) + "</table>"
    )


def _scalar(x):
    """Drill into arbitrarily-nested single-element lists down to the scalar."""
    while isinstance(x, list):
        x = x[0]
    return float(x)


def _stats_table(run, symbol: str) -> str:
    m = run.meta[symbol]
    labels = m["labels"]
    means = m["means_"]
    covars = m["covars_"]
    rows = []
    for s, lbl in enumerate(labels):
        # panel mode fits k=1 per symbol with diag cov_type; covars_[s] holds a
        # single variance nested at some depth depending on the internal shape.
        vol = _scalar(covars[s]) ** 0.5
        rows.append(f"<tr><td>{s}</td><td>{html.escape(lbl)}</td>"
                    f"<td>{_scalar(means[s]):.5f}</td><td>{vol:.5f}</td></tr>")
    transmat = "".join(
        "<tr>" + "".join(f"<td>{p:.3f}</td>" for p in row) + "</tr>"
        for row in m["transmat_"]
    )
    return (
        f"<p>Model: BIC={m['bic']:.1f} | logLik={m['loglik']:.1f} | states={m['S']}</p>"
        "<table><tr><th>State</th><th>Label</th><th>Mean return</th><th>Volatility</th></tr>"
        + "".join(rows) + "</table>"
        "<h3>Transition matrix</h3><table>" + transmat + "</table>"
    )


def generate_html_report(con: duckdb.DuckDBPyConnection,
                         results: Dict[str, SymbolRunResult], *,
                         out_dir: Path, asof: date) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)

    ok = {s: r for s, r in results.items() if r.status == "ok"}
    errored = {s: r for s, r in results.items() if r.status != "ok"}
    names = _display_names()

    def _name_div(symbol: str) -> str:
        name = names.get(symbol)
        return f'<div class="sub">{html.escape(name)}</div>' if name else ""
    changed = [s for s, r in ok.items() if r.changed_today]
    revised = [s for s, r in ok.items() if r.revised_last_n_days]

    nav_links = []
    for symbol in sorted(ok):
        flag = " flag" if symbol in changed or symbol in revised else ""
        nav_links.append(f'<a class="{flag.strip()}" href="#" onclick="show(\'sec-{symbol}\');return false;">{symbol}</a>')
    nav_links.append('<a href="#" onclick="show(null);return false;">↩ Recap</a>')

    recap_rows = []
    ordered = sorted(ok, key=lambda s: (s not in changed and s not in revised, s))
    for symbol in ordered:
        r = ok[symbol]
        cls = "flag" if (r.changed_today or r.revised_last_n_days) else ""
        badge = ""
        if r.changed_today:
            badge += ' <span class="badge rev">changed today</span>'
        if r.revised_last_n_days:
            badge += f' <span class="badge rev">{r.revised_last_n_days} revised</span>'
        vol_badge = '<span class="badge hv">high-vol</span>' if r.is_high_vol else '<span class="badge calm">calm</span>'
        recap_rows.append(
            f'<tr class="{cls}"><td><a href="#" onclick="show(\'sec-{symbol}\');return false;">{symbol}</a>{_name_div(symbol)}</td>'
            f"<td>{html.escape(r.current_label or '')} {vol_badge}</td>"
            f"<td>{r.prob_high_vol:.3f}</td><td>{r.n_states}</td><td>{badge}</td></tr>"
        )
    for symbol, r in errored.items():
        recap_rows.append(
            f'<tr class="err"><td>{symbol}{_name_div(symbol)}</td><td colspan="4">ERROR: {html.escape(r.error_msg or "")}</td></tr>'
        )

    sections = []
    for symbol in sorted(ok):
        r = ok[symbol]
        chart = _chart_img(r.run, symbol)
        stats = _stats_table(r.run, symbol)
        revs = _revision_table(con, symbol, r.revised_dates or [])
        sections.append(
            f'<section class="symbol" id="sec-{symbol}" style="display:none">'
            f"<h2>{symbol} &mdash; {html.escape(r.current_label or '')}</h2>"
            f"{_name_div(symbol)}"
            f"{chart}{stats}{revs}</section>"
        )

    body = f"""
<header>
  <h1>HMM Regime Monitor &mdash; {asof.isoformat()}</h1>
  <div class="sub">{len(ok)} symbols fitted &middot; {len(changed)} regime changes today &middot;
  {len(revised)} with retroactive revisions (30d) &middot; {len(errored)} errors &middot;
  generated {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
</header>
<nav>{''.join(nav_links)}</nav>
<main>
  <div id="landing">
    <table><tr><th>Symbol</th><th>Current regime</th><th>P(high-vol)</th><th>#States</th><th>Flags</th></tr>
    {''.join(recap_rows)}
    </table>
  </div>
  {''.join(sections)}
</main>
"""
    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<title>HMM Regime Monitor {asof.isoformat()}</title><style>{_CSS}</style></head>
<body>{body}<script>{_JS}</script></body></html>"""

    out_path = out_dir / f"hmm_regime_report_{asof.strftime('%Y%m%d')}.html"
    out_path.write_text(doc, encoding="utf-8")
    return out_path
