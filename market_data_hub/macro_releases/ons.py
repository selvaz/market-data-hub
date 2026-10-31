"""ONS ECY2 monthly index and the separately published bulletin growth rate."""
from __future__ import annotations

import csv
from datetime import datetime
from html.parser import HTMLParser
from io import StringIO
import re
from zoneinfo import ZoneInfo

from .adapters import Observation
from .http import FetchError, json_payload
from .ons_rates import fetch_rates, parse_rate
from .models import utc

# Archive-only identities/parser retained for replaying existing v69 evidence.
VERSION = 69
CSV_URL = "https://static.ons.gov.uk/datasets/gdp-to-four-decimal-places-time-series-v69.csv"
BULLETIN_BASE = "https://www.ons.gov.uk/economy/grossdomesticproductgdp/bulletins/gdpmonthlyestimateuk/"
# Reviewed editions, not current-date arithmetic. Each also covers revised months.
BULLETINS = ("july2026", "august2026")
BULLETIN_INDEX_URL = BULLETIN_BASE + "previousreleases"
INDEX_PATH = "/economy/grossdomesticproductgdp/timeseries/ecy2/mgdp"
INDEX_URL = "https://www.ons.gov.uk" + INDEX_PATH + "/data"
INDEX_KEY = "MGDP:ECY2:index-1dp"
INDEX_PARSER = "official-adapters:ons-ecy2-v1"


def bulletin_date(document):
    match = re.search(r'dataLayer\[0\]\["releaseDate"\]\s*=\s*"(\d{4}/\d{2}/\d{2})"',
                      document.body.decode("utf-8"))
    if not match:
        raise FetchError("ONS bulletin missing publication date")
    return match[1].replace("/", "-")


def check_freshness(bulletins):
    slugs = re.findall(r'/gdpmonthlyestimateuk/([a-z]+\d{4})(?:["?#/])',
                       bulletins.body.decode("utf-8"))
    try:
        latest = max(datetime.strptime(slug, "%B%Y") for slug in slugs)
    except ValueError as exc:
        raise FetchError("ONS bulletin index format needs review") from exc
    if latest != datetime.strptime(BULLETINS[-1], "%B%Y"):
        raise FetchError("ONS latest bulletin needs review")


class Paragraphs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paragraphs = []
        self.text = None

    def handle_starttag(self, tag, attrs):
        if tag == "p":
            self.text = ""

    def handle_data(self, text):
        if self.text is not None:
            self.text += text

    def handle_endtag(self, tag):
        if tag == "p" and self.text is not None:
            self.paragraphs.append(self.text)
            self.text = None




