"""Offline H2 contract: one latest-value cascade for macro, derived and release concepts."""
from copy import deepcopy
from datetime import date
import hashlib
import math

import duckdb
import pytest
import requests

from market_data_hub import concepts, latest
from market_data_hub.macro_releases import open_writer
from market_data_hub.macro_releases.adapters import Observation, persist
from market_data_hub.macro_releases.http import Document
from market_data_hub.macro_releases.models import ConsensusReading


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("latest-value tests must not use the network")

    monkeypatch.setattr(requests.Session, "request", forbidden)
    monkeypatch.delenv("MACRO_RELEASES_DB", raising=False)


def _observation(indicator, family, period, value, edition):
    payload = repr((indicator, period, value, edition)).encode()
    seen = edition + "T12:31:00Z"
    document = Document(payload, "https://issuer.example/publication", seen,
                        hashlib.sha256(payload).hexdigest())
    return Observation(indicator, family, period, value, str(value), indicator + ":series",
                       "BLS", document, edition, edition + "T12:30:00Z", stage="regular",
                       precision="exact")


@pytest.fixture
def releases(tmp_path):
    """Payrolls for 2026-08 (released 2026-09-04) and 2026-09 (released 2026-10-02)."""
    path = tmp_path / "macro.sqlite"
    with open_writer(path) as store:
        persist(store, [_observation("us_payrolls_change", "us_payrolls", "2026-08", 22, "2026-09-04")])
        persist(store, [_observation("us_payrolls_change", "us_payrolls", "2026-09", 29, "2026-10-02")])
        store.put(ConsensusReading(
            provider="Survey", indicator_id="us_payrolls_change",
            release_event_id="BLS:us_payrolls:regular:2026-10-02", measure="us_payrolls_change",
            reference_period="2026-09", estimate_stage="regular", value=25000, unit="persons",
            scale=1, captured_at="2026-10-01T12:00:00Z", release_cutoff_at="2026-10-02T12:30:00Z",
            cutoff_precision="exact", cutoff_basis="actual", content_hash="test-latest",
        ))
    return path


@pytest.fixture
def stale_releases(tmp_path):
    """Only the 2026-08 payrolls release is known."""
    path = tmp_path / "stale.sqlite"
    with open_writer(path) as store:
        persist(store, [_observation("us_payrolls_change", "us_payrolls", "2026-08", 22, "2026-09-04")])
    return path


MACRO_ROWS = [
    # PAYEMS: thousands of persons (level). Diff Sep = 30, Oct = 41.
    ("2026-07-01", "PAYEMS", 158950.0),
    ("2026-08-01", "PAYEMS", 159000.0),
    ("2026-09-01", "PAYEMS", 159030.0),
    # ICSA: number of claims, week ending Saturday.
    ("2026-10-10", "ICSA", 220000.0),
    # Treasury par yields for the 10Y-3M curve.
    ("2026-10-16", "UST_PAR_10Y", 4.10),
    ("2026-10-16", "UST_PAR_3M", 3.90),
    ("2026-10-19", "UST_PAR_10Y", 4.20),
    ("2026-10-19", "UST_PAR_3M", 3.95),
]


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "market_data.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE macro_series (date DATE NOT NULL, series_id VARCHAR NOT NULL, "
                "value DOUBLE, PRIMARY KEY (date, series_id))")
    con.executemany("INSERT INTO macro_series VALUES (?, ?, ?)", MACRO_ROWS)
    con.close()
    return str(path)


@pytest.fixture
def con(db_path):
    connection = duckdb.connect(db_path, read_only=True)
    yield connection
    connection.close()


def test_release_primary_fresh_wins_tie_and_carries_consensus(con, releases):
    result = latest.read_latest("us_payrolls_change", as_of="2026-10-20", con=con,
                                releases_db=releases)
    assert set(result) == set(latest.RESULT_KEYS)
    assert result["status"] == "primary" and result["source_kind"] == "release"
    assert result["value"] == 29 and result["reference_period"] == "2026-09"
    assert result["observation_date"] == date(2026, 9, 30)
    assert result["age_bd"] == 14 and result["max_age_bd"] == 33
    assert result["released_at"].startswith("2026-10-02")
    assert result["consensus"] == {"provider": "Survey", "value": 25}
    [fred] = result["alternatives"]
    # Same reference period on FRED: the tie goes to the higher-ranked release.
    assert fred["source_id"] == "PAYEMS" and fred["value"] == 30 and fred["fresh"]
    assert result["unit"] == "thousands of persons"
    assert result["registry_version"] == latest.registry_version()


