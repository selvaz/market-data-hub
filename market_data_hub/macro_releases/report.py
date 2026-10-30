"""Deterministic daily issuer-release report. Local read API only; no downloads."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from html import escape
import json
import math
from pathlib import Path
from urllib.parse import urlsplit

from market_data_hub.indicator_cards import indicator_card, render_card

from .catalogue import CENTRAL_BANKS, INDICATORS, LATAM_FAMILIES
from .models import utc
from .read_api import ReadAPI
from .report_style import REPORT_CSS, REPORT_SCRIPT
from .resolve import canonical_unit
from .store import open_reader

TITLE = "market_data_hub — Daily macro release report"
EMPTY_MESSAGE = "No official releases were first seen in this window."
GROUPS = ("Central banks", "USA", "Euro area and its countries", "UK", "Japan", "China", "Canada", "Others")
EURO_COUNTRIES = {
    "AUT", "BEL", "BGR", "CYP", "DEU", "ESP", "EST", "FIN", "FRA", "GRC", "HRV",
    "IRL", "ITA", "LTU", "LUX", "LVA", "MLT", "NLD", "PRT", "SVK", "SVN",
}
POLICY_INDICATORS = {
    i.id for measures in {**CENTRAL_BANKS, **LATAM_FAMILIES}.values()
    for i in measures if i.frequency == "E"
}


def release_group(card):
    if card["indicator_id"] in POLICY_INDICATORS:
        return GROUPS[0]
    geo = card["geography"]
    if geo in EURO_COUNTRIES or geo == "EMU" or geo.startswith("EA"):
        return GROUPS[2]
    return {"USA": "USA", "GBR": "UK", "JPN": "Japan", "CAN": "Canada"}.get(
        geo, "China" if geo == "CHN" or geo.startswith("CHN_") else "Others"
    )


def collect_report(db_path, *, since_hours=24, now=None):
    """A half-open UTC first-seen window ending at the single run timestamp."""
    if not math.isfinite(since_hours) or since_hours <= 0:
        raise ValueError("since-hours must be a positive finite number")
    end = utc((now or datetime.now(timezone.utc)).isoformat())
    start = utc((datetime.fromisoformat(end) - timedelta(hours=since_hours)).isoformat())
    with open_reader(db_path) as store:
        result = ReadAPI(store).first_seen_release_cards(start, end, as_known_at=end)
    groups = {label: [] for label in GROUPS}
    for card in result["cards"]:
        resolution = card["resolution"]
        selected = next(
            p for p in resolution["provenance"]
            if p["id"] == resolution["source"]["selected_evidence_id"]
        )
        document_url = selected["url"] or next((
            p["url"] for p in card["provenance"]
            if p["url"] and p["role"] == "issuer"
            and p["source_id"] == selected["source_id"]
            and p["series_key"] == selected["series_key"]
            and p["content_hash"] == selected["content_hash"]
        ), None)
        info = INDICATORS.get(card["indicator_id"])
        context = indicator_card(f'cat:{card["indicator_id"]}')
        forecasts = [
            dict(label="FF forecast", kind="ff_forecast", surprise_eligible=False,
                 value=f["normalized_value"], unit=resolution["unit"], scale=resolution["scale"],
                 provider=f["provider"], captured_at=f["captured_at"])
            for f in card["consensus"] if f["kind"] == "ff_forecast"
        ]
        release = dict(
            indicator_id=card["indicator_id"],
            indicator_name=info.definition if info else card["indicator_name"],
            geography=card["geography"], family=card["family"],
            reference_period=card["reference_period"], stage=card["stage"],
            official_value=resolution["value"], unit=resolution["unit"], scale=resolution["scale"],
            source_class=card["source_class"], status=resolution["status"],
            previous=card["previous"], previous_missing_reason=card["previous_missing_reason"],
            published_at=card["released_at"], publication_precision=card["publication_precision"],
            time_basis=card["time_basis"], first_official_seen_at=card["first_official_seen_at"],
            issuer=card["issuer_id"], document_url=document_url, ff_forecasts=forecasts,
            indicator_card=context.model_dump(mode="json", by_alias=True) if context else None,
            indicator_card_text=render_card(context) if context else None,
        )
        groups[release_group(card)].append(release)
    count = sum(map(len, groups.values()))
    missing_cards = [r["indicator_id"] for rows in groups.values() for r in rows
                     if r["indicator_card"] is None]
    card_count = count - len(missing_cards)
    return dict(
        title=TITLE, generated_at=end, since=start, until=end,
        window_basis=result["window_basis"], window_bounds="[since, until)",
        release_count=count, message=f"{count} release(s) first seen in this window." if count else EMPTY_MESSAGE,
        indicator_card_count=card_count, missing_indicator_card_count=len(missing_cards),
        missing_indicator_card_ids=sorted(set(missing_cards)),
        indicator_card_summary=f"Indicator cards: {card_count} of {count}; {len(missing_cards)} no card yet.",
        groups=[dict(name=label, releases=rows) for label, rows in groups.items() if rows],
        collection_issues=result["collection_issues"], limitations=result["limitations"],
    )


def _text(value):
    return escape(str(value), quote=True)


def _value(value, unit, scale=1):
    # Values retain their explicitly stored scale (e.g. thousands of persons).
    suffix = f" × {scale:g}" if scale != 1 else ""
    number = str(int(value)) if float(value).is_integer() else str(value)
    return f"{number}{suffix} {unit}"


def _document_link(url):
    if url and urlsplit(url).scheme.lower() in {"http", "https"}:
        return f'<a href="{_text(url)}">Issuer document</a>'
    return "Issuer document unavailable"


def _indicator_card_html(row):
    card = row["indicator_card"]
    if card is None:
        return '<div class="indicator-card"><p><b>Indicator card:</b> no card yet</p></div>'
    return (
        '<div class="indicator-card"><p><b>Indicator card</b> '
        f'<span class="meta">{_text(card["key"])}</span></p>'
        f'<p>{_text(row["indicator_card_text"])}</p></div>'
    )


def _change_html(row):
    """Colour numerical direction only, comparing explicitly compatible units."""
    previous = row["previous"]
    if not previous or canonical_unit(previous["unit"]) != canonical_unit(row["unit"]):
        return '<span class="release-change">Change unavailable</span>'
    prior = Decimal(str(previous["value"])) * Decimal(str(previous["scale"])) / Decimal(str(row["scale"]))
    change = Decimal(str(row["official_value"])) - prior
    direction = " pos" if change > 0 else " neg" if change < 0 else ""
    number = format(change.normalize(), "+f") if change else "0"
    unit = canonical_unit(row["unit"])
    label = f"{number} pp" if unit == "%" or unit.startswith("% ") else (
        number + (f' × {row["scale"]:g}' if row["scale"] != 1 else "") + " " + unit
    )
    return f'<span class="release-change{direction}" title="Change versus previous">{_text(label)}</span>'


def _release_html(row, report):
    # Tier is display metadata, deliberately not added to the JSON contract.
    info = INDICATORS.get(row["indicator_id"])
    tier = info.tier if info else None
    tier_number = tier[1:] if tier else ""
    badge_class = "tier1" if tier == "T1" else "tier2" if tier else "unknown"
    badge_label = f"Tier {tier_number}" if tier else "Tier unclassified"
    first_seen = utc(row["first_official_seen_at"])
    is_new = str(utc(report["since"]) <= first_seen < utc(report["until"])).lower()
    previous = row["previous"]
    previous_text = (
        _value(previous["value"], previous["unit"], previous["scale"])
        + f' ({previous["reference_period"]})'
        if previous else f'Unavailable — {row["previous_missing_reason"]}'
    )
    forecasts = "".join(
        f'<p><b>FF forecast:</b> {_text(_value(f["value"], f["unit"], f["scale"]))}</p>'
        for f in row["ff_forecasts"]
    )
    publication = row["published_at"] if row["publication_precision"] == "exact" else (
        row["published_at"][:10] + " (date only)"
    )
    return (
        f'<article class="release-card" data-report-item data-kind="release" data-new="{is_new}" '
        f'data-tier="{_text(tier_number)}" data-indicator-id="{_text(row["indicator_id"])}">'
        '<div class="release-top"><div>'
        f'<h3 class="release-name">{_text(row["indicator_name"])}</h3>'
        f'<p class="release-agency">{_text(row["issuer"])} · {_text(row["geography"])} · '
        f'{_text(row["source_class"])} issuer</p></div>'
        f'<span class="badge {badge_class}">{badge_label}</span></div>'
        '<div><span class="figure-label">Official value:</span><div class="release-figures">'
        f'<span class="release-value">{_text(_value(row["official_value"], row["unit"], row["scale"]))}</span>'
        f'{_change_html(row)}</div></div>'
        f'<p class="release-period"><b>Reference period:</b> {_text(row["reference_period"])} · {_text(row["stage"])}</p>'
        f'{_indicator_card_html(row)}'
        f'<div class="release-details"><p><b>Previous:</b> {_text(previous_text)}</p>{forecasts}'
        f'<p><b>Published:</b> {_text(publication)} · <b>Issuer:</b> {_text(row["issuer"])}</p></div>'
        f'<div class="commentary"><p>{_document_link(row["document_url"])}</p>'
        f'<p class="meta">{_text(row["indicator_id"])}</p>'
        f'<p class="meta">First seen: {_text(row["first_official_seen_at"])}</p></div></article>'
    )


def render_html(report):
    """Self-contained legacy monitor styling; input and JSON remain untouched."""
    sections = "".join(
        f'<section data-release-group><h2>{_text(group["name"])}</h2><div class="release-grid">'
        + "".join(_release_html(row, report) for row in group["releases"]) + "</div></section>"
        for group in report["groups"]
    )
    issues = "".join(
        f'<article class="status-row error" data-report-item data-kind="issue" data-family="{_text(r["family"])}">'
        f'<div class="status-name"><span class="n">{_text(r["family"])}</span>'
        f'<span class="a">{_text(r["status"])}</span></div><p class="status-reason">'
        f'{_text(r["error_type"] + ": " if r["error_type"] else "")}{_text(r["error_message"] or "No reason recorded")}</p>'
        f'<p class="meta mono">{_text(r["finished_at"])}</p></article>'
        for r in report["collection_issues"]
    )
    return f'''<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_text(report["title"])}</title><style>{REPORT_CSS}</style></head><body>
<header><div><p class="eyebrow">official sources only &middot; scheduled artifact</p>
<h1>Economic Release Monitor</h1><p class="subtitle">Daily macro release report · all geographies</p></div>
<div class="meta-grid mono">
<div class="meta-item"><span class="k">As of · UTC</span><span class="v">{_text(report["generated_at"][:10])}</span></div>
<div class="meta-item"><span class="k">Releases</span><span class="v">{_text(report["release_count"])}</span></div>
<div class="meta-item"><span class="k">Cards</span><span class="v">{_text(report["indicator_card_count"])}/{_text(report["release_count"])}</span></div>
<div class="meta-item"><span class="k">Collection issues</span><span class="v">{len(report["collection_issues"])}</span></div>
</div></header>
<p class="summary">{_text(report["message"])}</p>
<p class="summary">{_text(report["indicator_card_summary"])}</p>
<p class="window mono">First seen window (UTC): [{_text(report["since"])}, {_text(report["until"])})</p>
<div class="filter-bar"><input type="search" id="search" aria-label="Filter by name or agency" placeholder="Filter by name or agency...">
<div class="toggle-group" id="view-toggle" role="tablist" aria-label="Report filters">
<button type="button" role="tab" data-v="all" class="active" aria-selected="true" aria-controls="report-content">All</button>
<button type="button" role="tab" data-v="new" aria-selected="false" aria-controls="report-content">New today</button>
<button type="button" role="tab" data-v="tier1" aria-selected="false" aria-controls="report-content">Tier 1</button>
<button type="button" role="tab" data-v="error" aria-selected="false" aria-controls="report-content">Errors</button>
</div></div>
<p class="filter-note" id="filter-note" role="status">All releases in the window, with latest collection issues.</p>
<main id="report-content">{sections}
<section id="issues-section"><h2>Needs review / failed</h2>
{f'<div class="status-grid">{issues}</div>' if issues else '<p class="empty-note">No collection issues in the latest family outcomes.</p>'}
<p class="meta">Collection outcomes describe attempted fetches, not proof that a release was due.
No-change outcomes are healthy. Missing values are never inferred.</p></section>
<p class="empty-note" id="filter-empty" hidden>No items match this filter.</p></main>
<details class="limitations"><summary>Read API limitations</summary><ul>{"".join(f"<li>{_text(x)}</li>" for x in report["limitations"])}</ul></details>
<footer><span><b>Produced by</b> market_data_hub</span><span><b>Cadence</b> daily</span>
<span><b>Sources</b> official macro_releases store</span><span><b>Generated</b> {_text(report["generated_at"])}</span>
<span>{_text(report["title"])}</span></footer>
<script>{REPORT_SCRIPT}</script></body></html>
'''


def write_report(report, out_dir):
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(report["generated_at"]).strftime("%Y%m%d_%H%M%S_%f")
    html_path = directory / f"macro_release_report_{stamp}.html"
    json_path = html_path.with_suffix(".json")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    html_path.write_text(render_html(report), encoding="utf-8")
    return html_path, json_path
