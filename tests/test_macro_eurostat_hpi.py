"""Offline real Eurostat HPI publications, area identity and release gates."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json

import pytest

from market_data_hub.macro_releases import open_reader, run_daily
from market_data_hub.macro_releases import eurostat_hpi as hpi
from market_data_hub.macro_releases.housing_editions import EDITIONS
from market_data_hub.macro_releases.http import FetchError
from market_data_hub.macro_releases.schedule import PENDING_EDITIONS, RUN_INSTANT, release_instant
from test_macro_m9f import document, offline as offline


class Replay:
    def __init__(self, **kwargs):
        self.requests = []

    def get(self, provider, url, params=None):
        assert provider == "eurostat"
        self.requests.append(url)
        if url == hpi.CALENDAR:
            assert params == hpi.CALENDAR_PARAMS
            return document("eurostat-hpi-calendar-2026.json")
        assert not params
        for edition in EDITIONS[hpi.FAMILY]:
            if url == hpi.publication_url(edition):
                return document("eurostat-hpi-" + edition + ".html")
        raise AssertionError("Unexpected HPI request: " + url)


def with_calendar(rows):
    doc = document("eurostat-hpi-calendar-2026.json")
    return replace(doc, body=json.dumps(rows).encode())


def hpi_rows():
    return [row for row in document("eurostat-hpi-calendar-2026.json").json()
            if row.get("euroindAuthor") == "estat" and row.get("title") == "House price index"
            and row["start"][:10] >= "2026-07-01"]


def test_reviewed_calendar_and_published_clocks():
    assert hpi.calendar_editions(document("eurostat-hpi-calendar-2026.json")) == EDITIONS[hpi.FAMILY]
    assert set(EDITIONS[hpi.FAMILY]) == {"2026-07-02", "2026-10-01"}
    for edition, (period, stage, time) in EDITIONS[hpi.FAMILY].items():
        assert (stage, time) == ("regular", "11:00")
        assert release_instant(edition, family=hpi.FAMILY).hour == 9
        assert all(o.period == period and o.composition_id == "EA21" for o in
                   hpi.parse(document("eurostat-hpi-" + edition + ".html")))
    assert b"11 January 2027" in document("eurostat-hpi-2026-10-01.html").body


@pytest.mark.parametrize("field,value", [
    ("title", "Owner-occupied house price index"), ("preliminary", True),
    ("period", "Q3/2026"), ("datasetCodes", "prc_hpi_a"),
    ("start", "2026-10-15T11:00Z"), ("euroindAuthor", "other"),
])
def test_changed_calendar_requires_review(field, value):
    rows = hpi_rows()
    rows[-1][field] = value
    with pytest.raises(FetchError, match="needs review"):
        hpi.calendar_editions(with_calendar(rows))


def test_missing_duplicate_and_new_in_horizon_editions_require_review():
    rows = hpi_rows()
    for changed in (rows[:-1], rows + [dict(rows[-1])],
                    rows + [dict(rows[-1], start="2026-12-01T11:00Z", period="Q3/2026")]):
        with pytest.raises(FetchError, match="needs review"):
            hpi.calendar_editions(with_calendar(changed))
    # The allDay calendar field is not a UTC publication clock.
    rows[-1]["start"] = "2026-10-01T00:00Z"
    assert hpi.calendar_editions(with_calendar(rows)) == EDITIONS[hpi.FAMILY]


@pytest.mark.parametrize("before,after", [
    (b"2026-10-01T09:00:00Z", b"2026-10-01T10:00:00Z"),
    (b"2026-10-01T09:00:00Z", b"2026-10-01T09:00:00"),
    (b"4.0%", b"9.9%"),
    (b"1.1%", b"9.9%"),
    (b"In the second quarter of 2026, house prices", b"In the third quarter of 2026, house prices"),
    (b"Compared with the first quarter of 2026", b"Compared with the fourth quarter of 2025"),
    (b"The figures are not seasonally adjusted.", b"The figures are seasonally adjusted."),
    (b"both newly built and existing", b"only newly built"),
    (b"Euro area (EA21)", b"Euro area (EA20)"),
    (b"Change compared with the previous quarter, %", b"Index, 2025=100"),
    (b"<strong>Q2</strong>", b"<strong>Q3</strong>"),
])
def test_changed_identity_period_clock_or_disagreement_fails_closed(before, after):
    doc = document("eurostat-hpi-2026-10-01.html")
    assert before in doc.body
    with pytest.raises(FetchError, match="needs review"):
        hpi.parse(replace(doc, body=doc.body.replace(before, after)))


def test_equivalent_offset_timestamp_and_issuer_url_validation():
    doc = document("eurostat-hpi-2026-10-01.html")
    offset = doc.body.replace(b"2026-10-01T09:00:00Z", b"2026-10-01T11:00:00+02:00")
    assert [o.value for o in hpi.parse(replace(doc, body=offset))] == [1.1, 4.0]
    for url in (doc.url.replace("ec.europa.eu", "example.com"), doc.url.replace("-bp", "-ap"),
                doc.url + "?edition=2026-10-01"):
        with pytest.raises(FetchError, match="needs review"):
            hpi.parse(replace(doc, url=url))
    with pytest.raises(FetchError, match="needs review"):
        hpi.parse(replace(doc, body=offset.replace(b"+02:00", b"+01:00")))


def test_july_uses_ea21_instead_of_ea20_and_since_is_a_month_cutoff():
    july = document("eurostat-hpi-2026-07-02.html")
    assert [o.value for o in hpi.parse(july)] == [1.0, 4.7]  # EA20 is 0.9 / 4.6
    assert hpi.parse(july, since="2026-04") == []
    assert {o.edition for o in hpi.fetch(Replay(), since="2026-04")} == {"2026-10-01"}
    replay = Replay()
    assert hpi.fetch(replay, since="2026-07") == [] and replay.requests == []


@pytest.mark.parametrize("instant,editions", [
    ("2026-07-02T08:59:59Z", []), ("2026-07-02T09:00:00Z", ["2026-07-02"]),
    ("2026-10-01T08:59:59Z", ["2026-07-02"]),
    ("2026-10-01T09:00:00Z", ["2026-07-02", "2026-10-01"]),
])
def test_release_instant_gate(instant, editions):
    replay = Replay()
    token = RUN_INSTANT.set(instant)
    try:
        assert sorted({o.edition for o in hpi.fetch(replay)}) == editions
        if not editions:
            assert replay.requests == []
        assert hpi.publication_url("2026-10-01") not in replay.requests or "2026-10-01" in editions
    finally:
        RUN_INSTANT.reset(token)


def test_new_release_404_has_six_hour_grace_then_needs_review():
    class Missing(Replay):
        def get(self, provider, url, params=None):
            if url == hpi.publication_url("2026-10-01"):
                raise FetchError("eurostat: HTTP 404")
            return super().get(provider, url, params)
    token = RUN_INSTANT.set("2026-10-01T09:00:00Z")
    pending = PENDING_EDITIONS.set({})
    try:
        assert {o.edition for o in hpi.fetch(Missing())} == {"2026-07-02"}
        assert (hpi.FAMILY, "2026-10-01") in PENDING_EDITIONS.get()
        RUN_INSTANT.set("2026-10-01T15:00:00Z")
        PENDING_EDITIONS.get().clear()  # each real daily run has fresh pending state
        with pytest.raises(FetchError, match="missing; needs review"):
            hpi.fetch(Missing())
    finally:
        RUN_INSTANT.reset(token)
        PENDING_EDITIONS.reset(pending)


def test_expired_calendar_requires_review_without_fetching_unreviewed_editions():
    token = RUN_INSTANT.set("2027-01-01T00:00:00Z")
    try:
        replay = Replay()
        with pytest.raises(FetchError, match="horizon exhausted; needs review"):
            hpi.fetch(replay)
        assert replay.requests == []
    finally:
        RUN_INSTANT.reset(token)


def test_daily_cards_separate_editions_and_idempotence(tmp_path, monkeypatch):
    monkeypatch.setattr(run_daily, "HttpClient", Replay)
    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 24, 18, tzinfo=timezone.utc)
    monkeypatch.setattr(run_daily, "datetime", Clock)
    db = tmp_path / "hpi.sqlite"
    result = run_daily.execute(db, [hpi.FAMILY], 24, False, raw_dir=tmp_path / "none")
    assert (result["families"][hpi.FAMILY]["status"], result["families"][hpi.FAMILY]["vintages_added"]) == ("ok", 4), result
    with open_reader(db) as store:
        cards = store.release_cards("2026-10-01", "2026-10-01", indicators=["ea21_hpi_qq", "ea21_hpi_yy"])["cards"]
        assert {c["indicator_id"]: c["resolution"]["value"] for c in cards} == {"ea21_hpi_qq": 1.1, "ea21_hpi_yy": 4.0}
        assert all(c["issuer_id"] == "Eurostat" and c["source_class"] == "official" and
                   c["resolution"]["status"] == "official" and c["reference_period"] == "2026-Q2" and
                   c["released_at"] == "2026-10-01T09:00:00.000000+00:00" for c in cards)
        history = store.latest_official("ea21_hpi_yy")
        assert {row["reference_period"]: row["value"] for row in history} == {"2026-Q1": 4.7, "2026-Q2": 4.0}
    again = run_daily.execute(db, [hpi.FAMILY], 24, False, raw_dir=tmp_path / "none")
    assert again["families"][hpi.FAMILY]["status"] == "no_change", again


def test_changed_issuer_page_is_retained_for_review_without_partial_certification(tmp_path, monkeypatch):
    class Changed(Replay):
        def get(self, *args, **kwargs):
            doc = super().get(*args, **kwargs)
            body = doc.body.replace(b"Euro area (EA21)", b"Euro area (EA20)")
            return replace(doc, body=body, document_hash=hashlib.sha256(body).hexdigest())
    monkeypatch.setattr(run_daily, "HttpClient", Changed)
    db = tmp_path / "review.sqlite"
    result = run_daily.execute(db, [hpi.FAMILY], 24, False, raw_dir=tmp_path / "none")
    assert result["families"][hpi.FAMILY]["status"] == "needs_review", result
    with open_reader(db) as store:
        assert store.conn.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0] > 0
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 0


def test_ea_hpi_card_is_approved_as_a_measurement_identity():
    from market_data_hub.indicator_cards import indicator_card
    from market_data_hub.indicator_cards.coverage import draft_cards
    card = indicator_card("cat:ea21_hpi_qq")
    assert card.key == "ea_hpi" and indicator_card("cat:ea21_hpi_yy").key == "ea_hpi"
    assert card.timing.timing_class == "COIN" and card.timing.basis == "DEFINITION"
    assert set(card.ids.cat) == {"ea21_hpi_qq", "ea21_hpi_yy"}
    assert not [c for c in draft_cards() if c.key == "ea_hpi"]
