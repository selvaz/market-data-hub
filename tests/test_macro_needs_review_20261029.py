"""Offline replay of saved issuer evidence from the 29 October review queue."""
from dataclasses import replace
from datetime import datetime
import hashlib
import json
from pathlib import Path

import pytest
import requests

from market_data_hub.macro_releases import bls, discovery, ec_surveys, fred, housing
from market_data_hub.macro_releases import open_reader, open_writer, resolve, run_daily
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.http import Document, FetchError
from market_data_hub.macro_releases.schedule import RUN_INSTANT
from market_data_hub.macro_releases.tier1_publications import publication_text

ROOT = Path(__file__).parent / "fixtures/macro_releases"
NEW = "needs_review_20261029/"


def document(name):
    path = ROOT / name
    body = path.read_bytes()
    meta = json.loads(path.with_name(path.name + ".meta.json").read_text())
    assert hashlib.sha256(body).hexdigest() == meta["sha256"]
    return Document(body, meta["url"], meta["retrieved_at"], meta["sha256"])


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network forbidden: saved issuer evidence only")
    monkeypatch.setattr(requests.Session, "request", forbidden)
    for module in (bls, fred, discovery):
        monkeypatch.setattr(module, "api_key", lambda _: "fixture")
    monkeypatch.setattr(run_daily, "datetime", type("Clock", (), {
        "now": staticmethod(lambda tz: datetime(2026, 10, 29, 16, tzinfo=tz))}))
    token = RUN_INSTANT.set("2026-10-29T16:00:00Z")
    yield
    RUN_INSTANT.reset(token)


class IssuerReplay:
    def get(self, provider, url, params=None):
        names = [NEW + n for n in ("fhfa-listing.html", "fhfa-october.pdf", "ec-latest.html",
                                   "ec-october.pdf", "ec-october-annex.pdf")]
        names += ["m9f/fhfa-aug.pdf", "m9f/fhfa-sept.pdf"]
        for name in names:
            doc = document(name)
            if doc.url == url.split("?", 1)[0]:
                return doc
        raise AssertionError("Unexpected issuer request: " + url)


def test_fhfa_saved_october_and_old_formats_with_listing():
    rows = housing.fetch_fhfa(IssuerReplay(), "us_fhfa_hpi", "2026-01")
    assert {(r.edition, r.indicator_id, r.period, r.value) for r in rows} == {
        ("2026-08-25", "us_fhfa_hpi_mm", "2026-06", 0.0),
        ("2026-08-25", "us_fhfa_hpi_yy", "2026-06", 2.3),
        ("2026-09-29", "us_fhfa_hpi_mm", "2026-07", 0.3),
        ("2026-09-29", "us_fhfa_hpi_yy", "2026-07", 2.6),
        ("2026-10-27", "us_fhfa_hpi_mm", "2026-08", 0.3),
        ("2026-10-27", "us_fhfa_hpi_yy", "2026-08", 2.4),
    }
    assert {r.published_at for r in rows if r.edition == "2026-10-27"} == {"2026-10-27T13:00:00+00:00"}


@pytest.mark.parametrize("before,after", [
    ("October 27, 202 6", "October 28, 202 6"),
    ("Released on October 27, 2026", "Released on September 29, 2026"),
    ("Data through August 2026", "Data through July 2026"),
    ("For Immediate Release", "Unknown release date"),
])
def test_fhfa_unverifiable_metadata_stays_review(monkeypatch, before, after):
    doc = document(NEW + "fhfa-october.pdf")
    body, method = publication_text(doc)
    assert before in body
    monkeypatch.setattr(housing, "publication_text", lambda _: (body.replace(before, after), method))
    with pytest.raises(FetchError, match="needs review"):
        housing.parse_fhfa(doc)


def test_ec_saved_listing_release_and_annex(tmp_path):
    token = ec_surveys.STORED_EDITIONS.set(frozenset({"2026-09-29", "2026-10-22"}))
    try:
        rows = ec_surveys.fetch(IssuerReplay(), "ea_ec_surveys", "2026-01")
    finally:
        ec_surveys.STORED_EDITIONS.reset(token)
    assert {(r.indicator_id, r.period, r.value) for r in rows} == {
        ("ea_economic_sentiment_indicator", "2026-10", 96.9),
        ("ea_consumer_confidence", "2026-10", -17.9),
        ("ea_consumer_confidence", "2026-09", -16.8),
    }
    assert {r.edition for r in rows} == {"2026-10-29"}
    assert {r.stage for r in rows} == {"final"}
    with open_writer(tmp_path / "ec.sqlite") as store:
        persist(store, rows)
        assert "2026-10-29" in ec_surveys.stored_official_editions(store)
        assert resolve(store, "ea_economic_sentiment_indicator", "2026-10", stage="final").status == "official"


