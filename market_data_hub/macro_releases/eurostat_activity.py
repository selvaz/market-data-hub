"""Archived Eurostat headlines: issuer rates, explicit EA21 composition."""
import re
from datetime import datetime

from .http import FetchError, json_payload
from .schedule import due, run_date
from .tier1_editions import EDITIONS
from .tier1_publications import month, observation, plain, require, reviewed

CALENDAR = "https://ec.europa.eu/eurostat/o/calendars/eventsJson"
SPECS = {"ea_unemployment": ("Unemployment", "une_rt_m", "3", "ea_unemployment_rate"),
         "ea_core_hicp": ("Inflation (HICP)", "prc_hicp_manr", "2", "ea21_core_hicp_yy"),
         "ea_trade_balance": ("International trade in goods", "ext_st_easitc", "6", "ea_trade_balance"),
         "ea_industrial_production": ("Industrial production", "sts_inpr_m", "4", "ea_industrial_production_mm"),
         "ea_retail_sales": ("Retail trade", "sts_trtu_m", "4", "ea_retail_sales_mm")}


def calendar_editions(document, family):
    title, dataset, _, _ = SPECS[family]
    found = {}
    for row in json_payload(document):
        core_titles = {"Inflation (HICP)": "final", "Flash estimate inflation euro area": "flash"}
        if row.get("euroindAuthor") != "estat" or not row.get("euroind") or row.get("title") not in (core_titles if family == "ea_core_hicp" else {title}):
            continue
        edition = row["start"][:10]
        if edition < min(EDITIONS[family]) or edition > run_date():
            continue
        ref = datetime.strptime(row["period"], "%B %Y").strftime("%Y-%m")
        if (row.get("preliminary") or dataset not in row.get("datasetCodes", "").split(",") or
                edition not in EDITIONS[family] or EDITIONS[family][edition][0] != ref or
                family == "ea_core_hicp" and EDITIONS[family][edition][1] != core_titles[row["title"]]):
            raise FetchError("Eurostat calendar edition needs review")
        found[edition] = EDITIONS[family][edition]
    if set(due(EDITIONS[family])) != set(found):
        raise FetchError("Eurostat due calendar edition missing; needs review")
    return found


def publication_url(family, edition):
    suffix = "bp" if (family, edition) in (("ea_unemployment", "2026-09-01"), ("ea_trade_balance", "2026-10-16")) else "ap"
    return ("https://ec.europa.eu/eurostat/web/products-euro-indicators/w/" + SPECS[family][2] + "-" +
            datetime.strptime(edition, "%Y-%m-%d").strftime("%d%m%Y") + "-" + suffix)


def parse(document, family, since="1900-01"):
    raw = document.body.decode("utf-8")
    stamp = require(r'<meta property=[\"\x27]article:published_time[\"\x27] content=[\"\x27]([^\"\x27]+)', raw)[1]
    edition = stamp[:10]
    head, _, _ = reviewed(family, edition)
    body = plain(raw)
    membership = ("data for January 2026 onwards represent EA21 series" in body if family == "ea_core_hicp"
                  else bool(re.search(r"euro area \(EA21\)", body, re.I)))
    if not membership:
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
    elif family == "ea_core_hicp":
        stage = EDITIONS[family][edition][1]
        short = datetime.strptime(head, "%Y-%m").strftime("%b %y")
        if not re.search(r"Annual rate Monthly rate .*?" + short + " " + short, body):
            raise FetchError("Eurostat HICP table columns needs review")
        token = r"[+-]?\d+(?:\.\d+)?e?"
        row = require(r"energy, food, alcohol & tobacco ((?:" + token + r"\s+){8}" + token + ")", body)[1]
        values = row.split()
        printed = values[7]
        if printed.endswith("e") != (stage == "flash") or (stage == "flash" and "expected to be" not in body):
            raise FetchError("Eurostat HICP estimate stage needs review")
        printed = printed.removesuffix("e")
    elif family == "ea_trade_balance":
        from .bls import shift_month
        # The headline is NSA. Only the explicitly labelled SA annex matches.
        section = require(r"Annex - Seasonally adjusted data (.*?)EA and EU trade", body)[1]
        found = require(r"In " + month(head) + r" compared with " + month(shift_month(head, -1)) +
            r", euro area seasonally adjusted exports.*?The seasonally adjusted balance was [\u20ac]([+-]?[\d.]+) bn", section)
        printed = found[1]
        return [observation(document, family, edition, SPECS[family][3], head, printed,
                            value=float(printed) * 1000)] if head >= since else []
    else:
        raise FetchError("Eurostat measure needs review")
    return [observation(document, family, edition, SPECS[family][3], head, printed)] if head >= since else []


def fetch(client, family, since):
    document = client.get("eurostat", CALENDAR, {"start": "2026-09-01T00:00:00Z", "end": "2027-01-01T00:00:00Z"})
    out = []
    for edition, (period, _, _) in calendar_editions(document, family).items():
        if period >= since:
            doc = client.get("eurostat", publication_url(family, edition))
            rows = parse(doc, family, since)
            if not rows or {o.edition for o in rows} != {edition}:
                raise FetchError("Eurostat requested archive edition needs review")
            out += rows
    return out
