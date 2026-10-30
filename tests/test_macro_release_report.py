"""Daily report contract against a real offline SQLite fixture."""
from dataclasses import replace
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import shutil
import socket
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

import run_macro_release_report as cli
from market_data_hub.indicator_cards import indicator_card, render_card
from market_data_hub.macro_releases import report as report_module
from market_data_hub.macro_releases import open_reader, open_writer, run_log
from market_data_hub.macro_releases.adapters import Observation, persist
from market_data_hub.macro_releases.catalogue import INDICATORS
from market_data_hub.macro_releases.http import Document
from market_data_hub.macro_releases.models import ConsensusReading, utc
from market_data_hub.macro_releases.report import (
    EMPTY_MESSAGE, GROUPS, collect_report, release_group, render_html, write_report,
)

NOW = datetime(2026, 10, 29, 21, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Report must not access the network")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(cli, "collect_report", lambda db, **kw: collect_report(db, now=NOW, **kw))


def obs(indicator="ecb_deposit_facility_rate_decision", family="ecb_policy", *,
        seen="2026-10-29T13:16:00Z", published="2026-10-29T13:15:00Z",
        period="2026-10-29", value=2.5, **kwargs):
    payload = repr((indicator, period, value, published, seen)).encode()
    document = Document(payload, 'https://issuer.example/decision?a=1&b="two"', seen,
                        hashlib.sha256(payload).hexdigest())
    return Observation(indicator, family, period, value, str(value), indicator,
                       INDICATORS[indicator].issuer_id, document, published[:10], published,
                       precision="exact", **kwargs)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "macro.sqlite"
    with open_writer(path) as store:
        persist(store, [obs()])
    return path


def releases(report):
    return [r for group in report["groups"] for r in group["releases"]]


def test_central_bank_and_read_only_issuer_document(db):
    before = db.read_bytes()
    report = collect_report(db, now=NOW)
    assert report["groups"][0]["name"] == "Central banks"
    row, = releases(report)
    assert row["official_value"] == 2.5 and row["unit"] == "% per annum"
    assert row["reference_period"] == "2026-10-29" and row["issuer"] == "ECB"
    assert row["document_url"].startswith("https://issuer.example/decision")
    assert row["published_at"] == utc("2026-10-29T13:15:00Z")
    assert db.read_bytes() == before
    with open_reader(db) as reader, pytest.raises(sqlite3.OperationalError, match="readonly"):
        reader.conn.execute("CREATE TABLE forbidden(x)")


def test_ff_forecast_label_and_previous_value(db):
    with open_writer(db) as store:
        persist(store, [
            obs("us_cpi_headline_yy", "us_cpi", period="2026-08", value=2.8,
                published="2026-09-14T12:30:00Z", seen="2026-09-14T12:31:00Z"),
            obs("us_cpi_headline_yy", "us_cpi", period="2026-09", value=2.9,
                published="2026-10-29T12:30:00Z", seen="2026-10-29T12:31:00Z"),
        ])
        store.put(ConsensusReading(
            provider="Forex Factory", indicator_id="us_cpi_headline_yy",
            release_event_id="BLS:us_cpi:regular:2026-10-29", measure="us_cpi_headline_yy",
            reference_period="2026-09", estimate_stage="regular", value=3, unit="%",
            captured_at="2026-10-29T12:00:00Z", release_cutoff_at="2026-10-29T12:30:00Z",
            cutoff_precision="exact", cutoff_basis="actual", content_hash="forecast",
        ))
    report = collect_report(db, now=NOW)
    row = next(r for r in releases(report) if r["issuer"] == "BLS")
    assert row["previous"]["value"] == 2.8
    forecast, = row["ff_forecasts"]
    assert forecast["label"] == "FF forecast" and forecast["kind"] == "ff_forecast"
    assert forecast["surprise_eligible"] is False
    html = render_html(report)
    assert "FF forecast:</b> 3 %" in html
    assert "consensus" not in html.lower()
    assert "consensus" not in json.dumps(report).lower()


def test_empty_window_exit_3_writes_explicit_message(db, tmp_path, capsys):
    assert cli.main(["--db", str(db), "--out-dir", str(tmp_path), "--since-hours", "1"]) == 3
    assert EMPTY_MESSAGE in capsys.readouterr().out
    html, = tmp_path.glob("*.html")
    assert EMPTY_MESSAGE in html.read_text(encoding="utf-8")
    assert EMPTY_MESSAGE == json.loads(html.with_suffix(".json").read_text(encoding="utf-8"))["message"]


def log_outcome(store, monkeypatch, run, family, status, at):
    monkeypatch.setattr(run_log, "now", lambda: utc(at))
    run_log.start(store, run, [family], {})
    run_log.family_result(store, run, family, dict(
        status=status, error_type="ParseError" if status != "ok" else None,
        error_message="unexpected <table> & value" if status != "ok" else None,
        vintages_added=0, readings_added=0, readings_skipped=0,
        fetch_since="2026-01", fetch_until="2026-10-29",
    ))
    run_log.finish(store, run, status)


def test_latest_family_issues_include_failure_review_and_clear_old_failure(db, monkeypatch):
    with open_writer(db) as store:
        for run, family, status, at in [
            ("a", "us_cpi", "failed", "2026-10-28T10:00:00Z"),
            ("b", "us_cpi", "no_change", "2026-10-29T10:00:00Z"),
            ("c", "uk_gdp", "needs_review", "2026-10-29T11:00:00Z"),
            ("d", "ea_hicp", "failed", "2026-10-29T12:00:00Z"),
            ("e", "ea_hicp", "ok", "2026-10-30T12:00:00Z"),
        ]:
            log_outcome(store, monkeypatch, run, family, status, at)
    report = collect_report(db, now=NOW)
    assert [(r["family"], r["status"]) for r in report["collection_issues"]] == [
        ("ea_hicp", "failed"), ("uk_gdp", "needs_review"),
    ]
    assert "ParseError: unexpected &lt;table&gt; &amp; value" in render_html(report)


@pytest.mark.parametrize("seen,included", [
    ("2026-10-28T20:59:59.999999Z", False),
    ("2026-10-28T21:00:00Z", True),
    ("2026-10-29T20:59:59.999999Z", True),
    ("2026-10-29T21:00:00Z", False),
    ("2026-10-29T21:00:00.000001Z", False),
])
def test_24h_first_seen_boundaries_and_late_capture(tmp_path, seen, included):
    path = tmp_path / "bounds.sqlite"
    with open_writer(path) as store:
        # Older publication must be reported when first collected in the window.
        persist(store, [obs(seen=seen, published="2026-10-20T13:15:00Z", period="2026-10-20")])
    report = collect_report(path, now=NOW)
    assert bool(report["release_count"]) is included
    assert report["since"] == utc("2026-10-28T21:00:00Z")


def test_repeat_and_correction_do_not_reannounce_old_release(db):
    with open_writer(db) as store:
        persist(store, [obs(value=2.75, seen="2026-10-30T13:16:00Z")])
    report = collect_report(db, now=datetime(2026, 10, 30, 21, tzinfo=timezone.utc))
    assert report["release_count"] == 0
    # Later correction must not leak backwards through as_known_at.
    assert releases(collect_report(db, now=NOW))[0]["official_value"] == 2.5


def test_html_escaping_and_unsafe_links(db):
    report = collect_report(db, now=NOW)
    row, = releases(report)
    row["indicator_name"] = '<script>alert("x")</script>'
    row["reference_period"] = "<period>"
    row["issuer"] = "<issuer> & bank"
    html = render_html(report)
    assert html.count('<script>') == 1 and '&lt;script&gt;alert(&quot;x&quot;)' in html
    assert "&lt;period&gt;" in html and "&lt;issuer&gt; &amp; bank" in html
    assert 'href="https://issuer.example/decision?a=1&amp;b=&quot;two&quot;"' in html
    row["document_url"] = "javascript:alert(1)"
    assert "javascript:" not in render_html(report)


def test_values_preserve_precision_and_explicit_scale(db):
    report = collect_report(db, now=NOW)
    row, = releases(report)
    row.update(official_value=1234567.89, unit="persons", scale=1000)
    assert "1234567.89 × 1000 persons" in render_html(report)


def test_geography_order_and_all_policy_banks():
    for bank in ("ecb_deposit_facility_rate_decision", "boj_policy_rate_decision",
                 "banxico_overnight_rate_decision", "bcb_selic_rate_decision"):
        assert release_group(dict(indicator_id=bank, geography="anything")) == GROUPS[0]
    geos = ("USA", "EA21", "DEU", "GBR", "JPN", "CHN_TIER1", "CAN", "MEX")
    assert [release_group(dict(indicator_id="statistic", geography=g)) for g in geos] == [
        GROUPS[i] for i in (1, 2, 2, 3, 4, 5, 6, 7)
    ]


def test_mirror_and_unverified_values_excluded(tmp_path):
    path = tmp_path / "nonofficial.sqlite"
    with open_writer(path) as store:
        persist(store, [replace(obs(), source="FRED"), obs(needs_review=True)])
    assert collect_report(path, now=NOW)["release_count"] == 0


def test_cli_sends_only_with_flag_and_uses_existing_helper(db, tmp_path, monkeypatch):
    sent = []
    monkeypatch.setitem(sys.modules, "send_telegram_run_report", SimpleNamespace(
        send_report_document=lambda *a, **kw: sent.append((a, kw))))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fixture-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "fixture-chat")
    monkeypatch.setenv("MACRO_RELEASES_DB", str(db))
    args = ["--out-dir", str(tmp_path)]
    assert cli.main(args) == 0 and sent == []
    assert cli.main([*args, "--telegram"]) == 0
    assert sent[0][0][0].suffix == ".html"
    assert sent[0][1]["token"] == "fixture-token"
    assert sent[0][1]["chat_id"] == "fixture-chat"
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    assert cli.main([*args, "--telegram"]) == 1
    assert len(sent) == 1


