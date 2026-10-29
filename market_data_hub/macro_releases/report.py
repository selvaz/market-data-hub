"""Deterministic daily issuer-release report. Local read API only; no downloads."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
import json
import math
from pathlib import Path
from urllib.parse import urlsplit

from market_data_hub.indicator_cards import indicator_card, render_card

from .catalogue import CENTRAL_BANKS, INDICATORS, LATAM_FAMILIES
from .models import utc
from .read_api import ReadAPI
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


def render_html(report):
    sections = []
    for group in report["groups"]:
        cards = []
        for row in group["releases"]:
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
            cards.append(
                f'<article><h3>{_text(row["indicator_name"])}</h3>'
                f'<p class="meta">{_text(row["geography"])} · {_text(row["indicator_id"])} · '
                f'{_text(row["source_class"])} issuer</p>'
                f'<p><b>Reference period:</b> {_text(row["reference_period"])} · {_text(row["stage"])}</p>'
                f'<p><b>Official value:</b> {_text(_value(row["official_value"], row["unit"], row["scale"]))}</p>'
                f'{_indicator_card_html(row)}'
                f'<p><b>Previous:</b> {_text(previous_text)}</p>{forecasts}'
                f'<p><b>Published:</b> {_text(publication)} · <b>Issuer:</b> {_text(row["issuer"])}</p>'
                f'<p>{_document_link(row["document_url"])}</p>'
                f'<p class="meta">First seen: {_text(row["first_official_seen_at"])}</p></article>'
            )
        sections.append(f'<h2>{_text(group["name"])}</h2>' + "".join(cards))
    issues = "".join(
        f'<li><b>{_text(r["family"])}</b> — {_text(r["status"])}: '
        f'{_text(r["error_type"] or "")}: {_text(r["error_message"] or "No reason recorded")}'
        f' <span class="meta">({_text(r["finished_at"])})</span></li>'
        for r in report["collection_issues"]
    )
    return f'''<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_text(report["title"])}</title>
<style>
body{{font-family:-apple-system,Segoe UI,Arial,sans-serif;color:#1a1a2e;max-width:820px;margin:0 auto;padding:18px;font-size:14px}}
h1{{font-size:20px;border-bottom:3px solid #1d4ed8;padding-bottom:8px}}
h2{{font-size:16px;margin-top:24px;color:#1d4ed8;border-bottom:1px solid #e0e0e0;padding-bottom:6px}}
h3{{font-size:15px;margin:0 0 8px}} p{{margin:7px 0;overflow-wrap:anywhere}}
article{{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:14px;margin:12px 0}}
.indicator-card{{background:#fff;border-left:3px solid #1d4ed8;padding:6px 10px;margin:8px 0;font-size:12px;line-height:1.5}}
.meta{{color:#666;font-size:12px}} .summary{{background:#eff6ff;padding:12px;border-radius:8px}}
li{{margin:8px 0;overflow-wrap:anywhere}} a{{color:#1d4ed8}}
</style></head><body>
<h1>{_text(report["title"])}</h1>
<p class="meta">Generated: {_text(report["generated_at"])}</p>
<p>First seen window (UTC): [{_text(report["since"])}, {_text(report["until"])})</p>
<p class="summary">{_text(report["message"])}</p>
<p class="summary">{_text(report["indicator_card_summary"])}</p>
{"".join(sections)}
<h2>Needs review / failed</h2>
{f"<ul>{issues}</ul>" if issues else "<p>No collection issues in the latest family outcomes.</p>"}
<p class="meta">Collection outcomes describe attempted fetches, not proof that a release was due.
No-change outcomes are healthy. Missing values are never inferred.</p>
<details><summary>Read API limitations</summary><ul>{"".join(f"<li>{_text(x)}</li>" for x in report["limitations"])}</ul></details>
<p class="meta">market_data_hub · automatic report · official macro_releases store</p>
</body></html>
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
