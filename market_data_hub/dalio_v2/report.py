# -*- coding: utf-8 -*-
"""
report.py — self-contained HTML + optional CSV snapshot of engine_scores for
a given ref_date.

Deliberately simpler than make_dalio_report.py (dalio.py's dashboard): only
the engines implemented so far show up (Fase 1: sovereign_solvency,
political_execution — see docs/DALIO_5ENGINE_IMPLEMENTATION_PLAN_2026-07.md),
one row per country, one column per engine, sourced directly from
engine_scores rather than recomputing anything. Grows automatically as more
engines are added in later phases — no changes needed here.

Usage: see run_dalio_v2.py (repo root) for the CLI entry point.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

import duckdb
import pandas as pd

# (score < this value) -> css class; last bucket catches everything up to 100
_COLOR_BUCKETS = [(20, "s0"), (40, "s1"), (60, "s2"), (80, "s3"), (101, "s4")]


def _css_class(score: Optional[float]) -> str:
    if score is None or pd.isna(score):
        return "sna"
    for limit, cls in _COLOR_BUCKETS:
        if score < limit:
            return cls
    return "s4"


def _country_names(cfg_dir: Path) -> dict:
    try:
        import yaml
        countries = yaml.safe_load(
            (cfg_dir / "countries.yaml").read_text(encoding="utf-8"))["countries"]
        return {c["iso3"]: c.get("name", c["iso3"]) for c in countries}
    except Exception:
        return {}


def collect(con: duckdb.DuckDBPyConnection, ref_date,
           engines: Optional[Sequence[str]] = None) -> pd.DataFrame:
    """Long-format engine_scores for ref_date, one row per (country, engine),
    optionally filtered to a subset of engines."""
    q = ("SELECT country_iso3, engine, score, label, coverage_tier, confidence, "
        "n_components, n_expected, components_json, computed_at "
        "FROM engine_scores WHERE ref_date = ?")
    params = [ref_date]
    if engines:
        q += " AND engine IN (" + ",".join("?" * len(engines)) + ")"
        params += list(engines)
    return con.execute(q, params).fetch_df()


def to_csv(df: pd.DataFrame, out_path: Path) -> Path:
    """Wide CSV: one row per country, {engine}_{field} columns."""
    if df.empty:
        pd.DataFrame().to_csv(out_path)
        return out_path
    wide = df.pivot(index="country_iso3", columns="engine",
                    values=["score", "label", "coverage_tier", "confidence"])
    wide.columns = [f"{engine}_{field}" for field, engine in wide.columns]
    wide.sort_index().to_csv(out_path)
    return out_path


_HTML_TMPL = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Dalio v2 - engine scores ({ref_date})</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Arial, sans-serif; margin: 24px;
         background: #0e1117; color: #e6e6e6; }}
  h1 {{ font-size: 20px; margin-bottom: 4px; }}
  .meta {{ color: #9aa0a6; font-size: 13px; margin-bottom: 16px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  th, td {{ padding: 6px 10px; text-align: left; border-bottom: 1px solid #2a2f3a; }}
  th {{ position: sticky; top: 0; background: #161b22; }}
  tr:hover {{ background: #161b22; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; }}
  .s0 {{ background: #1e4620; color: #7ee787; }}
  .s1 {{ background: #2d4a1e; color: #b8e07e; }}
  .s2 {{ background: #4a3f1e; color: #e3c667; }}
  .s3 {{ background: #4a2f1e; color: #f0965f; }}
  .s4 {{ background: #4a1e1e; color: #ff7b7b; }}
  .sna {{ background: #2a2a2a; color: #888; }}
  .tier-proxy {{ opacity: 0.75; font-style: italic; }}
  .tier-insufficient {{ opacity: 0.4; }}
  caption {{ text-align: left; color: #9aa0a6; font-size: 12px; margin-bottom: 6px; }}
</style></head>
<body>
<h1>Dalio v2 &mdash; engine scores as of {ref_date}</h1>
<p class="meta">Generated {generated_at} UTC &middot; {n_countries} countries &middot;
   engines: {engines} &middot; model_version {model_version} &middot;
   <strong>not yet vintage-aware</strong> (live read &mdash; see
   docs/DALIO_VINTAGE_AND_AUDIT_PLAN_2026-07.md). Higher score = worse; badge
   opacity signals partial (proxy) or thin (insufficient) data coverage.</p>
<table>
<caption>Rows sorted by average risk across the engines shown (desc).</caption>
<thead><tr>{header_row}</tr></thead>
<tbody>{body_rows}</tbody>
</table>
</body></html>
"""


def generate_html_report(con: duckdb.DuckDBPyConnection, ref_date, out_dir: Path,
                         engines: Optional[Sequence[str]] = None,
                         cfg_dir: Optional[Path] = None) -> Path:
    df = collect(con, ref_date, engines)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"dalio_v2_{ref_date}.html"
    if df.empty:
        out_path.write_text(
            f"<p>No engine_scores rows for ref_date={ref_date}. "
            f"Run run_dalio_v2.py first.</p>", encoding="utf-8")
        return out_path

    names = _country_names(cfg_dir or Path(__file__).resolve().parents[1] / "config")
    engines_present = sorted(df["engine"].unique())
    model_version = "unknown"
    try:
        model_version = json.loads(df["components_json"].iloc[0]).get("model_version", "unknown")
    except Exception:
        pass

    pivot_score = df.pivot(index="country_iso3", columns="engine", values="score")
    pivot_label = df.pivot(index="country_iso3", columns="engine", values="label")
    pivot_tier = df.pivot(index="country_iso3", columns="engine", values="coverage_tier")
    avg_score = pivot_score.mean(axis=1, skipna=True).sort_values(ascending=False)

    header_row = "<th>Country</th>" + "".join(f"<th>{e}</th>" for e in engines_present)

    body_rows = []
    for iso3 in avg_score.index:
        cells = [f"<td>{names.get(iso3, iso3)} ({iso3})</td>"]
        for e in engines_present:
            score = pivot_score.loc[iso3, e] if e in pivot_score.columns else None
            label = pivot_label.loc[iso3, e] if e in pivot_label.columns else None
            tier = pivot_tier.loc[iso3, e] if e in pivot_tier.columns else None
            cls = _css_class(score)
            tier_cls = f"tier-{tier}" if isinstance(tier, str) else ""
            score_txt = "n/a" if score is None or pd.isna(score) else f"{score:.1f}"
            label_txt = "" if label is None or pd.isna(label) else str(label)
            tier_txt = tier if isinstance(tier, str) else "n/a"
            cells.append(
                f'<td><span class="badge {cls} {tier_cls}" title="coverage: {tier_txt}">'
                f'{score_txt} &middot; {label_txt}</span></td>')
        body_rows.append(f"<tr>{''.join(cells)}</tr>")

    html = _HTML_TMPL.format(
        ref_date=ref_date, generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        n_countries=len(avg_score), engines=", ".join(engines_present),
        model_version=model_version, header_row=header_row, body_rows="".join(body_rows))
    out_path.write_text(html, encoding="utf-8")
    return out_path
