"""Neutral country-data dashboard generated from ``market_data_hub`` only.

The dashboard deliberately reports facts, provenance and cross-country raw
percentiles. It does not contain investment scores, cycle labels, or any
downstream analytical interpretation.
"""
from __future__ import annotations

import html
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from market_data_hub.config_loader import get_countries, get_settings
from market_data_hub.db.connection import get_conn


ROOT = Path(__file__).resolve().parents[1]
MAX_TAG_AGE_YEARS = 2

CHART_INDICATORS = [
    ("gdp_growth_weo", "Real GDP growth", "%"),
    ("inflation_avg_weo", "CPI inflation", "%"),
    ("public_debt_gdp", "Public debt", "% GDP"),
    ("fiscal_balance_gdp", "Fiscal balance", "% GDP"),
    ("current_account_gdp", "Current account", "% GDP"),
    ("fx_reserves_months_imports", "FX reserves", "months of imports"),
    ("bis_policy_rate", "Policy rate", "%"),
    ("bis_credit_gap", "Credit-to-GDP gap", "pp"),
    ("reer_broad", "Real effective exchange rate", "index"),
]

PERCENTILE_INDICATORS = [
    ("public_debt_gdp", "Public debt", "% GDP"),
    ("inflation_avg_weo", "CPI inflation", "%"),
    ("fiscal_balance_gdp", "Fiscal balance", "% GDP"),
    ("current_account_gdp", "Current account", "% GDP"),
]

_ENERGY_INPUTS = (
    "fuel_exports_share",
    "fuel_imports_share",
    "exports_gdp",
    "imports_gdp",
)


def _report_dir() -> Path:
    cfg = get_settings().get("reports", {})
    path = Path(cfg.get("dir") or "reports")
    return path if path.is_absolute() else ROOT / path


