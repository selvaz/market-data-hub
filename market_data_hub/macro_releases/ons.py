"""Pinned ONS GDP index version and issuer-published monthly growth.

The four-decimal API contains levels, not the published rounded growth rate.
Keep them separate. Certified archived bulletins supply the published transform;
their HTML extraction is explicit and never labelled API.
"""
from __future__ import annotations

import csv
from datetime import datetime
from html.parser import HTMLParser
from io import StringIO
import re

from .adapters import Observation
from .http import FetchError, json_payload

VERSION = 69
VERSION_URL = ("https://api.beta.ons.gov.uk/v1/datasets/gdp-to-four-decimal-places/"
               "editions/time-series/versions/69")
CSV_URL = "https://static.ons.gov.uk/datasets/gdp-to-four-decimal-places-time-series-v69.csv"
BULLETIN_BASE = "https://www.ons.gov.uk/economy/grossdomesticproductgdp/bulletins/gdpmonthlyestimateuk/"
# Reviewed editions, not current-date arithmetic. Each also covers revised months.
BULLETINS = ("july2026", "august2026")


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
    def __init__(self, client):
        self.client = client

    def fetch(self, families, since):
        if "uk_gdp" not in families:
            return []
        meta = self.client.get("ons", VERSION_URL)
        payload = json_payload(meta)
        if payload.get("version") != VERSION or payload.get("edition") != "time-series":
            raise FetchError("ONS pinned dataset version changed")
        levels = self.client.get("ons", CSV_URL)
        out = self.parse_levels(levels, payload, since)
        for edition in BULLETINS:
            doc = self.client.get("ons", BULLETIN_BASE + edition)
            out.extend(self.parse_bulletin(doc, since))
        return out

    @staticmethod
    def parse_levels(document, metadata, since="1900-01"):
        if metadata.get("version") != VERSION or metadata.get("edition") != "time-series":
            raise FetchError("ONS pinned dataset version changed")
        rows = list(csv.DictReader(StringIO(document.body.decode("utf-8-sig"))))
        if not rows:
            raise FetchError("ONS GDP CSV empty")
        # ONS CSV format is checked against the saved v69 response, not guessed.
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
                                   "gdp-to-four-decimal-places:time-series:69:A--T", "ONS", document,
                                   "time-series:69", metadata["release_date"], provider_vintage="69"))
        if not out:
            raise FetchError("ONS pinned GDP version has no levels in requested window")
        return out

    @staticmethod
    def parse_bulletin(document, since="1900-01"):
        text = document.body.decode("utf-8")
        match = re.search(r'dataLayer\[0\]\["releaseDate"\]\s*=\s*"(\d{4}/\d{2}/\d{2})"', text)
        if not match:
            raise FetchError("ONS bulletin missing publication date")
        edition = match[1].replace("/", "-")
        paragraphs = Paragraphs()
        paragraphs.feed(text)
        summary = next((p for p in paragraphs.paragraphs if p.startswith("Monthly GDP grew by ")), None)
        if summary is None:
            raise FetchError("ONS published monthly GDP paragraph changed")
        # Bound the parser to the monthly-GDP summary, excluding quarterly rates,
        # component growth, and the 'previous publication' figure in parentheses.
        summary = re.sub(r"\([^)]*\)", "", summary)
        values = re.findall(r"(?:grew by|growth of) (-?[\d.]+)% in (\w+ \d{4})", summary)
        if len(values) < 2:
            raise FetchError("ONS monthly GDP revision window absent")
        out = []
        for index, (printed, month) in enumerate(values):
            period = datetime.strptime(month, "%B %Y").strftime("%Y-%m")
            if period >= since:
                out.append(Observation("uk_gdp_mm", "uk_gdp", period, float(printed), printed,
                                       "gdpmonthlyestimateuk:published-mm", "ONS", document,
                                       edition, edition + "T00:00:00Z", method="html",
                                       provider_vintage=document.url.rsplit("/", 1)[-1],
                                       revision="initial" if index == 0 else "routine"))
        return out
