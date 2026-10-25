"""BLS outage recovery uses release-bound ALFRED evidence, entirely offline."""
from dataclasses import replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from market_data_hub.macro_releases import open_reader, open_writer, resolve
from market_data_hub.macro_releases import bls, discovery, fred, run_daily
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.http import Document, FetchError
from market_data_hub.macro_releases.schedule import RUN_INSTANT, due, family_editions

FAMILIES = ("us_cpi", "us_payrolls", "us_ppi", "us_jolts", "us_unemployment", "us_ahe")


def document(payload):
    body = json.dumps(payload).encode()
    return Document(body, "https://api.stlouisfed.org/fred/series/observations",
                    "2026-10-25T20:00:00Z", hashlib.sha256(body).hexdigest())


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network and host credentials forbidden")
    monkeypatch.setattr(requests.Session, "request", forbidden)
    monkeypatch.setattr("market_data_hub.macro_releases.http.api_key", forbidden)
    for module in (bls, discovery, fred):
        monkeypatch.setattr(module, "api_key", lambda _: "stub-key")
    monkeypatch.setattr(run_daily.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(run_daily, "datetime", SimpleNamespace(now=lambda tz: datetime(2026, 10, 25, 20, tzinfo=tz)))
    token = RUN_INSTANT.set("2026-10-25T20:00:00Z")
    yield
    RUN_INSTANT.reset(token)


class Outage:
    def __init__(self, mode="ok"):
        self.calls = []
        self.mode = mode

    def request(self, *args, **kwargs):
        self.calls.append(("bls", kwargs))
        raise FetchError("bls: HTTP 503")

    def get(self, provider, url, params=None):
        assert provider == "fred" and params["api_key"] == "stub-key"
        self.calls.append((url, params))
        edition = params["realtime_end"]
        assert params["realtime_start"] == ("1776-07-04" if params["output_type"] == 4 else edition)
        row = dict(date=params["observation_start"], value="0.2",
                   realtime_start=edition, realtime_end=edition)
        if params["output_type"] == 4:
            if self.mode == "late_list":
                row["realtime_start"] = "2026-10-26"
            elif self.mode == "early_list":
                row["realtime_start"] = "2026-01-01"
            return document(dict(observations=[row], count=1, offset=0, output_type=4,
                                 units="lin", realtime_start="1776-07-04", realtime_end=edition))
        if self.mode == "late_row":
            row["realtime_start"] = "2026-10-26"
        elif self.mode == "wrong_period":
            row["date"] = "2026-01-01"
        payload = dict(units=params["units"], output_type=1, realtime_start=edition, realtime_end=edition,
                       observations=[row], count=1, offset=0)
        if self.mode == "current_snapshot":
            payload["realtime_end"] = "2026-10-25"
        return document(payload)


@pytest.mark.parametrize("family", FAMILIES)
def test_outage_falls_back_to_redistributor(tmp_path, family):
    client = Outage()
    batch = discovery.fetch_daily(client, family, "2026-06")
    assert client.calls[0][0] == "bls"
    assert not batch.issues and batch.observations
    with open_writer(tmp_path / "macro.sqlite") as store:
        persist(store, batch.observations)
        for obs in batch.observations:
            result = resolve(store, obs.indicator_id, obs.period)
            assert (result.source_class, result.status, result.issuer_id) == ("redistributor", "provisional", "BLS")
            assert result.source["source_id"] == "ALFRED"
            assert obs.provider_vintage == obs.edition
            assert result.value == 0.2


@pytest.mark.parametrize("family", FAMILIES)
def test_daily_fallback_commits_then_is_idempotent(tmp_path, monkeypatch, family):
    monkeypatch.setattr(run_daily, "HttpClient", lambda **kwargs: Outage())
    path = tmp_path / "daily.sqlite"
    first = run_daily.execute(path, [family], 24, False, raw_dir=tmp_path / "empty")
    assert first["families"][family]["status"] == "ok"
    second = run_daily.execute(path, [family], 24, False, raw_dir=tmp_path / "empty")
    assert second["families"][family]["status"] == "no_change"
    assert second["families"][family]["vintages_added"] == 0


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("mode", ["unknown_edition", "missing_calculation"])
def test_successful_bls_validation_failure_cannot_use_older_alfred(tmp_path, monkeypatch, family, mode):
    class InvalidBLS:
        def request(self, provider, url, *, payload):
            assert provider == "bls"
            rows = [dict(seriesID=key, data=[dict(year="2026", period="M12" if mode == "unknown_edition" else "M09",
                        value="1", calculations={})]) for key in payload["seriesid"]]
            if family in ("us_unemployment", "us_jolts") and mode == "missing_calculation":
                rows = []  # Published levels have no calculation to omit.
            return replace(document(dict(status="REQUEST_SUCCEEDED", Results=dict(series=rows))), url=bls.ENDPOINT)

        def get(self, provider, url, params=None):
            assert provider == "bls", "ALFRED cannot hide a successful BLS response needing review"
            raise FetchError("bls: HTTP 403")  # RSS absence cannot certify the unknown API edition.

    monkeypatch.setattr(run_daily, "HttpClient", lambda **kwargs: InvalidBLS())
    path = tmp_path / "validation.sqlite"
    result = run_daily.execute(path, [family], 24, False, raw_dir=tmp_path / "empty")
    assert result["families"][family]["status"] == "needs_review"
    assert result["families"][family]["vintages_added"] == 0
    with open_reader(path) as store:
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 0
        assert store.conn.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0] > 0
        reasons = " ".join(r[0] for r in store.conn.execute("SELECT reasons FROM review_queue"))
        assert ("BLS publication metadata needs review" if mode == "unknown_edition" else
                "BLS series empty or absent" if family in ("us_unemployment", "us_jolts") else
                "BLS published calculation absent") in reasons


