"""Offline official Japanese/Chinese publication and edition regression tests."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest
import requests

from market_data_hub.macro_releases import open_writer, run_daily
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.asia import AsiaAdapter
from market_data_hub.macro_releases.asia_editions import EDITIONS, SCHEDULES
from market_data_hub.macro_releases.catalogue import ASIA_FAMILIES
from market_data_hub.macro_releases.http import Document, FetchError
from market_data_hub.macro_releases.schedule import RUN_INSTANT, due

ROOT = Path(__file__).parent / "fixtures/macro_releases/m9d1"
GOLDEN = json.loads((ROOT / "golden-set.json").read_text())["cases"]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network forbidden in M9d1 tests")
    monkeypatch.setattr(requests.Session, "request", forbidden)
    token = RUN_INSTANT.set("2026-10-27T18:00:00Z")
    yield
    RUN_INSTANT.reset(token)


def document(name):
    body = (ROOT / name).read_bytes()
    meta = json.loads((ROOT / (name + ".meta.json")).read_text())
    assert hashlib.sha256(body).hexdigest() == meta["document_hash"]
    return Document(body, meta["url"], meta["retrieved_at"], meta["document_hash"])


def parser(family):
    from market_data_hub.macro_releases import nbs_macro, japan_macro
    return (japan_macro if family.startswith("jp_") else nbs_macro).parse


@pytest.mark.parametrize("case", GOLDEN, ids=lambda c: c["family"] + ":" + c["edition"] + ":" + c["indicator"])
def test_issuer_golden_and_read_card(case, tmp_path):
    rows = parser(case["family"])(document(case["fixture"]), case["family"])
    row = next(r for r in rows if r.indicator_id == case["indicator"])
    assert (row.value, row.period, row.edition, row.stage) == (
        case["value"], case["period"], case["edition"], case["stage"])
    assert row.document.url == case["source_url"]
    assert row.series_key.startswith(row.indicator_id + ":")
    with open_writer(tmp_path / "golden.sqlite") as store:
        persist(store, [row])
        utc_day = row.published_at[:10]
        cards = store.release_cards(utc_day, utc_day, indicators=[row.indicator_id])["cards"]
        actual = next(c for c in cards if c.get("resolution"))
        assert actual["source_class"] == actual["resolution"]["status"] == "official"
        assert actual["resolution"]["value"] == case["value"]
        assert actual["resolution"]["edition"] == case["edition"]


def test_family_calendars_and_golden_completeness():
    for family in ASIA_FAMILIES:
        cases = [c for c in GOLDEN if c["family"] == family]
        assert len({c["edition"] for c in cases}) >= 2
        assert SCHEDULES[family].startswith("https://")
        assert max(EDITIONS[family]) <= "2026-12-31"
        assert {c["indicator"] for c in cases} == {i.id for i in ASIA_FAMILIES[family]}


class Replay:
    def __init__(self, **kwargs):
        self.requests = []

    def get(self, provider, url, params=None):
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        self.requests.append(url)
        for path in ROOT.glob("*.meta.json"):
            meta = json.loads(path.read_text())
            if meta["url"] == url:
                return document(path.name.removesuffix(".meta.json"))
        raise AssertionError("Unrecorded issuer URL: " + url)


@pytest.mark.parametrize("family", ASIA_FAMILIES)
def test_daily_schedules_and_idempotence(family, tmp_path, monkeypatch):
    monkeypatch.setattr(run_daily, "HttpClient", Replay)
    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 27, 18, tzinfo=timezone.utc)
    monkeypatch.setattr(run_daily, "datetime", Clock)
    target = tmp_path / "daily.sqlite"
    first = run_daily.execute(target, [family], 24, False, raw_dir=tmp_path / "none")
    assert first["families"][family]["status"] == "ok", first
    assert first["families"][family]["vintages_added"] >= 2
    second = run_daily.execute(target, [family], 24, False, raw_dir=tmp_path / "none")
    assert second["families"][family]["status"] == "no_change", second


def test_cpi_unknown_date_clock_measure_and_disagreement_fail_closed():
    doc = document("cn-cpi-sep.html")
    for before, after in ((b"2026/10/15 09:30", b"2026/10/16 09:30"),
                          (b"2026/10/15 09:30", b"2026/10/15 10:00"),
                          (b"Consumer Price Index in September 2026", b"Consumer Price Index in August 2026"),
                          (b"increased by 0.9% year on year", b"increased by 9.9% year on year")):
        assert before in doc.body
        with pytest.raises(FetchError, match="needs review"):
            parser("cn_cpi")(replace(doc, body=doc.body.replace(before, after)), "cn_cpi")


def test_release_instant_and_horizon_fail_closed():
    client = Replay()
    token = RUN_INSTANT.set("2026-09-10T01:29:59Z")
    try:
        assert AsiaAdapter(client).fetch(["cn_cpi"], "2026-01") == []
        assert client.requests == []
    finally:
        RUN_INSTANT.reset(token)
    token = RUN_INSTANT.set("2027-01-01T00:00:00Z")
    try:
        with pytest.raises(FetchError, match="needs review"):
            AsiaAdapter(client).fetch(["cn_cpi"], "2026-01")
    finally:
        RUN_INSTANT.reset(token)


@pytest.mark.parametrize("family", ASIA_FAMILIES)
def test_unreviewed_source_url_fails_closed(family):
    case = next(c for c in GOLDEN if c["family"] == family)
    with pytest.raises(FetchError, match="needs review"):
        parser(family)(replace(document(case["fixture"]), url="https://example.invalid/unreviewed"), family)


def test_gdp_cumulative_columns_and_constant_price_basis_are_required():
    doc = document("cn-gdp-native-q3.html")
    for before, after in (("前三季度", "三季度"), ("增长速度按不变价计算", "增长速度按现价计算")):
        raw = doc.body.decode("utf-8")
        assert before in raw
        with pytest.raises(FetchError, match="needs review"):
            parser("cn_gdp")(replace(doc, body=raw.replace(before, after).encode()), "cn_gdp")


def test_tokyo_release_date_claim_and_local_release_instant():
    assert "2026-10-22" not in EDITIONS["jp_tokyo_cpi"]
    assert EDITIONS["jp_tokyo_cpi"]["2026-10-30"][0] == "2026-10"
    client = Replay()
    token = RUN_INSTANT.set("2026-10-29T23:29:59Z")
    try:
        assert AsiaAdapter(client).fetch(["jp_tokyo_cpi"], "2026-10") == []
        assert client.requests == []
    finally:
        RUN_INSTANT.reset(token)
    assert "2026-10-30" in due(EDITIONS["jp_tokyo_cpi"], "2026-10-29T23:30:00Z", family="jp_tokyo_cpi")


def test_only_reviewed_stages_have_calendar_membership():
    from market_data_hub.macro_releases.read_api import indicators_for_stage
    for family in ASIA_FAMILIES:
        assert indicators_for_stage(family, "unreviewed-stage") == set()
    assert indicators_for_stage("jp_trade_balance", "final") == set()
    assert indicators_for_stage("jp_tokyo_cpi", "final") == set()


def test_real_issuer_schedule_evidence_covers_the_reviewed_horizon():
    from io import BytesIO
    from openpyxl import load_workbook
    from market_data_hub.macro_releases.tier1_publications import publication_text
    files = {json.loads(p.read_text())["url"] for p in ROOT.glob("*.meta.json")}
    assert set(SCHEDULES.values()).issubset(files)
    cpi, _ = publication_text(document("cpi-calendar.html"))
    assert "October 30" in cpi and "December 25" in cpi and "December 18" in cpi
    nbs, _ = publication_text(document("nbs-calendar.html"))
    assert "Regular Press Release Calendar of NBS in 2026" in nbs
    assert "31/Sat 30/Mon 31/Thu" in nbs
    gdp, _ = publication_text(document("jp-gdp-schedule.pdf"))
    assert "November 16, 2026" in gdp and "December 8, 2026" in gdp
    workbook = load_workbook(BytesIO(document("boj-release.xlsx").body), data_only=True)
    try:
        rows = [r for r in workbook['Statistics data'].values
                if r[1] == 'Tankan (Short-Term Economic Survey of Enterprises in Japan) / Summary and Outline']
        assert rows[0][3] == '8:50 a.m.'
        assert rows[1][7].date().isoformat() == '2026-10-01'
        assert rows[1][9].date().isoformat() == '2026-12-14'
    finally:
        workbook.close()


def test_e_stat_index_sheet_cannot_replace_published_rate_sheet():
    from io import BytesIO
    from openpyxl import load_workbook
    doc = document("jp-cpi-sep.xlsx")
    workbook = load_workbook(BytesIO(doc.body))
    try:
        sheet = next(s for s in workbook if s['H8'].value == 'Monthly, % Change over the year')
        sheet['H8'] = 'Monthly, Index'
        stream = BytesIO()
        workbook.save(stream)
    finally:
        workbook.close()
    with pytest.raises(FetchError, match="needs review"):
        parser("jp_cpi")(replace(doc, body=stream.getvalue()), "jp_cpi")


def test_trade_units_and_provisional_stage_must_match(monkeypatch):
    from market_data_hub.macro_releases import japan_macro
    from market_data_hub.macro_releases.tier1_publications import publication_text
    doc = document("trade-sep.pdf")
    body, method = publication_text(doc)
    for before, after in (("millions of YEN", "billions of YEN"), ("(Provisional)", "(Final)")):
        assert before in body
        monkeypatch.setattr(japan_macro, "publication_text", lambda _: (body.replace(before, after), method))
        with pytest.raises(FetchError, match="needs review"):
            japan_macro.parse(doc, "jp_trade_balance")
