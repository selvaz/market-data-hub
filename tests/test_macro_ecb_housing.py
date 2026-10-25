"""Offline ECB mortgage publications, exact SDMX identity and separate clocks."""
import csv
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from io import BytesIO, StringIO
import json

import pytest

from market_data_hub.macro_releases import ecb_housing as ecb, open_reader, run_daily
from market_data_hub.macro_releases.housing_editions import EDITIONS
from market_data_hub.macro_releases.http import FetchError
from market_data_hub.macro_releases.schedule import PENDING_EDITIONS, RUN_INSTANT, release_instant
from test_macro_m9f import document, offline as offline


class Replay:
    def __init__(self, **kwargs):
        self.requests = []

    def get(self, provider, url, params=None):
        assert provider == "ecb"
        self.requests.append((url, params))
        if url == ecb.CALENDAR:
            return document("ecb-statistical-calendar.html")
        for family, tag in ((ecb.LOANS, "bsi"), (ecb.COST, "mir")):
            if url == ecb.LISTINGS[family]:
                return document("ecb-" + tag + "-index.html")
            if url == ecb.series_url(family):
                assert params == {"startPeriod": "2026-08", "format": "csvdata"}
                return document("ecb-" + tag + "-house.csv")
            for edition, path in ecb.ARCHIVES[family].items():
                if url == path:
                    assert not params
                    return document(f"ecb-{tag}-{edition}.html")
        for edition in ("2026-08-27", "2026-09-25"):
            doc = document(f"ecb-bsi-{edition}.pdf")
            if url == doc.url:
                return doc
        raise AssertionError("Unexpected ECB request: " + url)