def test_release_store_behind_fred_gives_newer_secondary(con, stale_releases):
    # Release 2026-08 is still fresh on 2026-10-06, but FRED already has 2026-09.
    early = latest.read_latest("us_payrolls_change", as_of="2026-10-06", con=con,
                               releases_db=stale_releases)
    assert early["status"] == "newer_secondary" and early["source_id"] == "PAYEMS"
    assert early["value"] == 30 and early["observation_date"] == date(2026, 9, 30)
    assert early["alternatives"][0]["fresh"] and early["alternatives"][0]["value"] == 22
    # Later, the release is stale and FRED is the only fresh source.
    late = latest.read_latest("us_payrolls_change", as_of="2026-10-20", con=con,
                              releases_db=stale_releases)
    assert late["status"] == "newer_secondary" and late["value"] == 30
    assert not late["alternatives"][0]["fresh"]
    assert "stale" in late["alternatives"][0]["reason"]


def test_release_cutoff_hides_unreleased_period(con, releases):
    result = latest.read_latest("us_payrolls_change", as_of="2026-10-20", con=con,
                                releases_db=releases, as_known_at="2026-10-01T00:00:00Z")
    assert result["source_id"] == "PAYEMS" and result["status"] == "newer_secondary"


def test_both_stale_returns_newest_stale(con, releases):
    result = latest.read_latest("us_payrolls_change", as_of="2026-12-31", con=con,
                                releases_db=releases)
    assert result["status"] == "stale" and result["source_kind"] == "release"
    assert result["value"] == 29 and result["age_bd"] > result["max_age_bd"]


def test_unavailable_without_any_observation(con):
    result = latest.read_latest("us_cpi_headline_yy", as_of="2026-10-20", con=con)
    assert result["status"] == "unavailable" and result["value"] is None
    assert "not configured" in result["reason"]


def test_claims_scale_converts_persons_to_thousands(con):
    result = latest.read_latest("us_initial_jobless_claims", as_of="2026-10-16", con=con)
    assert result["status"] == "newer_secondary" and result["source_id"] == "ICSA"
    assert math.isclose(result["value"], 220.0)
    assert result["observation_date"] == date(2026, 10, 10)
    assert result["alternatives"][0]["source_id"] == "us_initial_jobless_claims"


def test_derived_curve_uses_resolved_inputs(con):
    result = latest.read_latest("US_CURVE_10Y3M", as_of="2026-10-19", con=con)
    assert result["status"] == "primary" and result["source_kind"] == "derived"
    assert math.isclose(result["value"], 0.25)
    assert result["observation_date"] == date(2026, 10, 19)
    assert [(i["concept"], i["source_id"]) for i in result["inputs"]] == [
        ("US_10Y", "UST_PAR_10Y"), ("US_3M", "UST_PAR_3M")]


def test_derived_with_price_input_is_unavailable(con):
    registry = concepts.load_concepts()
    entry = deepcopy(registry["US_CURVE_10Y3M"])
    entry.update(concept="SPY_MINUS_10Y", expression="PX:SPY - US_10Y")
    registry["SPY_MINUS_10Y"] = entry
    ctx = latest._Context("2026-10-19", None, con, None, None)
    ctx.registry = registry
    result = latest._resolve(ctx, "SPY_MINUS_10Y", set())
    assert result["status"] == "unavailable" and "price" in result["reason"]


def test_price_concept_not_implemented(con):
    with pytest.raises(NotImplementedError, match="price kind arrives in a later PR"):
        latest.read_latest("PX:SPY", as_of="2026-10-19", con=con)