def _scalar(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return value


def _year(value: Any) -> Optional[int]:
    if value is None or pd.isna(value):
        return None
    return int(pd.Timestamp(value).year)


def _label_fx(country: dict[str, Any]) -> str:
    if country.get("euro"):
        return "Currency: EUR"
    regime = str(country.get("fx_regime") or "unknown").replace("_", " ")
    return f"FX regime: {regime}"


def _fuel_trade(values: dict[str, dict[str, Any]], reference_year: int) -> dict[str, Any]:
    inputs = [values.get(key, {}) for key in _ENERGY_INPUTS]
    raw = [item.get("value") for item in inputs]
    years = [_year(item.get("date")) for item in inputs]
    if any(value is None for value in raw) or any(year is None for year in years):
        return {"status": "unavailable", "label": "Fuel trade: unavailable", "as_of": None,
                "value": None}

    as_of = min(years)
    if reference_year - as_of > MAX_TAG_AGE_YEARS:
        return {"status": "stale", "label": f"Fuel trade: stale ({as_of})", "as_of": as_of,
                "value": None}

    fuel_exports, fuel_imports, exports_gdp, imports_gdp = raw
    net = fuel_exports * exports_gdp / 100.0 - fuel_imports * imports_gdp / 100.0
    if net <= -10:
        position = "strong importer"
    elif net < -3:
        position = "importer"
    elif net < 3:
        position = "balanced"
    elif net < 10:
        position = "exporter"
    else:
        position = "strong exporter"
    return {"status": "fresh", "label": f"Net fuel trade: {position}", "as_of": as_of,
            "value": round(net, 2)}


def _latest_values(con, indicator_ids: list[str], reference_date: date) -> dict[str, dict[str, dict[str, Any]]]:
    if not indicator_ids:
        return {}
    placeholders = ",".join("?" * len(indicator_ids))
    query = f"""
        SELECT country_iso3, indicator_id, date, value, indicator_name, unit,
               source, provider_dataset
        FROM macro_panel
        WHERE indicator_id IN ({placeholders})
          AND date <= ?
          AND value IS NOT NULL
        QUALIFY row_number() OVER (
            PARTITION BY country_iso3, indicator_id ORDER BY date DESC
        ) = 1
    """
    df = con.execute(query, [*indicator_ids, reference_date]).fetch_df()
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for _, row in df.iterrows():
        out.setdefault(str(row["country_iso3"]), {})[str(row["indicator_id"])] = {
            key: _scalar(row[key]) for key in row.index
        }
    return out


def _country_data_status(con, reference_date: date) -> dict[str, dict[str, Any]]:
    df = con.execute(
        """
        SELECT country_iso3,
               max(date) FILTER (WHERE date <= ?) AS latest_period,
               max(date) FILTER (WHERE provider_dataset = 'WEO' AND date > ?) AS forecast_to,
               max(updated_at) AS ingested_at,
               count(DISTINCT indicator_id) AS indicator_count
        FROM macro_panel
        WHERE value IS NOT NULL
        GROUP BY country_iso3
        """,
        [reference_date, reference_date],
    ).fetch_df()
    return {str(r["country_iso3"]): {key: _scalar(r[key]) for key in r.index if key != "country_iso3"}
            for _, r in df.iterrows()}


def _series(con, indicator_ids: list[str]) -> tuple[dict[str, dict[str, list[dict[str, Any]]]], dict[str, dict[str, str]]]:
    placeholders = ",".join("?" * len(indicator_ids))
    query = f"""
        SELECT country_iso3, indicator_id, year(date) AS year, value, unit,
               indicator_name, provider_dataset
        FROM macro_panel
        WHERE indicator_id IN ({placeholders}) AND value IS NOT NULL
        ORDER BY country_iso3, indicator_id, date
    """
    df = con.execute(query, indicator_ids).fetch_df()
    # Keep the latest point for each annual bucket in pandas. The equivalent
    # DuckDB window query can allocate disproportionately on a full backfill.
    if not df.empty:
        df = df.drop_duplicates(["country_iso3", "indicator_id", "year"], keep="last")
    series: dict[str, dict[str, list[dict[str, Any]]]] = {}
    metadata: dict[str, dict[str, str]] = {}
    for _, row in df.iterrows():
        iso, iid = str(row["country_iso3"]), str(row["indicator_id"])
        series.setdefault(iso, {}).setdefault(iid, []).append({
            "year": int(row["year"]),
            "value": round(float(row["value"]), 4),
            "dataset": str(row["provider_dataset"] or ""),
        })
        metadata[iid] = {
            "name": str(row["indicator_name"] or iid),
            "unit": str(row["unit"] or ""),
        }
    return series, metadata


def _percentiles(con, indicator_specs: list[tuple[str, str, str]], completed_year: int) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for indicator_id, label, unit in indicator_specs:
        period = con.execute(
            """
            SELECT max(date) FROM macro_panel
            WHERE indicator_id = ? AND value IS NOT NULL AND date <= ?
            """,
            [indicator_id, date(completed_year, 12, 31)],
        ).fetchone()[0]
        if period is None:
            continue
        df = con.execute(
            """
            SELECT country_iso3, value FROM macro_panel
            WHERE indicator_id = ? AND date = ? AND value IS NOT NULL
            ORDER BY value, country_iso3
            """,
            [indicator_id, period],
        ).fetch_df()
        count = len(df)
        if not count:
            continue
        by_country = {}
        for rank, (_, row) in enumerate(df.iterrows()):
            percentile = 50 if count == 1 else round(100 * rank / (count - 1))
            by_country[str(row["country_iso3"])] = {
                "value": round(float(row["value"]), 3), "percentile": percentile,
            }
        result[indicator_id] = {
            "label": label, "unit": unit, "year": _year(period), "n": count,
            "countries": by_country,
        }
    return result


def collect_dashboard(con, *, now: Optional[datetime] = None) -> dict[str, Any]:
    """Collect neutral reference facts and macro data for the HTML dashboard."""
    now = now or datetime.now(timezone.utc)
    reference_date = now.date()
    reference_year = now.year
    completed_year = reference_year - 1
    chart_ids = [item[0] for item in CHART_INDICATORS]
    latest = _latest_values(con, list(_ENERGY_INPUTS), reference_date)
    status = _country_data_status(con, reference_date)
    series, metadata = _series(con, chart_ids)
    percentiles = _percentiles(con, PERCENTILE_INDICATORS, completed_year)

    countries: dict[str, dict[str, Any]] = {}
    for country in get_countries():
        iso = str(country["iso3"])
        fuel = _fuel_trade(latest.get(iso, {}), reference_year)
        tags = [
            f"Market: {country.get('development', 'unknown')}",
            str(country.get("income") or "Income: unknown"),
            _label_fx(country),
        ]
        if country.get("g7"):
            tags.append("G7")
        if country.get("eu"):
            tags.append("EU")
        if country.get("euro"):
            tags.append("Euro area")
        if country.get("imf_program"):
            program = str(country.get("imf_program_type") or "program")
            tags.append(f"IMF arrangement: {program}")
        if fuel["status"] == "fresh":
            tags.append(fuel["label"])

        countries[iso] = {
            "iso": iso,
            "name": str(country.get("name") or iso),
            "region": str(country.get("region_group") or "Unknown"),
            "region_geo": str(country.get("region_geo") or "Unknown"),
            "income": str(country.get("income") or "Unknown"),
            "market": str(country.get("development") or "Unknown"),
            "currency": "EUR" if country.get("euro") else None,
            "fx_regime": str(country.get("fx_regime") or "unknown").replace("_", " "),
            "imf_arrangement": str(country.get("imf_program_type") or "") if country.get("imf_program") else None,
            "tags": tags,
            "fuel": fuel,
            "status": status.get(iso, {"latest_period": None, "forecast_to": None,
                                        "ingested_at": None, "indicator_count": 0}),
            "series": series.get(iso, {}),
            "percentiles": {
                indicator_id: {key: value for key, value in details.items() if key != "countries"}
                | {"country": details["countries"].get(iso)}
                for indicator_id, details in percentiles.items()
                if details["countries"].get(iso) is not None
            },
        }

    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M UTC"),
        "reference_year": reference_year,
        "completed_year": completed_year,
        "countries": countries,
        "chart_indicators": [{"id": iid, "label": label, "unit": unit} for iid, label, unit in CHART_INDICATORS],
        "indicator_metadata": metadata,
    }


