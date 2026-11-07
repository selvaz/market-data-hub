"""DG ECFIN flash CCI and final BCS are distinct dated issuer editions."""
from datetime import datetime
from contextvars import ContextVar
from html import unescape
from io import BytesIO
import re
from urllib.parse import urljoin

from .bls import shift_month
from .http import FetchError
from .schedule import due, get_edition, mark_pending, publication_grace
from .tier1_editions import EDITIONS
from .tier1_publications import month, observation, publication_text, require, reviewed

LATEST = "https://economy-finance.ec.europa.eu/economic-forecast-and-surveys/business-and-consumer-surveys/latest-business-and-consumer-surveys_en"
BASE = "https://economy-finance.ec.europa.eu"
STORED_EDITIONS = ContextVar("ecfin_stored_official_editions", default=frozenset())
ARCHIVES = {
    "2026-09-29": (
        BASE + "/document/download/60c46029-7f60-40f0-b8ef-a9ca8aa46aae_en?filename=bcs_2026_09_en.pdf",
        BASE + "/document/download/67757055-585f-4928-a381-35f3f0dd5b97_en?filename=bcs_2026_09_statistical_annex_en.pdf"),
    "2026-10-22": (BASE + "/document/download/1e455a38-643d-44ad-81dd-e71f6c17d3a1_en?filename=Flash_consumer_2026_10_en.pdf",),
}
# The linked September annex prints 2025 in its cover date. This exact reviewed
# file is bound to the independently dated 29 September 2026 release, not to
# retrieval time. Unknown replacements/other date mismatches fail closed.
REVIEWED_ANNEX_TYPO = "45a1991de7d16eb6a930d80b4b3406ef967d678967527b2f3322ee89c8d10ca3"


def stored_official_editions(store):
    """A final edition needs both headline cards; a partial save cannot skip it."""
    rows = store.conn.execute(
        "SELECT e.edition,v.indicator_id,v.reference_period,v.estimate_stage "
        "FROM value_vintages v JOIN release_events e ON e.id=v.release_event_id "
        "JOIN source_series s ON s.id=v.source_series_id "
        "JOIN indicators i ON i.id=v.indicator_id "
        "WHERE e.family='ea_ec_surveys' AND v.verification_status='verified' "
        "AND s.evidence_role='issuer' AND s.is_published=1 "
        "AND s.source_id=i.issuer_id AND s.issuer_id=i.issuer_id AND e.issuer_id=i.issuer_id"
    ).fetchall()
    found = {(r[0], r[1], r[2], r[3]) for r in rows}
    complete = set()
    for edition, (period, stage, _) in EDITIONS["ea_ec_surveys"].items():
        indicators = ["ea_consumer_confidence"]
        if stage == "final":
            indicators.append("ea_economic_sentiment_indicator")
        if all((edition, indicator, period, stage) in found for indicator in indicators):
            complete.add(edition)
    return frozenset(complete)


def metadata(body, family):
    label = require(r"(\d{1,2} \w+ \d{4})", body)[1]
    edition = datetime.strptime(label, "%d %B %Y").date().isoformat()
    head, stage, _ = reviewed(family, edition)
    return edition, head, stage


def parse(document, family="ea_ec_surveys", since="1900-01"):
    body, method = publication_text(document)
    edition, head, stage = metadata(body, family)
    if stage == "flash":
        if ("FLASH CONSUMER CONFIDENCE INDICATOR FOR THE EU AND EURO AREA" not in body or
                "In " + month(head) not in body or "Balances, %" not in body):
            raise FetchError("EC flash definition or period needs review")
        printed = require(r"([+-]?[\d.]+) points \(euro area\)", body)[1]
        return [observation(document, family, edition, "ea_consumer_confidence", head, printed, method)] if head >= since else []
    if "Business and consumer survey results for " + month(head) not in body:
        raise FetchError("EC final period needs review")
    # October 2026 prints a single level explicitly shared by EU and EA.
    # Stay within the ESI introduction, before the separate EEI measure.
    introduction = body.split("Employment Expectations Indicator", 1)[0]
    values = re.findall(r"Economic Sentiment Indicator \(ESI\).*?euro area \([+-]?[\d.]+ points to ([\d.]+)\)", introduction)
    values += re.findall(
        r"Economic Sentiment Indicator \(ESI\) declined for the second consecutive month "
        r"in both the EU and the euro area, falling by [\d.]+ points to ([\d.]+) in both areas\.",
        introduction)
    if len(values) != 1:
        raise FetchError("EC ESI headline needs review")
    printed = values[0]
    return [observation(document, family, edition, "ea_economic_sentiment_indicator", head, printed, method)] if head >= since else []


