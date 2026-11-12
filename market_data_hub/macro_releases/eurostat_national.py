"""National headlines (DE, FR, IT, ES, PL) redistributed by the Eurostat API.

The national statistical office is the issuer (Destatis, INSEE, Istat, INE,
GUS; DG ECFIN for the economic sentiment indicator); Eurostat is only the
distributor. This is the single Eurostat JSON-stat downloader for national
headlines: GDP, ILO unemployment, industrial production, retail volume,
harmonised HICP (current edition plus first-released flash) and ESI. Readings therefore carry mirror provenance
and stay unverified: a Eurostat snapshot never becomes an issuer print.

Each card is mapped only where the card's concept matches the Eurostat
dataset definition (see ``MAPPED``). Card concepts that differ -- Germany's
registered (BA) unemployment, the quarterly ILO/EPA rates of France and Spain,
the national CPIs -- are deliberately absent and need issuer collectors. The
monthly ILO rates of Germany, France, Spain and Poland and the national HICPs
have their own cards. Poland is outside the euro area: it has no HICP flash
estimate, so the flash request covers only the euro-area countries.

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
        # Real GDP, chain-linked volumes, % change on previous quarter, SCA.
        "filters": {"unit": "CLV_PCH_PRE", "s_adj": "SCA", "na_item": "B1GQ"},
        "indicators": {"DE": "de_gdp_qq", "FR": "fr_gdp_qq", "IT": "it_gdp_qq", "ES": "es_gdp_qq",
                       "PL": "pl_gdp_qq"},
    },
    "ea_big4_unemployment": {
        "dataset": "une_rt_m", "frequency": "M",
        # ILO unemployment, % of active population, both sexes, SA. In une_rt_m
        # age=TOTAL is labelled "Total" and covers ages 15-74; Y15-74 is not a
        # code of this dataset (verified live by the owner, 2026-11-12).
        "filters": {"s_adj": "SA", "age": "TOTAL", "unit": "PC_ACT", "sex": "T"},
        # Italy's card is Istat's monthly ILO rate. Germany, France, Spain and
        # Poland get separate monthly ILO cards: their BA-registered, INSEE
        # quarterly, EPA quarterly and GUS registered cards are different
        # concepts and stay unmapped.
        "indicators": {"DE": "de_unemployment_ilo_rate", "FR": "fr_unemployment_ilo_rate",
                       "IT": "it_unemployment_rate", "ES": "es_unemployment_ilo_rate",
                       "PL": "pl_unemployment_ilo_rate"},
    },
    "ea_big4_industrial_production": {
        "dataset": "sts_inpr_m", "frequency": "M",
        # Production volume, B-D (industry excluding construction), SCA, m/m.
        "filters": {"indic_bt": "PRD", "nace_r2": "B-D", "s_adj": "SCA", "unit": "PCH_PRE"},
        "indicators": {"DE": "de_industrial_production_mm", "FR": "fr_industrial_production_mm",
                       "IT": "it_industrial_production_mm", "ES": "es_industrial_production_mm",
                       "PL": "pl_industrial_production_mm"},
    },
    "ea_big4_retail": {
        "dataset": "sts_trtu_m", "frequency": "M",
        # Retail trade except motor vehicles (G47), volume of sales, SCA, m/m.
        "filters": {"indic_bt": "VOL_SLS", "nace_r2": "G47", "s_adj": "SCA", "unit": "PCH_PRE"},
        "indicators": {"DE": "de_retail_sales_mm", "FR": "fr_retail_sales_mm", "IT": "it_retail_sales_mm",
                       "ES": "es_retail_sales_mm", "PL": "pl_retail_sales_mm"},
    },
    "ea_big4_hicp": {
        # ECOICOP v2 monthly rates (current edition). The national HICP is a
        # harmonised index, NOT the national CPI: separate indicators and cards.
        "dataset": "prc_hicp_minr", "frequency": "M",
        "filters": {"coicop18": "TOTAL"},
        "split": ("unit", {"RCH_A": "yy", "RCH_M": "mm"}),
        "indicators": {"DE": "de_hicp_{}", "FR": "fr_hicp_{}", "IT": "it_hicp_{}", "ES": "es_hicp_{}",
                       "PL": "pl_hicp_{}"},
        # First-released flash estimates fill only months not yet in minr.
        # Flash estimates exist only for euro-area members (not Poland).
        "flash": {"dataset": "prc_hicp_fpd", "filters": {"release": "FLS"},
                  "geo": ("DE", "ES", "FR", "IT")},
    },
    "ea_big4_esi": {
        # DG ECFIN Economic Sentiment Indicator by country, redistributed by
        # Eurostat (ei_bssi_m_r2, NACE Rev. 2 survey basis), SA, long-term mean 100.
        "dataset": "ei_bssi_m_r2", "frequency": "M",
        "filters": {"indic": "BS-ESI-I", "s_adj": "SA"},
        "indicators": {"DE": "de_economic_sentiment_indicator", "FR": "fr_economic_sentiment_indicator",
                       "IT": "it_economic_sentiment_indicator", "ES": "es_economic_sentiment_indicator",
                       "PL": "pl_economic_sentiment_indicator"},
    },
}


def indicator_ids(spec):
    """{(geo, split code or None): indicator id} for one family."""
    split = spec.get("split")
    if split is None:
        return {(geo, None): indicator for geo, indicator in spec["indicators"].items()}
    return {(geo, code): template.format(basis) for geo, template in spec["indicators"].items()
            for code, basis in split[1].items()}


MAPPED = {indicator: (family, geo) for family, spec in SPECS.items()
          for (geo, _), indicator in indicator_ids(spec).items()}


def params(family, since, flash=False):
    spec = SPECS[family]
    start = since if spec["frequency"] == "M" else f"{since[:4]}-Q{(int(since[5:7]) - 1) // 3 + 1}"
    out = {"lang": "EN", **spec["filters"]}
    if flash:
        out.update(spec["flash"]["filters"])
    if spec.get("split"):
        out[spec["split"][0]] = sorted(spec["split"][1])
    geos = spec["flash"]["geo"] if flash else spec["indicators"]
    return {**out, "geo": sorted(geos), "sinceTimePeriod": start}


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


def parse(document, family, since="1900-01", flash=False, after=None):
    """Latest two periods per mapped series; ``flash`` reads the first-released dataset.

    ``after`` (flash only) drops flash months already covered by the regular dataset.
    """
    from .eurostat import cells
    spec = SPECS[family]
    dataset = spec["flash"]["dataset"] if flash else spec["dataset"]
    filters = {**spec["filters"], **(spec["flash"]["filters"] if flash else {})}
    split = spec.get("split")
    payload = json_payload(document)
    if (payload.get("source") != "ESTAT" or payload.get("class") != "dataset" or
            payload.get("extension", {}).get("id") != dataset.upper()):
        raise FetchError("Eurostat national dataset identity needs review")
    expected = {"freq": {spec["frequency"]}, **{dim: {code} for dim, code in filters.items()}}
    if split:
        expected[split[0]] = set(split[1])
    if set(payload.get("id", [])) != set(expected) | {"geo", "time"}:
        raise FetchError("Eurostat national dimensions need review")
    for dim, codes in expected.items():
        if set(payload["dimension"][dim]["category"]["index"]) != codes:
            raise FetchError("Eurostat national units, adjustment or coverage need review")
    geos = spec["flash"]["geo"] if flash else spec["indicators"]
    if not set(payload["dimension"]["geo"]["category"]["index"]) <= set(geos):
        raise FetchError("Eurostat national geography needs review")
    edition = snapshot_date(payload)
    ids = indicator_ids(spec)
    by_series = {}
    for dim, value, flag in cells(payload):
        if any(f in REJECTED_FLAGS for f in (flag or "")):
            raise FetchError("Eurostat national observation status needs review")
        period_end(dim["time"])
        code = dim[split[0]] if split else None
        by_series.setdefault((dim["geo"], code), []).append((dim["time"], value))
    if not flash and set(by_series) != set(ids):
        raise FetchError("Eurostat national headline missing for a mapped country; needs review")
    out = []
    for (geo, code), rows in sorted(by_series.items()):
        indicator = ids[(geo, code)]
        key = ":".join([*filters.values(), *([code] if code else [])])
        for period, value in sorted(rows)[-HEAD_PERIODS:]:
            if period_end(period) < since or (after and period <= after.get(indicator, "")):
                continue
            out.append(Observation(
                indicator, family, period, float(value), str(value),
                f"{dataset}:{geo}:{key}", "Eurostat", document, edition,
                edition + "T00:00:00Z", stage="flash" if flash else "regular", revision="provider_history",
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
            spec = SPECS[family]
            document = self.client.get("eurostat", BASE + spec["dataset"], params(family, since))
            rows = parse(document, family, since)
            out.extend(rows)
            if spec.get("flash"):
                newest = {}
                for o in rows:
                    newest[o.indicator_id] = max(newest.get(o.indicator_id, ""), o.period)
                flash = self.client.get("eurostat", BASE + spec["flash"]["dataset"], params(family, since, flash=True))
                out.extend(parse(flash, family, since, flash=True, after=newest))
        return out
