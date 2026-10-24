"""FRED/ALFRED is a redistributor, even when it matches a current issuer print."""
from __future__ import annotations

from dataclasses import replace

from .adapters import Observation
from .bea import EDITIONS as GDP_EDITIONS
from .bls import EDITIONS as BLS_EDITIONS
from .bls import revision_start, shift_month
from .http import FetchError, api_key, json_payload, require_complete
from .publication_times import new_york_release
from .us_editions import EDITIONS as MONTHLY_EDITIONS, reviewed, observation_stage
from .schedule import due, run_date

ENDPOINT = "https://api.stlouisfed.org/fred/series/observations"
MAPPINGS = {
    "us_cpi_headline_mm": ("us_cpi", "CPIAUCSL", "pch"),
    "us_cpi_headline_yy": ("us_cpi", "CPIAUCNS", "pc1"),
    "us_cpi_core_mm": ("us_cpi", "CPILFESL", "pch"),
    "us_cpi_core_yy": ("us_cpi", "CPILFENS", "pc1"),
    "us_payrolls_change": ("us_payrolls", "PAYEMS", "chg"),
    "us_gdp_qq_saar": ("us_gdp", "A191RL1Q225SBEA", "lin"),
}
MAPPINGS.update({
    "us_retail_sales_mm": ("us_retail_sales", "RSAFS", "pch"),
    "us_retail_sales_ex_auto_mm": ("us_retail_sales", "RSFSXMV", "pch"),
    "us_housing_starts": ("us_housing_starts", "HOUST", "lin"),
    "us_building_permits": ("us_housing_starts", "PERMIT", "lin"),
    "us_durable_goods_orders_mm": ("us_durable_goods", "DGORDER", "pch"),
    "us_durable_goods_ex_transport_mm": ("us_durable_goods", "ADXTNO", "pch"),
    "us_industrial_production_mm": ("us_industrial_production", "INDPRO", "pch"),
    "us_capacity_utilization": ("us_industrial_production", "TCU", "lin"),
    "us_trade_balance": ("us_trade_balance", "BOPGSTB", "lin"),
})


class FREDAdapter:
    def __init__(self, client):
        self.client = client

    def fetch(self, families, since):
        out = []
        for indicator, (family, series, units) in MAPPINGS.items():
            if family not in families:
                continue
            if family in MONTHLY_EDITIONS:
                # A current-head probe is only a freshness guard. Its values
                # are never attached to an older reviewed as-of edition.
                latest = max(p for p, _, _ in due(MONTHLY_EDITIONS[family]).values())
                current = self.client.get("fred", ENDPOINT, {
                    "api_key": api_key("FRED_API_KEY"), "file_type": "json", "series_id": series,
                    "units": units, "observation_start": latest + "-01",
                    "sort_order": "desc", "limit": 1,
                })
                payload = json_payload(current)
                rows = payload.get("observations", [])
                if (payload.get("units") != units or not rows or rows[0]["value"] == "." or
                        rows[0]["date"][:7] != latest):
                    raise FetchError(f"FRED current headline metadata needs review: {family}")
            editions = ([(d, stage) for d, (p, stage, _) in due(MONTHLY_EDITIONS[family]).items() if p >= since]
                        if family in MONTHLY_EDITIONS else
                        [(d, stage) for d, (_, stage) in due(GDP_EDITIONS).items()] if family == "us_gdp"
                        else [(max(d for (f, _), d in BLS_EDITIONS.items() if f == family and d <= run_date()), "regular")])
            # An explicit as-of request for EVERY edition preserves the unchanged
            # second GDP estimate that ALFRED's change-only history omits.
            for edition, stage in editions:
                start = since
                if family == "us_gdp":
                    start = f"{since[:4]}-{(int(since[5:]) - 1) // 3 * 3 + 1:02d}"
                elif family in MONTHLY_EDITIONS:
                    start = min(since, shift_month(MONTHLY_EDITIONS[family][edition][0], -2))
                else:
                    latest = max(p for (f, p), d in BLS_EDITIONS.items() if f == family and d <= run_date())
                    start = revision_start(family, latest, start, edition)
                doc = self.client.get("fred", ENDPOINT, {
                    "api_key": api_key("FRED_API_KEY"), "file_type": "json", "series_id": series,
                    "units": units, "observation_start": start + "-01",
                    "realtime_start": edition, "realtime_end": edition, "limit": 10000,
                })
                out.extend(self.parse(doc, indicator=indicator, edition=edition, stage=stage,
                                      since=since if family == "us_gdp" else start))
        return out

    @staticmethod
    def parse(document, *, indicator, edition, stage="regular", since="1900-01"):
        family, series, units = MAPPINGS[indicator]
        monthly = family in MONTHLY_EDITIONS
        if monthly:
            headline, stage, release_time = reviewed(family, edition)
        payload = json_payload(document)
        if "error_code" in payload or payload.get("units") != units:
            raise FetchError("FRED error or unexpected units")
        if payload.get("realtime_start") != edition or payload.get("realtime_end") != edition:
            raise FetchError("FRED response is not the requested publication as-of")
        rows = payload.get("observations", [])
        require_complete(payload, rows)
        numeric_months = [r["date"][:7] for r in rows if r["value"] != "."]
        if monthly and (not numeric_months or max(numeric_months) != headline):
            raise FetchError(f"FRED headline/edition metadata needs review: {family} {edition}")
        out = []
        for row in rows:
            if row["value"] == ".":
                continue
            month = row["date"][:7]
            period = month if family != "us_gdp" else f"{month[:4]}-Q{(int(month[5:]) - 1) // 3 + 1}"
            end = month if family != "us_gdp" else f"{month[:4]}-{int(period[-1]) * 3:02d}"
            if end < since:
                continue
            value = float(row["value"]) / (1000 if indicator == "us_trade_balance" else 1)
            out.append(Observation(indicator, family, period, value, row["value"],
                                   series + ":" + units, "FRED", document, edition,
                                   **(new_york_release(edition, release_time) if monthly else
                                      {"published_at": edition + "T00:00:00Z"}),
                                   stage=observation_stage(family, edition, period) if monthly else stage,
                                   event_stage=stage if monthly else None,
                                   precision="exact" if monthly else "date_only",
                                   provider_vintage=row["realtime_start"], revision="provider_history"))
            if family == "us_gdp" and period != GDP_EDITIONS[edition][0]:
                out[-1] = replace(out[-1], stage="regular", event_stage=stage)
        return out


class USRedistributorAdapter(FREDAdapter):
    """ALFRED history plus independently parsed issuer publications.

    FRED values always retain mirror provenance. Only the actual issuer document
    supplies an official value; a rounded match never promotes a mirror.
    """

    def fetch(self, families, since):
        from .us_publications import fetch_publications
        return super().fetch(families, since) + fetch_publications(self.client, families, since)
