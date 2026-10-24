"""ONS published rates. Labour reference months explicitly mean window ending."""
from datetime import datetime
import re
from zoneinfo import ZoneInfo

from .adapters import Observation
from .bls import shift_month
from .http import FetchError, json_payload
from .publication_times import local_release

# https://www.ons.gov.uk/releasecalendar?keywords=consumer+price+inflation&release-type=type-upcoming
# https://www.ons.gov.uk/releasecalendar?keywords=labour+market&release-type=type-upcoming
# edition -> (headline ending month, stage, Europe/London local time)
EDITIONS = {
    "uk_cpi": {"2026-09-16": ("2026-08", "regular", "07:00"),
               "2026-10-21": ("2026-09", "regular", "07:00"),
               "2026-11-18": ("2026-10", "regular", "07:00"),
               "2026-12-16": ("2026-11", "regular", "07:00")},
    "uk_labour_market": {"2026-09-15": ("2026-07", "regular", "07:00"),
                         "2026-10-20": ("2026-08", "regular", "07:00"),
                         "2026-11-17": ("2026-09", "regular", "07:00"),
                         "2026-12-15": ("2026-10", "regular", "07:00")},
}
SERIES = {
    "D7G7": ("uk_cpi", "uk_cpi_yy", "MM23", "CPI ANNUAL RATE 00: ALL ITEMS 2015=100",
             "/economy/inflationandpriceindices/timeseries/d7g7/mm23"),
    "DKO8": ("uk_cpi", "uk_core_cpi_yy", "MM23", "CPI 12mth: Excluding Energy, food, alcoholic beverages & tobacco 2015=100",
             "/economy/inflationandpriceindices/timeseries/dko8/mm23"),
    "MGSX": ("uk_labour_market", "uk_unemployment_rate", "LMS", "Unemployment rate (aged 16 and over, seasonally adjusted): %",
             "/employmentandlabourmarket/peoplenotinwork/unemployment/timeseries/mgsx/lms"),
    "KAI9": ("uk_labour_market", "uk_regular_pay_yy", "LMS", "AWE: Whole Economy Year on Year Three Month Average Growth (%): Seasonally Adjusted Regular Pay Excluding Arrears",
             "/employmentandlabourmarket/peopleinwork/earningsandworkinghours/timeseries/kai9/lms"),
}


def period(row, cdid):
    result = datetime.strptime(row["date"], "%Y %b").strftime("%Y-%m")
    if cdid == "MGSX":
        result = shift_month(result, 1)  # ONS date is the middle of its printed window.
        first = datetime.strptime(shift_month(result, -2), "%Y-%m").strftime("%b").upper()
        last = datetime.strptime(result, "%Y-%m").strftime("%b").upper()
        if row.get("label") != shift_month(result, -2)[:4] + " " + first + "-" + last:
            raise FetchError("ONS three-month reference window needs review")
    return result


def parse_rate(document, cdid, since="1900-01"):
    family, indicator, dataset, title, path = SERIES[cdid]
    payload = json_payload(document)
    meta = payload.get("description", {})
    if (payload.get("type") != "timeseries" or payload.get("uri") != path or
            meta.get("cdid") != cdid or meta.get("datasetId") != dataset or
            meta.get("unit") != "%" or meta.get("title") != title):
        raise FetchError("ONS series identity, definition or units needs review")
    # releaseDate is local midnight serialized in UTC, not the 07:00 publication.
    edition = datetime.fromisoformat(meta["releaseDate"].replace("Z", "+00:00")).astimezone(ZoneInfo("Europe/London")).date().isoformat()
    if edition not in EDITIONS[family]:
        raise FetchError("ONS edition metadata needs review")
    headline, stage, time = EDITIONS[family][edition]
    rows = payload.get("months", [])
    if not rows or max(period(r, cdid) for r in rows) != headline:
        raise FetchError("ONS headline period needs review")
    if cdid == "MGSX" and meta.get("monthLabelStyle") != "three month average":
        raise FetchError("ONS unemployment window definition needs review")
    if cdid == "KAI9" and meta.get("keyNote") != "Year on year 3 months average":
        raise FetchError("ONS earnings window definition needs review")
    versions = payload.get("versions", [])
    if not versions or versions[-1].get("updateDate", "")[:10] != edition:
        raise FetchError("ONS edition/version association needs review")
    out = []
    for row in rows:
        ref = period(row, cdid)
        if ref < since:
            continue
        printed = row["value"]
        if row.get("sourceDataset") != dataset or not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", printed):
            raise FetchError("ONS published rate format needs review")
        out.append(Observation(indicator, family, ref, float(printed), printed, f"{dataset}:{cdid}",
            "ONS", document, edition, **local_release(edition, time, "Europe/London"),
            precision="exact", stage=stage, provider_vintage=versions[-1]["label"],
            revision="initial" if ref == headline else "routine"))
    return out


def fetch_rates(client, families, since):
    out = []
    for cdid, (family, _, _, _, path) in SERIES.items():
        if family in families:
            out += parse_rate(client.get("ons", "https://www.ons.gov.uk" + path + "/data"), cdid, since)
    editions = {o.edition for o in out}
    if len(set(families) & set(EDITIONS)) == 1 and len(editions) != 1:
        raise FetchError("ONS constituent series editions needs review")
    return out
