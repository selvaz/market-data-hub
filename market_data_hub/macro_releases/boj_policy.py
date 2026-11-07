"""BOJ policy-board statements; meeting hours are never publication times."""
from datetime import datetime
from email.utils import parsedate_to_datetime
import re
from urllib.parse import urlsplit
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from .http import FetchError
from .policy import change, due_editions, observation, require_origin, reviewed
from .policy_editions import EDITIONS
from .tier1_publications import publication_text, require

HOST = "https://www.boj.or.jp"
LISTING = HOST + "/en/mopo/mpmdeci/mpr_2026/index.htm"
RSS = HOST + "/en/rss/whatsnew.xml"
BASELINE = "2025-12-19"
BASELINE_URL = HOST + "/en/mopo/mpmdeci/mpr_2025/k251219a.pdf"
TIME_GAP = "Issuer statement and meeting calendar publish a date only; no matching issuer RSS publication timestamp is available. Meeting end time is not a release time."


def previous_edition(edition):
    return max(d for d in (BASELINE, *EDITIONS["boj_policy"]) if d < edition)


def statement(document):
    require_origin(document, "www.boj.or.jp")
    text, method = publication_text(document)
    text = re.sub(r"\b(20\d)\s+(\d)\b", r"\1\2", text)
    date = require(r"([A-Z][a-z]+ \d{1,2})\s*,\s*(20\d{2})\s+Bank of Japan", text)
    edition = datetime.strptime(date[1] + ", " + date[2], "%B %d, %Y").date().isoformat()
    require(r"Statement on Monetary Policy|Change in the Guideline for Money Market Operations", text)
    votes = r"(?:an? (\d+)-(\d+) majority vote|a unanimous vote)"
    if re.search(r"1\. Guideline for money market operations", text):
        # October's multi-decision statement puts the rate vote in heading 1.
        # Heading 2 has a separate ETF vote; dissenting proposals follow later.
        text = require(r"1\. Guideline for money market operations (.*?) 2\. ", text)[1]
        vote = require(r"^\(" + votes + r"\) \[Note\] At the Monetary Policy Meeting \(MPM\) held today, "
                       r"the Policy Board of the Bank of Japan decided to set the following guideline", text)
    else:
        vote = require(r"Policy Board of the Bank of Japan decided, by " + votes + r", "
                       r"to set the following guideline", text)
    rate = require(r"The Bank will encourage the uncollateralized o\s*vernight ca\s*ll rate to remain at around (\d+(?:\.\s*\d+)?) percent", text)
    return edition, float(rate[1].replace(" ", "")), {"for": int(vote[1] or 9), "against": int(vote[2] or 0)}, method


def release_clock(document, feed, edition):
    """Only a matching issuer RSS item can establish the variable release clock."""
    if feed is None:
        return None
    require_origin(feed, "www.boj.or.jp")
    matches = []
    for item in ElementTree.fromstring(feed.body).findall(".//item"):
        url = urlsplit(item.findtext("link", ""))
        if url.hostname == "www.boj.or.jp" and url.path == urlsplit(document.url).path:
            stamp = parsedate_to_datetime(item.findtext("pubDate", ""))
            if stamp.tzinfo is None:
                raise FetchError("BOJ RSS publication timezone needs review")
            local = stamp.astimezone(ZoneInfo("Asia/Tokyo"))
            if local.date().isoformat() != edition:
                raise FetchError("BOJ RSS/statement publication date needs review")
            matches.append(local.strftime("%H:%M:%S"))
    if len(set(matches)) > 1:
        raise FetchError("BOJ RSS publication timestamp needs review")
    return matches[0] if matches else None


def parse(document, previous, family="boj_policy", since="1900-01", *, feed=None):
    edition, value, votes, method = statement(document)
    reviewed(family, edition)
    prior, old, _, _ = statement(previous)
    if prior != previous_edition(edition):
        raise FetchError("BOJ consecutive issuer baseline needs review")
    rates = {"overnight_call_rate": dict(rate=value, change_bp=change(value, old), change_basis="consecutive_issuer_statements")}
    evidence = [dict(source_url=previous.url, decision_date=prior, rate=old)]
    clock = release_clock(document, feed, edition)
    if clock:
        evidence.append(dict(source_url=feed.url, evidence="issuer_publication_timestamp",
                             retrieved_at=feed.retrieved_at, content_hash=feed.document_hash))
    return [observation(document, family, edition, rates, votes, evidence=evidence, method=method,
                        time=clock, time_missing_reason=None if clock else TIME_GAP)] if edition[:7] >= since else []


def fetch(client, family, since):
    listing = client.get("boj", LISTING)
    indexed = {BASELINE: BASELINE_URL}
    for path, digits in re.findall(rb'href="(/en/mopo/mpmdeci/(?:mpr|state)_2026/k(26\d{4})a\.(?:pdf|htm))"', listing.body):
        edition = datetime.strptime(digits.decode(), "%y%m%d").date().isoformat()
        reviewed(family, edition)
        indexed[edition] = HOST + path.decode()
    feed = client.get("boj", RSS)
    documents = {}
    def get(edition):
        if edition not in indexed:
            raise FetchError("Due BOJ statement/baseline absent from issuer listing; needs review")
        if edition not in documents:
            documents[edition] = client.get("boj", indexed[edition])
        return documents[edition]
    out = []
    for edition in due_editions(family, since):
        rows = parse(get(edition), get(previous_edition(edition)), family, since, feed=feed)
        if {o.edition for o in rows} != {edition}:
            raise FetchError("BOJ requested statement edition needs review")
        out += rows
    return out
