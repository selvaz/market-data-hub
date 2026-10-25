"""Offline NBS new-residential city-tier headlines and dated release calendar."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib

from bs4 import BeautifulSoup
import pytest

from market_data_hub.macro_releases import nbs_housing as cn, open_reader, run_daily
from market_data_hub.macro_releases.http import FetchError
from market_data_hub.macro_releases.schedule import RUN_INSTANT, PENDING_EDITIONS, release_instant
from test_macro_m9f import document, offline as offline


class Replay:
    def __init__(self, **kwargs):
        self.requests = []

    def get(self, provider, url, params=None):
        assert provider == "nbs" and not params
        self.requests.append(url)
        if url == cn.CALENDAR:
            return document("nbs-calendar2026.html")
        if url == cn.INDEX:
            return document("nbs-interpretation-index.html")
        for name in ("nbs-august2026-interpretation.html", "nbs-september2026-headlines.html"):
            doc = document(name)
            if url == doc.url:
                return doc
        raise AssertionError("Unexpected NBS request: " + url)


def changed(doc, before, after, *, first_only=False):
    soup = BeautifulSoup(doc.body.decode("utf-8"), "html.parser")
    editors = soup.select(".trs_editor_view")
    assert editors
    for editor in editors[:1] if first_only else editors:
        text = cn.compact(str(editor))
        assert before in text
        editor.clear()
        editor.append(text.replace(before, after))
    body = str(soup).encode()
    return replace(doc, body=body, document_hash=hashlib.sha256(body).hexdigest())


def test_calendar_regular_stages_and_real_discovery(monkeypatch):
    from market_data_hub.macro_releases.read_api import indicators_for_stage
    assert set(cn.calendar_editions(document("nbs-calendar2026.html"))) == {"2026-09-15", "2026-10-19", "2026-11-16", "2026-12-15"}
    assert release_instant("2026-10-19", family=cn.FAMILY).isoformat() == "2026-10-19T01:30:00+00:00"
    assert cn.publication_links(document("nbs-interpretation-index.html"))["2026-10-19"] == cn.ARCHIVES["2026-10-19"]
    monkeypatch.setattr(cn, "ARCHIVES", {"2026-09-15": cn.ARCHIVES["2026-09-15"]})
    assert len(cn.fetch(Replay())) == 12
    assert len(indicators_for_stage(cn.FAMILY, "regular")) == 6
    assert indicators_for_stage(cn.FAMILY, "advance") == set()


def test_published_tiers_and_flat_transition_are_not_a_national_average():
    august = cn.parse(document("nbs-august2026-interpretation.html"))
    september = cn.parse(document("nbs-september2026-headlines.html"))
    assert [r.value for r in august] == [0.1, -0.9, -0.1, -2.7, -0.2, -4.1]
    assert [r.value for r in september] == [0.1, -0.5, 0.0, -2.4, -0.2, -3.9]
    assert {r.period for r in september} == {"2026-09"}
    assert {r.indicator_id for r in september} == {f"cn_new_home_prices_tier{tier}_{basis}" for tier in (1, 2, 3) for basis in ("mm", "yy")}
    assert cn.monthly_change("由上月下降0.1%转为持平")[0] == 0.0
    assert cn.parse(document("nbs-september2026-headlines.html"), since="2026-10") == []
    doc = document("nbs-september2026-headlines.html")
    for url in (doc.url.replace("20261019", "20261018"), doc.url.replace("www.stats.gov.cn", "example.com")):
        with pytest.raises(FetchError, match="needs review"):
            cn.parse(replace(doc, url=url))


@pytest.mark.parametrize("before,after", [
    ("新建商品住宅", "二手住宅"),
    ("2026年9月份", "2026年8月份"),
    ("等31个城市", "等32个城市"),
    ("同比分别下降2.4%和3.9%", "同比分别下降未公布和3.9%"),
])
def test_changed_product_period_city_scope_or_value_needs_review(before, after):
    with pytest.raises(FetchError, match="needs review"):
        cn.parse(changed(document("nbs-september2026-headlines.html"), before, after))


def test_conflicting_mobile_and_desktop_copies_need_review():
    doc = changed(document("nbs-september2026-headlines.html"), "同比分别下降2.4%和3.9%", "同比分别下降2.5%和3.9%", first_only=True)
    with pytest.raises(FetchError, match="agreement needs review"):
        cn.parse(doc)


@pytest.mark.parametrize("stamp", ["2026/10/19 10:30", "2026/10/18 09:30"])
def test_changed_publication_instant_needs_review(stamp):
    doc = document("nbs-september2026-headlines.html")
    soup = BeautifulSoup(doc.body.decode("utf-8"), "html.parser")
    soup.find("meta", attrs={"name": "PubDate"})["content"] = stamp
    with pytest.raises(FetchError, match="needs review"):
        cn.parse(replace(doc, body=str(soup).encode()))


@pytest.mark.parametrize("clock", [False, True])
def test_calendar_date_or_clock_drift_needs_review(clock):
    doc = document("nbs-calendar2026.html")
    soup = BeautifulSoup(doc.body.decode("utf-8"), "html.parser")
    found = 0
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) == 14 and cn.compact(str(cells[1])) == "商品住宅销售价格指数月度报告":
            cell = row.find_next_sibling("tr").find_all("td")[10] if clock else cells[12]
            cell.clear()
            cell.append("10:30" if clock else "17/二")
            found += 1
    assert found
    with pytest.raises(FetchError, match="needs review"):
        cn.calendar_editions(replace(doc, body=str(soup).encode()))


def test_release_instant_publication_delay_and_horizon():
    class Missing(Replay):
        def get(self, provider, url, params=None):
            if url == cn.ARCHIVES["2026-10-19"]:
                raise FetchError("nbs: HTTP 404")
            return super().get(provider, url, params)
    token = RUN_INSTANT.set("2026-09-15T01:29:59Z")
    pending = PENDING_EDITIONS.set({})
    try:
        client = Replay()
        assert cn.fetch(client) == [] and client.requests == []
        RUN_INSTANT.set("2026-10-19T01:29:59Z")
        assert len(cn.fetch(Replay())) == 6
        RUN_INSTANT.set("2026-10-19T01:30:00Z")
        assert len(cn.fetch(Replay())) == 12
        assert len(cn.fetch(Missing())) == 6
        assert (cn.FAMILY, "2026-10-19") in PENDING_EDITIONS.get()
        PENDING_EDITIONS.get().clear()
        RUN_INSTANT.set("2026-10-19T07:30:00Z")
        with pytest.raises(FetchError, match="missing; needs review"):
            cn.fetch(Missing())
        RUN_INSTANT.set("2027-01-01T00:00:00Z")
        with pytest.raises(FetchError, match="horizon exhausted; needs review"):
            cn.fetch(Replay())
    finally:
        RUN_INSTANT.reset(token)
        PENDING_EDITIONS.reset(pending)


def test_latest_official_tier_cards_repeat_and_scope_review(tmp_path, monkeypatch):
    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 24, 18, tzinfo=timezone.utc)
    monkeypatch.setattr(run_daily, "datetime", Clock)
    monkeypatch.setattr(run_daily, "HttpClient", Replay)
    path = tmp_path / "nbs.sqlite"
    result = run_daily.execute(path, [cn.FAMILY], 24, False, raw_dir=tmp_path / "raw")
    assert result["families"][cn.FAMILY]["status"] == "ok", result
    with open_reader(path) as store:
        cards = store.release_cards("2026-10-19", "2026-10-19", indicators=[f"cn_new_home_prices_tier{tier}_{basis}" for tier in (1, 2, 3) for basis in ("mm", "yy")])["cards"]
        assert len(cards) == 6
        assert {c["reference_period"] for c in cards} == {"2026-09"}
        assert {c["source_class"] for c in cards} == {"official"}
        assert {c["released_at"] for c in cards} == {"2026-10-19T01:30:00.000000+00:00"}
    assert run_daily.execute(path, [cn.FAMILY], 24, False, raw_dir=tmp_path / "raw")["families"][cn.FAMILY]["status"] == "no_change"
    class Changed(Replay):
        def get(self, *args, **kwargs):
            doc = super().get(*args, **kwargs)
            if "/sj/sjjd/2026" in doc.url:
                return changed(doc, "新建商品住宅", "二手住宅")
            return doc
    monkeypatch.setattr(run_daily, "HttpClient", Changed)
    fresh = tmp_path / "review.sqlite"
    assert run_daily.execute(fresh, [cn.FAMILY], 24, False, raw_dir=tmp_path / "raw")["families"][cn.FAMILY]["status"] == "needs_review"
    with open_reader(fresh) as store:
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 0