def changed_csv(family, change):
    doc = document("ecb-" + ("bsi" if family == ecb.LOANS else "mir") + "-house.csv")
    reader = csv.DictReader(StringIO(doc.body.decode()))
    rows = list(reader)
    change(rows)
    text = StringIO()
    writer = csv.DictWriter(text, fieldnames=reader.fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return replace(doc, body=text.getvalue().encode())


@pytest.mark.parametrize("family", [ecb.LOANS, ecb.COST])
def test_calendar_dates_clocks_and_separate_real_stages(family):
    found = ecb.calendar_editions(document("ecb-statistical-calendar.html"), family)
    expected = {d: meta for d, meta in EDITIONS[family].items() if meta[0] >= "2026-08"}
    assert found == expected
    assert all(meta[1:] == ("regular", "10:00") for meta in found.values())
    assert release_instant(min(found), family=family).hour == 8
    assert release_instant(max(found), family=family).hour == 9
    # The two stage memberships must not acquire each other's publication dates.
    from market_data_hub.macro_releases.read_api import indicators_for_stage
    assert indicators_for_stage(family, "regular") == {ecb.INDICATORS[family]}
    assert indicators_for_stage(family, "flash") == set()
    assert set(EDITIONS[ecb.LOANS]).isdisjoint(EDITIONS[ecb.COST])


@pytest.mark.parametrize("before,after", [
    (b"All times are local to Frankfurt am Main.", b"All times are UTC."),
    (b"27/10/2026 10:00", b"27/10/2026 09:00"),
    (b"27/10/2026 10:00", b"28/10/2026 10:00"),
    (b"Reference period: Sep-2026", b"Reference period: Dec-2026"),
    (b"Monetary developments in the euro area (Dataset: BSI)", b"Monetary developments in the euro area (Dataset: MIR)"),
])
def test_changed_calendar_needs_review(before, after):
    doc = document("ecb-statistical-calendar.html")
    assert before in doc.body
    with pytest.raises(FetchError, match="needs review"):
        ecb.calendar_editions(replace(doc, body=doc.body.replace(before, after)), ecb.LOANS)


def test_missing_or_duplicate_due_calendar_edition_needs_review():
    import re
    doc = document("ecb-statistical-calendar.html")
    row = re.search(rb"<dt>\s*25/09/2026 10:00\s*</dt>\s*<dd>\s*Monetary developments.*?</dd>", doc.body, re.S)[0]
    for body in (doc.body.replace(row, b""), doc.body.replace(row, row + row)):
        with pytest.raises(FetchError, match="needs review"):
            ecb.calendar_editions(replace(doc, body=body), ecb.LOANS)


@pytest.mark.parametrize("before,after", [
    (b'content="2026-10-01"', b'content="2026-10-02"'),
    (b"August 2026", b"July 2026"),
    (b"1 October 2026", b"2 October 2026"),
    (b"new loans to households for house purchase", b"new loans to households for consumption"),
    (b"MIR.M.U2.B.A2C.AM.R.A.2250.EUR.N", b"MIR.M.U2.B.A2I.AM.R.A.2240.EUR.N"),
    (b"which combines interest rates on all loans to households for house purchase", b"which combines only fixed-rate mortgages"),
])
def test_changed_mir_measure_period_or_publication_date_needs_review(before, after):
    doc = document("ecb-mir-2026-10-01.html")
    assert before in doc.body
    with pytest.raises(FetchError, match="needs review"):
        ecb.parse(replace(doc, body=doc.body.replace(before, after)), ecb.COST)


def test_mir_table_crosscheck_and_publication_url():
    doc = document("ecb-mir-2026-09-02.html")
    assert b"3.54%" in doc.body
    # The summary and table independently print 3.54. Change only the summary.
    changed = doc.body.replace(b"3.54%", b"8.54%", 1)
    with pytest.raises(FetchError, match="disagree; needs review"):
        ecb.parse(replace(doc, body=changed), ecb.COST)
    for url in (doc.url.replace("www.ecb.europa.eu", "example.org"), doc.url.replace("260902", "261002")):
        with pytest.raises(FetchError, match="needs review"):
            ecb.parse(replace(doc, url=url), ecb.COST)


def test_bsi_original_print_is_not_revised_history_or_all_household_lending():
    july, = ecb.parse(document("ecb-bsi-2026-08-27.pdf"), ecb.LOANS)
    august, = ecb.parse(document("ecb-bsi-2026-09-25.pdf"), ecb.LOANS)
    assert (july.period, july.printed, july.value) == ("2026-07", "3.0", 3.0)
    assert (august.period, august.printed, august.value) == ("2026-08", "3.1", 3.1)
    revised = list(csv.DictReader(StringIO(document("ecb-bsi-house.csv").body.decode())))[0]
    assert revised["TIME_PERIOD"] == july.period and float(revised["OBS_VALUE"]) > 3.05
    # July all-household growth is 3.1; only the mortgage row is 3.0.
    assert july.series_key == ecb.SERIES[ecb.LOANS]
    assert july.composition_id == "EA_CHANGING" and july.issuer_timezone == "Europe/Berlin"
    assert ecb.parse(document("ecb-bsi-2026-08-27.pdf"), ecb.LOANS, "2026-08") == []
    with pytest.raises(FetchError, match="needs review"):
        ecb.parse(replace(document("ecb-bsi-2026-08-27.pdf"), url=august.document.url), ecb.LOANS)


@pytest.mark.parametrize("before,after", [
    ("4 Adjusted loans to the private sector:", "4 Unadjusted loans to the private sector:"),
    ("August 2026", "September 2026"),
    ("data adjusted for seasonal effects", "data unadjusted for seasonal effects"),
    ("1.2. Lending for house purchase", "1.2. Lending for consumption"),
    ("Aug Jun Jul Aug Jun Jul Aug", "Aug Jun Jul Aug Aug Jun Jul"),
    ("Annual growth rate", "Monthly growth rate"),
    ("Loans adjusted for the effects of transfers", "Loans unadjusted for the effects of transfers"),
])
def test_changed_bsi_table_identity_or_columns_need_review(monkeypatch, before, after):
    from pypdf import PdfReader
    from types import SimpleNamespace
    doc = document("ecb-bsi-2026-09-25.pdf")
    pages = [page.extract_text() for page in PdfReader(BytesIO(doc.body)).pages]
    assert before in pages[-1]
    pages[-1] = pages[-1].replace(before, after)
    monkeypatch.setattr("pypdf.PdfReader", lambda _: SimpleNamespace(pages=[
        SimpleNamespace(extract_text=lambda text=text: text) for text in pages]))
    with pytest.raises(FetchError, match="needs review"):
        ecb.parse(doc, ecb.LOANS)


def test_corrupted_pdf_needs_review():
    doc = document("ecb-bsi-2026-09-25.pdf")
    with pytest.raises(FetchError, match="PDF structure needs review"):
        ecb.parse(replace(doc, body=b"not a PDF"), ecb.LOANS)


@pytest.mark.parametrize("family", [ecb.LOANS, ecb.COST])
@pytest.mark.parametrize("field,value", [
    ("KEY", "MIR.M.DE.B.A2C.AM.R.A.2250.EUR.N"), ("REF_AREA", "DE"),
    ("BS_ITEM", "A20T"), ("BS_COUNT_SECTOR", "2240"),
    ("UNIT", "EUR"), ("UNIT_MULT", "3"), ("DECIMALS", "0"),
    ("TIME_PERIOD", "2026-09"), ("OBS_VALUE", "9.9"),
    ("OBS_VALUE", "NaN"), ("OBS_VALUE", "Infinity"),
    ("OBS_STATUS", "M"), ("OBS_CONF", "C"),
])
def test_sdmx_identity_latest_period_and_printed_value_fail_closed(family, field, value):
    fixture = "ecb-bsi-2026-09-25.pdf" if family == ecb.LOANS else "ecb-mir-2026-10-01.html"
    obs, = ecb.parse(document(fixture), family)
    doc = changed_csv(family, lambda rows: rows[-1].update({field: value}))
    with pytest.raises(FetchError, match="needs review"):
        ecb.check_series(doc, family, obs)


@pytest.mark.parametrize("family", [ecb.LOANS, ecb.COST])
def test_sdmx_is_only_latest_headline_corroboration(family):
    obs, = ecb.parse(document("ecb-bsi-2026-09-25.pdf" if family == ecb.LOANS else "ecb-mir-2026-10-01.html"), family)
    doc = document("ecb-" + ("bsi" if family == ecb.LOANS else "mir") + "-house.csv")
    assert ecb.check_series(doc, family, obs) is None
    duplicate = changed_csv(family, lambda rows: rows.append(dict(rows[-1])))
    with pytest.raises(FetchError, match="needs review"):
        ecb.check_series(duplicate, family, obs)
    with pytest.raises(FetchError, match="needs review"):
        ecb.check_series(replace(doc, url=doc.url.replace("data-api.ecb.europa.eu", "example.org")), family, obs)


@pytest.mark.parametrize("family,instant", [(ecb.LOANS, "2026-08-27T07:59:59Z"), (ecb.COST, "2026-09-02T07:59:59Z")])
def test_before_first_release_no_network(family, instant):
    replay = Replay()
    token = RUN_INSTANT.set(instant)
    try:
        assert ecb.fetch(replay, family) == [] and replay.requests == []
    finally:
        RUN_INSTANT.reset(token)


@pytest.mark.parametrize("family", [ecb.LOANS, ecb.COST])
def test_only_due_archive_requests_since_cutoff_and_expired_horizon(family):
    replay = Replay()
    rows = ecb.fetch(replay, family)
    assert len(rows) == 2 and {o.edition for o in rows} == set(ecb.ARCHIVES[family])
    assert all("2026-11" not in url and "2026-12" not in url for url, _ in replay.requests)
    assert len(ecb.fetch(Replay(), family, "2026-08")) == 1
    empty = Replay()
    assert ecb.fetch(empty, family, "2026-09") == [] and empty.requests == []
    token = RUN_INSTANT.set("2027-01-01T00:00:00Z")
    try:
        with pytest.raises(FetchError, match="horizon exhausted; needs review"):
            ecb.fetch(Replay(), family)
    finally:
        RUN_INSTANT.reset(token)


@pytest.mark.parametrize("family", [ecb.LOANS, ecb.COST])
def test_saved_issuer_listing_discovers_exact_dated_release(monkeypatch, family):
    monkeypatch.setattr(ecb, "ARCHIVES", {ecb.LOANS: {}, ecb.COST: {}})
    # Replay's URL map must retain the real archive independently of the
    # production module's bootstrap map being removed for this discovery test.
    original = {document(f"ecb-{tag}-{edition}.html").url: document(f"ecb-{tag}-{edition}.html")
                for tag, editions in (("bsi", ("2026-08-27", "2026-09-25")), ("mir", ("2026-09-02", "2026-10-01")))
                for edition in editions}
    class Discover(Replay):
        def get(self, provider, url, params=None):
            return original[url] if url in original else super().get(provider, url, params)
    assert len(ecb.fetch(Discover(), family)) == 2


def test_next_due_missing_listing_link_has_grace_and_dst_gate():
    token = RUN_INSTANT.set("2026-10-27T08:59:59Z")
    pending = PENDING_EDITIONS.set({})
    try:
        assert len(ecb.fetch(Replay(), ecb.LOANS)) == 2
        RUN_INSTANT.set("2026-10-27T09:00:00Z")
        assert len(ecb.fetch(Replay(), ecb.LOANS)) == 2
        assert (ecb.LOANS, "2026-10-27") in PENDING_EDITIONS.get()
        RUN_INSTANT.set("2026-10-27T15:00:00Z")
        PENDING_EDITIONS.get().clear()
        with pytest.raises(FetchError, match="link missing; needs review"):
            ecb.fetch(Replay(), ecb.LOANS)
    finally:
        RUN_INSTANT.reset(token)
        PENDING_EDITIONS.reset(pending)


def test_due_404_grace_then_review_and_no_series_fetch_for_pending_head():
    class Missing(Replay):
        def get(self, provider, url, params=None):
            if url == ecb.ARCHIVES[ecb.COST]["2026-10-01"]:
                raise FetchError("ecb: HTTP 404")
            return super().get(provider, url, params)
    token = RUN_INSTANT.set("2026-10-01T08:00:00Z")
    pending = PENDING_EDITIONS.set({})
    try:
        replay = Missing()
        assert [o.period for o in ecb.fetch(replay, ecb.COST)] == ["2026-07"]
        assert (ecb.COST, "2026-10-01") in PENDING_EDITIONS.get()
        assert ecb.series_url(ecb.COST) not in {url for url, _ in replay.requests}
        RUN_INSTANT.set("2026-10-01T14:00:00Z")
        PENDING_EDITIONS.get().clear()
        with pytest.raises(FetchError, match="missing; needs review"):
            ecb.fetch(Missing(), ecb.COST)
    finally:
        RUN_INSTANT.reset(token)
        PENDING_EDITIONS.reset(pending)


def test_daily_official_cards_schedule_membership_history_and_idempotence(tmp_path, monkeypatch):
    monkeypatch.setattr(run_daily, "HttpClient", Replay)
    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 24, 18, tzinfo=timezone.utc)
    monkeypatch.setattr(run_daily, "datetime", Clock)
    target = tmp_path / "ecb.sqlite"
    result = run_daily.execute(target, [ecb.LOANS, ecb.COST], 24, False, raw_dir=tmp_path / "raw")
    for family in (ecb.LOANS, ecb.COST):
        assert (result["families"][family]["status"], result["families"][family]["vintages_added"]) == ("ok", 2), result
    with open_reader(target) as store:
        cards = store.release_cards("2026-09-25", "2026-10-01", indicators=list(ecb.INDICATORS.values()))["cards"]
        assert {c["indicator_id"]: c["resolution"]["value"] for c in cards} == {"ea_loans_house_purchase_yy": 3.1, "ea_cost_borrow_house": 3.6}
        assert all(c["issuer_id"] == "ECB" and c["source_class"] == "official" and c["reference_period"] == "2026-08" and
                   c["resolution"]["status"] == "official" and c["released_at"].endswith("T08:00:00.000000+00:00") for c in cards)
        history = store.latest_official("ea_loans_house_purchase_yy")
        assert {r["reference_period"]: r["value"] for r in history} == {"2026-07": 3.0, "2026-08": 3.1}
    again = run_daily.execute(target, [ecb.LOANS, ecb.COST], 24, False, raw_dir=tmp_path / "raw")
    assert all(r["status"] == "no_change" for r in again["families"].values()), again


