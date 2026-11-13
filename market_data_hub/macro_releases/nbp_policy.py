"""National Bank of Poland reference rate from the issuer's interest-rate table.

NBP publishes its current rates as one XML file (``stopy_procentowe.xml``). The
``ref`` row is the reference rate (minimum money market intervention rate):
the value uses a decimal comma and ``obowiazuje_od`` is the date from which the
Monetary Policy Council decision applies, used here as the decision date. The
table holds only the current rate, so one reading is emitted per run; an
unchanged table re-read adds nothing. A missing or malformed ``ref`` row fails
closed. The publication clock is not in the file (date-only precision).
"""
from __future__ import annotations

from datetime import date
import re
from urllib.parse import urlsplit
from xml.etree import ElementTree

from .adapters import Observation
from .catalogue import NBP_FAMILIES
from .http import FetchError

URL = "https://static.nbp.pl/dane/stopy/stopy_procentowe.xml"
HOST = "static.nbp.pl"
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
RATE = re.compile(r"\d{1,2},\d{1,2}")


def _date(text):
    if not text or not DATE.fullmatch(text):
        raise ValueError()
    return date.fromisoformat(text).isoformat()


def reference_rate(document):
    """(decision date, printed rate, publication date) from the ``ref`` row."""
    parts = urlsplit(document.url)
    if parts.scheme != "https" or parts.hostname != HOST or parts.path != urlsplit(URL).path:
        raise FetchError("NBP issuer origin needs review")
    if b"<!DOCTYPE" in document.body or b"<!ENTITY" in document.body:
        raise FetchError("NBP rate table schema needs review")
    try:
        root = ElementTree.fromstring(document.body)
        if root.tag != "stopy_procentowe":
            raise ValueError()
        published = _date(root.get("data_publikacji"))
        rows = [p for t in root.findall("tabela") if t.get("id") == "stoproc"
                for p in t.findall("pozycja") if p.get("id") == "ref"]
        if len(rows) != 1:
            raise ValueError()
        row = rows[0]
        printed = (row.get("oprocentowanie") or "").strip()
        if not RATE.fullmatch(printed):
            raise ValueError()
        effective = _date(row.get("obowiazuje_od"))
        value = float(printed.replace(",", "."))
        if not 0 <= value <= 100 or effective > published:
            raise ValueError()
    except (ElementTree.ParseError, ValueError, TypeError):
        raise FetchError("NBP reference rate row missing or malformed; needs review") from None
    return effective, printed, published


def parse(document, family="nbp_policy", since="1900-01"):
    effective, printed, published = reference_rate(document)
    if effective[:7] < since:
        return []
    measure = NBP_FAMILIES[family][0]
    value = float(printed.replace(",", "."))
    policy = dict(decision_date=effective, announcement_date=effective,
                  rates={"reference_rate": dict(rate=value)}, vote_split=None,
                  source_url=document.url, published_at=None, publication_precision="date_only",
                  time_missing_reason="NBP rate table prints the effective date only; no publication clock",
                  supporting_sources=[], table_published=published)
    return [Observation(measure.id, family, effective, value, printed, measure.id + ":ref",
                        measure.issuer_id, document, effective, effective + "T00:00:00Z",
                        precision="date_only", method="api", provider_vintage=published,
                        policy_decision=policy)]


class NBPAdapter:
    def __init__(self, client):
        self.client = client

    def fetch(self, families, since):
        out = []
        for family in families:
            if family in NBP_FAMILIES:
                out.extend(parse(self.client.get("nbp", URL), family, since))
        return out
