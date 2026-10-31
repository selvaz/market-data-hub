"""Issuer edition discovery for daily runs; reviewed editions remain anchors.

No date is inferred from retrieval time. A changed/unreadable structure retains
raw documents and any safely mapped observations for review, without certification.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
import hashlib
from html import unescape
import json
import re
from xml.etree import ElementTree

from .adapters import Observation
from . import bea, bls, eurostat, ons
from .catalogue import BLS_SERIES, hicp_indicator
from .http import Document, FetchError, api_key
from .publication_times import new_york_release

BLS_FEEDS = {"us_cpi": "https://www.bls.gov/feed/cpi.rss",
             "us_payrolls": "https://www.bls.gov/feed/empsit.rss"}
BEA_LISTING = "https://www.bea.gov/news/current-releases"
EUROSTAT_CALENDAR = "https://ec.europa.eu/eurostat/o/calendars/eventsJson"


@dataclass
class FamilyFetch:
    observations: list = field(default_factory=list)
    documents: list = field(default_factory=list)
    issues: list = field(default_factory=list)
    anchors: bool = True
    # Availability notices are audited without invalidating valid fallback data.
    review_reasons: list = field(default_factory=list)

    def issue(self, reason):
        if reason not in self.issues:
            self.issues.append(reason)

    def get(self, client, provider, url, params=None):
        doc = client.get(provider, url, params)
        self.documents.append(doc)
        return doc


def text(body):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", body)))


def bls_publication(document, family, period):
    root = ElementTree.fromstring(document.body)
    matches = []
    for item in root.findall(".//item"):
        title = text(" ".join(item.findtext(k, "") for k in ("title", "description")))
        release = parsedate_to_datetime(item.findtext("pubDate")).date()
        month = datetime.strptime(period, "%Y-%m")
        expected = "consumer price index" if family == "us_cpi" else "employment situation"
        if expected not in title.lower() or month.strftime("%B").lower() not in title.lower():
            continue
        years = re.findall(month.strftime("%B") + r"\s+(\d{4})", title, re.I)
        if years and years[0] != period[:4]:
            continue
        end = date(month.year + (month.month == 12), month.month % 12 + 1, 1) - timedelta(days=1)
        if end < release <= end + timedelta(days=45):
            matches.append(release.isoformat())
    if len(set(matches)) != 1:
        raise ValueError("No unambiguous issuer publication")
    return matches[0]


def bea_publications(document):
    result = {}
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", document.body.decode(), re.S):
        title = text(row)
        stage = re.search(r"\((Advance|Second|Third) Estimate\)", title, re.I)
        quarter = re.search(r"([1-4])(?:st|nd|rd|th) Quarter (\d{4})", title, re.I)
        stamp = re.search(r'<time\b[^>]*datetime="(\d{4}-\d{2}-\d{2})T', row)
        if stage and quarter and stamp and re.search(r"\bGDP\b|Gross Domestic Product", title, re.I):
            result[stamp[1]] = (f"{quarter[2]}-Q{quarter[1]}", stage[1].lower())
    return result


def eurostat_publications(document):
    result = {}
    for row in document.json():
        if row.get("euroindAuthor") != "estat" or row.get("preliminary"):
            continue
        title = row.get("title", "").lower()
        if "inflation" not in title:
            continue
        stage = "flash" if "flash" in title else "final" if "hicp" in title else None
        if stage:
            period = datetime.strptime(row["period"], "%B %Y").strftime("%Y-%m")
            result[period, stage] = date.fromisoformat(row["start"][:10]).isoformat()
    return result


def _bls(client, family, since):
    batch = FamilyFetch()
    keys = [k for k, (i, _, _) in BLS_SERIES.items()
            if ("us_payrolls" if i.startswith("us_payrolls") else "us_cpi") == family]
    year = date.today().year
    doc = client.request("bls", bls.ENDPOINT, payload={"seriesid": keys,
        "startyear": str(min(int(since[:4]) - 1, year - 5)), "endyear": str(year),
        "calculations": True, "catalog": True, "registrationkey": api_key("BLS_API_KEY")})
    batch.documents.append(doc)
    payload = doc.json()
    series = {s["seriesID"]: s for s in payload.get("Results", {}).get("series", [])}
    if payload.get("status") != "REQUEST_SUCCEEDED" or set(series) != set(keys):
        batch.issue("series_identity_or_structure_changed")
    for key in keys:
        catalog = series.get(key, {}).get("catalog", {})
        expected = "Not Seasonally Adjusted" if key.startswith("CUUR") else "Seasonally Adjusted"
        if catalog.get("series_id") != key or catalog.get("seasonality") != expected:
            batch.issue("series_catalogue_or_adjustment_changed")
    registry = dict(bls.EDITIONS)
    newest = max((r["year"] + "-" + r["period"][1:] for s in series.values() for r in s.get("data", [])
                  if re.fullmatch(r"M(0[1-9]|1[0-2])", r.get("period", ""))), default=None)
    batch.anchors = (family, newest) in registry
    try:
        listing = batch.get(client, "bls", BLS_FEEDS[family])
        publication = bls_publication(listing, family, newest)
        if (family, newest) in registry and registry[family, newest] != publication:
            batch.issue("issuer_calendar_date_mismatch")
        batch.anchors &= registry.get((family, newest)) == publication
        registry[family, newest] = publication
    except FetchError:
        if not batch.anchors:
            batch.issue("issuer_publication_unconfirmed")
    except (ValueError, TypeError, ElementTree.ParseError):
        # Deliberately review malformed CPI/payrolls RSS even when its edition
        # is already reviewed. Contradictory issuer evidence cannot be excused
        # by that anchor; HTTP unavailability alone can use the reviewed date.
        batch.issue("issuer_publication_unconfirmed")
    publication = registry.get((family, newest))
    try:
        batch.observations = bls.BLSAdapter(client).parse(doc, [family], since, editions=registry)
    except (FetchError, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, FetchError):
            batch.review_reasons.append(str(exc))
        batch.issue("expected_series_units_or_periods_missing")
        # Salvage only the requested calculation, never substitute level data.
        for key in keys:
            indicator, calc, lag = BLS_SERIES[key]
            for row in series.get(key, {}).get("data", []):
                if not re.fullmatch(r"M(0[1-9]|1[0-2])", row.get("period", "")):
                    continue
                period = row["year"] + "-" + row["period"][1:]
                value = row.get("calculations", {}).get(calc, {}).get(lag)
                if period < since:
                    continue
                numeric = float(value) if value not in (None, "", "-") else None
                batch.observations.append(Observation(indicator, family, period, numeric,
                    str(value) if numeric is not None else "requested calculation unavailable",
                    f"{key}:{calc}:{lag}", "BLS", doc, publication or "unconfirmed:" + doc.identity_hash,
                    new_york_release(publication)["published_at"] if publication else None, needs_review=True))
    # All constituent series must have the same headline period and revisions.
    headline_keys = {o.indicator_id for o in batch.observations if o.period == newest}
    if len(headline_keys) != len(keys):
        batch.issue("headline_series_or_periods_missing")
    if family == "us_payrolls" and newest:
        required = {bls.shift_month(newest, offset) for offset in (0, -1, -2)}
        if not required.issubset({o.period for o in batch.observations}):
            batch.issue("payroll_revision_periods_missing")
    return batch


def _bea(client, family, since):
    batch = FamilyFetch()
    year = date.today().year
    doc = batch.get(client, "bea", bea.ENDPOINT, {"UserID": api_key("BEA_API_KEY"), "method": "GetData",
        "datasetname": "NIPA", "TableName": bea.TABLE, "Frequency": "Q",
        "Year": ",".join(str(y) for y in range(int(since[:4]), year + 1)), "ResultFormat": "JSON"})
    result = doc.json().get("BEAAPI", {}).get("Results", {})
    note = " ".join(n.get("NoteText", "") for n in result.get("Notes", []) if n.get("NoteRef") == bea.TABLE)
    match = re.search(r"LastRevised:\s*(\w+ \d+, \d{4})", note)
    publication = datetime.strptime(match[1], "%B %d, %Y").date().isoformat() if match else None
    registry = dict(bea.EDITIONS)
    batch.anchors = publication in registry
    try:
        listed = bea_publications(batch.get(client, "bea", BEA_LISTING))
        if publication in registry and any(d != publication and meta == registry[publication]
                                           for d, meta in listed.items()):
            batch.issue("issuer_calendar_or_estimate_stage_mismatch")
        if publication in registry and publication in listed and registry[publication] != listed[publication]:
            batch.issue("issuer_calendar_or_estimate_stage_mismatch")
        registry.update(listed)
    except (FetchError, ValueError, TypeError):
        if not batch.anchors:
            batch.issue("issuer_listing_unavailable")
    if publication not in registry:
        batch.issue("issuer_calendar_or_estimate_stage_unconfirmed")
    try:
        batch.observations = bea.BEAAdapter.parse(doc, since=since, editions=registry)
    except (FetchError, ValueError, KeyError, TypeError):
        batch.issue("dataset_series_units_or_periods_changed")
        for row in result.get("Data", []):
            if str(row.get("LineNumber")) != "1" or not re.fullmatch(r"\d{4}Q[1-4]", row.get("TimePeriod", "")):
                continue
            period = row["TimePeriod"].replace("Q", "-Q")
            if f"{period[:4]}-{int(period[-1]) * 3:02d}" < since:
                continue
            value = row.get("DataValue", "")
            try:
                value = float(value.replace(",", ""))
            except (ValueError, AttributeError):
                continue
            batch.observations.append(Observation("us_gdp_qq_saar", family, period, value, str(value),
                "T10101:1:A191RL", "BEA", doc, publication or "unconfirmed:" + doc.identity_hash,
                new_york_release(publication)["published_at"] if publication else None,
                stage=registry.get(publication, (None, "unknown"))[1], needs_review=True,
                unit=row.get("CL_UNIT", "unknown")))
    if batch.anchors:
        for edition, url in bea.ARCHIVES.items():
            period, _ = bea.EDITIONS[edition]
            if f"{period[:4]}-{int(period[-1]) * 3:02d}" >= since:
                archive = batch.get(client, "bea", url)
                try:
                    batch.observations.extend(bea.BEAAdapter.parse_archived_release(archive))
                except Exception:
                    batch.issue("archived_estimate_structure_changed")
    return batch


def _eurostat(client, family, since):
    batch = FamilyFetch()
    registry = dict(eurostat.PUBLICATIONS)
    data = []
    for stage, dataset in eurostat.DATASETS.items():
        params = {"lang": "EN", "geo": "EA", "coicop18": "TOTAL", "sinceTimePeriod": since}
        if stage == "flash":
            params["release"] = "FLS"
        doc = batch.get(client, "eurostat", eurostat.BASE + dataset, params)
        data.append((stage, doc))
    unknown = any((p, stage) not in registry for stage, doc in data
                  for p in doc.json().get("dimension", {}).get("time", {}).get("category", {}).get("index", {}))
    batch.anchors = not unknown
    try:
        until = date.today() + timedelta(days=60)
        calendar = batch.get(client, "eurostat", EUROSTAT_CALENDAR,
            {"start": since + "-01T00:00:00Z", "end": until.isoformat() + "T00:00:00Z", "theme": "2", "category": "2"})
        listed = eurostat_publications(calendar)
        if any(key in listed and listed[key] != value for key, value in registry.items()):
            batch.issue("issuer_calendar_date_mismatch")
        registry.update(listed)
    except (FetchError, ValueError, KeyError, TypeError, AttributeError):
        if unknown:
            batch.issue("issuer_calendar_unavailable")
    for stage, doc in data:
        payload = doc.json()
        expected = {"freq", "unit", "coicop18", "geo", "time"} | ({"release"} if stage == "flash" else set())
        if set(payload.get("id", [])) != expected:
            batch.issue("dataset_dimensions_changed")
        units = set(payload.get("dimension", {}).get("unit", {}).get("category", {}).get("index", {}))
        if not {"RCH_A", "RCH_M"}.issubset(units) or not units.issubset({"I25", "I15", "RCH_A", "RCH_M", "RCH_MV12MAVR"}):
            batch.issue("dataset_units_changed")
        try:
            parsed = eurostat.EurostatAdapter.parse(doc, stage=stage, since=since, publications=registry)
        except (FetchError, ValueError, KeyError, TypeError):
            batch.issue("dataset_dimensions_units_periods_or_publication_changed")
            parsed = []
            try:
                newest = max(payload["dimension"]["time"]["category"]["index"])
                for dims, value, flag in eurostat.cells(payload):
                    period = dims.get("time", "")
                    if dims.get("unit") not in ("RCH_A", "RCH_M") or period < since:
                        continue
                    info = hicp_indicator(period, "yy" if dims["unit"] == "RCH_A" else "mm")
                    published = registry.get((newest if stage == "final" else period, stage))
                    parsed.append(Observation(info.id, family, period, float(value), str(value),
                        f"{eurostat.DATASETS[stage]}:EA:TOTAL:{dims['unit']}:{stage}", "Eurostat", doc,
                        published or "unconfirmed:" + doc.identity_hash,
                        published + "T00:00:00Z" if published else None, stage=stage, needs_review=True,
                        composition_id=info.geography,
                        unit="%" if dims.get("freq") == "M" and dims.get("geo") == "EA" and dims.get("coicop18") == "TOTAL"
                        else "unconfirmed HICP dimensions"))
            except (KeyError, ValueError, TypeError):
                pass
        geo = payload.get("dimension", {}).get("geo", {}).get("category", {}).get("label", {}).get("EA", "")
        # The 2026 accession rule continues forward only while the issuer still
        # explicitly labels the headline area EA21. Unknown membership is evidence.
        memberships = [(int(year), members) for members, year in re.findall(r"EA(\d+)-(\d{4})", geo)]
        def confirmed(obs):
            eligible = [(year, members) for year, members in memberships if year <= int(obs.period[:4])]
            return bool(eligible and max(eligible)[1] == ("21" if obs.period >= "2026-01" else "20"))
        if any(not confirmed(o) for o in parsed):
            batch.issue("future_euro_area_composition_unconfirmed")
            parsed = [replace(o, indicator_id=hicp_indicator(o.period,
                "yy" if o.indicator_id.endswith("yy") else "mm", "EA_PENDING").id,
                composition_id="EA_PENDING", needs_review=True) if not confirmed(o) else o for o in parsed]
        batch.observations.extend(parsed)
    return batch


def _ons(client, family, since):
    batch = FamilyFetch()
    index = batch.get(client, "ons", ons.BULLETIN_INDEX_URL)
    index_doc = batch.get(client, "ons", ons.INDEX_URL)
    try:
        batch.observations.extend(ons.ONSAdapter.parse_index(index_doc, since))
    except FetchError:
        batch.issue("index_series_or_json_shape_changed")
    batch.anchors = False
    slugs = set(re.findall(r'/gdpmonthlyestimateuk/([a-z]+\d{4})(?:["?#/])', index.body.decode()))
    slugs = sorted(slugs, key=lambda slug: datetime.strptime(slug, "%B%Y"))
    dates = {}
    for tag in re.findall(r'<a\b[^>]+>', index.body.decode(), re.S):
        slug = re.search(r'/gdpmonthlyestimateuk/([a-z]+\d{4})', tag)
        stamp = re.search(r'data-gtm-search-result-release-date="(\d{8})"', tag)
        if slug and stamp:
            dates[slug[1]] = datetime.strptime(stamp[1], "%Y%m%d").date().isoformat()
    if not slugs:
        batch.issue("issuer_bulletin_listing_empty")
        return batch
    for slug in slugs[-2:]:
        doc = batch.get(client, "ons", ons.BULLETIN_BASE + slug)
        try:
            if slug in dates and ons.bulletin_date(doc) != dates[slug]:
                batch.issue("issuer_listing_publication_date_mismatch")
        except FetchError:
            batch.issue("issuer_publication_date_missing")
        try:
            if slug in slugs[-2:]:
                parsed = ons.ONSAdapter.parse_bulletin(doc, since)
                expected = datetime.strptime(slug, "%B%Y").strftime("%Y-%m")
                if not parsed or max(o.period for o in parsed) != expected:
                    batch.issue("bulletin_reference_period_mismatch")
                batch.observations.extend(parsed)
                batch.anchors &= slug in ons.BULLETINS
        except (FetchError, ValueError, KeyError, TypeError):
            batch.issue("bulletin_structure_changed")
    return batch


def fetch_daily(client, family, since):
    from .us_editions import EDITIONS
    from .catalogue import REMAINING_FAMILIES, CENTRAL_BANKS, HOUSING_FAMILIES, ASIA_FAMILIES, LATAM_FAMILIES
    strategy = {"us_cpi": _bls, "us_payrolls": _bls, "us_gdp": _bea,
                "us_unemployment": _employment, "us_ahe": _employment,
                "ea_hicp": _eurostat, "uk_gdp": _ons, "uk_cpi": _ons_rates,
                "uk_labour_market": _ons_rates, "ea_gdp": _gdp, "us_jobless_claims": _claims,
                **{f: _monthly for f in EDITIONS},
                **{f: _remaining for f in REMAINING_FAMILIES},
                **{f: _policy for f in CENTRAL_BANKS},
                **{f: _housing for f in HOUSING_FAMILIES},
                **{f: _asia for f in ASIA_FAMILIES},
                **{f: _latam for f in LATAM_FAMILIES}}[family]
    documents = []
    reasons = []
    class Capture:
        def get(self, *args, **kwargs):
            doc = client.get(*args, **kwargs)
            documents.append(doc)
            return doc

        def request(self, *args, **kwargs):
            doc = client.request(*args, **kwargs)
            documents.append(doc)
            return doc
    try:
        try:
            batch = strategy(Capture(), family, since)
        except FetchError as exc:
            if family not in ("us_cpi", "us_payrolls", "us_ppi", "us_jolts", "us_unemployment", "us_ahe"):
                raise
            reasons.append(str(exc))
            # HTTP failures have no successful response document. Retain the
            # controlled original reason as evidence, without URLs or secrets.
            from .schedule import run_instant
            body = json.dumps(dict(provider="BLS", reason=str(exc))).encode()
            documents.append(Document(body, bls.ENDPOINT, run_instant().isoformat(), hashlib.sha256(body).hexdigest()))
            if not bls.unavailable(exc):
                raise FetchError(str(exc) + "; needs review") from None
            from .fred import BLSFallbackAdapter
            try:
                batch = FamilyFetch(observations=BLSFallbackAdapter(Capture()).fetch([family], since))
            except FetchError as fallback_error:
                raise FetchError(str(fallback_error) + "; needs review") from None
        batch.documents = documents
        batch.review_reasons.extend(reasons)
        return batch
    except FetchError as exc:
        if "needs review" not in str(exc):
            raise
        return FamilyFetch(documents=documents, issues=["publication_metadata_or_format_needs_review"],
                           anchors=False, review_reasons=[*reasons, str(exc)])
    except (ValueError, KeyError, TypeError, AttributeError):
        # A malformed top-level payload must not turn a source-format change
        # into a red run. Keep all fetched documents even if no values map.
        return FamilyFetch(documents=documents, issues=["payload_structure_changed"], anchors=False, review_reasons=reasons)


def _monthly(client, family, since):
    from .fred import USRedistributorAdapter
    adapter = bls.BLSAdapter if family in ("us_ppi", "us_jolts") else bea.BEAAdapter if family == "us_pce" else USRedistributorAdapter
    rows = (adapter(client).fetch([family], since, fallback=False) if adapter is bls.BLSAdapter
            else adapter(client).fetch([family], since))
    return FamilyFetch(observations=rows)


def _employment(client, family, since):
    return FamilyFetch(observations=bls.BLSAdapter(client).fetch([family], since, fallback=False))


def _remaining(client, family, since):
    from .tier1_publications import Tier1Adapter
    return FamilyFetch(observations=Tier1Adapter(client).fetch([family], since))


def _latam(client, family, since):
    from .latam import LatamAdapter
    return FamilyFetch(observations=LatamAdapter(client).fetch([family], since))


def _asia(client, family, since):
    from .asia import AsiaAdapter
    return FamilyFetch(observations=AsiaAdapter(client).fetch([family], since))


def _policy(client, family, since):
    from .policy import PolicyAdapter
    return FamilyFetch(observations=PolicyAdapter(client).fetch([family], since))


def _housing(client, family, since):
    from .housing import HousingAdapter
    return FamilyFetch(observations=HousingAdapter(client).fetch([family], since))


def _ons_rates(client, family, since):
    return FamilyFetch(observations=ons.ONSAdapter(client).fetch([family], since))


def _gdp(client, family, since):
    return FamilyFetch(observations=eurostat.EurostatAdapter(client).fetch([family], since))


def _claims(client, family, since):
    from .dol import DOLAdapter
    return FamilyFetch(observations=DOLAdapter(client).fetch([family], since))