@pytest.mark.parametrize("hours", ["0", "-1", "nan", "inf", "invalid"])
def test_cli_invalid_window_is_error(db, tmp_path, hours):
    assert cli.main(["--db", str(db), "--out-dir", str(tmp_path), "--since-hours", hours]) == 1


def test_missing_db_errors_without_creating_database(tmp_path):
    path = tmp_path / "missing.sqlite"
    assert cli.main(["--db", str(path), "--out-dir", str(tmp_path)]) == 1
    assert not path.exists()


def test_deterministic_html_and_json(db, tmp_path):
    report = collect_report(db, now=NOW)
    assert report == collect_report(db, now=NOW)
    html_path, json_path = write_report(report, tmp_path)
    assert html_path.read_text(encoding="utf-8") == render_html(report)
    assert json.loads(json_path.read_text(encoding="utf-8")) == report


def test_release_indicator_card_class_description_and_json(db, tmp_path):
    report = collect_report(db, now=NOW)
    row, = releases(report)
    expected = indicator_card("cat:" + row["indicator_id"])
    assert expected is not None
    assert row["indicator_card"] == expected.model_dump(mode="json", by_alias=True)
    assert row["indicator_card_text"] == render_card(expected)
    assert report["indicator_card_count"] == 1
    assert report["missing_indicator_card_count"] == 0
    html = render_html(report)
    assert expected.timing.timing_class in html
    assert expected.rationale in html
    assert html.index("Official value:") < html.index('class="indicator-card"') < html.index("Previous:")
    _, json_path = write_report(report, tmp_path)
    saved = releases(json.loads(json_path.read_text(encoding="utf-8")))[0]["indicator_card"]
    assert saved["key"] == expected.key
    assert saved["timing"]["class"] == expected.timing.timing_class
    assert saved["rationale"] == expected.rationale