@pytest.mark.parametrize("transform,frequency,rows,expected", [
    ("level", "M", [("2026-09-01", 5.0)], 5.0),
    ("diff", "M", [("2026-08-01", 159000.0), ("2026-09-01", 159030.0)], 30.0),
    ("pct_change_mm", "M", [("2026-08-01", 100.0), ("2026-09-01", 101.0)], 1.0),
    ("pct_change_yy", "M", [("2025-09-01", 100.0), ("2026-08-01", 102.0), ("2026-09-01", 103.0)], 3.0),
    ("pct_change_qq_saar", "Q", [("2026-04-01", 100.0), ("2026-07-01", 101.0)], 4.060401),
    ("pct_change_yy", "W", [("2025-10-11", 200.0), ("2026-10-10", 220.0)], 10.0),
])
def test_transform_math(transform, frequency, rows, expected):
    rows = [(date.fromisoformat(d), v) for d, v in rows]
    stored, value = latest.apply_transform(rows, transform, frequency)[-1]
    assert stored == rows[-1][0] and math.isclose(value, expected, rel_tol=1e-9)


def test_transform_skips_gaps_and_applies_scale():
    rows = [(date(2026, 6, 1), 100.0), (date(2026, 8, 1), 101.0)]
    assert latest.apply_transform(rows, "pct_change_mm", "M") == []
    assert latest.apply_transform([(date(2026, 10, 10), 220000.0)], "level", "W", 0.001) == [
        (date(2026, 10, 10), 220.0)]


def test_period_end_dates():
    assert latest.period_end_date("2026-02") == date(2026, 2, 28)
    assert latest.period_end_date("2026-Q3") == date(2026, 9, 30)
    assert latest.period_end_date("2026-10-10") == date(2026, 10, 10)
    assert latest._observation_date(date(2026, 7, 1), "Q") == date(2026, 9, 30)


def test_history_is_single_transformed_source(con):
    frame = latest.read_concept_history("us_payrolls_change", "2026-08-01", "2026-12-31", con=con)
    assert list(frame["value"]) == [50.0, 30.0]
    assert list(frame["date"]) == [date(2026, 8, 31), date(2026, 9, 30)]
    assert set(frame["source_id"]) == {"PAYEMS"}


def test_cli_smoke(db_path, releases, capsys):
    assert latest.main(["--as-of", "2026-10-20", "--concept", "us_payrolls_change",
                        "--concept", "US_CURVE_10Y3M", "--db", db_path,
                        "--releases-db", str(releases)]) == 0
    out = capsys.readouterr().out
    assert "us_payrolls_change" in out and "2026-09" in out and "primary" in out
    assert "US_CURVE_10Y3M" in out and "UST_PAR" not in out.splitlines()[0]
    assert latest.main(["--as-of", "2026-10-20", "--family", "releases", "--db", db_path]) == 0
    assert "US_OECD_CLI" in capsys.readouterr().out


def test_us_release_concepts_cover_catalogue_ids():
    registry = concepts.load_concepts()
    releases = {k: v for k, v in registry.items() if v["family"] == "releases"}
    for name, spec in releases.items():
        if name == "US_OECD_CLI":
            continue
        assert spec["sources"][0] == {**spec["sources"][0], "kind": "release", "id": name,
                                      "role": "primary"}
        assert len({s["max_age_business_days"] for s in spec["sources"]}) == 1
    assert releases["us_initial_jobless_claims"]["sources"][1]["scale"] == 0.001
    assert releases["us_payrolls_change"]["sources"][1]["transform"] == "diff"
    assert releases["us_umich_consumer_sentiment"]["history"] == {"mode": "single", "id": "UMCSENT"}
    assert len(releases["us_cpi_headline_yy"]["sources"]) == 1  # SA FRED index omitted


@pytest.fixture
def macro_entry():
    return {
        "concept": "TEST", "family": "releases", "label": "Test", "unit": "%",
        "frequency": "M", "calendar": "us_govt",
        "sources": [{"kind": "macro", "id": "CPIAUCSL", "role": "primary",
                     "max_age_business_days": 38, "convention": "FRED index",
                     "transform": "pct_change_mm", "scale": 1}],
    }


@pytest.fixture
def transform_catalogues():
    return {"macro": {"CPIAUCSL": {"frequency": "M"}, "GDPC1": {"frequency": "Q"}},
            "price": {}, "release": {"us_cpi_headline_mm": {}}}


def test_transform_validation_accepts_declared_source(macro_entry, transform_catalogues):
    assert concepts.validate_concepts([macro_entry], transform_catalogues)["TEST"]