@pytest.mark.parametrize("mode", ["early_list", "late_list", "late_row", "wrong_period", "current_snapshot"])
@pytest.mark.parametrize("family", FAMILIES)
def test_invalid_fallback_is_needs_review_and_has_no_vintage(tmp_path, monkeypatch, mode, family):
    monkeypatch.setattr(run_daily, "HttpClient", lambda **kwargs: Outage(mode))
    path = tmp_path / "review.sqlite"
    result = run_daily.execute(path, [family], 24, True, raw_dir=tmp_path / "empty")
    assert result["families"][family]["status"] == "needs_review"
    assert result["families"][family]["vintages_added"] == 0
    # The writer path also keeps rejected provider documents in the review queue.
    result = run_daily.execute(path, [family], 24, False, raw_dir=tmp_path / "empty")
    assert result["families"][family]["status"] == "needs_review"
    with open_reader(path) as store:
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 0
        assert store.conn.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0] > 0


def test_release_instant_gate_including_dst():
    for edition, before, after in [("2026-10-02", "12:29:59", "12:30:00"),
                                    ("2026-11-06", "13:29:59", "13:30:00")]:
        token = RUN_INSTANT.set(edition + "T" + before + "Z")
        try:
            assert edition not in due(family_editions("us_payrolls"), family="us_payrolls")
            with pytest.raises(FetchError, match="not released"):
                fred.BLSFallbackAdapter.parse_first_print(document({}), indicator="us_payrolls_change", edition=edition)
            RUN_INSTANT.set(edition + "T" + after + "Z")
            assert edition in due(family_editions("us_payrolls"), family="us_payrolls")
        finally:
            RUN_INSTANT.reset(token)


def test_official_recovery_wins_without_rewriting_fallback_history(tmp_path, monkeypatch):
    client = Outage()
    mirrors = bls.BLSAdapter(client).fetch(["us_payrolls"], "2026-09")
    official = [replace(mirrors[0], source="BLS", value=151, series_key="CES0000000001:net_changes:1")]
    monkeypatch.setattr(bls.BLSAdapter, "fetch_official", lambda *args: official)
    client.calls.clear()
    assert bls.BLSAdapter(client).fetch(["us_payrolls"], "2026-09") == official
    assert not client.calls
    with open_writer(tmp_path / "recovery.sqlite") as store:
        persist(store, mirrors)
        before = [tuple(r) for r in store.conn.execute("SELECT * FROM value_vintages")]
        persist(store, official)
        result = resolve(store, "us_payrolls_change", "2026-09")
        assert (result.source_class, result.status, result.value) == ("official", "official", 151)
        after = [tuple(r) for r in store.conn.execute("SELECT * FROM value_vintages ORDER BY id")]
        assert after[:len(before)] == before and len(after) == len(before) + 1


def test_employment_definitions_official_calculations_and_shared_calendar():
    payload = dict(status="REQUEST_SUCCEEDED", Results=dict(series=[
        dict(seriesID="LNS14000000", data=[dict(year="2026", period="M09", value="4.2")]),
        dict(seriesID="CES0500000003", data=[dict(year="2026", period="M09", value="37.81",
             calculations=dict(pct_changes={"1": "0.1", "12": "3.0"}))])]))
    rows = bls.BLSAdapter(None).parse(document(payload), ["us_unemployment", "us_ahe"], "2026-09")
    assert {r.indicator_id: r.value for r in rows} == {
        "us_unemployment_rate": 4.2, "us_avg_hourly_earnings_mm": 0.1, "us_avg_hourly_earnings_yy": 3.0}
    for family in ("us_unemployment", "us_ahe"):
        assert family_editions(family) == family_editions("us_payrolls")
    assert {r.published_at for r in rows} == {"2026-10-02T12:30:00+00:00"}


