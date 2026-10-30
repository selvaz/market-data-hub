"""Inline visual system shared in spirit with the legacy Economic Release Monitor."""

REPORT_CSS = """
:root {
  --bg:#F5F6FA; --surface:#FFFFFF; --surface-2:#ECEEF3;
  --ink:#12151C; --ink-soft:#4B5468; --ink-faint:#8890A0;
  --border:#DDE1E8; --accent:#4C6FA6; --accent-ink:#2E4468;
  --pos:#B24A2E; --neg:#2E6E8E; --mid:#B8860B; --mid-ink:#7A5A07;
  --radius:10px;
  --shadow:0 1px 2px rgba(20,24,34,.04),0 8px 24px -12px rgba(20,24,34,.12);
}
@media (prefers-color-scheme:dark) {
  :root {
    --bg:#0D0F15; --surface:#151822; --surface-2:#1D212D;
    --ink:#E9EAF0; --ink-soft:#9AA3B7; --ink-faint:#656E82;
    --border:#262B38; --accent:#7C9BD0; --accent-ink:#B9CCE8;
    --pos:#D97B5C; --neg:#5DA0C4; --mid:#E0BB4A; --mid-ink:#F0D48A;
    --shadow:0 1px 2px rgba(0,0,0,.3),0 8px 24px -12px rgba(0,0,0,.5);
  }
}
* { box-sizing:border-box; }
[hidden] { display:none !important; }
html,body { margin:0; padding:0; background:var(--bg); color:var(--ink);
  font-family:-apple-system,"Segoe UI",ui-sans-serif,system-ui,sans-serif; -webkit-font-smoothing:antialiased; }
body { max-width:1180px; margin:0 auto; padding:28px 24px 64px; }
.mono { font-family:ui-monospace,"Cascadia Mono","SFMono-Regular",Consolas,monospace;
  font-variant-numeric:tabular-nums; }
header { display:flex; flex-wrap:wrap; align-items:flex-end; justify-content:space-between; gap:16px;
  padding-bottom:20px; border-bottom:1px solid var(--border); margin-bottom:24px; }
.eyebrow { font-size:12px; font-weight:600; letter-spacing:.08em; text-transform:uppercase;
  color:var(--accent-ink); margin:0 0 6px; }
h1 { font-size:26px; font-weight:700; letter-spacing:-.01em; margin:0; text-wrap:balance; }
h2 { font-size:15px; margin:0 0 12px; }
h3 { margin:0; font-size:15px; }
p { margin:0; overflow-wrap:anywhere; }
.subtitle { margin-top:6px; font-size:13px; color:var(--ink-soft); }
.meta-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(90px,max-content));
  gap:4px 26px; text-align:right; }
.meta-item .k { font-size:10.5px; letter-spacing:.06em; text-transform:uppercase; color:var(--ink-faint); display:block; }
.meta-item .v { font-size:13px; color:var(--ink-soft); }
section { margin-bottom:32px; }
.summary { color:var(--ink-soft); font-size:13px; margin:0 0 8px; }
.window { margin:14px 0 20px; font-size:11.5px; color:var(--ink-faint); overflow-wrap:anywhere; }
.filter-bar { display:flex; gap:8px; align-items:center; margin-bottom:12px; flex-wrap:wrap; }
.filter-bar input { flex:1; min-width:160px; padding:8px 12px; border-radius:999px; border:1px solid var(--border);
  background:var(--surface); color:var(--ink); font:inherit; font-size:13px; }
.toggle-group { display:inline-flex; background:var(--surface-2); border-radius:999px; padding:3px; gap:2px; }
.toggle-group button { border:none; background:transparent; font:inherit; font-size:12px; font-weight:600;
  color:var(--ink-soft); padding:6px 14px; border-radius:999px; cursor:pointer; white-space:nowrap; }
.toggle-group button.active { background:var(--surface); color:var(--ink); box-shadow:var(--shadow); }
button:focus-visible,input:focus-visible,a:focus-visible { outline:2px solid var(--accent); outline-offset:3px; }
.filter-note,.meta { color:var(--ink-faint); font-size:11.5px; line-height:1.5; }
.filter-note { margin-bottom:22px; }
.release-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(min(100%,380px),1fr)); gap:12px; }
.release-card { background:var(--surface); border:1px solid var(--accent); border-radius:var(--radius);
  box-shadow:var(--shadow); padding:16px 18px; display:flex; flex-direction:column; gap:10px; min-width:0; }
.release-top { display:flex; align-items:baseline; justify-content:space-between; gap:8px; }
.release-name { font-weight:700; font-size:15px; overflow-wrap:anywhere; }
.release-agency { margin-top:4px; font-size:11px; color:var(--ink-faint); overflow-wrap:anywhere; }
.badge { font-size:10px; font-weight:700; letter-spacing:.02em; padding:2px 7px; border-radius:999px; white-space:nowrap; }
.badge.tier1 { color:var(--pos); background:color-mix(in srgb,var(--pos) 14%,transparent); }
.badge.tier2 { color:var(--mid-ink); background:color-mix(in srgb,var(--mid) 20%,transparent); }
.badge.unknown { color:var(--ink-soft); background:var(--surface-2); }
.release-figures { display:flex; flex-wrap:wrap; gap:8px 18px; align-items:baseline; }
.figure-label { display:block; font-size:10.5px; font-weight:400; color:var(--ink-faint); margin-bottom:3px; }
.release-value { font-size:22px; font-weight:700; font-family:ui-monospace,monospace; overflow-wrap:anywhere; }
.release-change { font-size:13px; font-weight:600; font-family:ui-monospace,monospace; }
.release-change.pos { color:var(--pos); } .release-change.neg { color:var(--neg); }
.release-period { font-size:11px; color:var(--ink-faint); }
.indicator-card { color:var(--ink-soft); font-size:12.5px; line-height:1.5; }
.indicator-card p+p { margin-top:5px; }
.release-details { display:flex; flex-direction:column; gap:6px; font-size:12px; color:var(--ink-soft); }
.commentary { display:flex; flex-direction:column; gap:6px; border-top:1px solid var(--border); padding-top:10px; }
a { color:var(--accent-ink); text-decoration:none; font-weight:600; }
a:hover { text-decoration:underline; }
.commentary a { font-size:12px; }
.empty-note { font-size:13px; color:var(--ink-faint); padding:20px 0; }
.status-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(min(100%,280px),1fr)); gap:8px; }
.status-row { background:var(--surface); border:1px solid var(--border); border-radius:var(--radius);
  padding:10px 14px; display:flex; flex-direction:column; gap:6px; font-size:12.5px; overflow-wrap:anywhere; }
.status-row.error { border-left:3px solid var(--pos); }
.status-name { display:flex; align-items:baseline; justify-content:space-between; gap:10px; }
.status-name .n { font-weight:600; } .status-name .a { font-size:10.5px; color:var(--ink-faint); }
.status-reason { color:var(--ink-soft); font-size:12px; line-height:1.5; }
.limitations { font-size:11.5px; color:var(--ink-soft); line-height:1.5; }
.limitations summary { cursor:pointer; }
footer { margin-top:40px; padding-top:18px; border-top:1px solid var(--border); font-size:11.5px;
  color:var(--ink-faint); display:flex; flex-wrap:wrap; gap:6px 22px; }
footer b { color:var(--ink-soft); font-weight:600; }
@media (max-width:600px) {
  body { padding:22px 16px 40px; } .meta-grid { text-align:left; } h1 { font-size:23px; }
  .toggle-group { max-width:100%; overflow-x:auto; } .toggle-group button { padding:7px 12px; }
}
@media print { .filter-bar,.filter-note { display:none; } .release-card { break-inside:avoid; box-shadow:none; } }
"""

