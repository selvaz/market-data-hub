"""Archived Eurostat headlines: issuer rates, explicit EA21 composition."""
import re
from datetime import datetime

from .http import FetchError, json_payload
from .schedule import due, run_date
from .tier1_editions import EDITIONS
from .tier1_publications import month, observation, plain, require, reviewed

CALENDAR = "https://ec.europa.eu/eurostat/o/calendars/eventsJson"
SPECS = {"ea_unemployment": ("Unemployment", "une_rt_m", "3", "ea_unemployment_rate"),
         "ea_industrial_production": ("Industrial production", "sts_inpr_m", "4", "ea_industrial_production_mm"),
         "ea_retail_sales": ("Retail trade", "sts_trtu_m", "4", "ea_retail_sales_mm")}


def calendar_editions(document, family):
    title, dataset, _, _ = SPECS[family]
    found = {}
    for row in json_payload(document):
        if row.get("euroindAuthor") != "estat" or not row.get("euroind") or row.get("title") != title:
            continue
        edition = row["start"][:10]
        if edition < min(EDITIONS[family]) or edition > run_date():
            continue
        ref = datetime.strptime(row["period"], "%B %Y").strftime("%Y-%m")
        if (row.get("preliminary") or dataset not in row.get("datasetCodes", "").split(",") or
                edition not in EDITIONS[family] or EDITIONS[family][edition][0] != ref):
            raise FetchError("Eurostat calendar edition needs review")
        found[edition] = EDITIONS[family][edition]
    if set(due(EDITIONS[family])) != set(found):
        raise FetchError("Eurostat due calendar edition missing; needs review")
    return found


def publication_url(family, edition):
    suffix = "bp" if (family, edition) == ("ea_unemployment", "2026-09-01") else "ap"
    return ("https://ec.europa.eu/eurostat/web/products-euro-indicators/w/" + SPECS[family][2] + "-" +
            datetime.strptime(edition, "%Y-%m-%d").strftime("%d%m%Y") + "-" + suffix)


def parse(document, family, since="1900-01"):
    raw = document.body.decode("utf-8")
    stamp = require(r'<meta property=[\"\x27]article:published_time[\"\x27] content=[\"\x27]([^\"\x27]+)', raw)[1]
    edition = stamp[:10]
    head, _, _ = reviewed(family, edition)
    body = plain(raw)
    if "Euro area (EA21)" not in body:
        raise FetchError("Eurostat composition needs review")
    if family == "ea_unemployment":
        if "persons aged 15 to 74" not in body:
            raise FetchError("Eurostat unemployment age definition needs review")
        printed = require(r"In " + month(head) + r", the euro area seasonally adjusted unemployment rate was ([\d.]+)%", body)[1]
    elif family in ("ea_retail_sales", "ea_industrial_production"):
        from .bls import shift_month
        prior = month(shift_month(head, -1))
        label = "the seasonally adjusted retail trade volume" if family == "ea_retail_sales" else "seasonally adjusted industrial production"
        found = require(r"In " + month(head) + r", compared with " + prior +
            r", " + label + r" (increased|decreased) by ([\d.]+)% in (?:both )?the euro area", body)
        printed = ("-" if found[1].lower() == "decreased" else "") + found[2]
    else:
        raise FetchError("Eurostat measure needs review")
    return [observation(document, family, edition, SPECS[family][3], head, printed)] if head >= since else []


def fetch(client, family, since):
    document = client.get("eurostat", CALENDAR, {"start": min(EDITIONS[family]) + "T00:00:00Z", "end": "2027-01-01T00:00:00Z"})
    out = []
    for edition, (period, _, _) in calendar_editions(document, family).items():
        if period >= since:
            doc = client.get("eurostat", publication_url(family, edition))
            rows = parse(doc, family, since)
            if not rows or {o.edition for o in rows} != {edition}:
                raise FetchError("Eurostat requested archive edition needs review")
            out += rows
    return out