def test_new_families_seed_membership_per_family_and_stage(tmp_path):
    from market_data_hub.macro_releases.scheduled_events import persist_scheduled_events
    with open_writer(tmp_path / "schedule.sqlite") as store:
        persist_scheduled_events(store, recorded_at="2026-10-25T20:00:00Z")
        for family, count in (("us_payrolls", 1), ("us_unemployment", 1), ("us_ahe", 2)):
            event = f"BLS:{family}:regular:2026-11-06"
            rows = store.conn.execute("SELECT * FROM readings WHERE release_event_id=?", (event,)).fetchall()
            assert len(rows) == count
            assert all(r["value"] is None and r["estimate_stage"] == "regular" for r in rows)
            schedule = store.conn.execute("SELECT * FROM schedule_versions WHERE release_event_id=?", (event,)).fetchone()
            assert schedule["expected_at"] == "2026-11-06T13:30:00.000000+00:00"


def test_employment_card_aliases_reuse_approved_concepts():
    from market_data_hub.indicator_cards import indicator_card
    for indicator, key in (("us_unemployment_rate", "us_unrate"),
                           ("us_avg_hourly_earnings_mm", "us_ahe"), ("us_avg_hourly_earnings_yy", "us_ahe")):
        assert indicator_card("cat:" + indicator).key == key


@pytest.mark.parametrize("indicator", [i for i, (family, _, units) in fred.MAPPINGS.items()
                                     if family in FAMILIES and units in ("pch", "pc1")])
@pytest.mark.parametrize("raw,printed", [("0.3158", "0.3"), ("0.35000", "0.4"), ("-0.35000", "-0.4")])
def test_fallback_percent_changes_match_bls_published_precision(indicator, raw, printed):
    family, _, units = fred.MAPPINGS[indicator]
    edition = max(due(family_editions(family), family=family))
    headline = family_editions(family)[edition][0]
    doc = document(dict(units=units, output_type=1, realtime_start=edition, realtime_end=edition,
                        observations=[dict(date=headline + "-01", value=raw,
                                           realtime_start=edition, realtime_end=edition)]))
    obs, = fred.BLSFallbackAdapter.parse_first_print(doc, indicator=indicator, edition=edition)
    assert (obs.value, obs.printed) == (float(printed), printed)
    assert obs.document.json()["observations"][0]["value"] == raw


def test_rounding_does_not_create_a_fake_official_discrepancy(tmp_path):
    obs = next(o for o in discovery.fetch_daily(Outage(), "us_ahe", "2026-09").observations
               if o.indicator_id == "us_avg_hourly_earnings_mm")
    payload = obs.document.json()
    payload["observations"][0]["value"] = "0.3158"
    doc = document(payload)
    mirror, = fred.BLSFallbackAdapter.parse_first_print(doc, indicator=obs.indicator_id, edition=obs.edition)
    official = replace(mirror, source="BLS", series_key="CES0500000003:pct_changes:1")
    with open_writer(tmp_path / "rounded.sqlite") as store:
        persist(store, [mirror, official])
        result = resolve(store, obs.indicator_id, obs.period)
        assert (result.value, result.status, result.source_class) == (0.3, "official", "official")


def test_added_bls_mappings_do_not_expand_generic_provider_history():
    class Forbidden:
        def get(self, *args, **kwargs):
            raise AssertionError("Fallback-only mappings cannot create generic FRED provider history")
    assert fred.FREDAdapter(Forbidden()).fetch(list(fred.BLS_FALLBACK_ONLY), "2026-06") == []


@pytest.mark.parametrize("message,allowed", [
    ("bls: HTTP 503", True), ("bls: HTTP 403", True), ("bls: TLS verification failed", True),
    ("bls: transport failed after bounded attempts", True), ("bls: non-transient request failure", True),
    ("BLS publication metadata needs review", False), ("BLS series empty or absent: X", False),
    ("BLS requested calculation absent: X", False), ("bls: metadata needs review", False),
    ("Missing credential: BLS_API_KEY", False),
])
def test_fallback_only_accepts_controlled_availability_failures(message, allowed):
    assert bls.unavailable(FetchError(message)) is allowed