@pytest.mark.parametrize("before,after", [
    ("in both areas.", "in the EU."),
    ("in both the EU and the euro area", "in the EU"),
    ("Economic Sentiment Indicator (ESI)", "Employment Expectations Indicator (EEI)"),
    ("results for October 2026", "results for September 2026"),
])
def test_ec_shared_headline_needs_explicit_measure_area_and_period(before, after):
    doc = document(NEW + "ec-october.pdf")
    body, _ = publication_text(doc)
    assert before in body
    with pytest.raises(FetchError, match="needs review"):
        ec_surveys.parse(replace(doc, body=body.replace(before, after).encode()))


def test_ec_wrong_annex_companion_stays_review():
    with pytest.raises(FetchError, match="needs review"):
        ec_surveys.parse_annex(document(NEW + "ec-october-annex.pdf"), document("m9b/ec-final.pdf"))


class RejectedJOLTS:
    """Real BLS rejection; explicitly synthetic ALFRED rows, no live requests."""
    def __init__(self, mode="ok", **kwargs):
        self.mode = mode
        self.calls = []

    def request(self, provider, url, *, payload):
        assert provider == "bls" and payload["seriesid"] == ["JTS000000000000000JOL"]
        return document(NEW + "jolts-request-failed.json")

    def get(self, provider, url, params=None):
        assert provider == "fred" and params["series_id"] == "JTSJOL" and params["units"] == "lin"
        self.calls.append(params)
        edition = params["realtime_end"]
        row = dict(date=params["observation_start"], value="7000", realtime_start=edition, realtime_end=edition)
        if self.mode == "late":
            row["realtime_start"] = "2026-10-29"
        payload = dict(units="lin", output_type=params["output_type"], realtime_start=params["realtime_start"],
                       realtime_end=edition, count=1, offset=0, observations=[row])
        body = json.dumps(payload).encode()
        return Document(body, url, "2026-10-29T16:00:00Z", hashlib.sha256(body).hexdigest())


@pytest.mark.parametrize("manual", [False, True])
def test_saved_jolts_rejection_uses_release_bound_redistributor(tmp_path, manual):
    client = RejectedJOLTS()
    if manual:
        rows = bls.BLSAdapter(client).fetch(["us_jolts"], "2026-08")
    else:
        batch = discovery.fetch_daily(client, "us_jolts", "2026-08")
        assert not batch.issues and batch.review_reasons == ["BLS rejected request"]
        assert document(NEW + "jolts-request-failed.json") in batch.documents
        rows = batch.observations
    row, = rows
    assert (row.period, row.edition, row.provider_vintage, row.published_at) == (
        "2026-08", "2026-09-29", "2026-09-29", "2026-09-29T14:00:00+00:00")
    assert {p["output_type"] for p in client.calls} == {1, 4}
    with open_writer(tmp_path / "jolts.sqlite") as store:
        persist(store, rows)
        result = resolve(store, "us_jolts_openings", "2026-08")
        assert (result.source_class, result.status, result.issuer_id) == ("redistributor", "provisional", "BLS")
        assert result.source["source_id"] == "ALFRED"


@pytest.mark.parametrize("mode", ["ok", "late"])
def test_saved_jolts_rejection_is_audited_and_late_mirror_stays_review(tmp_path, monkeypatch, mode):
    path = tmp_path / "jolts.sqlite"
    monkeypatch.setattr(run_daily, "HttpClient", lambda **kwargs: RejectedJOLTS(mode))
    result = run_daily.execute(path, ["us_jolts"], 24, False, raw_dir=tmp_path / "empty")
    assert result["families"]["us_jolts"]["status"] == ("ok" if mode == "ok" else "needs_review")
    with open_reader(path) as store:
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == (3 if mode == "ok" else 0)
        reasons = " ".join(r[0] for r in store.conn.execute("SELECT reasons FROM review_queue"))
        assert "BLS rejected request" in reasons
        if mode == "late":
            assert "ALFRED vintage date" in reasons
    if mode == "ok":
        again = run_daily.execute(path, ["us_jolts"], 24, False, raw_dir=tmp_path / "empty")
        assert again["families"]["us_jolts"]["status"] == "no_change"


@pytest.mark.parametrize("changes", [
    {"status": "UNKNOWN"}, {"message": []}, {"Results": {"series": []}},
])
def test_other_bls_envelopes_do_not_enable_fallback(changes):
    doc = document(NEW + "jolts-request-failed.json")
    payload = dict(doc.json(), **changes)
    with pytest.raises(FetchError) as error:
        bls.BLSAdapter(None).parse(replace(doc, body=json.dumps(payload).encode()), ["us_jolts"], "2026-08")
    assert not bls.unavailable(error.value)