def parse_annex(document, release, since="1900-01"):
    # Parse and bind the independently dated final release before reading tables.
    main = parse(release, since=since)
    body, method = publication_text(document)
    release_body, _ = publication_text(release)
    edition, head, stage = metadata(release_body, "ea_ec_surveys")
    if stage != "final" or "Business and consumer survey results for " + month(head) not in body:
        raise FetchError("EC annex period or stage needs review")
    cover = require(r"(\d{1,2} \w+ \d{4})", body)[1]
    if (datetime.strptime(cover, "%d %B %Y").date().isoformat() != edition and
            document.document_hash != REVIEWED_ANNEX_TYPO):
        raise FetchError("EC annex date needs review")
    from pypdf import PdfReader
    table = PdfReader(BytesIO(document.body)).pages[1].extract_text()
    if "indicators (s.a.)" not in table or "2025 2026" not in re.sub(r"\s+", " ", table):
        raise FetchError("EC annex table definition or year columns needs review")
    # Text extraction fragments glyphs (Co ns umer, -1 6.5). Keep each row
    # separate and normalize only whitespace INSIDE a printed decimal token.
    section = require(r"EA Indus(.*?)BE Indus", re.sub(r"\s+", " ", table))[1]
    row = require(r"Co ns umer (.*?)Retail trade", section)[1]
    values = [re.sub(r"\s+", "", token) for token in re.findall(r"-?\d[\d ]*\.\s*\d", row)]
    if len(values) != 15:
        raise FetchError("EC final consumer table columns needs review")
    out = list(main)
    for offset, printed in ((0, values[-1]), (-1, values[-2])):
        period = shift_month(head, offset)
        if period >= since:
            out.append(observation(document, "ea_ec_surveys", edition, "ea_consumer_confidence", period, printed, method))
    return out


def fetch(client, family, since):
    links = None
    out = []
    for edition, (period, stage, _) in due(EDITIONS[family], family=family).items():
        if period < since or edition in STORED_EDITIONS.get():
            continue
        urls = ARCHIVES.get(edition)
        if urls is None:
            if links is None:
                listing = client.get("ecfin", LATEST)
                links = [urljoin(BASE, unescape(u)) for u in re.findall(r'href="([^"]+)"', listing.body.decode())]
            stem = period.replace("-", "_")
            names = (["Flash_consumer_" + stem + "_en.pdf"] if stage == "flash" else
                     ["bcs_" + stem + "_en.pdf", "bcs_" + stem + "_statistical_annex_en.pdf"])
            urls = []
            for name in names:
                matches = [u for u in links if u.endswith("filename=" + name)]
                if len(matches) != 1:
                    previous_names = [
                        ("Flash_consumer_" if meta[1] == "flash" else "bcs_") + meta[0].replace("-", "_") + "_en.pdf"
                        for d, meta in EDITIONS[family].items() if d < edition]
                    if (not matches and publication_grace(family, edition) and
                            any(u.endswith("filename=" + old) for u in links for old in previous_names)):
                        mark_pending(family, edition)
                        urls = []
                        break
                    raise FetchError("EC due publication link needs review")
                urls += matches
        if not urls:
            continue
        release = get_edition(client, "ecfin", urls[0], family, edition)
        if release is None:
            continue
        if stage == "flash":
            rows = parse(release, family, since)
        else:
            annex = get_edition(client, "ecfin", urls[1], family, edition)
            if annex is None:
                continue
            rows = parse_annex(annex, release, since)
        if not rows or {o.edition for o in rows} != {edition}:
            raise FetchError("EC requested archive edition needs review")
        out += rows
    return out