@pytest.mark.parametrize("family", FAMILIES)
def test_original_bls_outage_reason_is_audited_without_rejecting_fallback(tmp_path, monkeypatch, family):
    monkeypatch.setattr(run_daily, "HttpClient", lambda **kwargs: Outage())
    path = tmp_path / "reason.sqlite"
    result = run_daily.execute(path, [family], 24, False, raw_dir=tmp_path / "empty")
    assert result["families"][family]["status"] == "ok"
    with open_reader(path) as store:
        rows = store.conn.execute("SELECT reasons FROM review_queue WHERE family=?", (family,)).fetchall()
        assert rows and all("bls: HTTP 503" in json.loads(r[0]) for r in rows)


@pytest.mark.parametrize("family", ["us_ahe", "us_unemployment"])
def test_empty_initial_list_on_release_day_is_pending_not_review(tmp_path, monkeypatch, family):
    class NotPosted(Outage):
        def get(self, *args, **kwargs):
            doc = super().get(*args, **kwargs)
            payload = doc.json()
            if payload["realtime_end"] == "2026-11-06":
                payload.update(observations=[], count=0)
            return document(payload)
    monkeypatch.setattr(run_daily, "datetime", SimpleNamespace(now=lambda tz: datetime(2026, 11, 6, 20, tzinfo=tz)))
    monkeypatch.setattr(run_daily, "HttpClient", lambda **kwargs: NotPosted())
    result = run_daily.execute(tmp_path / "pending.sqlite", [family], 24, False, raw_dir=tmp_path / "empty")
    row = result["families"][family]
    assert row["status"] == "ok" and not row.get("review_reasons")
    pending = next(r for r in row["scheduled_editions"] if r["edition"] == "2026-11-06")
    assert (pending["status"], pending["reason"]) == ("scheduled", "not_yet_published")


def test_official_fetch_intentionally_filters_editions_before_the_release_instant(monkeypatch):
    mirror = bls.BLSAdapter(Outage()).fetch(["us_payrolls"], "2026-09")[0]
    official = replace(mirror, source="BLS", edition="2026-11-06", period="2026-10")
    monkeypatch.setattr(bls.BLSAdapter, "fetch_official", lambda *args: [official])
    token = RUN_INSTANT.set("2026-11-06T13:29:59Z")
    try:
        assert bls.BLSAdapter(None).fetch(["us_payrolls"], "2026-09") == []
        RUN_INSTANT.set("2026-11-06T13:30:00Z")
        assert bls.BLSAdapter(None).fetch(["us_payrolls"], "2026-09") == [official]
    finally:
        RUN_INSTANT.reset(token)


@pytest.mark.parametrize("family", ["us_payrolls", "us_ahe"])
def test_first_vintage_is_selected_from_multi_vintage_multi_observation_fixture(family):
    fixture = json.loads((Path(__file__).parent / "fixtures/macro_releases/bls_fallback/vintages.json").read_text())
    class Replay(Outage):
        def get(self, provider, url, params=None):
            assert provider == "fred" and params["api_key"] == "stub-key"
            self.calls.append((url, params))
            history = fixture[params["series_id"]]
            assert len({r["date"] for r in history}) > 1
            assert len({r["realtime_start"] for r in history}) > 1
            # Replay FRED's server-side date and as-of filters on the full
            # fixture, which also contains a later revised September print.
            eligible = [r for r in history if params["observation_start"] <= r["date"] <= params["observation_end"]
                        and r["realtime_start"] <= params["realtime_end"]]
            first = params["output_type"] == 4
            picked = min(eligible, key=lambda r: r["realtime_start"]) if first else max(eligible, key=lambda r: r["realtime_start"])
            row = dict(date=picked["date"], realtime_start=picked["realtime_start"] if first else params["realtime_start"],
                       realtime_end=params["realtime_end"], value=picked[params["units"]])
            return document(dict(units=params["units"], output_type=params["output_type"],
                                 realtime_start=params["realtime_start"], realtime_end=params["realtime_end"],
                                 observations=[row], count=1, offset=0))
    rows = bls.BLSAdapter(Replay()).fetch([family], "2026-09")
    assert {r.indicator_id: r.value for r in rows} == ({"us_payrolls_change": 29.0} if family == "us_payrolls" else
            {"us_avg_hourly_earnings_mm": 0.1, "us_avg_hourly_earnings_yy": 3.0})
    assert all(r.period == "2026-09" and r.provider_vintage == "2026-10-02" for r in rows)
