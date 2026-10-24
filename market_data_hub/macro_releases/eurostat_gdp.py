"""Quarterly published GDP rates, with separate, immutable estimate editions."""
from datetime import datetime
from html import unescape
import re

from .adapters import Observation
from .http import FetchError, json_payload
from .schedule import due, run_date, get_edition

CALENDAR = "https://ec.europa.eu/eurostat/o/calendars/eventsJson"
DATASET = "namq_10_gdp"
BASE = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
# https://ec.europa.eu/eurostat/news/euro-indicators/release-calendar
# https://ec.europa.eu/eurostat/documents/24987/6642470/QNA_release_calendar.pdf
# Calendar start fields are marked allDay; do not interpret their Z as an exact time.
EDITIONS = {
    "2026-07-30": ("2026-Q2", "preliminary_flash", "11:00"),
    "2026-08-14": ("2026-Q2", "flash", "11:00"),
    "2026-09-07": ("2026-Q2", "regular", "11:00"),
    "2026-10-20": ("2026-Q2", "update", "11:00"),
    "2026-10-30": ("2026-Q3", "preliminary_flash", "11:00"),
    "2026-11-13": ("2026-Q3", "flash", "11:00"),
    "2026-12-07": ("2026-Q3", "regular", "11:00"),
}
TITLES = {"Preliminary flash estimate GDP - EU and euro area": "preliminary_flash",
          "Flash estimate GDP and employment - EU and euro area": "flash",
          "GDP main aggregates and employment": "regular",
          "GDP main aggregates and employment - update": "update"}


def publication_url(edition):
    return "https://ec.europa.eu/eurostat/web/products-euro-indicators/w/2-" + datetime.strptime(edition, "%Y-%m-%d").strftime("%d%m%Y") + "-ap"


def calendar_editions(document):
    found = {}
    for row in json_payload(document):
        if row.get("euroindAuthor") != "estat" or DATASET not in row.get("datasetCodes", "").split(","):
            continue
        edition = row["start"][:10]
        if edition < min(EDITIONS) or edition > run_date():
            continue
        if row.get("preliminary") or row.get("title") not in TITLES:
            raise FetchError("Eurostat GDP calendar format or confirmation needs review")
        stage = TITLES[row["title"]]
        match = re.fullmatch(r"Q([1-4])/(\d{4})", row.get("period", ""))
        if not match or EDITIONS.get(edition, ())[:2] != (f"{match[2]}-Q{match[1]}", stage):
            raise FetchError("Eurostat GDP calendar edition needs review")
        found[edition] = EDITIONS[edition]
    if not set(due(EDITIONS, family="ea_gdp")).issubset(found):
        raise FetchError("Eurostat GDP due calendar edition missing; needs review")
    return due(found, family="ea_gdp")


def parse_publication(document, since="1900-01"):
    raw = document.body.decode("utf-8")
    stamp = re.search(r"<meta property=['\"]article:published_time['\"] content=['\"]([^'\"]+)", raw)
    if not stamp or stamp[1][:10] not in EDITIONS:
        raise FetchError("Eurostat GDP publication date needs review")
    edition = stamp[1][:10]
    period, stage, _ = EDITIONS[edition]
    body = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", raw)))
    quarter = ("first", "second", "third", "fourth")[int(period[-1]) - 1]
    label = {"preliminary_flash": "Preliminary flash estimate for", "flash": "GDP and employment flash estimates for",
             "regular": "GDP main components and employment estimates for"}.get(stage)
    if (not label or f"{label} the {quarter} quarter of {period[:4]}" not in body or
            "Euro area (EA21)" not in body or "seasonally and calendar adjusted" not in body):
        raise FetchError("Eurostat GDP stage, reference period, adjustment or membership needs review")
    patterns = {
        "qq": rf"In the {quarter} quarter of {period[:4]}, seasonally adjusted GDP (increased|decreased) by ([\d.]+)% in the euro area",
        "yy": r"Compared with the same quarter of the previous year, seasonally adjusted GDP (increased|decreased) by ([\d.]+)% in the euro area",
    }
    out = []
    for basis, pattern in patterns.items():
        match = re.search(pattern, body)
        if not match:
            raise FetchError("Eurostat GDP published rate paragraph needs review")
        printed = ("-" if match[1] == "decreased" else "") + match[2]
        if f"{period[:4]}-{int(period[-1])*3:02d}" >= since:
            out.append(Observation(f"ea21_gdp_{basis}", "ea_gdp", period, float(printed), printed,
                f"{DATASET}:EA21:B1GQ:SCA:{basis}", "Eurostat", document, edition, stamp[1],
                stage=stage, precision="exact", method="html", provider_vintage=edition,
                composition_id="EA21", revision="initial" if stage == "preliminary_flash" else "routine"))
    return out