def test_missing_indicator_card_is_explicit_and_counted_per_release(db):
    assert indicator_card("cat:mx_core_cpi_yy") is None
    with open_writer(db) as store:
        persist(store, [
            obs("mx_core_cpi_yy", "mx_cpi", period=period, published=published, seen=seen)
            for period, published, seen in (
                ("2026-08", "2026-09-09T12:00:00Z", "2026-10-29T14:00:00Z"),
                ("2026-09", "2026-10-09T12:00:00Z", "2026-10-29T14:01:00Z"),
            )
        ])
    report = collect_report(db, now=NOW)
    missing = [r for r in releases(report) if r["indicator_id"] == "mx_core_cpi_yy"]
    assert len(missing) == 2
    assert all(r["indicator_card"] is None and r["indicator_card_text"] is None for r in missing)
    assert report["indicator_card_count"] == 1
    assert report["missing_indicator_card_count"] == 2
    assert report["missing_indicator_card_ids"] == ["mx_core_cpi_yy"]
    assert report["indicator_card_summary"] == "Indicator cards: 1 of 3; 2 no card yet."
    html = render_html(report)
    assert "Indicator cards: 1 of 3; 2 no card yet." in html
    assert html.count("<b>Indicator card:</b> no card yet") == 2


def test_indicator_card_text_and_key_are_html_escaped(db, monkeypatch):
    original = indicator_card("cat:ecb_deposit_facility_rate_decision")
    malicious = original.model_copy(update={
        "key": '<key "unsafe">', "concept": '<script>alert("card")</script>',
        "rationale": "Measures <rates> & policy.",
    })

    def lookup(identity):
        assert identity == "cat:ecb_deposit_facility_rate_decision"
        return malicious

    monkeypatch.setattr(report_module, "indicator_card", lookup)
    report = collect_report(db, now=NOW)
    assert releases(report)[0]["indicator_card"]["rationale"] == malicious.rationale
    html = render_html(report)
    assert html.count('<script>') == 1 and '<rates>' not in html
    assert '&lt;script&gt;alert(&quot;card&quot;)&lt;/script&gt;' in html
    assert '&lt;key &quot;unsafe&quot;&gt;' in html
    assert 'Measures &lt;rates&gt; &amp; policy.' in html