class ONSAdapter:
    parse_rate = staticmethod(parse_rate)

    def __init__(self, client):
        self.client = client

    def fetch(self, families, since):
        rates = fetch_rates(self.client, families, since)
        if "uk_gdp" not in families:
            return rates
        check_freshness(self.client.get("ons", BULLETIN_INDEX_URL))
        out = self.parse_index(self.client.get("ons", INDEX_URL), since)
        for edition in BULLETINS:
            doc = self.client.get("ons", BULLETIN_BASE + edition)
            out.extend(self.parse_bulletin(doc, since))
        return rates + out

    @staticmethod
    def parse_index(document, since="1900-01"):
        payload = json_payload(document)
        try:
            meta = payload["description"]
            if (payload["type"] != "timeseries" or payload["uri"] != INDEX_PATH or
                    meta["cdid"] != "ECY2" or meta["datasetId"] != "MGDP" or
                    meta["title"] != "Gross Value Added - Monthly (Index 1dp) :CVM SA" or
                    meta["unit"] != ""):
                raise ValueError("series identity")
            rows = payload["months"]
            if not isinstance(rows, list) or not rows:
                raise ValueError("months")
            out, periods = [], set()
            for row in rows:
                period = datetime.strptime(row["date"], "%Y %b").strftime("%Y-%m")
                printed = row["value"]
                if (row["sourceDataset"] != "MGDP" or period in periods or
                        not re.fullmatch(r"\d+\.\d", printed)):
                    raise ValueError("monthly series")
                periods.add(period)
                stamp = datetime.fromisoformat(row["updateDate"].replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    raise ValueError("updateDate timezone")
                edition = stamp.astimezone(ZoneInfo("Europe/London")).date().isoformat()
                if period >= since:
                    out.append(Observation("uk_gdp_index", "uk_gdp", period, float(printed), printed,
                        INDEX_KEY, "ONS", document, edition, edition + "T00:00:00Z",
                        provider_vintage=edition, revision="routine", method="json", parser_version=INDEX_PARSER))
            return out
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise FetchError("ONS ECY2 series identity or monthly JSON shape changed") from exc

    @staticmethod
    def parse_levels(document, metadata, since="1900-01", *, publication=None, version=VERSION):
        if metadata.get("version") != version or metadata.get("edition") != "time-series":
            raise FetchError("ONS pinned dataset version changed")
        rows = list(csv.DictReader(StringIO(document.body.decode("utf-8-sig"))))
        if not rows:
            raise FetchError("ONS GDP CSV empty")
        # The dataset has its own publication, distinct from the monthly GDP
        # bulletin and from last_updated. Midnight certifies only a date.
        try:
            published_at = utc(metadata["release_date"])
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise FetchError("ONS index dataset release_date missing or invalid; not tier-1-resolvable") from exc
        edition = published_at[:10]
        out = []
        for row in rows:
            if row.get("sic-unofficial") != "A--T":
                continue
            if row.get("uk-only") != "K02000001":
                raise FetchError("ONS geography changed")
            period = datetime.strptime(row["mmm-yy"], "%b-%y").strftime("%Y-%m")
            if period < since:
                continue
            out.append(Observation("uk_gdp_index", "uk_gdp", period, float(row["v4_0"]), row["v4_0"],
                                   f"gdp-to-four-decimal-places:time-series:{version}:A--T", "ONS", document,
                                   edition, published_at, provider_vintage=str(version), revision="routine"))
        if not out:
            raise FetchError("ONS pinned GDP version has no levels in requested window")
        return out

    @staticmethod
    def parse_bulletin(document, since="1900-01"):
        text = document.body.decode("utf-8")
        edition = bulletin_date(document)
        paragraphs = Paragraphs()
        paragraphs.feed(text)
        summary = next((p for p in paragraphs.paragraphs if re.match(
            r"Monthly (?:real gross domestic product \(GDP\)|GDP) (?:grew|fell|showed|was|is estimated to have)", p)), None)
        if summary is None:
            raise FetchError("ONS published monthly GDP paragraph changed")
        # Bound the parser to the monthly-GDP summary, excluding quarterly rates,
        # component growth, and the 'previous publication' figure in parentheses.
        summary = re.sub(r"\([^)]*\)", "", summary)
        values = [(m.start(), m[1], m[2], m[3]) for m in re.finditer(
            r"(grew by|grown by|growth of|fell by|fallen by|fall of) (-?[\d.]+)% in (\w+ \d{4})", summary)]
        values.extend((m.start(), "growth of", "0", m[1]) for m in re.finditer(
            r"(?:no growth|flat) in (\w+ \d{4})", summary))
        values.sort()
        if len(values) < 2:
            raise FetchError("ONS monthly GDP revision window absent")
        out = []
        for index, (_, direction, printed, month) in enumerate(values):
            period = datetime.strptime(month, "%B %Y").strftime("%Y-%m")
            if period >= since:
                numeric = -abs(float(printed)) if direction in ("fell by", "fallen by", "fall of") else float(printed)
                out.append(Observation("uk_gdp_mm", "uk_gdp", period, numeric, str(numeric),
                                       "gdpmonthlyestimateuk:published-mm", "ONS", document,
                                       edition, edition + "T00:00:00Z", method="html",
                                       provider_vintage=document.url.rsplit("/", 1)[-1],
                                       revision="initial" if index == 0 else "routine"))
        return out