def parse_snapshot(document, edition, since="1900-01"):
    from .eurostat import cells
    payload = json_payload(document)
    period, stage, _ = EDITIONS[edition]
    if stage != "update" or payload.get("source") != "ESTAT" or payload.get("class") != "dataset" or payload.get("extension", {}).get("id") != "NAMQ_10_GDP":
        raise FetchError("Eurostat GDP dataset identity or estimate stage needs review")
    expected = {"freq": {"Q"}, "unit": {"CLV_PCH_PRE", "CLV_PCH_SM"},
                "s_adj": {"SCA"}, "na_item": {"B1GQ"}, "geo": {"EA21"}}
    if set(payload.get("id", [])) != set(expected) | {"time"}:
        raise FetchError("Eurostat GDP dimensions needs review")
    for dim, codes in expected.items():
        if set(payload["dimension"][dim]["category"]["index"]) != codes:
            raise FetchError("Eurostat GDP units, adjustment or geography needs review")
    periods = payload["dimension"]["time"]["category"]["index"]
    if max(periods, default="") != period:
        raise FetchError("Eurostat GDP headline period needs review")
    out = []
    for dim, value, flag in cells(payload):
        ref = dim["time"]
        if not re.fullmatch(r"\d{4}-Q[1-4]", ref) or any(f in flag for f in ("e", "c", "u")):
            raise FetchError("Eurostat GDP period or status needs review")
        if f"{ref[:4]}-{int(ref[-1])*3:02d}" < since:
            continue
        basis = "qq" if dim["unit"] == "CLV_PCH_PRE" else "yy"
        out.append(Observation(f"ea21_gdp_{basis}", "ea_gdp", ref, float(value), str(value),
            f"{DATASET}:EA21:B1GQ:SCA:{basis}", "Eurostat", document, edition, edition + "T00:00:00Z",
            stage=stage, precision="date_only", provider_vintage=payload.get("updated"),
            composition_id="EA21", revision="routine"))
    if {o.indicator_id for o in out if o.period == period} != {"ea21_gdp_qq", "ea21_gdp_yy"}:
        raise FetchError("Eurostat GDP headline rates missing; needs review")
    return out


def fetch_gdp(client, since):
    calendar = client.get("eurostat", CALENDAR, {"start": min(EDITIONS) + "T00:00:00Z",
        "end": "2027-01-01T00:00:00Z", "theme": "2"})
    editions = calendar_editions(calendar)
    out = []
    for edition, (period, stage, _) in editions.items():
        if stage != "update" and f"{period[:4]}-{int(period[-1])*3:02d}" >= since:
            doc = get_edition(client, "eurostat", publication_url(edition), "ea_gdp", edition)
            if doc is None:
                continue
            parsed = parse_publication(doc, since)
            if not parsed or {o.edition for o in parsed} != {edition}:
                raise FetchError("Eurostat GDP archive edition needs review")
            out += parsed
    latest = max(editions, default=None)
    if latest and editions[latest][1] == "update":
        params = {"lang": "EN", "geo": "EA21", "na_item": "B1GQ", "s_adj": "SCA",
                  "unit": ["CLV_PCH_PRE", "CLV_PCH_SM"],
                  "sinceTimePeriod": f"{since[:4]}-Q{(int(since[5:])-1)//3+1}"}
        out += parse_snapshot(client.get("eurostat", BASE + DATASET, params), latest, since)
    return out