class FilterDOM(HTMLParser):
    """Read actual rendered items and scripts for the dependency-free Node DOM harness."""
    def __init__(self, html):
        super().__init__()
        self.items, self.buttons, self.groups, self.scripts = [], [], [], []
        self.group = self.item = self.button = None
        self.in_script = False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "data-release-group" in attrs:
            self.group = len(self.groups)
            self.groups.append(self.group)
        if "data-report-item" in attrs:
            self.item = dict(attrs=attrs, group=self.group, text="")
            self.items.append(self.item)
        if tag == "button" and "data-v" in attrs:
            self.button = dict(attrs=attrs, text="")
            self.buttons.append(self.button)
        if tag == "script":
            self.in_script = True
            self.scripts.append("")

    def handle_endtag(self, tag):
        if tag == "article":
            self.item = None
        if tag == "section":
            self.group = None
        if tag == "button":
            self.button = None
        if tag == "script":
            self.in_script = False

    def handle_data(self, data):
        if self.item is not None:
            self.item["text"] += data
        if self.button is not None:
            self.button["text"] += data
        if self.in_script:
            self.scripts[-1] += data


@pytest.mark.skipif(shutil.which("node") is None, reason="Node required to execute the inline tab JavaScript")
def test_four_tabs_filter_actual_rendered_items(db, monkeypatch):
    with open_writer(db) as store:
        persist(store, [
            obs("us_cpi_headline_yy", "us_cpi", period="2026-09"),
            obs("us_gdp_qq_saar", "us_gdp", period="2026-Q3"),
        ])
        log_outcome(store, monkeypatch, "tab-issue", "uk_gdp", "failed", "2026-10-29T15:00:00Z")
    monkeypatch.setattr(report_module, "INDICATORS", dict(
        INDICATORS, us_cpi_headline_yy=INDICATORS["us_cpi_headline_yy"].model_copy(update={"tier": "T2"})))
    report = collect_report(db, now=NOW)
    # The renderer must honour first-seen bounds independently of publication date.
    next(r for r in releases(report) if r["indicator_id"] == "us_gdp_qq_saar")["first_official_seen_at"] = report["until"]
    before = deepcopy(report)
    dom = FilterDOM(render_html(report))
    assert report == before  # styling/filtering never mutates the JSON payload
    assert [(b["attrs"]["data-v"], b["text"]) for b in dom.buttons] == [
        ("all", "All"), ("new", "New today"), ("tier1", "Tier 1"), ("error", "Errors"),
    ]
    assert len(dom.scripts) == 1
    harness = r'''
const fs = require('fs'), vm = require('vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
function element(record = {attrs: {}, text: ''}) {
  return {
    dataset: Object.fromEntries(Object.entries(record.attrs).filter(([k]) => k.startsWith('data-'))
      .map(([k,v]) => [k.slice(5).replace(/-([a-z])/g, (_,c) => c.toUpperCase()), v])),
    textContent: record.text, hidden: false, value: '', handlers: {}, attrs: record.attrs,
    classList: {toggle() {}},
    setAttribute(k,v) {this.attrs[k] = v;},
    addEventListener(k,fn) {this.handlers[k] = fn;}
  };
}
const items = input.items.map(element), buttons = input.buttons.map(element);
const groups = input.groups.map(n => ({hidden:false, querySelectorAll() {
  return items.filter((_,i) => input.items[i].group === n);
}}));
const ids = Object.fromEntries(['issues-section','search','filter-note','filter-empty'].map(id => [id,element()]));
const document = {
  getElementById(id) {return ids[id];},
  querySelectorAll(s) {return {'[data-report-item]':items,'[data-release-group]':groups,'#view-toggle button':buttons}[s];}
};
vm.runInNewContext(input.script, {document});
const result = {};
for (const button of buttons) {
  button.handlers.click();
  result[button.dataset.v] = {
    visible:items.filter(i => !i.hidden).map(i => i.dataset.indicatorId || i.dataset.family).sort(),
    groups:groups.filter(g => !g.hidden).length, issues:!ids['issues-section'].hidden,
    selected:buttons.filter(b => b.attrs['aria-selected'] === 'true').map(b => b.dataset.v)
  };
}
buttons[0].handlers.click();
ids.search.value = 'ECB'; ids.search.handlers.input();
result.search = items.filter(i => !i.hidden).map(i => i.dataset.indicatorId || i.dataset.family);
ids.search.value = 'no-such-indicator'; ids.search.handlers.input();
result.empty = !ids['filter-empty'].hidden;
process.stdout.write(JSON.stringify(result));
'''
    run = subprocess.run([shutil.which("node"), "-e", harness], input=json.dumps(dict(
        items=dom.items, buttons=dom.buttons, groups=dom.groups, script=dom.scripts[0],
    )), text=True, capture_output=True, check=True, timeout=15)
    results = json.loads(run.stdout)
    ecb, cpi, gdp, issue = "ecb_deposit_facility_rate_decision", "us_cpi_headline_yy", "us_gdp_qq_saar", "uk_gdp"
    assert results["all"]["visible"] == sorted([ecb, cpi, gdp, issue])
    assert results["new"]["visible"] == sorted([ecb, cpi])
    assert results["tier1"]["visible"] == sorted([ecb, gdp])
    assert results["error"]["visible"] == [issue]
    assert results["error"]["groups"] == 0 and results["error"]["issues"]
    assert not results["new"]["issues"] and not results["tier1"]["issues"]
    assert all(results[view]["selected"] == [view] for view in ("all", "new", "tier1", "error"))
    assert results["search"] == [ecb] and results["empty"]


@pytest.mark.parametrize("current,prior,unit,prior_unit,scale,prior_scale,expected", [
    (3.4, 3.8, "%", "%", 1, 1, 'class="release-change neg" title="Change versus previous">-0.4 pp'),
    (2.5, 2, "% per annum", "% per annum", 1, 1, 'class="release-change pos" title="Change versus previous">+0.5 pp'),
    (2.5, 2.5, "%", "percent", 1, 1, 'class="release-change" title="Change versus previous">0 pp'),
    (2500, 2, "persons", "persons", 1, 1000, 'class="release-change pos" title="Change versus previous">+500 persons'),
    (2.5, 2, "%", "persons", 1, 1, 'class="release-change">Change unavailable'),
])
def test_change_sign_colouring_and_units(db, current, prior, unit, prior_unit, scale, prior_scale, expected):
    report = collect_report(db, now=NOW)
    row, = releases(report)
    row.update(official_value=current, unit=unit, scale=scale,
               previous=dict(value=prior, unit=prior_unit, scale=prior_scale, reference_period="2026-09-10"))
    assert expected in render_html(report)
