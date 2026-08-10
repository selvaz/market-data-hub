"""Render a ``regime_window_comparison`` depot row as a self-contained HTML
report. ``render_html(row)`` is a pure function of the exact dict shape
``lazystats.io.depot.ResultDepot.load()`` returns -- no live DB access, no
re-fitting -- so any saved row can always be re-rendered from its JSON
alone.
"""

from __future__ import annotations

import json

__all__ = ["render_html"]

_TEMPLATE = r"""<title>Regime Window Comparison — depot artifact</title>
<style>
  :root {
    --bg: #F5F6FA; --surface: #FFFFFF; --surface-2: #ECEEF3;
    --ink: #12151C; --ink-soft: #4B5468; --ink-faint: #8890A0;
    --border: #DDE1E8; --accent: #4C6FA6; --accent-ink: #2E4468;
    --pos: #B24A2E; --neg: #2E6E8E; --mid: #B8860B; --mid-ink: #7A5A07;
    --radius: 10px;
    --shadow: 0 1px 2px rgba(20,24,34,.04), 0 8px 24px -12px rgba(20,24,34,.12);
  }
  :root[data-theme="dark"] {
    --bg: #0D0F15; --surface: #151822; --surface-2: #1D212D;
    --ink: #E9EAF0; --ink-soft: #9AA3B7; --ink-faint: #656E82;
    --border: #262B38; --accent: #7C9BD0; --accent-ink: #B9CCE8;
    --pos: #D97B5C; --neg: #5DA0C4; --mid: #E0BB4A; --mid-ink: #F0D48A;
    --shadow: 0 1px 2px rgba(0,0,0,.3), 0 8px 24px -12px rgba(0,0,0,.5);
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --bg: #0D0F15; --surface: #151822; --surface-2: #1D212D;
      --ink: #E9EAF0; --ink-soft: #9AA3B7; --ink-faint: #656E82;
      --border: #262B38; --accent: #7C9BD0; --accent-ink: #B9CCE8;
      --pos: #D97B5C; --neg: #5DA0C4; --mid: #E0BB4A; --mid-ink: #F0D48A;
      --shadow: 0 1px 2px rgba(0,0,0,.3), 0 8px 24px -12px rgba(0,0,0,.5);
    }
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: var(--bg); color: var(--ink);
    font-family: -apple-system, "Segoe UI", ui-sans-serif, system-ui, sans-serif; -webkit-font-smoothing: antialiased; }
  body { max-width: 1180px; margin: 0 auto; padding: 28px 24px 64px; }
  .mono { font-family: ui-monospace, "Cascadia Mono", "SFMono-Regular", Consolas, monospace; font-variant-numeric: tabular-nums; }

  header { display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between; gap: 16px;
    padding-bottom: 20px; border-bottom: 1px solid var(--border); margin-bottom: 24px; }
  .eyebrow { font-size: 12px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: var(--accent-ink); margin: 0 0 6px; }
  h1 { font-size: 26px; font-weight: 700; letter-spacing: -.01em; margin: 0; text-wrap: balance; }
  .meta-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, max-content)); gap: 4px 26px; text-align: right; }
  .meta-item .k { font-size: 10.5px; letter-spacing: .06em; text-transform: uppercase; color: var(--ink-faint); display: block; }
  .meta-item .v { font-size: 13px; color: var(--ink-soft); }

  section { margin-bottom: 32px; }
  .section-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 12px; flex-wrap: wrap; }
  h2 { font-size: 15px; font-weight: 700; letter-spacing: .01em; margin: 0; }
  .section-note { font-size: 12.5px; color: var(--ink-faint); }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow); }
  .empty-note { padding: 18px 20px; color: var(--ink-faint); font-size: 13px; }

  .flag-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 10px; padding: 14px; }
  .flag-card { border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; display: flex; flex-direction: column; gap: 6px; position: relative; overflow: hidden; }
  .flag-card::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: var(--pos); }
  .flag-top { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
  .flag-ticker { font-weight: 700; font-size: 14px; }
  .flag-name { font-size: 11px; color: var(--ink-faint); }
  .flag-compare { display: flex; align-items: center; gap: 8px; font-size: 12px; }
  .pill { display: inline-block; font-size: 10.5px; font-weight: 700; padding: 2px 8px; border-radius: 999px; }
  .pill.calm { color: var(--neg); background: color-mix(in srgb, var(--neg) 14%, transparent); }
  .pill.mid { color: var(--mid-ink); background: color-mix(in srgb, var(--mid) 20%, transparent); }
  .pill.highvol { color: var(--pos); background: color-mix(in srgb, var(--pos) 14%, transparent); }
  .pill.single { color: var(--ink-faint); background: var(--surface-2); }
  .mode-note { font-size: 10px; color: var(--ink-faint); font-style: italic; }
  .arrow { color: var(--ink-faint); }
  .flag-detail { font-size: 11px; color: var(--ink-faint); }

  table.compare { border-collapse: collapse; width: 100%; font-size: 12.5px; }
  table.compare th { text-align: right; font-size: 10.5px; letter-spacing: .04em; text-transform: uppercase;
    color: var(--ink-faint); font-weight: 700; padding: 10px 8px; border-bottom: 1px solid var(--border); }
  table.compare th:first-child, table.compare th:nth-child(2), table.compare td:first-child, table.compare td:nth-child(2) { text-align: left; }
  table.compare td { text-align: right; padding: 7px 8px; border-bottom: 1px solid var(--border); }
  table.compare tr:last-child td { border-bottom: none; }
  table.compare tr.disagree { background: color-mix(in srgb, var(--pos) 6%, transparent); }
  .ticker-cell { font-weight: 700; }
  .name-cell { font-size: 11px; color: var(--ink-faint); }
  .wrap-x { overflow-x: auto; padding: 4px 14px 14px; }

  footer { margin-top: 40px; padding-top: 18px; border-top: 1px solid var(--border); font-size: 11.5px;
    color: var(--ink-faint); display: flex; flex-wrap: wrap; gap: 6px 22px; }
  footer b { color: var(--ink-soft); font-weight: 600; }
</style>

<header>
  <div>
    <p class="eyebrow">lazystats_depot &middot; scheduled artifact</p>
    <h1>Regime Window Comparison</h1>
  </div>
  <div class="meta-grid mono" id="meta-grid"></div>
</header>

<section>
  <div class="section-head">
    <h2>Structural changes</h2>
    <span class="section-note">Full-history vs. windowed classification disagree -- the signal this report exists for.</span>
  </div>
  <div class="card" id="disagree-section"></div>
</section>

<section>
  <div class="section-head">
    <h2>Single-state flags</h2>
    <span class="section-note">One window (or both) found no distinguishable regime structure -- not compared.</span>
  </div>
  <div class="card" id="single-section"></div>
</section>

<section>
  <div class="section-head">
    <h2>All symbols</h2>
  </div>
  <div class="card wrap-x">
    <table class="compare" id="all-table"></table>
  </div>
</section>

<footer id="footer"></footer>

<script>
const ROW = __ROW_JSON__;
const P = ROW.payload;

function pill(tier) {
  const label = { calm: "calm", mid: "mid vol", highvol: "high-vol", single: "single" }[tier] || tier;
  return `<span class="pill ${tier}">${label}</span>`;
}
function modeNote(mode) {
  return mode === "direct"
    ? `<span class="mode-note">same state count -- compared directly</span>`
    : `<span class="mode-note">state counts differ -- collapsed to calm/high-vol</span>`;
}

(function renderHeader() {
  const items = [
    ["As of", P.as_of],
    ["Window", P.variant],
    ["Symbols", P.summary.n_symbols],
    ["Disagree", P.summary.n_disagree],
    ["Single-state", P.summary.n_single_state],
    ["Missing", P.summary.n_missing],
  ];
  document.getElementById("meta-grid").innerHTML = items.map(([k, v]) =>
    `<div class="meta-item"><span class="k">${k}</span><span class="v">${v}</span></div>`).join("");
})();

(function renderDisagree() {
  const flagged = P.symbols.filter(s => s.comparison.status === "ok" && s.comparison.agreement === "disagree");
  const el = document.getElementById("disagree-section");
  if (!flagged.length) {
    el.innerHTML = `<div class="empty-note">No structural disagreements -- every symbol's calm/high-vol classification agrees across both windows.</div>`;
    return;
  }
  el.innerHTML = `<div class="flag-grid">` + flagged.map(s => {
    const c = s.comparison;
    return `
      <div class="flag-card">
        <div class="flag-top">
          <span><span class="flag-ticker">${s.symbol}</span>${s.name ? ` <span class="flag-name">${s.name}</span>` : ""}</span>
        </div>
        <div class="flag-compare">
          <span>Full (${c.n_states_full}s)</span>${pill(c.current_tier_full)}
          <span class="arrow">&rarr;</span>
          <span>${P.variant} (${c.n_states_windowed}s)</span>${pill(c.current_tier_windowed)}
        </div>
        <div class="flag-detail">full data from ${c.data_start_full} &middot; ${P.variant} data from ${c.data_start_windowed} &middot; ${modeNote(c.comparison_mode)}</div>
      </div>`;
  }).join("") + `</div>`;
})();

(function renderSingle() {
  const flagged = P.symbols.filter(s => s.comparison.status === "ok" && s.comparison.agreement === "single_state");
  const el = document.getElementById("single-section");
  if (!flagged.length) {
    el.innerHTML = `<div class="empty-note">No single-state windows.</div>`;
    return;
  }
  el.innerHTML = `<div class="flag-grid">` + flagged.map(s => {
    const c = s.comparison;
    return `
      <div class="flag-card">
        <div class="flag-top">
          <span><span class="flag-ticker">${s.symbol}</span>${s.name ? ` <span class="flag-name">${s.name}</span>` : ""}</span>
        </div>
        <div class="flag-compare">
          <span>Full (${c.n_states_full}s)</span>${pill(c.current_tier_full)}
          <span class="arrow">&rarr;</span>
          <span>${P.variant} (${c.n_states_windowed}s)</span>${pill(c.current_tier_windowed)}
        </div>
      </div>`;
  }).join("") + `</div>`;
})();

(function renderAll() {
  const thead = `<tr><th>Ticker</th><th>Name</th><th>Full states</th><th>Full tier</th><th>${P.variant} states</th><th>${P.variant} tier</th><th>Agreement</th><th>Mode</th></tr>`;
  const tbody = P.symbols.map(s => {
    const c = s.comparison;
    if (c.status === "missing") {
      return `<tr><td class="ticker-cell">${s.symbol}</td><td class="name-cell">${s.name || ""}</td><td colspan="6">missing (full=${c.full_available}, ${P.variant}=${c.windowed_available})</td></tr>`;
    }
    const rowCls = c.agreement === "disagree" ? "disagree" : "";
    return `
      <tr class="${rowCls}">
        <td class="ticker-cell">${s.symbol}</td>
        <td class="name-cell">${s.name || ""}</td>
        <td>${c.n_states_full}</td>
        <td>${pill(c.current_tier_full)}</td>
        <td>${c.n_states_windowed}</td>
        <td>${pill(c.current_tier_windowed)}</td>
        <td>${c.agreement === "disagree" ? "&#9888; disagree" : c.agreement === "single_state" ? "single" : "agree"}</td>
        <td class="name-cell">${c.comparison_mode === "direct" ? "direct" : "collapsed"}</td>
      </tr>`;
  }).join("");
  document.getElementById("all-table").innerHTML = `<thead>${thead}</thead><tbody>${tbody}</tbody>`;
})();

(function renderFooter() {
  const p = P.provenance;
  document.getElementById("footer").innerHTML = `
    <span><b>Window</b> ${p.variant}</span>
    <span><b>Periods/year</b> ${p.periods_per_year}</span>
    <span><b>Rule</b> ${p.classification_rule}</span>
    <span><b>Saved</b> ${ROW.created_at}</span>
  `;
})();
</script>
"""


def render_html(row: dict) -> str:
    """Render ``row`` (the dict shape :meth:`ResultDepot.load` returns) as a
    self-contained HTML report. Pure function -- no I/O."""
    return _TEMPLATE.replace("__ROW_JSON__", json.dumps(row))
