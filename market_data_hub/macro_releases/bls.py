"""BLS v2 CPI rates and CES net changes supplied by BLS calculations."""
from __future__ import annotations

from datetime import date, datetime
import re

from .adapters import Observation
from .catalogue import BLS_SERIES
from .http import FetchError, api_key, json_payload

ENDPOINT = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
# The API has no publication timestamp/edition field. Reviewed release metadata,
# never a guess from collection time or reference period. New releases fail closed
# until their issuer publication metadata has been reviewed.
EDITIONS = {
    ("us_cpi", "2026-09"): "2026-10-14",
    ("us_payrolls", "2026-09"): "2026-10-02",
}


def shift_month(period, offset):
    y, m = map(int, period.split("-"))
    y, m = divmod(y * 12 + m - 1 + offset, 12)
    return f"{y:04d}-{m + 1:02d}"


def revision_start(family, newest, since):
    # Annual SA review covers five complete calendar years, plus the current
    # year. A rolling 60-month cutoff would miss the first months of year five.
    window = shift_month(newest, -2) if family == "us_payrolls" else f"{int(newest[:4]) - 5:04d}-01"
    return min(since, window)


class BLSAdapter:
    def __init__(self, client):
        self.client = client

    def fetch(self, families, since):
        series = [key for key, (indicator, _, _) in BLS_SERIES.items()
                  if ("us_payrolls" if indicator.startswith("us_payrolls") else "us_cpi") in families]
        if not series:
            return []
        # One batch, well below BLS's 50-series ceiling. Inspect the SA revision
        # window and include calculation lookbacks, independent of output cutoff.
        current_year = date.today().year
        startyear = min(int(since[:4]) - 1, current_year - 5)
        if current_year - startyear >= 20:
            raise FetchError("BLS request exceeds 20-year window; narrow --since")
        document = self.client.request("bls", ENDPOINT, payload={
            "seriesid": series, "startyear": str(startyear), "endyear": str(current_year),
            "calculations": True, "catalog": True, "registrationkey": api_key("BLS_API_KEY"),
        })
        return self.parse(document, families, since)

    def parse(self, document, families, since):
        payload = json_payload(document)
        if payload.get("status") != "REQUEST_SUCCEEDED":
            raise FetchError("BLS rejected request")
        series = {s["seriesID"]: s for s in payload.get("Results", {}).get("series", [])}
        observations = []
        for key, (indicator, calc, lag) in BLS_SERIES.items():
            family = "us_payrolls" if indicator.startswith("us_payrolls") else "us_cpi"
            if family not in families:
                continue
            rows = series.get(key, {}).get("data", [])
            rows = [r for r in rows if re.fullmatch(r"M(0[1-9]|1[0-2])", r["period"])]
            if not rows:
                raise FetchError(f"BLS series empty or absent: {key}")
            newest = max(r["year"] + "-" + r["period"][1:] for r in rows)
            edition = EDITIONS.get((family, newest))
            if edition is None:
                raise FetchError(f"BLS publication metadata needs review: {family} {newest}")
            cutoff = revision_start(family, newest, since)
            by_period = {r["year"] + "-" + r["period"][1:]: r for r in rows}
            for row in rows:
                period = row["year"] + "-" + row["period"][1:]
                if period < cutoff:
                    continue
                value = row.get("calculations", {}).get(calc, {}).get(lag)
                if value in (None, "", "-"):
                    base = by_period.get(shift_month(period, -int(lag)), {})
                    if row["value"] != "-" and base.get("value") != "-":
                        raise FetchError(f"BLS published calculation absent: {key} {period}")
                observations.append(Observation(
                    indicator, family, period, float(value) if value not in (None, "", "-") else None,
                    value or f"not published: {calc}/{lag}", key + ":" + calc + ":" + lag,
                    "BLS", document, edition, edition + "T12:30:00Z", precision="exact",
                    revision="initial" if period == newest else "routine",
                ))
        return observations

    @staticmethod
    def parse_payroll_publication(document, edition, headline_period):
        """Deterministic archived issuer text, an independent certification oracle.

        Historical text is never passed off as a v2 response. The current API
        cannot recover old vintages. Use this only with saved issuer publications.
        """
        text = document.body.decode("utf-8")
        if text.startswith('"'):
            text = document.json()  # Saved browser/web fetch response, labelled as such.
        text = re.sub(r"L\d+:\s*", "", text)
        text = re.sub(r"\s+", " ", text)
        release_date = re.search(r"8:30 a\.m\. \(ET\) \w+, (\w+ \d+, \d{4})", text)
        release_month = re.search(r"THE EMPLOYMENT SITUATION - ([A-Z]+ \d{4})", text)
        if release_date is None or release_month is None:
            raise FetchError("BLS archive publication metadata absent")
        if (datetime.strptime(release_date[1], "%B %d, %Y").date().isoformat() != edition or
                datetime.strptime(release_month[1].title(), "%B %Y").strftime("%Y-%m") != headline_period):
            raise FetchError("BLS archive does not match requested edition/period")
        first = (re.search(r"Total nonfarm payroll employment (?:increased|rose) by ([\d,]+)", text)
                 or re.search(r"Both nonfarm payroll employment \(([+-]?[\d,]+)\)", text))
        if not first:
            raise FetchError("BLS archive headline did not match certified format")
        out = [Observation("us_payrolls_change", "us_payrolls", headline_period,
                           float(first[1].replace(",", "")) / 1000, first[1],
                           "CES0000000001:archive-change", "BLS", document,
                           edition, edition + "T12:30:00Z", method="html", precision="exact")]
        months = {"January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
                  "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12}
        for match in re.finditer(r"(?:change in total nonfarm payroll employment for|change for) "
                                r"(\w+) was revised (?:up|down) by [\d,]+, from [+-]?[\d,]+ to ([+-]?[\d,]+)", text):
            month = months.get(match[1])
            if month is None:
                raise FetchError("BLS archive month unknown")
            year = int(headline_period[:4]) - (month > int(headline_period[5:]))
            out.append(Observation("us_payrolls_change", "us_payrolls", f"{year:04d}-{month:02d}",
                                   float(match[2].replace(",", "")) / 1000, match[2],
                                   "CES0000000001:archive-change", "BLS", document,
                                   edition, edition + "T12:30:00Z", revision="routine",
                                   method="html", precision="exact"))
        return out