REPORT_SCRIPT = """
(() => {
  const items = Array.from(document.querySelectorAll('[data-report-item]'));
  const groups = Array.from(document.querySelectorAll('[data-release-group]'));
  const buttons = Array.from(document.querySelectorAll('#view-toggle button'));
  const issues = document.getElementById('issues-section');
  const search = document.getElementById('search');
  const note = document.getElementById('filter-note');
  const empty = document.getElementById('filter-empty');
  const issueItems = items.filter(item => item.dataset.kind === 'issue');
  const descriptions = {
    all: 'All releases in the window, with latest collection issues.',
    new: 'First seen in the window. Every release in this report meets this rule.',
    tier1: 'Tier 1 releases, using the indicator catalogue classification.',
    error: 'Latest collection outcomes needing review or reporting failure.'
  };
  let currentFilter = 'all';
  function matchesFilter(item, view, query) {
    const release = item.dataset.kind === 'release';
    if (view === 'new' && (!release || item.dataset.new !== 'true')) return false;
    if (view === 'tier1' && (!release || item.dataset.tier !== '1')) return false;
    if (view === 'error' && release) return false;
    return item.textContent.toLowerCase().includes(query);
  }
  function applyFilter() {
    const query = search.value.trim().toLowerCase();
    items.forEach(item => { item.hidden = !matchesFilter(item, currentFilter, query); });
    groups.forEach(group => {
      group.hidden = !Array.from(group.querySelectorAll('[data-report-item]')).some(item => !item.hidden);
    });
    issues.hidden = !['all', 'error'].includes(currentFilter)
      || (query.length > 0 && !issueItems.some(item => !item.hidden));
    const visible = items.filter(item => !item.hidden);
    const releases = visible.filter(item => item.dataset.kind === 'release').length;
    note.textContent = descriptions[currentFilter] + ' Showing ' + releases
      + ' release(s) and ' + (visible.length - releases) + ' collection issue(s).';
    empty.hidden = visible.length > 0
      || (['all', 'error'].includes(currentFilter) && !query && issueItems.length === 0);
    buttons.forEach(button => {
      const active = button.dataset.v === currentFilter;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', String(active));
    });
  }
  buttons.forEach(button => button.addEventListener('click', () => {
    currentFilter = button.dataset.v;
    applyFilter();
  }));
  search.addEventListener('input', applyFilter);
  applyFilter();
})();
"""