@pytest.mark.parametrize("change,message", [
    ({"transform": "pct_change"}, "invalid transform"),
    ({"transform": "pct_change_qq_saar"}, "needs a"),
    ({"scale": 0}, "invalid source scale"),
    ({"scale": True}, "invalid source scale"),
    ({"scale": float("nan")}, "invalid source scale"),
])
def test_transform_validation_rejects(macro_entry, transform_catalogues, change, message):
    macro_entry["sources"][0].update(change)
    with pytest.raises(ValueError, match=message):
        concepts.validate_concepts([macro_entry], transform_catalogues)


def test_transform_only_on_macro_sources(macro_entry, transform_catalogues):
    macro_entry["sources"][0] = {"kind": "release", "id": "us_cpi_headline_mm", "role": "primary",
                                 "max_age_business_days": 38, "convention": "issuer",
                                 "transform": "level"}
    with pytest.raises(ValueError, match="unknown keys"):
        concepts.validate_concepts([macro_entry], transform_catalogues)


def test_derived_alignment_walks_back_over_staggered_gaps(tmp_path):
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE macro_series (date DATE, series_id VARCHAR, value DOUBLE)")
    con.executemany("INSERT INTO macro_series VALUES (?, ?, ?)", [
        ("2026-10-15", "UST_PAR_10Y", 4.00), ("2026-10-19", "UST_PAR_10Y", 4.20),
        ("2026-10-15", "UST_PAR_3M", 3.80), ("2026-10-16", "UST_PAR_3M", 3.85),
    ])
    result = latest.read_latest("US_CURVE_10Y3M", as_of="2026-10-19", con=con)
    assert result["observation_date"] == date(2026, 10, 15)
    assert math.isclose(result["value"], 0.20)


def test_history_end_bound_applies_to_period_end(con):
    frame = latest.read_concept_history("us_payrolls_change", end="2026-09-15", con=con)
    assert list(frame["date"]) == [date(2026, 8, 31)]


def test_release_older_than_window_is_stale_not_unavailable(con, releases, monkeypatch):
    monkeypatch.setattr(latest, "_RELEASE_WINDOW_DAYS", 10)
    result = latest.read_latest("us_payrolls_change", as_of="2026-12-31", con=con,
                                releases_db=releases)
    assert result["status"] == "stale" and result["source_kind"] == "release"
    assert result["value"] == 29


def test_fallback_value_rounded_to_issuer_precision(con, stale_releases):
    # 2026-09 payrolls only on FRED: 159030.4 - 159000 = 30.4, issuer prints whole thousands.
    con2 = duckdb.connect(":memory:")
    con2.execute("CREATE TABLE macro_series (date DATE, series_id VARCHAR, value DOUBLE)")
    con2.executemany("INSERT INTO macro_series VALUES (?, ?, ?)", [
        ("2026-08-01", "PAYEMS", 159000.0), ("2026-09-01", "PAYEMS", 159030.6),
        ("2026-08-01", "CPIAUCSL", 320.0), ("2026-09-01", "CPIAUCSL", 321.12),
    ])
    payrolls = latest.read_latest("us_payrolls_change", as_of="2026-10-20", con=con2,
                                  releases_db=stale_releases)
    assert payrolls["status"] == "newer_secondary"
    assert payrolls["value"] == 31.0 and math.isclose(payrolls["value_raw"], 30.6, abs_tol=1e-6)
    cpi = latest.read_latest("us_cpi_headline_mm", as_of="2026-10-20", con=con2)
    assert math.isclose(cpi["value_raw"], 0.35) and cpi["value"] == 0.4  # half up, not banker's
    assert cpi["alternatives"][0]["value"] is None


def test_primary_value_and_alternatives_rounded(con, releases):
    result = latest.read_latest("us_payrolls_change", as_of="2026-10-20", con=con,
                                releases_db=releases)
    assert result["value"] == result["value_raw"] == 29
    assert result["alternatives"][0]["value"] == 30.0


def test_round_published():
    assert latest.round_published(0.35028, 1) == 0.4
    assert latest.round_published(0.25, 1) == 0.3
    assert latest.round_published(-0.25, 1) == -0.3
    assert latest.round_published(4.0625, 2) == 4.06
    assert latest.round_published(None, 1) is None
    assert latest.round_published(1.23456, None) == 1.23456


@pytest.mark.parametrize("value", [-1, 7, 1.5, True, "1"])
def test_decimals_validation(macro_entry, transform_catalogues, value):
    macro_entry["decimals"] = value
    with pytest.raises(ValueError, match="decimals"):
        concepts.validate_concepts([macro_entry], transform_catalogues)