def _template(data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=True, separators=(",", ":"))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Country data dashboard</title>
<style>
:root{{--ink:#172033;--muted:#667085;--line:#d9e0ea;--paper:#fff;--bg:#f5f7fa;--blue:#155eef;--green:#067647;--amber:#b54708;--red:#b42318}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:Segoe UI,Arial,sans-serif;font-size:14px}}header{{background:#102a56;color:white;padding:18px 24px}}header h1{{margin:0;font-size:21px;font-weight:650}}header p{{margin:5px 0 0;color:#d0ddf5;font-size:12px}}nav{{background:#173b76;display:flex;padding:0 16px}}nav button{{border:0;background:transparent;color:#d0ddf5;padding:11px 15px;cursor:pointer;font-size:13px;border-bottom:3px solid transparent}}nav button.active{{color:#fff;border-bottom-color:#84adff}}main{{max-width:1380px;margin:auto;padding:22px}}.tab{{display:none}}.tab.active{{display:block}}h2{{font-size:16px;margin:0 0 14px}}.toolbar{{display:flex;gap:12px;align-items:center;margin-bottom:12px;flex-wrap:wrap}}input,select{{font:inherit;border:1px solid var(--line);background:#fff;padding:8px 10px;border-radius:4px}}input{{min-width:260px}}.meta{{font-size:12px;color:var(--muted)}}table{{border-collapse:collapse;width:100%;background:var(--paper);font-size:12px}}th{{background:#edf2ff;color:#344054;text-align:left;padding:8px;border-bottom:1px solid var(--line);position:sticky;top:0}}td{{padding:7px 8px;border-bottom:1px solid #edf0f4;vertical-align:top}}tr.data-row:hover td{{background:#f7faff;cursor:pointer}}.table-wrap{{max-height:70vh;overflow:auto;border:1px solid var(--line)}}.tag{{display:inline-block;background:#eaf0ff;color:#1d3f8f;border-radius:3px;padding:3px 6px;font-size:11px;margin:0 4px 4px 0}}.tag.warn{{background:#fff1e8;color:var(--amber)}}.tag.ok{{background:#eaf8f0;color:var(--green)}}.section{{margin:20px 0}}.country-head{{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;flex-wrap:wrap;border-bottom:1px solid var(--line);padding-bottom:12px}}.country-head h2{{margin:0}}.facts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:10px;margin:16px 0}}.fact{{border-left:3px solid #84adff;background:#fff;padding:9px 10px;min-height:58px}}.fact .label{{display:block;font-size:11px;color:var(--muted)}}.fact .value{{display:block;font-size:16px;font-weight:650;margin-top:3px}}.pctl{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px}}.pctl-row{{background:#fff;border:1px solid var(--line);padding:9px}}.pctl-head{{display:flex;justify-content:space-between;font-size:12px;margin-bottom:7px}}.bar{{height:7px;background:#e7ecf5;position:relative}}.bar i{{display:block;height:100%;background:var(--blue)}}.charts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:14px}}.chart{{background:#fff;border:1px solid var(--line);padding:10px;min-height:220px}}.chart h3{{font-size:13px;margin:0 0 3px}}.chart small{{color:var(--muted)}}.chart svg{{width:100%;height:178px;display:block;margin-top:4px}}.quality{{background:#fff7ed;border-left:3px solid #f79009;padding:10px;font-size:12px}}.sources{{font-size:12px;color:var(--muted);line-height:1.6;max-width:760px}}@media(max-width:680px){{main{{padding:14px}}.table-wrap{{max-height:none}}table{{min-width:900px}}input{{min-width:100%}}}}
</style></head><body><header><h1>Country data dashboard</h1><p id="generated"></p></header>
<nav><button class="active" onclick="tab('overview',this)">Countries</button><button onclick="tab('country',this)">Country sheet</button><button onclick="tab('data',this)">Data notes</button></nav>
<main><section id="overview" class="tab active"><div class="toolbar"><input id="search" oninput="renderTable()" placeholder="Search country, region, market or tag"><span class="meta" id="country-count"></span></div><div class="table-wrap"><table><thead><tr><th>Country</th><th>Market</th><th>Income</th><th>Currency / FX</th><th>IMF arrangement</th><th>Net fuel trade</th><th>Latest source period</th><th>WEO horizon</th></tr></thead><tbody id="country-table"></tbody></table></div></section>
<section id="country" class="tab"><div class="toolbar"><select id="country-select" onchange="showCountry(this.value)"></select></div><div id="country-sheet"></div></section>
<section id="data" class="tab"><h2>Data notes</h2><div class="sources"><p>Country profile tags use the configured country reference file. Currency, EU/euro-area, G7 and IMF-arrangement fields are displayed as reference facts from that file.</p><p>Net fuel trade is a simple mechanical exposure: fuel exports share x exports/GDP minus fuel imports share x imports/GDP. It is shown only when all four inputs are no more than two calendar years old; otherwise it is marked unavailable or stale.</p><p>Percentile bars are raw cross-country percentiles for the latest completed annual period with data. They are descriptive comparisons only and do not imply a risk, policy, cycle or investment conclusion.</p><p>Historical lines use the hub's macro panel. Dashed line segments indicate WEO observations beyond the last completed calendar year.</p></div></section></main>
<script>const DATA={payload};const countries=Object.values(DATA.countries).sort((a,b)=>a.name.localeCompare(b.name));document.getElementById('generated').textContent='Generated '+DATA.generated_at+' | '+countries.length+' country profiles';
function esc(v){{return String(v??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));}}function tab(id,b){{document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));document.querySelectorAll('nav button').forEach(x=>x.classList.remove('active'));document.getElementById(id).classList.add('active');b.classList.add('active');}}
function fuel(c){{const f=c.fuel;if(f.status==='fresh')return esc(f.label)+' ('+Number(f.value).toFixed(1)+'% GDP, '+f.as_of+')';return '<span class="tag warn">'+esc(f.label)+'</span>';}}function latest(c){{return c.status.latest_period||'n/a';}}function renderTable(){{const q=document.getElementById('search').value.toLowerCase();const rows=countries.filter(c=>(c.name+' '+c.region+' '+c.market+' '+c.tags.join(' ')).toLowerCase().includes(q));document.getElementById('country-count').textContent=rows.length+' countries';document.getElementById('country-table').innerHTML=rows.map(c=>'<tr class="data-row" onclick="openCountry(\''+c.iso+'\')"><td><b>'+esc(c.name)+'</b><br><span class="meta">'+c.iso+' · '+esc(c.region)+'</span></td><td>'+esc(c.market)+'</td><td>'+esc(c.income)+'</td><td>'+esc(c.currency||c.fx_regime)+'</td><td>'+esc(c.imf_arrangement||'—')+'</td><td>'+fuel(c)+'</td><td>'+latest(c)+'</td><td>'+((c.status.forecast_to)||'—')+'</td></tr>').join('');}}
function openCountry(iso){{document.getElementById('country-select').value=iso;showCountry(iso);const b=[...document.querySelectorAll('nav button')][1];tab('country',b);}}function fmt(v){{return v===null||v===undefined?'—':Number(v).toLocaleString(undefined,{{maximumFractionDigits:1}});}}
function chart(points){{if(!points||points.length<2)return '<svg viewBox="0 0 500 180"><text x="12" y="90" fill="#667085">No comparable series</text></svg>';const W=500,H=180,L=40,R=12,T=10,B=26;const xs=points.map(p=>p.year),ys=points.map(p=>p.value);let lo=Math.min(...ys),hi=Math.max(...ys);if(lo===hi){{lo-=1;hi+=1;}}const pad=(hi-lo)*.1;lo-=pad;hi+=pad;const sx=x=>L+(x-Math.min(...xs))/(Math.max(...xs)-Math.min(...xs)||1)*(W-L-R),sy=y=>T+(1-(y-lo)/(hi-lo))*(H-T-B);let actual=[],forecast=[];points.forEach((p,i)=>{{const cmd=i?'L':'M';const piece=cmd+sx(p.year).toFixed(1)+' '+sy(p.value).toFixed(1);if(p.dataset==='WEO'&&p.year>DATA.completed_year)forecast.push(piece);else actual.push(piece);}});const all=points.map((p,i)=>(i?'L':'M')+sx(p.year).toFixed(1)+' '+sy(p.value).toFixed(1)).join(' ');const firstForecast=points.find(p=>p.dataset==='WEO'&&p.year>DATA.completed_year);let dashed='';if(firstForecast){{const idx=points.indexOf(firstForecast);if(idx>0)dashed='M'+sx(points[idx-1].year).toFixed(1)+' '+sy(points[idx-1].value).toFixed(1)+' '+forecast.join(' ');}}return '<svg viewBox="0 0 '+W+' '+H+'"><line x1="'+L+'" y1="'+(H-B)+'" x2="'+(W-R)+'" y2="'+(H-B)+'" stroke="#d9e0ea"/><path d="'+all+'" fill="none" stroke="#155eef" stroke-width="1.8"/>'+(dashed?'<path d="'+dashed+'" fill="none" stroke="#155eef" stroke-width="1.8" stroke-dasharray="5 4"/>':'')+'<text x="2" y="'+(T+8)+'" font-size="10" fill="#667085">'+hi.toFixed(1)+'</text><text x="2" y="'+(H-B)+'" font-size="10" fill="#667085">'+lo.toFixed(1)+'</text><text x="'+L+'" y="'+(H-7)+'" font-size="10" fill="#667085">'+Math.min(...xs)+'</text><text x="'+(W-R-28)+'" y="'+(H-7)+'" font-size="10" fill="#667085">'+Math.max(...xs)+'</text></svg>';}}
function showCountry(iso){{const c=DATA.countries[iso];if(!c)return;const tags=c.tags.map(t=>'<span class="tag">'+esc(t)+'</span>').join('');const facts=[['Latest source period',c.status.latest_period],['WEO forecast horizon',c.status.forecast_to],['Indicators available',c.status.indicator_count],['Fuel input year',c.fuel.as_of]];const pct=Object.values(c.percentiles).map(p=>{{const x=p.country;return '<div class="pctl-row"><div class="pctl-head"><span>'+esc(p.label)+'</span><b>'+x.percentile+'th</b></div><div class="bar"><i style="width:'+x.percentile+'%"></i></div><div class="meta" style="margin-top:5px">'+fmt(x.value)+' '+esc(p.unit)+' · '+p.year+' · '+p.n+' countries</div></div>';}}).join('')||'<span class="meta">No comparable annual percentiles.</span>';const charts=DATA.chart_indicators.map(i=>'<div class="chart"><h3>'+esc(i.label)+'</h3><small>'+esc(i.unit)+'</small>'+chart(c.series[i.id])+'</div>').join('');document.getElementById('country-sheet').innerHTML='<div class="country-head"><div><h2>'+esc(c.name)+' <span class="meta">('+c.iso+')</span></h2><p class="meta">'+esc(c.region)+' · '+esc(c.region_geo)+'</p><div>'+tags+'</div></div><div class="quality">Fuel status: '+esc(c.fuel.status)+(c.fuel.status==='stale'?' · excluded from factual exposure tag':'')+'</div></div><div class="section"><div class="facts">'+facts.map(f=>'<div class="fact"><span class="label">'+f[0]+'</span><span class="value">'+(f[1]??'—')+'</span></div>').join('')+'</div></div><div class="section"><h2>Peer percentiles</h2><div class="pctl">'+pct+'</div></div><div class="section"><h2>Historical series</h2><div class="charts">'+charts+'</div></div>';}}
document.getElementById('country-select').innerHTML=countries.map(c=>'<option value="'+c.iso+'">'+esc(c.name)+' ('+c.iso+')</option>').join('');renderTable();showCountry(countries[0]?.iso);
</script></body></html>"""


def _display(value: Any, digits: int = 1) -> str:
    if value is None:
        return "-"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{float(value):,.{digits}f}"
    return str(value)


def _svg_line(points: list[dict[str, Any]], completed_year: int) -> str:
    valid = [p for p in points if p.get("value") is not None and math.isfinite(float(p["value"]))]
    if len(valid) < 2:
        return '<svg viewBox="0 0 520 180"><text x="12" y="90" fill="#667085">No comparable series</text></svg>'
    width, height, left, right, top, bottom = 520, 180, 42, 12, 10, 26
    years = [int(p["year"]) for p in valid]
    values = [float(p["value"]) for p in valid]
    low, high = min(values), max(values)
    if low == high:
        low, high = low - 1.0, high + 1.0
    pad = (high - low) * 0.10
    low, high = low - pad, high + pad

    def sx(year: int) -> float:
        return left + (year - min(years)) / (max(years) - min(years) or 1) * (width - left - right)

    def sy(value: float) -> float:
        return top + (1 - (value - low) / (high - low)) * (height - top - bottom)

    path = " ".join(
        f"{'M' if index == 0 else 'L'}{sx(int(p['year'])):.1f} {sy(float(p['value'])):.1f}"
        for index, p in enumerate(valid)
    )
    forecast_start = next(
        (index for index, p in enumerate(valid)
         if p.get("dataset") == "WEO" and int(p["year"]) > completed_year),
        None,
    )
    dashed = ""
    if forecast_start is not None and forecast_start > 0:
        forecast_points = valid[forecast_start - 1:]
        forecast_path = " ".join(
            f"{'M' if index == 0 else 'L'}{sx(int(p['year'])):.1f} {sy(float(p['value'])):.1f}"
            for index, p in enumerate(forecast_points)
        )
        dashed = f'<path d="{forecast_path}" fill="none" stroke="#155eef" stroke-width="1.8" stroke-dasharray="5 4"/>'
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img">'
        f'<line x1="{left}" y1="{height - bottom}" x2="{width - right}" y2="{height - bottom}" stroke="#d9e0ea"/>'
        f'<path d="{path}" fill="none" stroke="#155eef" stroke-width="1.8"/>{dashed}'
        f'<text x="2" y="{top + 8}" font-size="10" fill="#667085">{high:.1f}</text>'
        f'<text x="2" y="{height - bottom}" font-size="10" fill="#667085">{low:.1f}</text>'
        f'<text x="{left}" y="{height - 7}" font-size="10" fill="#667085">{min(years)}</text>'
        f'<text x="{width - right - 28}" y="{height - 7}" font-size="10" fill="#667085">{max(years)}</text>'
        '</svg>'
    )


def _country_sheet_html(country: dict[str, Any], data: dict[str, Any]) -> str:
    tags = "".join(f'<span class="tag">{html.escape(tag)}</span>' for tag in country["tags"])
    facts = [
        ("Latest source period", country["status"].get("latest_period")),
        ("WEO forecast horizon", country["status"].get("forecast_to")),
        ("Indicators available", country["status"].get("indicator_count")),
        ("Fuel input year", country["fuel"].get("as_of")),
    ]
    facts_html = "".join(
        f'<div class="fact"><span>{html.escape(label)}</span><b>{html.escape(_display(value, 0))}</b></div>'
        for label, value in facts
    )
    percentile_html = ""
    for percentile in country["percentiles"].values():
        point = percentile["country"]
        pct = int(point["percentile"])
        percentile_html += (
            '<div class="percentile">'
            f'<div><span>{html.escape(percentile["label"])}</span><b>{pct}th</b></div>'
            f'<div class="bar"><i style="width:{pct}%"></i></div>'
            f'<small>{html.escape(_display(point["value"]))} {html.escape(percentile["unit"])}'
            f' | {percentile["year"]} | {percentile["n"]} countries</small></div>'
        )
    if not percentile_html:
        percentile_html = '<p class="meta">No comparable annual percentiles.</p>'
    charts = ""
    for spec in data["chart_indicators"]:
        chart_id = f'chart-{html.escape(country["iso"])}-{html.escape(spec["id"])}'
        svg = _svg_line(country["series"].get(spec["id"], []), data["completed_year"])
        charts += (
            f'<article class="chart" id="{chart_id}">'
            f'<a class="chart-expand" href="#{chart_id}" aria-label="Expand chart">'
            f'<h3>{html.escape(spec["label"])}</h3><small>{html.escape(spec["unit"])}</small>'
            f'{svg}'
            '</a>'
            f'<a class="chart-close" href="#" aria-label="Close">&times;</a>'
            '</article>'
        )
    fuel_note = country["fuel"]["status"]
    if fuel_note == "fresh":
        fuel_note += f' | {_display(country["fuel"].get("value"))}% GDP'
    return (
        f'<details id="country-{html.escape(country["iso"])}" class="country">'
        f'<summary><b>{html.escape(country["name"])}</b> <span>{html.escape(country["iso"])} | '
        f'{html.escape(country["region"])}</span></summary>'
        '<div class="country-body">'
        f'<div class="tags">{tags}</div>'
        f'<p class="meta">{html.escape(country["region_geo"])} | Fuel data: {html.escape(fuel_note)}</p>'
        f'<div class="facts">{facts_html}</div>'
        f'<h3>Peer percentiles</h3><div class="percentiles">{percentile_html}</div>'
        f'<h3>Historical series</h3><div class="charts">{charts}</div>'
        '</div></details>'
    )


def _static_dashboard(data: dict[str, Any]) -> str:
    countries = sorted(data["countries"].values(), key=lambda item: item["name"])
    rows = "".join(
        '<tr>'
        f'<td><a href="#country-{html.escape(country["iso"])}">{html.escape(country["name"])}</a><br>'
        f'<span class="meta">{html.escape(country["iso"])} | {html.escape(country["region"])}</span></td>'
        f'<td>{html.escape(country["market"])}</td><td>{html.escape(country["income"])}</td>'
        f'<td>{html.escape(country["currency"] or country["fx_regime"])}</td>'
        f'<td>{html.escape(country["imf_arrangement"] or "-")}</td>'
        f'<td>{html.escape(country["fuel"]["label"])}'
        f'{" (" + _display(country["fuel"].get("value")) + "% GDP)" if country["fuel"].get("value") is not None else ""}</td>'
        f'<td>{html.escape(_display(country["status"].get("latest_period"), 0))}</td>'
        f'<td>{html.escape(_display(country["status"].get("forecast_to"), 0))}</td>'
        '</tr>'
        for country in countries
    )
    sheets = "".join(_country_sheet_html(country, data) for country in countries)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Country data dashboard</title>
<style>
:root{{--ink:#172033;--muted:#667085;--line:#d9e0ea;--paper:#fff;--bg:#f5f7fa;--blue:#155eef;--green:#067647;--amber:#b54708}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:Segoe UI,Arial,sans-serif;font-size:14px}}header{{background:#102a56;color:#fff;padding:18px 24px}}header h1{{margin:0;font-size:21px}}header p{{margin:5px 0 0;color:#d0ddf5;font-size:12px}}main{{max-width:1380px;margin:auto;padding:22px}}h2{{font-size:17px;margin:0 0 12px}}h3{{font-size:14px;margin:18px 0 9px}}.meta{{font-size:12px;color:var(--muted)}}.table-wrap{{overflow:auto;border:1px solid var(--line);background:#fff}}table{{border-collapse:collapse;width:100%;min-width:950px;font-size:12px}}th{{background:#edf2ff;color:#344054;text-align:left;padding:8px;border-bottom:1px solid var(--line)}}td{{padding:7px 8px;border-bottom:1px solid #edf0f4;vertical-align:top}}tr:hover td{{background:#f7faff}}a{{color:#1249b7;text-decoration:none}}a:hover{{text-decoration:underline}}.country{{display:block;background:#fff;border:1px solid var(--line);margin:14px 0}}summary{{cursor:pointer;padding:12px 14px;font-size:15px}}summary span{{font-size:12px;color:var(--muted);font-weight:400}}.country-body{{padding:0 14px 16px;border-top:1px solid var(--line)}}.tag{{display:inline-block;background:#eaf0ff;color:#1d3f8f;border-radius:3px;padding:3px 6px;font-size:11px;margin:12px 4px 0 0}}.facts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin:16px 0}}.fact{{border-left:3px solid #84adff;background:#f8faff;padding:9px 10px;min-height:58px}}.fact span{{display:block;font-size:11px;color:var(--muted)}}.fact b{{display:block;font-size:16px;margin-top:3px}}.percentiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(235px,1fr));gap:10px}}.percentile{{border:1px solid var(--line);padding:9px}}.percentile div:first-child{{display:flex;justify-content:space-between;font-size:12px;margin-bottom:7px}}.percentile small{{color:var(--muted);display:block;margin-top:5px}}.bar{{height:7px;background:#e7ecf5}}.bar i{{display:block;height:100%;background:var(--blue)}}.charts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:14px}}.chart{{position:relative;border:1px solid var(--line);padding:10px;min-height:220px}}.chart h3{{margin:0 0 3px}}.chart small{{color:var(--muted)}}.chart svg{{width:100%;height:178px;display:block;margin-top:4px}}.notes{{font-size:12px;color:var(--muted);line-height:1.6;max-width:820px;margin-top:28px}}
.chart-expand{{display:block;color:inherit;text-decoration:none;cursor:zoom-in}}.chart-close{{display:none}}
.chart:target{{position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);width:min(900px,92vw);max-height:85vh;overflow:auto;z-index:60;background:#fff;box-shadow:0 0 0 9999px rgba(15,23,42,.6),0 10px 40px rgba(0,0,0,.35)}}
.chart:target .chart-expand{{cursor:default}}.chart:target svg{{height:min(60vh,480px)}}
.chart:target .chart-close{{display:flex;position:absolute;top:8px;right:8px;align-items:center;justify-content:center;width:26px;height:26px;border-radius:4px;background:#eef1f6;color:var(--ink);text-decoration:none;font-size:15px;line-height:1}}
@media(max-width:680px){{main{{padding:14px}}.charts{{grid-template-columns:1fr}}}}
</style></head><body><header><h1>Country data dashboard</h1><p>Generated {html.escape(data["generated_at"])} | {len(countries)} country profiles</p></header><main>
<h2>Countries</h2><div class="table-wrap"><table><thead><tr><th>Country</th><th>Market</th><th>Income</th><th>Currency / FX</th><th>IMF arrangement</th><th>Net fuel trade</th><th>Latest source period</th><th>WEO horizon</th></tr></thead><tbody>{rows}</tbody></table></div>
<section class="notes"><p>Country profile tags use the configured country reference file. Net fuel trade is shown only when all required inputs are no more than two calendar years old. Percentiles are raw cross-country comparisons for the latest completed annual period and do not imply a risk, policy, cycle or investment conclusion. Dashed chart segments indicate WEO observations beyond the last completed calendar year. Click any chart to expand it.</p></section>
<section><h2>Country sheets</h2>{sheets}</section></main>
</body></html>"""


def render_html(data: dict[str, Any]) -> str:
    """Render a standalone dashboard with no browser-side runtime dependency."""
    return _static_dashboard(data)


def write_dashboard(db_path: Optional[str] = None, *, now: Optional[datetime] = None) -> Path:
    """Generate the dashboard and return its HTML path."""
    now = now or datetime.now(timezone.utc)
    con = get_conn(db_path, read_only=True)
    try:
        data = collect_dashboard(con, now=now)
    finally:
        con.close()
    output_dir = _report_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"country_data_dashboard_{now.strftime('%Y%m%d_%H%M')}.html"
    path.write_text(render_html(data), encoding="utf-8")
    return path
