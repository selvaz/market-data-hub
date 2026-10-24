"""M9a: real saved API/publication certification and fully offline daily replay."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest
import requests

from market_data_hub.macro_releases import open_reader, open_writer, run_daily
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.bea import BEAAdapter
from market_data_hub.macro_releases.bls import BLSAdapter, shift_month
from market_data_hub.macro_releases.catalogue import FAMILIES, INDICATORS, US_MONTHLY
from market_data_hub.macro_releases.fred import FREDAdapter, MAPPINGS
from market_data_hub.macro_releases.http import Document, FetchError
from market_data_hub.macro_releases.us_editions import EDITIONS as CALENDAR
from market_data_hub.macro_releases.schedule import RUN_DATE, due
from market_data_hub.macro_releases.us_publications import PARSERS, parse_pce_publication

ROOT = Path(__file__).parent / "fixtures/macro_releases/us_monthly"
GOLDEN = json.loads((ROOT / "golden.json").read_text())['cases']
EDITIONS = {f: due(editions, "2026-10-24") for f, editions in CALENDAR.items()}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr("market_data_hub.macro_releases.schedule.run_date", lambda: "2026-10-24")
    def forbidden(*args, **kwargs):
        raise AssertionError("Network forbidden in monthly release certification")
    monkeypatch.setattr(requests.Session, "request", forbidden)
    for module in ("bls", "bea", "fred"):
        monkeypatch.setattr("market_data_hub.macro_releases." + module + ".api_key", lambda _: "fixture-key")


def document(name):
    body = (ROOT / name).read_bytes()
    meta = json.loads((ROOT / (name + ".meta.json")).read_text())
    assert hashlib.sha256(body).hexdigest() == meta["sha256"]
    return Document(body, meta["url"], meta["retrieved_at"], meta["sha256"])


def parse(family, name):
    doc = document(name)
    if name == "bls.json":
        return BLSAdapter(None).parse(doc, [family], "2026-05")
    if name == "T20807.json":
        return BEAAdapter.parse_pce(doc, since="2026-05")
    return (parse_pce_publication if family == "us_pce" else PARSERS[family])(doc)


@pytest.mark.parametrize("case", GOLDEN, ids=lambda c: c["indicator"] + ":" + c["period"])
def test_published_golden(case, tmp_path):
    rows = parse(case["family"], case["fixture"])
    obs = next(o for o in rows if o.indicator_id == case["indicator"] and o.period == case["period"])
    assert obs.value == case["value"] and obs.edition == case["edition"]
    assert obs.provider_vintage == obs.edition and obs.stage
    assert INDICATORS[obs.indicator_id].issuer_id == obs.source
    assert case["source_url"].startswith("https://")
    if case.get("revision"):
        assert obs.period == shift_month(EDITIONS[obs.family][obs.edition][0], -1)
        assert obs.revision == "routine" and obs.value != case["prior_value"]
        if case.get("prior_fixture"):
            previous = next(o for o in parse(obs.family, case["prior_fixture"])
                            if o.indicator_id == obs.indicator_id and o.period == obs.period)
            assert previous.value == case["prior_value"]
    with open_writer(tmp_path / "golden.sqlite") as store:
        persist(store, [obs])
        assert store.latest_official(obs.indicator_id)[0]["value"] == case["value"]


class Replay:
    def __init__(self, **kwargs):
        pass

    def request(self, provider, url, **kwargs):
        assert provider == "bls"
        assert set(kwargs["payload"]["seriesid"]) <= {"WPSFD4", "WPUFD4", "JTS000000000000000JOL"}
        return document("bls.json")

    def get(self, provider, url, params=None):
        if params and "series_id" in params:
            series = params["series_id"]
            family = next(f for f, s, _ in MAPPINGS.values() if s == series)
            edition = params.get("realtime_start", max(EDITIONS[family]))
            if "realtime_start" in params:
                assert params["realtime_end"] == edition
            else:
                assert params["sort_order"] == "desc" and params["limit"] == 1
                return document(series + "-current.json")
            return document(series + "-" + edition + ".json")
        if params and params.get("TableName") == "T20807":
            assert params["Frequency"] == "M"
            return document("T20807.json")
        for name in ("housing.pdf", "retail.pdf", "durable.pdf", "pce.html", "pce-july.html",
                     "ip.html", "ip-august.html", "ip-july.html", "trade.html", "trade-july.html", "trade-june.html"):
            if document(name).url == url:
                return document(name)
        raise AssertionError("Unexpected fixture URL: " + url)


def test_daily_all_eight_families_official_cards_and_idempotence(tmp_path, monkeypatch):
    monkeypatch.setattr(run_daily, "HttpClient", Replay)
    db = tmp_path / "monthly.sqlite"
    assert set(US_MONTHLY) <= set(FAMILIES) == set(run_daily.ADAPTERS)
    first = run_daily.execute(db, list(US_MONTHLY), 24, False, raw_dir=tmp_path / "missing-consensus")
    assert first["status"] == "ok", first
    assert all(row["vintages_added"] > 0 for row in first["families"].values())
    with open_reader(db) as store:
        cards = store.release_cards("2026-10-19", "2026-10-23")["cards"]
        assert {c["indicator_id"] for c in cards} == {"us_housing_starts", "us_building_permits"}
        assert all(c["resolution"]["status"] == "official" for c in cards)
        assert all(c["resolution"]["source"]["issuer_id"] == "Census" for c in cards)
        mirrors = store.conn.execute("SELECT * FROM source_series WHERE source_id='FRED'").fetchall()
        assert mirrors and all(r["evidence_role"] == "mirror" and r["issuer_id"] != "FRED" for r in mirrors)
        assert not store.conn.execute("SELECT 1 FROM value_vintages v JOIN source_series s ON s.id=v.source_series_id WHERE s.source_id='FRED' AND v.verification_status='verified'").fetchall()
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages WHERE provider_vintage_id IS NULL").fetchone()[0] == 0
    second = run_daily.execute(db, list(US_MONTHLY), 24, False, raw_dir=tmp_path / "missing-consensus")
    assert second["status"] == "no_change", second
    assert all(row["vintages_added"] == 0 for row in second["families"].values())


@pytest.mark.parametrize("indicator", [i for i, (f, _, _) in MAPPINGS.items() if f in US_MONTHLY])
def test_alfred_edition_units_and_source(indicator):
    family, series, units = MAPPINGS[indicator]
    edition = max(EDITIONS[family])
    doc = document(series + "-" + edition + ".json")
    rows = FREDAdapter.parse(doc, indicator=indicator, edition=edition)
    assert rows and all(o.source == "FRED" and o.provider_vintage == edition for o in rows)
    with pytest.raises(FetchError, match="needs review"):
        FREDAdapter.parse(doc, indicator=indicator, edition="2026-11-30")
    payload = doc.json()
    payload['units'] = 'wrong'
    with pytest.raises(FetchError, match="units"):
        FREDAdapter.parse(replace(doc, body=json.dumps(payload).encode()), indicator=indicator, edition=edition)


def test_bea_pce_missing_core_line_and_unknown_edition_fail_closed():
    doc = document("T20807.json")
    payload = doc.json()
    payload["BEAAPI"]["Results"]["Data"] = [r for r in payload["BEAAPI"]["Results"]["Data"] if r["LineNumber"] != "25"]
    with pytest.raises(FetchError, match="requested measure"):
        BEAAdapter.parse_pce(replace(doc, body=json.dumps(payload).encode()))
    with pytest.raises(FetchError, match="needs review"):
        BEAAdapter.parse_pce(replace(doc, body=doc.body.replace(b"September 30, 2026", b"October 29, 2026")))


@pytest.mark.parametrize("family", ["us_ppi", "us_jolts"])
def test_bls_new_month_requires_review(family):
    doc = document("bls.json")
    with pytest.raises(FetchError, match="needs review"):
        BLSAdapter(None).parse(replace(doc, body=doc.body.replace(b'"2026"', b'"2027"')), [family], "2026-05")


def test_clocks_adjustment_scales_and_golden_coverage():
    assert INDICATORS["us_ppi_yy"].seasonal_adjustment == "NSA"
    assert INDICATORS["us_trade_balance"].scale == 1e9
    assert INDICATORS["us_housing_starts"].scale == INDICATORS["us_jolts_openings"].scale == 1000
    for family in US_MONTHLY:
        cases = [c for c in GOLDEN if c["family"] == family]
        assert len(cases) >= 2 and any(c.get("revision") for c in cases)
    jolts = parse("us_jolts", "bls.json")[-1]
    ip = parse("us_industrial_production", "ip.html")[0]
    assert "T10:00:00-04:00" in jolts.issuer_local_time
    assert "T09:15:00-04:00" in ip.issuer_local_time


def test_unreviewed_fred_current_head_is_never_attached_to_old_edition():
    class NextMonth(Replay):
        def get(self, provider, url, params=None):
            assert "realtime_start" not in params  # fail before historical reads
            doc = document("HOUST-current.json")
            return replace(doc, body=doc.body.replace(b'2026-09-01', b'2026-10-01'))
    with pytest.raises(FetchError, match="needs review"):
        FREDAdapter(NextMonth()).fetch(["us_housing_starts"], "2026-05")


def test_short_fred_window_includes_previous_month_revisions():
    rows = FREDAdapter(Replay()).fetch(["us_housing_starts"], "2026-09")
    assert {o.edition for o in rows} == {"2026-10-20"}
    assert {"2026-08", "2026-09"} <= {o.period for o in rows}


def test_known_publication_date_cannot_relabel_another_reference_month():
    doc = document("pce.html")
    # Fault injection: August release text with July's reviewed edition date.
    doc = replace(doc, body=doc.body.replace(b"September 30, 2026", b"August 26, 2026"))
    with pytest.raises(FetchError, match="needs review"):
        parse_pce_publication(doc)
