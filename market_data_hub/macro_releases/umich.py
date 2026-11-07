"""Public issuer headline and public dated reports; no delayed FRED substitute."""
from datetime import datetime

from .http import FetchError
from .schedule import current_edition_allowed, due, run_date
from .tier1_editions import EDITIONS
from .tier1_publications import month, observation, publication_text, require, reviewed

URL = "https://www.sca.isr.umich.edu/"
ARCHIVES = {
    "2026-09-11": "https://data.sca.isr.umich.edu/fetchdoc.php?docid=81974",
    "2026-09-25": "https://data.sca.isr.umich.edu/fetchdoc.php?docid=82114",
}


def parse(document, family="us_umich_sentiment", since="1900-01"):
    body, method = publication_text(document)
    if method == "pdf_table":
        header = require(r"(Preliminary results from the (\w+ \d{4}) survey|(?:Final results from the )?(\w+ \d{4}) survey results) (\w+ \d{1,2}, \d{4})", body)
        stage = "preliminary" if header[2] else "final"
        head = datetime.strptime(header[2] or header[3], "%B %Y").strftime("%Y-%m")
        edition = datetime.strptime(header[4], "%B %d, %Y").date().isoformat()
        if reviewed(family, edition)[:2] != (head, stage):
            raise FetchError("Michigan report edition/period/stage needs review")
        numbers = require(r"Index of Consumer Sentiment ((?:[\d.]+\s+){12}[\d.]+) Current Economic Conditions", body)[1].split()
        printed = numbers[-1]
    else:
        header = require(r"(Preliminary|Final) Results for (\w+ \d{4})", body)
        head = datetime.strptime(header[2], "%B %Y").strftime("%Y-%m")
        stage = header[1].lower()
        candidates = [d for d, meta in EDITIONS[family].items() if meta[:2] == (head, stage)]
        if len(candidates) != 1 or candidates[0] > run_date():
            raise FetchError("Michigan public headline edition needs review")
        edition = candidates[0]
        # The headline has no publication date. Bind title/stage to the issuer
        # calendar AND its independently printed next-release date and stage.
        next_editions = dict(EDITIONS[family], **{"2027-01-08": ("2027-01", "preliminary", "10:00")})
        # https://data.sca.isr.umich.edu/fetchdoc.php?docid=81621 (2027 calendar)
        future = sorted(d for d in next_editions if d > edition)
        if not future:
            raise FetchError("Michigan next edition metadata needs review")
        next_date = future[0]
        next_head, next_stage, _ = next_editions[next_date]
        expected = datetime.strptime(next_date, "%Y-%m-%d")
        next_match = require(r"Next data release: (\w+), (\w+) (\d{1,2}), (\d{4}) for (Preliminary|Final) (\w+) data at 10am ET", body)
        if (datetime.strptime(" ".join((next_match[2], next_match[3], next_match[4])), "%B %d %Y").date().isoformat() != next_date or
                next_match[1] != expected.strftime("%A") or next_match[5].lower() != next_stage or
                next_match[6] != month(next_head).split()[0]):
            raise FetchError("Michigan next-release association needs review")
        printed = require(r"Index of Consumer Sentiment ([\d.]+) [\d.]+ [\d.]+ [+-]?[\d.]+% [+-]?[\d.]+%", body)[1]
    return [observation(document, family, edition, "us_umich_consumer_sentiment", head, printed, method)] if head >= since else []


def fetch(client, family, since):
    out = []
    for edition, url in ARCHIVES.items():
        if edition in due(EDITIONS[family], family=family) and EDITIONS[family][edition][0] >= since:
            rows = parse(client.get("umich", url), family, since)
            if not rows or {o.edition for o in rows} != {edition}:
                raise FetchError("Michigan requested archive edition needs review")
            out += rows
    headline = parse(client.get("umich", URL), family, since)
    if headline and current_edition_allowed(family, headline[0].edition):
        out += headline
    return out
