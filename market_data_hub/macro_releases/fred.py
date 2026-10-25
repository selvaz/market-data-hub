"""FRED/ALFRED is a redistributor, even when it matches a current issuer print."""
from __future__ import annotations

from dataclasses import replace

from .adapters import Observation
from .bea import EDITIONS as GDP_EDITIONS
from .bls import revision_start, shift_month
from .http import FetchError, api_key, json_payload, require_complete
from .publication_times import new_york_release
from .us_editions import EDITIONS as MONTHLY_EDITIONS, reviewed, observation_stage
from .schedule import due, family_editions, publication_grace, mark_pending

ENDPOINT = "https://api.stlouisfed.org/fred/series/observations"
MAPPINGS = {
    "us_cpi_headline_mm": ("us_cpi", "CPIAUCSL", "pch"),
    "us_cpi_headline_yy": ("us_cpi", "CPIAUCNS", "pc1"),
    "us_cpi_core_mm": ("us_cpi", "CPILFESL", "pch"),
    "us_cpi_core_yy": ("us_cpi", "CPILFENS", "pc1"),
    "us_payrolls_change": ("us_payrolls", "PAYEMS", "chg"),
    "us_ppi_mm": ("us_ppi", "PPIFIS", "pch"),
    "us_ppi_yy": ("us_ppi", "PPIFID", "pc1"),
    "us_jolts_openings": ("us_jolts", "JTSJOL", "lin"),
    "us_unemployment_rate": ("us_unemployment", "UNRATE", "lin"),
    "us_avg_hourly_earnings_mm": ("us_ahe", "CES0500000003", "pch"),
    "us_avg_hourly_earnings_yy": ("us_ahe", "CES0500000003", "pc1"),
    "us_gdp_qq_saar": ("us_gdp", "A191RL1Q225SBEA", "lin"),
}
MAPPINGS.update({
    "us_case_shiller_national_yy": ("us_case_shiller", "CSUSHPINSA", "pc1"),
    "us_case_shiller_20_yy": ("us_case_shiller", "SPCS20RNSA", "pc1"),
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
            calendar = due(family_editions(family), family=family)
            if family == "us_case_shiller" and not calendar:
                continue
            if family in MONTHLY_EDITIONS:
                # A current-head probe is only a freshness guard. Its values
                # are never attached to an older reviewed as-of edition.
                latest = max(p for p, _, _ in calendar.values())
                current = self.client.get("fred", ENDPOINT, {
                    "api_key": api_key("FRED_API_KEY"), "file_type": "json", "series_id": series,
                    "units": units, "observation_start": latest + "-01",
                    "sort_order": "desc", "limit": 1,
                })
                payload = json_payload(current)
                rows = payload.get("observations", [])
                actual = rows[0]["date"][:7] if rows else None
                newest = max(calendar)
                previous = {d: meta for d, meta in calendar.items() if d != newest}
                if (actual != latest and previous and publication_grace(family, newest) and
                        actual == max(p for p, _, _ in previous.values())):
                    mark_pending(family, newest)
                    calendar = previous
                    latest = max(p for p, _, _ in calendar.values())
                if (payload.get("units") != units or not rows or rows[0]["value"] == "." or
                        actual != latest):
                    raise FetchError(f"FRED current headline metadata needs review: {family}")
            editions = ([(d, stage) for d, (p, stage, _) in calendar.items() if p >= since]
                        if family in MONTHLY_EDITIONS else
                        [(d, stage) for d, (_, stage) in due(GDP_EDITIONS, family=family).items()] if family == "us_gdp"
                        else [(max(calendar), "regular")])
            # An explicit as-of request for EVERY edition preserves the unchanged
            # second GDP estimate that ALFRED's change-only history omits.
            for edition, stage in editions:
                start = since
                if family == "us_gdp":
                    start = f"{since[:4]}-{(int(since[5:]) - 1) // 3 * 3 + 1:02d}"
                elif family in MONTHLY_EDITIONS:
                    start = min(since, shift_month(MONTHLY_EDITIONS[family][edition][0], -2))
                elif family == "us_case_shiller":
                    # Optional as-of redistributor history, never issuer prints
                    # or a BLS revision window. Keep the caller's history cutoff.
                    start = since
                else:
                    latest = max(p for p, _, _ in calendar.values())
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
        if family == "us_case_shiller":
            from .housing_editions import reviewed as housing_reviewed
            headline, _, _ = housing_reviewed(family, edition)
            if stage != "regular" or not numeric_months or max(numeric_months) != headline:
                raise FetchError("FRED Case-Shiller as-of reference period needs review")
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


class BLSFallbackAdapter(FREDAdapter):
    """Edition-bound ALFRED first prints; never a current FRED snapshot.

    Initial-release metadata proves when this particular headline first
    appeared. The transformed as-of response must match that scheduled edition.
    ALFRED only has daily resolution: the BLS calendar supplies the release
    instant, not an invented intraday provider availability timestamp.
    """

    def fetch(self, families, since):
        out = []
        for family in families:
            calendar = due(family_editions(family), family=family)
            for indicator, (mapped_family, series, units) in MAPPINGS.items():
                if mapped_family != family:
                    continue
                for edition, (headline, stage, clock) in sorted(calendar.items()):
                    if headline < since:
                        continue
                    params = dict(api_key=api_key("FRED_API_KEY"), file_type="json",
                                  series_id=series, realtime_start=edition, realtime_end=edition)
                    first_doc = self.client.get("fred", ENDPOINT, dict(params,
                        realtime_start="1776-07-04", output_type=4, units="lin",
                        observation_start=headline + "-01", observation_end=headline + "-01", limit=10000))
                    first = json_payload(first_doc)
                    initial = first.get("observations", [])
                    require_complete(first, initial)
                    if (first.get("output_type") != 4 or first.get("units") != "lin" or
                            first.get("realtime_start") != "1776-07-04" or first.get("realtime_end") != edition or
                            len(initial) != 1 or initial[0].get("date") != headline + "-01" or
                            initial[0].get("value") in (None, ".") or initial[0].get("realtime_start") != edition):
                        raise FetchError("ALFRED vintage date does not match BLS edition; needs review")
                    doc = self.client.get("fred", ENDPOINT, dict(params, units=units,
                        output_type=1, observation_start=headline + "-01", observation_end=headline + "-01", limit=10000))
                    out.extend(self.parse_first_print(doc, indicator=indicator, edition=edition))
        return out

    @staticmethod
    def parse_first_print(document, *, indicator, edition):
        family, series, units = MAPPINGS[indicator]
        calendar = family_editions(family)
        if edition not in due(calendar, family=family):
            raise FetchError("BLS edition is not released; needs review")
        headline, stage, clock = calendar[edition]
        payload = json_payload(document)
        rows = payload.get("observations", [])
        require_complete(payload, rows)
        if (payload.get("output_type") != 1 or payload.get("units") != units or payload.get("realtime_start") != edition or
                payload.get("realtime_end") != edition or len(rows) != 1 or
                rows[0].get("date") != headline + "-01" or rows[0].get("value") in (None, ".") or
                rows[0].get("realtime_start") != edition or rows[0].get("realtime_end") != edition):
            raise FetchError("ALFRED first-print vintage/period does not match BLS edition; needs review")
        row = rows[0]
        return [Observation(indicator, family, headline, float(row["value"]), row["value"],
                            series + ":" + units, "ALFRED", document, edition,
                            **new_york_release(edition, clock), stage=stage,
                            precision="exact", provider_vintage=edition, revision="initial")]


class USRedistributorAdapter(FREDAdapter):
    """ALFRED history plus independently parsed issuer publications.

    FRED values always retain mirror provenance. Only the actual issuer document
    supplies an official value; a rounded match never promotes a mirror.
    """

    def fetch(self, families, since):
        from .us_publications import fetch_publications
        return super().fetch(families, since) + fetch_publications(self.client, families, since)