def test_changed_sdmx_retains_evidence_without_partial_certification(tmp_path, monkeypatch):
    class Changed(Replay):
        def get(self, provider, url, params=None):
            doc = super().get(provider, url, params)
            if url == ecb.series_url(ecb.COST):
                body = doc.body.replace(b",3.6,A,F,", b",9.9,A,F,")
                assert body != doc.body
                return replace(doc, body=body, document_hash=hashlib.sha256(body).hexdigest())
            return doc
    monkeypatch.setattr(run_daily, "HttpClient", Changed)
    target = tmp_path / "review.sqlite"
    result = run_daily.execute(target, [ecb.COST], 24, False, raw_dir=tmp_path / "raw")
    assert result["families"][ecb.COST]["status"] == "needs_review", result
    with open_reader(target) as store:
        assert store.conn.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0] > 0
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 0


def test_cards_use_approved_mir_alias_and_separate_pending_mortgage_growth_draft():
    from market_data_hub.indicator_cards import indicator_card
    from market_data_hub.indicator_cards.coverage import DATA_DIR, draft_cards
    assert indicator_card("cat:ea_cost_borrow_house").key == "pan_mir"
    card = indicator_card("cat:ea_loans_house_purchase_yy")
    assert card.key == "ea_loans_house_purchase"
    assert not [c for c in draft_cards() if c.key == "ea_loans_house_purchase"]
    assert card.timing.timing_class == "UNKNOWN" and card.timing.basis is None
    assert card.ids.cat == ("ea_loans_house_purchase_yy",)
    assert "data.ecb.europa.eu" in card.rationale and "approval" not in card.flag
    assert json.loads((DATA_DIR / "timing_v2.3-draft.json").read_text())["approval_status"] == "pending_owner_approval"
    from market_data_hub.macro_releases.consensus import ALIAS_FILE
    aliases = json.loads(ALIAS_FILE.read_text())["aliases"]
    assert not any(a["indicator_id"] in ecb.INDICATORS.values() for a in aliases)
