"""Euro-area big-four national headlines redistributed by the Eurostat API.

The national statistical office is the issuer (Destatis, INSEE, Istat, INE);
Eurostat is only the distributor. Readings therefore carry mirror provenance
and stay unverified: a Eurostat snapshot never becomes an issuer print.

Each card is mapped only where the card's concept matches the Eurostat
dataset definition (see ``MAPPED``). Card concepts that differ -- Germany's
registered (BA) unemployment, the quarterly ILO/EPA rates of France and Spain
-- are deliberately absent and need issuer collectors.

JSON-stat ``updated`` is a DATASET timestamp, not the national release date.
It is the edition of the snapshot (date-only precision), recorded as provider
history; only each country's latest period and the one before it are emitted,
so a nightly dataset refresh does not rewrite the whole lookback window.
"""
from __future__ import annotations

from datetime import datetime
import re

from .adapters import Observation
from .http import FetchError, json_payload

BASE = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
HEAD_PERIODS = 2
# Status flags that mean the value is not the national issuer's figure.
REJECTED_FLAGS = frozenset("cdfnsuz")

SPECS = {
    "ea_big4_gdp": {
        "dataset": "namq_10_gdp", "frequency": "Q",
        # Real GDP, chain-linked volumes, % change on previous period, SCA.
        "filters": {"unit": "CLV_PCH_PRE", "s_adj": "SCA", "na_item": "B1GQ"},
        "indicators": {"DE": "de_gdp_qq", "FR": "fr_gdp_qq", "IT": "it_gdp_qq", "ES": "es_gdp_qq"},
    },
    "ea_big4_unemployment": {
        "dataset": "une_rt_m", "frequency": "M",
        # ILO unemployment, % of active population, total, 15-74, SA.
        "filters": {"s_adj": "SA", "age": "Y15-74", "unit": "PC_ACT", "sex": "T"},
        "indicators": {"IT": "it_unemployment_rate"},
    },
    "ea_big4_industrial_production": {
        "dataset": "sts_inpr_m", "frequency": "M",
        # Production volume, B-D (industry excluding construction), SCA, m/m.
        "filters": {"indic_bt": "PRD", "nace_r2": "B-D", "s_adj": "SCA", "unit": "PCH_PRE"},
        "indicators": {"FR": "fr_industrial_production_mm", "IT": "it_industrial_production_mm",
                       "ES": "es_industrial_production_mm"},
    },
}
MAPPED = {indicator: (family, geo) for family, spec in SPECS.items()
          for geo, indicator in spec["indicators"].items()}


def params(family, since):
    spec = SPECS[family]
    start = since if spec["frequency"] == "M" else f"{since[:4]}-Q{(int(since[5:7]) - 1) // 3 + 1}"
    return {"lang": "EN", **spec["filters"], "geo": sorted(spec["indicators"]), "sinceTimePeriod": start}


def period_end(period):
    if re.fullmatch(r"\d{4}-Q[1-4]", period):
        return f"{period[:4]}-{int(period[-1]) * 3:02d}"
    if re.fullmatch(r"\d{4}-\d{2}", period):
        return period
    raise FetchError("Eurostat national period format needs review")


def snapshot_date(payload):
    try:
        # Eurostat emits +0200; Python 3.9/3.10 require +02:00 here.
        stamp = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", payload["updated"])
        updated = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (KeyError, ValueError, TypeError, AttributeError):
        raise FetchError("Eurostat national snapshot timestamp needs review") from None
    if updated.tzinfo is None:
        raise FetchError("Eurostat national snapshot timestamp needs review")
    # The Luxembourg-local dataset date, never a national release instant.
    return updated.date().isoformat()


def parse(document, family, since="1900-01"):
    from .eurostat import cells
    spec = SPECS[family]
    payload = json_payload(document)
    if (payload.get("source") != "ESTAT" or payload.get("class") != "dataset" or
            payload.get("extension", {}).get("id") != spec["dataset"].upper()):
        raise FetchError("Eurostat national dataset identity needs review")
    expected = {"freq": {spec["frequency"]}, **{dim: {code} for dim, code in spec["filters"].items()}}
    if set(payload.get("id", [])) != set(expected) | {"geo", "time"}:
        raise FetchError("Eurostat national dimensions need review")
    for dim, codes in expected.items():
        if set(payload["dimension"][dim]["category"]["index"]) != codes:
            raise FetchError("Eurostat national units, adjustment or coverage need review")
    if not set(payload["dimension"]["geo"]["category"]["index"]) <= set(spec["indicators"]):
        raise FetchError("Eurostat national geography needs review")
    edition = snapshot_date(payload)
    by_geo = {}
    for dim, value, flag in cells(payload):
        if any(f in REJECTED_FLAGS for f in (flag or "")):
            raise FetchError("Eurostat national observation status needs review")
        period_end(dim["time"])
        by_geo.setdefault(dim["geo"], []).append((dim["time"], value))
    if set(by_geo) != set(spec["indicators"]):
        raise FetchError("Eurostat national headline missing for a mapped country; needs review")
    key = ":".join(spec["filters"].values())
    out = []
    for geo, rows in sorted(by_geo.items()):
        indicator = spec["indicators"][geo]
        for period, value in sorted(rows)[-HEAD_PERIODS:]:
            if period_end(period) < since:
                continue
            out.append(Observation(
                indicator, family, period, float(value), str(value),
                f"{spec['dataset']}:{geo}:{key}", "Eurostat", document, edition,
                edition + "T00:00:00Z", stage="regular", revision="provider_history",
                precision="date_only", provider_vintage=payload.get("updated"),
                method="api"))
    return out


class EurostatNationalAdapter:
    def __init__(self, client):
        self.client = client

    def fetch(self, families, since):
        out = []
        for family in families:
            if family not in SPECS:
                continue
            document = self.client.get("eurostat", BASE + SPECS[family]["dataset"], params(family, since))
            out.extend(parse(document, family, since))
        return out
