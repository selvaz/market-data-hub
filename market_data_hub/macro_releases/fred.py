"""FRED/ALFRED is a redistributor, even when it matches a current issuer print."""
from __future__ import annotations

from dataclasses import replace

from .adapters import Observation
from .bea import EDITIONS as GDP_EDITIONS
from .bls import EDITIONS as BLS_EDITIONS
from .bls import revision_start
from .http import FetchError, api_key, json_payload, require_complete

ENDPOINT = "https://api.stlouisfed.org/fred/series/observations"
MAPPINGS = {
    "us_cpi_headline_mm": ("us_cpi", "CPIAUCSL", "pch"),
    "us_cpi_headline_yy": ("us_cpi", "CPIAUCNS", "pc1"),
    "us_cpi_core_mm": ("us_cpi", "CPILFESL", "pch"),
    "us_cpi_core_yy": ("us_cpi", "CPILFENS", "pc1"),
    "us_payrolls_change": ("us_payrolls", "PAYEMS", "chg"),
    "us_gdp_qq_saar": ("us_gdp", "A191RL1Q225SBEA", "lin"),
}


class FREDAdapter:
    def __init__(self, client):
        self.client = client

    def fetch(self, families, since):
        out = []
        for indicator, (family, series, units) in MAPPINGS.items():
            if family not in families:
                continue
            editions = ([(d, stage) for d, (_, stage) in GDP_EDITIONS.items()] if family == "us_gdp"
                        else [(max(d for (f, _), d in BLS_EDITIONS.items() if f == family), "regular")])
            # An explicit as-of request for EVERY edition preserves the unchanged
            # second GDP estimate that ALFRED's change-only history omits.
            for edition, stage in editions:
                start = since
                if family == "us_gdp":
                    start = f"{since[:4]}-{(int(since[5:]) - 1) // 3 * 3 + 1:02d}"
                else:
                    latest = max(p for (f, p) in BLS_EDITIONS if f == family)
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
        payload = json_payload(document)
        if "error_code" in payload or payload.get("units") != units:
            raise FetchError("FRED error or unexpected units")
        if payload.get("realtime_start") != edition or payload.get("realtime_end") != edition:
            raise FetchError("FRED response is not the requested publication as-of")
        rows = payload.get("observations", [])
        require_complete(payload, rows)
        out = []
        for row in rows:
            if row["value"] == ".":
                continue
            month = row["date"][:7]
            period = month if family != "us_gdp" else f"{month[:4]}-Q{(int(month[5:]) - 1) // 3 + 1}"
            end = month if family != "us_gdp" else f"{month[:4]}-{int(period[-1]) * 3:02d}"
            if end < since:
                continue
            out.append(Observation(indicator, family, period, float(row["value"]), row["value"],
                                   series + ":" + units, "FRED", document, edition,
                                   edition + "T00:00:00Z", stage=stage,
                                   provider_vintage=row["realtime_start"], revision="provider_history"))
            if family == "us_gdp" and period != GDP_EDITIONS[edition][0]:
                out[-1] = replace(out[-1], stage="regular", event_stage=stage)
        return out
