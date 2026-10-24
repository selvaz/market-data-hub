"""DOL's stable weekly claims PDF: initial SA claims, in thousands of claims."""
from datetime import date, datetime, timedelta
from io import BytesIO
import re

from .adapters import Observation
from .http import FetchError
from .publication_times import new_york_release
from .schedule import due

LATEST = "https://www.dol.gov/ui/data.pdf"
SCHEDULE = "https://oui.doleta.gov/unemploy/claims_arch.asp"
# https://oui.doleta.gov/unemploy/claims_arch.asp
# Thursday 08:30 ET except Thanksgiving week: Wednesday November 25.
# edition -> (Saturday week ending, event stage, New York local time)
EDITIONS = {
    "2026-10-01": ("2026-09-26", "advance", "08:30"),
    "2026-10-08": ("2026-10-03", "advance", "08:30"),
    "2026-10-15": ("2026-10-10", "advance", "08:30"),
    "2026-10-22": ("2026-10-17", "advance", "08:30"),
    "2026-10-29": ("2026-10-24", "advance", "08:30"),
    "2026-11-05": ("2026-10-31", "advance", "08:30"),
    "2026-11-12": ("2026-11-07", "advance", "08:30"),
    "2026-11-19": ("2026-11-14", "advance", "08:30"),
    "2026-11-25": ("2026-11-21", "advance", "08:30"),
    "2026-12-03": ("2026-11-28", "advance", "08:30"),
    "2026-12-10": ("2026-12-05", "advance", "08:30"),
    "2026-12-17": ("2026-12-12", "advance", "08:30"),
    "2026-12-24": ("2026-12-19", "advance", "08:30"),
    "2026-12-31": ("2026-12-26", "advance", "08:30"),
}


def archive_url(edition):
    stamp = datetime.strptime(edition, "%Y-%m-%d")
    return f"https://oui.doleta.gov/press/{stamp.year}/{stamp:%m%d%y}.pdf"


def parse_text(text, document, since="1900-01"):
    """Parse only the initial-claims paragraph, excluding insured/NSA/4-week data."""
    body = re.sub(r"\s+", " ", text)
    stamp = re.search(r"8:30 A\.M\. \(Eastern\) (?:Wednesday|Thursday), (\w+ \d{1,2}, \d{4})", body)
    if not stamp or "UNEMPLOYMENT INSURANCE WEEKLY CLAIMS SEASONALLY ADJUSTED DATA" not in body:
        raise FetchError("DOL claims publication identity or time needs review")
    edition = datetime.strptime(stamp[1], "%B %d, %Y").date().isoformat()
    if edition not in EDITIONS:
        raise FetchError("DOL claims edition needs review")
    head, stage, time = EDITIONS[edition]
    match = re.search(r"In the week ending (\w+ \d{1,2}), the advance figure for seasonally adjusted initial claims was ([\d,]+),(.*?)(?:The 4\s*-\s*week moving average)", body)
    if not match:
        raise FetchError("DOL initial claims paragraph needs review")
    expected = datetime.strptime(head, "%Y-%m-%d").strftime("%B %d").replace(" 0", " ")
    if match[1] != expected or date.fromisoformat(head).weekday() != 5:
        raise FetchError("DOL week ending date needs review")
    previous = (date.fromisoformat(head) - timedelta(days=7)).isoformat()
    revision = re.search(r"The previous week's level was revised (?:up|down) by [\d,]+ from [\d,]+ to ([\d,]+)\.", match[3])
    unchanged = re.search(r"(?:previous week's (?:unrevised|revised) level of|The previous week's level was unrevised at) ([\d,]+)", match[3])
    if not revision and not unchanged:
        raise FetchError("DOL prior-week initial claims level needs review")
    out = []
    for period, printed, estimate, kind in ((head, match[2], stage, "initial"),
            (previous, (revision or unchanged)[1], "revised", "routine")):
        if period < since:
            continue
        out.append(Observation("us_initial_jobless_claims", "us_jobless_claims", period,
            float(printed.replace(",", "")) / 1000, printed, "ETA:initial-claims:SA", "DOL", document,
            edition, **new_york_release(edition, time), precision="exact", stage=estimate,
            event_stage=stage, revision=kind, provider_vintage=edition, method="pdf_table"))
    return out


class DOLAdapter:
    def __init__(self, client):
        self.client = client

    @staticmethod
    def parse(document, since="1900-01"):
        from pypdf import PdfReader
        if not document.body.startswith(b"%PDF"):
            raise FetchError("DOL claims PDF format needs review")
        try:
            text = PdfReader(BytesIO(document.body)).pages[0].extract_text()
        except Exception:
            raise FetchError("DOL claims PDF extraction needs review") from None
        return parse_text(text, document, since)

    def fetch(self, families, since):
        if "us_jobless_claims" not in families:
            return []
        editions = due(EDITIONS)
        out = self.parse(self.client.get("dol", LATEST), since)
        if not out or {o.edition for o in out} != {max(editions, default=None)}:
            raise FetchError("DOL latest scheduled edition missing; needs review")
        latest = out[0].edition
        for edition, (period, _, _) in editions.items():
            if edition != latest and period >= since:
                parsed = self.parse(self.client.get("dol", archive_url(edition)), since)
                if {o.edition for o in parsed} != {edition}:
                    raise FetchError("DOL archive edition needs review")
                out += parsed
        return out
