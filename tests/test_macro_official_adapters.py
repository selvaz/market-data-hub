"""Certify M5 against real saved responses. Tests must stay offline."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from market_data_hub.macro_releases import open_writer
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.bea import BEAAdapter
from market_data_hub.macro_releases.bls import BLSAdapter, revision_start
from market_data_hub.macro_releases.catalogue import INDICATORS
from market_data_hub.macro_releases.collect import main
from market_data_hub.macro_releases.derived import percent_change
from market_data_hub.macro_releases.eurostat import EurostatAdapter
from market_data_hub.macro_releases.fred import FREDAdapter
from market_data_hub.macro_releases.http import Document, FetchError, HttpClient, RateLimiter
from market_data_hub.macro_releases.ons import ONSAdapter

FIXTURES = Path(__file__).parent / "fixtures/macro_releases"
GOLDEN = json.loads((FIXTURES.parent / "macro_releases_golden.json").read_text(encoding="utf-8"))["cases"]
SLICE_IDS = [c["id"] for c in GOLDEN if c["id"].startswith(
    ("us_cpi_", "us_nfp_", "us_gdp_", "ea_hicp_", "uk_gdp_"))]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network forbidden in offline certification")
    monkeypatch.setattr(requests.Session, "request", forbidden)


def document(relative):
    path = FIXTURES / relative
    body = path.read_bytes()
    meta = json.loads(path.with_suffix(".meta.json").read_text(encoding="utf-8"))
    assert hashlib.sha256(body).hexdigest() == meta["sha256"]
    return Document(body, meta["url"], meta["retrieved_at"], meta["sha256"])


@pytest.fixture
def observations():
    out = BLSAdapter(None).parse(document("bls/cpi-ces.json"), ["us_cpi", "us_payrolls"], "2026-06")
    out += BLSAdapter.parse_payroll_publication(document("bls/august-web.json"), "2026-09-04", "2026-08")
    out += BEAAdapter.parse(document("bea/nipa.json"), since="2026-06")
    for name in ("advance", "second"):
        out += BEAAdapter.parse_archived_release(document(f"bea/{name}-release.pdf"))
    for name in ("flash", "final"):
        out += EurostatAdapter.parse(document(f"eurostat/{name}-rates.json"), stage=name, since="2026-06")
    for name in ("july", "august"):
        out += ONSAdapter.parse_bulletin(document(f"ons/{name}-content.html"), "2026-06")
    out += ONSAdapter.parse_levels(document("ons/gdp69-csv.csv"), document("ons/gdp69.json").json(), "2026-05")
    return out


def golden_indicator(case):
    name = case["id"]
    if name.startswith("us_cpi_"):
        return "_".join(name.split("_")[:4])
    if name.startswith("us_nfp_"):
        return "us_payrolls_change"
    if name.startswith("us_gdp_"):
        return "us_gdp_qq_saar"
    return "ea_hicp_yy" if name.startswith("ea_") else "uk_gdp_mm"


@pytest.mark.parametrize("case_id", SLICE_IDS)
def test_every_slice_golden_case(case_id, observations, tmp_path):
    case = next(c for c in GOLDEN if c["id"] == case_id)
    indicator_id = golden_indicator(case)
    # The golden final HICP date was not independently checked in M3.
    edition = "2026-10-16" if case_id == "ea_hicp_yy_final_2026-09" else case["publication_date"]
    matches = [o for o in observations if o.indicator_id == indicator_id and
               o.period == case["reference_period"] and o.edition == edition]
    assert matches, case_id
    indicator = INDICATORS[indicator_id]
    assert indicator.geography == case["country"]
    assert indicator.unit == case["unit"]
    assert indicator.transform == {"mm_level_change": "change"}.get(case["change_basis"], case["change_basis"])
    if case["seasonal_adjustment"] != "n/a":
        assert indicator.seasonal_adjustment == case["seasonal_adjustment"]
    if case_id.startswith(("us_cpi_", "us_nfp_")):
        assert all(o.series_key.split(":")[0] == case["series_id"] for o in matches)
    assert all(o.value * indicator.scale == pytest.approx(case["value"]) for o in matches)
    assert all(o.stage == case["vintage"] for o in matches if o.family in ("ea_hicp", "us_gdp"))
    with open_writer(tmp_path / "macro.sqlite") as store:
        persist(store, matches)
        rows = store.latest_official(indicator_id)
        assert rows[0]["value"] * rows[0]["scale"] == pytest.approx(case["value"])
        assert rows[0]["extraction_method"] in ("api", "html", "pdf")
        assert rows[0]["verification_status"] == "verified"
        assert rows[0]["first_seen_at"].startswith("2026-10-17")
        assert not store.as_known_at("2026-10-01T00:00:00Z")


def test_revision_chains_and_unchanged_editions(observations, tmp_path):
    with open_writer(tmp_path / "macro.sqlite") as store:
        persist(store, observations)
        first = {(r["indicator_id"], r["reference_period"]): r for r in store.first_release()}
        latest = {(r["indicator_id"], r["reference_period"]): r for r in store.latest_official()}
        assert first["us_payrolls_change", "2026-08"]["value"] == 162
        assert latest["us_payrolls_change", "2026-08"]["value"] == 133
        assert first["uk_gdp_mm", "2026-06"]["value"] == .3
        assert latest["uk_gdp_mm", "2026-06"]["value"] == .4
        gdp = store.conn.execute("SELECT estimate_stage,value FROM value_vintages "
                                 "WHERE indicator_id='us_gdp_qq_saar' ORDER BY issuer_published_at").fetchall()
        assert [tuple(r) for r in gdp] == [("advance", 1.5), ("second", 1.5), ("third", 2.2)]
        monthly_hicp = store.conn.execute("SELECT estimate_stage,value FROM value_vintages "
                                          "WHERE indicator_id='ea_hicp_mm' AND reference_period='2026-09' "
                                          "ORDER BY issuer_published_at").fetchall()
        assert [tuple(r) for r in monthly_hicp] == [("flash", .6), ("final", .6)]
        before = store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0]
        persist(store, observations)
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == before
        persist(store, [replace(o, document=replace(o.document, document_hash="reread:" + o.document.document_hash))
                        for o in observations])
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == before


def test_payroll_new_month_and_two_revisions_are_one_event(observations, tmp_path):
    rows = [o for o in observations if o.family == "us_payrolls" and o.edition == "2026-10-02"
            and o.period >= "2026-07"]
    assert {o.period: o.value for o in rows} == {"2026-07": -10, "2026-08": 133, "2026-09": 29}
    with open_writer(tmp_path / "macro.sqlite") as store:
        persist(store, rows)
        assert store.conn.execute("SELECT COUNT(*) FROM release_events").fetchone()[0] == 1
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 3


def test_payroll_archive_confirms_api_revision_window():
    rows = BLSAdapter.parse_payroll_publication(document("bls/september-web.json"), "2026-10-02", "2026-09")
    assert {o.period: o.value for o in rows} == {"2026-07": -10, "2026-08": 133, "2026-09": 29}
    with pytest.raises(FetchError, match="does not match"):
        BLSAdapter.parse_payroll_publication(document("bls/august-web.json"), "2026-09-05", "2026-08")


def test_bea_missing_line_must_not_return_entire_table():
    with pytest.raises(FetchError, match="requested line absent"):
        BEAAdapter.parse(document("bea/nipa.json"), line="999999")


def test_bea_revised_quarter_shares_headline_publication(tmp_path):
    rows = BEAAdapter.parse(document("bea/nipa.json"))
    with open_writer(tmp_path / "macro.sqlite") as store:
        persist(store, rows)
        assert store.conn.execute("SELECT COUNT(*) FROM release_events").fetchone()[0] == 1
        assert {o.stage for o in rows} == {"regular", "third"}


def test_hicp_date_exception_is_backed_by_issuer_publication():
    text = document("eurostat/final-publication.html").body.decode("utf-8")
    assert "16 October 2026" in text
    assert next(c for c in GOLDEN if c["id"] == "ea_hicp_yy_final_2026-09")["publication_date"] == "2026-10-17"


def test_bls_unavailable_cells_are_readings_without_numeric_vintages(observations, tmp_path):
    missing = [o for o in observations if o.value is None]
    assert len(missing) == 6
    with open_writer(tmp_path / "macro.sqlite") as store:
        persist(store, missing)
        assert store.conn.execute("SELECT COUNT(*) FROM readings WHERE reading_kind='unavailable'").fetchone()[0] == 6
        assert not store.latest_official()


@pytest.mark.parametrize("mirror_first", [False, True])
def test_mismatching_redistributor_creates_discrepancy(observations, tmp_path, mirror_first):
    official = next(o for o in observations if o.indicator_id == "us_cpi_headline_mm" and o.period == "2026-09")
    mirror = FREDAdapter.parse(document("fred/cpi.json"), indicator="us_cpi_headline_mm", edition="2026-10-14")[0]
    mirror = replace(mirror, value=9.9, printed="9.9")  # Explicit fault injection.
    with open_writer(tmp_path / "macro.sqlite") as store:
        persist(store, [mirror, official] if mirror_first else [official, mirror])
        selected = store.latest_official("us_cpi_headline_mm")[0]
        assert selected["value"] == .6
        discrepancies = store.conn.execute("SELECT * FROM discrepancies").fetchall()
        assert len(discrepancies) == 1
        assert discrepancies[0]["selected_vintage_id"] == selected["id"]
        reading = store.conn.execute("SELECT * FROM readings WHERE source_id='FRED'").fetchone()
        assert reading["verification_status"] == "unverified"
        assert reading["issuer_id"] == "BLS" and reading["distributor_id"] == "FRED"


def test_mirror_never_compared_with_different_edition(observations, tmp_path):
    official = next(o for o in observations if o.indicator_id == "us_cpi_headline_mm" and o.period == "2026-09")
    with open_writer(tmp_path / "macro.sqlite") as store:
        persist(store, [official, replace(official, source="FRED", value=99, edition="newer-unknown")])
        assert store.conn.execute("SELECT COUNT(*) FROM discrepancies").fetchone()[0] == 0
        assert store.latest_official()[0]["value"] == .6


def test_alfred_unchanged_second_requires_explicit_asof():
    assert len(document("fred/gdp-vintages.json").json()["observations"]) == 2
    rows = FREDAdapter.parse(document("fred/gdp-second.json"), indicator="us_gdp_qq_saar",
                             edition="2026-08-26", stage="second")
    assert rows[0].value == 1.5 and rows[0].stage == "second"
    assert rows[0].edition == "2026-08-26"


def test_derived_rate_separate_with_real_input_ids(observations, tmp_path):
    levels = sorted([o for o in observations if o.indicator_id == "uk_gdp_index"], key=lambda o: o.period)
    official = next(o for o in observations if o.indicator_id == "uk_gdp_mm" and o.period == "2026-06")
    with open_writer(tmp_path / "macro.sqlite") as store:
        ids = persist(store, levels)
        persist(store, [official])
        derived_id = percent_change(store, official, ids[-2], ids[-1])
        derived = store.conn.execute("SELECT * FROM value_vintages WHERE id=?", (derived_id,)).fetchone()
        assert json.loads(derived["derived_from_json"]) == ids[-2:]
        assert derived["formula_version"] == "percent-change:v1"
        assert derived["extraction_method"] == "calculation"
        assert store.latest_official("uk_gdp_mm")[0]["value"] == .3


def test_bls_published_calculation_required_never_synthesized():
    doc = document("bls/cpi-ces.json")
    data = doc.json()
    data["Results"]["series"][0]["data"][0]["calculations"] = {}
    with pytest.raises(FetchError, match="published calculation absent"):
        BLSAdapter(None).parse(replace(doc, body=json.dumps(data).encode()), ["us_cpi"], "2026-09")


def test_revision_window_keeps_complete_years_and_prior_payroll_months():
    assert revision_start("us_cpi", "2026-09", "2026-09") == "2021-01"
    assert revision_start("us_payrolls", "2026-01", "2026-01") == "2025-11"


def test_unknown_bls_edition_fails_closed():
    doc = document("bls/cpi-ces.json")
    payload = doc.json()
    for series in payload["Results"]["series"]:
        for row in series["data"]:
            row["year"] = "2027"
    with pytest.raises(FetchError, match="publication metadata needs review"):
        BLSAdapter(None).parse(replace(doc, body=json.dumps(payload).encode()), ["us_cpi"], "2027-01")


def test_eurostat_flash_dimension_and_no_estimate_promoted_to_final():
    doc = document("eurostat/flash-rates.json")
    assert "FLS" in doc.json()["dimension"]["release"]["category"]["index"]
    assert {o.stage for o in EurostatAdapter.parse(doc, stage="flash")} == {"flash"}
    final = document("eurostat/final-rates.json")
    payload = final.json()
    payload["status"] = {k: "e" for k in payload["value"]}
    with pytest.raises(FetchError, match="no certified final"):
        EurostatAdapter.parse(replace(final, body=json.dumps(payload).encode()), stage="final")


@pytest.mark.parametrize("statuses", [[429, 200], [503, 503], [403]])
def test_http_bounded_retry_secrets_redacted(tmp_path, monkeypatch, statuses):
    calls = []
    def request(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status_code=statuses[min(len(calls) - 1, len(statuses) - 1)],
                               content=b'{"USERID":"super-secret"}')
    monkeypatch.setattr("market_data_hub.macro_releases.http.time.sleep", lambda _: None)
    client = HttpClient(session=SimpleNamespace(request=request), rate_directory=tmp_path)
    if statuses[-1] == 200:
        doc = client.get("bea", "https://apps.bea.gov/api/data/", {"UserID": "super-secret"})
        assert b"super-secret" not in doc.body
        assert doc.document_hash == hashlib.sha256(b'{"USERID":"super-secret"}').hexdigest()
    else:
        with pytest.raises(FetchError) as error:
            client.get("bea", "https://apps.bea.gov/api/data/", {"UserID": "super-secret"})
        assert "super-secret" not in str(error.value)
    assert len(calls) == len(statuses)
    assert calls[0]["verify"] and calls[0]["allow_redirects"] is False


def test_provider_rate_limit_shared_and_quota(tmp_path):
    RateLimiter("bls", tmp_path, interval=0, daily_limit=1).acquire()
    with pytest.raises(FetchError, match="daily quota"):
        RateLimiter("bls", tmp_path, interval=0, daily_limit=1).acquire()


def test_cli_rejects_missing_path_bad_family_date_and_programdata():
    for argv in (["--families", "us_cpi"], ["--db", "scratch.sqlite", "--families", "unknown"],
                 ["--db", "C:/ProgramData/test.sqlite", "--families", "us_cpi"],
                 ["--db", "scratch.sqlite", "--families", "us_cpi", "--since", "2026-13"]):
        with pytest.raises(SystemExit) as error:
            main(argv)
        assert error.value.code == 2


def test_cli_replay_and_partial_failure(tmp_path, monkeypatch, capsys):
    class Replay:
        def request(self, provider, url, **kwargs):
            assert len(kwargs["payload"]["seriesid"]) == 5
            assert kwargs["payload"]["calculations"] is True
            return document("bls/cpi-ces.json")

        def get(self, provider, url, params=None):
            if provider == "bea":
                raise FetchError("BEA fixture failure")
            if "gdp-to-four" in url:
                return document("ons/gdp69-csv.csv" if url.endswith(".csv") else "ons/gdp69.json")
            if provider == "ons":
                return document("ons/" + ("july" if url.endswith("july2026") else "august") + "-content.html")
            return document("eurostat/" + ("flash" if url.endswith("prc_hicp_fpd") else "final") + "-rates.json")
    monkeypatch.setattr("market_data_hub.macro_releases.collect.HttpClient", Replay)
    monkeypatch.setattr("market_data_hub.macro_releases.bls.api_key", lambda _: "not-a-real-key")
    monkeypatch.setattr("market_data_hub.macro_releases.bea.api_key", lambda _: "not-a-real-key")
    target = tmp_path / "macro.sqlite"
    assert main(["--db", str(target), "--families", "us_cpi,us_payrolls,us_gdp,ea_hicp,uk_gdp", "--since", "2026-06"]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["failures"] == {"BEAAdapter": "BEA fixture failure"}
    assert set(output["families"]) == {"BLSAdapter", "EurostatAdapter", "ONSAdapter"}
    assert target.exists()
