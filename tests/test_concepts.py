"""Offline H1 contract: catalogued sources and unchanged legacy rate chains."""
from copy import deepcopy
import os
from pathlib import Path

import pytest
import yaml

from market_data_hub import concepts, config_loader, resolved_rates
from market_data_hub.config_loader import _CONFIG_DIR, get_yahoo_tickers


def test_rates_registry_matches_legacy_golden():
    golden = yaml.safe_load(
        (Path(__file__).parent / "fixtures" / "resolved_rates_h1_golden.yaml")
        .read_text(encoding="utf-8")
    )
    config, table = resolved_rates.load_resolved_rates()
    # The compatibility loader adds catalogue frequency to each source in place.
    for entry in config["concepts"]:
        for source in entry["sources"]:
            source.pop("frequency", None)
    assert config == golden
    registry = concepts.load_concepts()
    assert set(table) == {entry["concept"] for entry in golden["concepts"]}
    for entry in golden["concepts"]:
        assert [s["id"] for s in registry[entry["concept"]]["sources"]] == [
            s["series_id"] for s in entry["sources"]
        ]
    assert not (_CONFIG_DIR / "resolved_rates.yaml").exists()


def test_every_source_id_in_catalogue():
    catalogues = concepts.load_source_catalogues()
    for entry in concepts.load_concepts().values():
        for source in entry.get("sources", []):
            if source["kind"] != "derived":
                assert source["id"] in catalogues[source["kind"]]


@pytest.mark.parametrize("tenor,fred", [("1M", "DGS1MO"), ("3M", "DGS3MO"), ("6M", "DGS6MO")])
def test_us_3m_primary_is_ust_par_and_bill_curve(tenor, fred):
    spec = concepts.load_concepts()[f"US_{tenor}"]
    assert [(s["id"], s["role"]) for s in spec["sources"]] == [
        (f"UST_PAR_{tenor}", "primary"), (fred, "fallback")
    ]


def test_concept_ids_unique_across_files(tmp_path):
    entry = deepcopy(concepts.load_concepts()["US_3M"])
    for name in ("money_market", "rates"):
        (tmp_path / f"{name}.yaml").write_text(
            yaml.safe_dump({"concepts": [entry]}), encoding="utf-8"
        )
    with pytest.raises(ValueError, match="duplicate concept"):
        concepts.load_concepts(tmp_path)


def test_estr_is_pending_instead_of_an_unknown_source():
    spec = concepts.load_concepts()["EUR_STR"]
    assert spec["status"] == "pending_source"
    assert not spec["sources"]
    invalid = deepcopy(concepts.load_concepts()["EFFR"])
    invalid["sources"][0]["id"] = "ESTR"
    with pytest.raises(ValueError, match="unknown macro source.*ESTR"):
        concepts.validate_concepts([invalid])


def test_px_generation_covers_yahoo_universe():
    registry = concepts.load_concepts()
    assert {key[3:] for key in registry if key.startswith("PX:")} == {
        ticker["symbol"] for ticker in get_yahoo_tickers()
    }
    spy = registry["PX:SPY"]
    assert spy["sources"][0]["field"] == "adj_close"
    assert spy["sources"][0]["live"] == "post_close"
    for symbol in ("^VIX", "EURUSD=X", "BZ=F"):
        assert registry[f"PX:{symbol}"]["sources"][0]["field"] == "close"


def test_declared_cross_convention_check_and_derived_spread():
    registry = concepts.load_concepts()
    check = registry["FR_10Y"]["check"]
    assert check["ids"] == ["CB_FR_10Y", "TV_FR_10Y"]
    assert check["unit"] == "bp" and check["tolerance"] > 0
    spread = registry["IT_DE_10Y"]
    assert spread["kind"] == "derived" and spread["unit"] == "bp"
    assert spread["expression"] == "IT_10Y - DE_10Y"


@pytest.fixture
def simple_concept():
    return {
        "concept": "TEST", "family": "money_market", "label": "Test rate",
        "unit": "%", "frequency": "D", "calendar": "us_govt",
        "sources": [{"kind": "macro", "id": "EFFR", "role": "primary",
                     "max_age_business_days": 3, "convention": "effective overnight rate"}],
    }


@pytest.fixture
def catalogues():
    return {"macro": {"EFFR": {}, "OTHER": {}}, "price": {"SPY": {}},
            "release": {"us_cpi_headline_mm": {}}}


@pytest.mark.parametrize("field,value,message", [
    ("kind", "not_a_kind", "source kind"),
    ("id", "ESTR", "unknown macro source"),
    ("role", "best", "source role"),
    ("max_age_business_days", -1, "max_age_business_days"),
    ("max_age_business_days", True, "max_age_business_days"),
    ("max_age_business_days", 1.5, "max_age_business_days"),
    ("convention", "", "missing convention"),
    ("comparability_note", "", "comparability_note"),
])
def test_concept_rejects_invalid_source_metadata(simple_concept, catalogues, field, value, message):
    simple_concept["sources"][0][field] = value
    with pytest.raises(ValueError, match=message):
        concepts.validate_concepts([simple_concept], catalogues)


@pytest.mark.parametrize("field,value,message", [
    ("family", "unknown", "family"), ("unit", "", "missing unit"),
    ("calendar", None, "missing calendar"), ("label", " ", "missing label"),
    ("frequency", "", "missing frequency"), ("sources", [], "nonempty"),
    ("status", "disabled", "status"),
])
def test_concept_rejects_invalid_metadata(simple_concept, catalogues, field, value, message):
    simple_concept[field] = value
    with pytest.raises(ValueError, match=message):
        concepts.validate_concepts([simple_concept], catalogues)


def test_concept_primary_order_and_duplicate_sources(simple_concept, catalogues):
    source = simple_concept["sources"][0]
    simple_concept["sources"].append(dict(source, id="OTHER", role="secondary"))
    concepts.validate_concepts([simple_concept], catalogues)
    simple_concept["sources"].reverse()
    with pytest.raises(ValueError, match="primary first"):
        concepts.validate_concepts([simple_concept], catalogues)
    simple_concept["sources"].reverse()
    simple_concept["sources"][1]["role"] = "primary"
    with pytest.raises(ValueError, match="primary first"):
        concepts.validate_concepts([simple_concept], catalogues)
    simple_concept["sources"][1].update(id="EFFR", role="secondary")
    with pytest.raises(ValueError, match="duplicate source"):
        concepts.validate_concepts([simple_concept], catalogues)


@pytest.mark.parametrize("field,value,message", [
    ("ids", ["EFFR", "MISSING"], "declared pair"),
    ("ids", ["EFFR", "EFFR"], "declared pair"),
    ("ids", ["EFFR"], "declared pair"),
    ("tolerance", -1, "tolerance"), ("tolerance", float("nan"), "tolerance"),
    ("tolerance", float("inf"), "tolerance"), ("tolerance", True, "tolerance"),
    ("unit", "unknown", "unit"), ("note", "", "note"),
    ("basis", "unknown", "basis"), ("window", 0, "window"),
])
def test_concept_check_validates_declared_pairs(simple_concept, catalogues, field, value, message):
    simple_concept["sources"].append(dict(simple_concept["sources"][0], id="OTHER", role="secondary",
                                          convention="different convention"))
    simple_concept["check"] = {"ids": ["EFFR", "OTHER"], "tolerance": 1, "unit": "bp",
                               "note": "Declared comparable despite different conventions."}
    concepts.validate_concepts([simple_concept], catalogues)
    simple_concept["check"][field] = value
    with pytest.raises(ValueError, match=message):
        concepts.validate_concepts([simple_concept], catalogues)


def test_concept_price_release_and_derived_source_ids(simple_concept, catalogues):
    price = deepcopy(simple_concept)
    price["sources"][0].update(kind="price", id="SPY", field="adj_close", live="post_close")
    release = deepcopy(simple_concept)
    release["sources"][0].update(kind="release", id="us_cpi_headline_mm")
    derived = deepcopy(simple_concept)
    derived.update(concept="DERIVED")
    derived["sources"][0].update(kind="derived", id="TEST")
    for entry in (price, release):
        concepts.validate_concepts([entry], catalogues)
        entry["sources"][0]["id"] = "MISSING"
        with pytest.raises(ValueError, match="unknown .* source"):
            concepts.validate_concepts([entry], catalogues)
    concepts.validate_concepts([derived, simple_concept], catalogues)
    derived["sources"][0]["id"] = "MISSING"
    with pytest.raises(ValueError, match="unknown derived source"):
        concepts.validate_concepts([derived, simple_concept], catalogues)


@pytest.mark.parametrize("field,value,message", [
    ("field", "open", "price field"), ("live", "any", "live policy"),
    ("role", "proxy", "proxy requires its own unit"),
])
def test_concept_price_policies(simple_concept, catalogues, field, value, message):
    simple_concept["sources"][0].update(kind="price", id="SPY", field="adj_close", live="post_close")
    simple_concept["sources"][0][field] = value
    with pytest.raises(ValueError, match=message):
        concepts.validate_concepts([simple_concept], catalogues)


def test_concept_derived_forward_inputs_missing_inputs_and_cycles(simple_concept, catalogues):
    derived = dict(simple_concept, concept="SPREAD", kind="derived", sources=[],
                   expression="TEST - TEST", alignment="same_date", scale=100)
    concepts.validate_concepts([derived, simple_concept], catalogues)
    derived["expression"] = "TEST - MISSING"
    with pytest.raises(ValueError, match="unknown derived inputs"):
        concepts.validate_concepts([derived, simple_concept], catalogues)
    derived["expression"] = "TEST - SPREAD"
    with pytest.raises(ValueError, match="cycle"):
        concepts.validate_concepts([derived, simple_concept], catalogues)
    derived["expression"] = "TEST - SECOND"
    second = dict(derived, concept="SECOND", expression="TEST - SPREAD")
    with pytest.raises(ValueError, match="cycle"):
        concepts.validate_concepts([derived, second, simple_concept], catalogues)
    derived["expression"] = "TEST * TEST"
    with pytest.raises(ValueError, match="difference/sum"):
        concepts.validate_concepts([derived, simple_concept], catalogues)


def test_concept_pending_and_history_cannot_hide_missing_ids(simple_concept, catalogues):
    simple_concept.update(status="pending_source", pending_reason="Collector not yet available")
    with pytest.raises(ValueError, match="pending_source requires no sources"):
        concepts.validate_concepts([simple_concept], catalogues)
    simple_concept.pop("status")
    simple_concept["history"] = {"mode": "single", "id": "MISSING"}
    with pytest.raises(ValueError, match="history must name a declared source"):
        concepts.validate_concepts([simple_concept], catalogues)


def test_concept_catalogue_conventions_are_enforced(simple_concept, catalogues):
    catalogues["macro"]["EFFR"]["convention"] = "different convention"
    with pytest.raises(ValueError, match="convention does not match EFFR"):
        concepts.validate_concepts([simple_concept], catalogues)


def test_concept_duplicate_yaml_keys_and_empty_directory(tmp_path):
    with pytest.raises(ValueError, match="no concept family files"):
        concepts.load_concepts(tmp_path)
    path = tmp_path / "rates.yaml"
    path.write_text("concepts: []\nconcepts: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate configuration key"):
        concepts.load_concepts(tmp_path)


def test_concept_generated_ids_also_participate_in_uniqueness(tmp_path):
    spec = concepts.load_concepts()["PX:SPY"]
    (tmp_path / "equity.yaml").write_text(
        yaml.safe_dump({"generate": "yahoo_prices", "concepts": [spec]}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="duplicate concept: PX:SPY"):
        concepts.load_concepts(tmp_path)


def test_concept_validation_does_not_mutate_inputs(simple_concept, catalogues):
    before = deepcopy(simple_concept)
    registry = concepts.validate_concepts([simple_concept], catalogues)
    registry["TEST"]["sources"][0]["id"] = "CHANGED"
    assert simple_concept == before


def test_concept_offline_loader_does_not_connect_to_databases(monkeypatch):
    import duckdb
    import sqlite3

    def forbidden(*args, **kwargs):
        pytest.fail("The concept registry must load without a database connection")

    monkeypatch.setattr(duckdb, "connect", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    assert concepts.load_concepts()["US_3M"]


def _write_changed_yaml(path, config):
    previous = path.stat().st_mtime_ns if path.exists() else 0
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    # Make invalidation deterministic even on filesystems with coarse timestamps.
    changed = max(path.stat().st_mtime_ns, previous + 1_000_000_000)
    os.utime(path, ns=(changed, changed))


def test_registry_cache_isolated_returns_and_file_invalidation(
    tmp_path, monkeypatch, simple_concept, catalogues,
):
    calls = []

    def sources():
        calls.append(True)
        return catalogues

    monkeypatch.setattr(concepts, "load_source_catalogues", sources)
    path = tmp_path / "money_market.yaml"
    _write_changed_yaml(path, {"concepts": [simple_concept]})
    first = concepts.load_concepts(tmp_path)
    first["TEST"]["sources"][0]["id"] = "MUTATED"
    assert concepts.load_concepts(tmp_path)["TEST"]["sources"][0]["id"] == "EFFR"
    assert len(calls) == 1
    simple_concept["label"] = "Updated rate"
    _write_changed_yaml(path, {"concepts": [simple_concept]})
    assert concepts.load_concepts(tmp_path)["TEST"]["label"] == "Updated rate"
    assert len(calls) == 2


@pytest.mark.parametrize("changed_file", ["concepts/rates.yaml", "macro_series.yaml"])
def test_rates_cache_validates_only_macro_and_tracks_dependencies(
    tmp_path, monkeypatch, simple_concept, changed_file,
):
    (tmp_path / "concepts").mkdir()
    simple_concept.update(concept="US_10Y", family="rates")
    rates = {"tolerance_bp": 10, "concepts": [simple_concept]}
    _write_changed_yaml(tmp_path / "concepts/rates.yaml", rates)
    _write_changed_yaml(tmp_path / "macro_series.yaml", {"fred": [{"symbol": "EFFR", "frequency": "D"}]})
    for filename in ("sovereign_layers.yaml", "sovereign_yields.yaml"):
        _write_changed_yaml(tmp_path / filename, {"series": []})
    # Broken unrelated families/tickers must not affect sovereign rate reads.
    (tmp_path / "concepts/fx.yaml").write_text("invalid: [", encoding="utf-8")
    (tmp_path / "tickers.yaml").write_text("invalid: [", encoding="utf-8")
    monkeypatch.setattr(concepts, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_loader, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(resolved_rates, "_CONFIG_DIR", tmp_path)
    calls = []
    original = resolved_rates.validate_concepts

    def validate(*args):
        calls.append(True)
        return original(*args)

    def forbidden():
        pytest.fail("Runtime rates must not load the complete registry/catalogues")

    monkeypatch.setattr(resolved_rates, "validate_concepts", validate)
    monkeypatch.setattr(concepts, "load_concepts", forbidden)
    monkeypatch.setattr(concepts, "load_source_catalogues", forbidden)
    config, table = resolved_rates.load_resolved_rates()
    assert config["concepts"][0] is table["US_10Y"]
    table["US_10Y"]["sources"][0]["frequency"] = "MUTATED"
    assert resolved_rates.load_resolved_rates()[1]["US_10Y"]["sources"][0]["frequency"] == "D"
    assert len(calls) == 1
    if changed_file == "concepts/rates.yaml":
        rates["tolerance_bp"] = 20
        _write_changed_yaml(tmp_path / changed_file, rates)
        assert resolved_rates.load_resolved_rates()[0]["tolerance_bp"] == 20
    else:
        _write_changed_yaml(tmp_path / changed_file,
                            {"fred": [{"symbol": "EFFR", "frequency": "M"}]})
        assert resolved_rates.load_resolved_rates()[1]["US_10Y"]["sources"][0]["frequency"] == "M"
    assert len(calls) == 2


def test_registry_cache_tracks_catalogue_edits(tmp_path, monkeypatch, simple_concept):
    (tmp_path / "concepts").mkdir()
    _write_changed_yaml(tmp_path / "concepts/money_market.yaml", {"concepts": [simple_concept]})
    macro = tmp_path / "macro_series.yaml"
    _write_changed_yaml(macro, {"fred": [{"symbol": "EFFR"}]})
    for filename in ("sovereign_layers.yaml", "sovereign_yields.yaml"):
        _write_changed_yaml(tmp_path / filename, {"series": []})
    _write_changed_yaml(tmp_path / "tickers.yaml", {"yahoo": []})
    monkeypatch.setattr(concepts, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_loader, "_CONFIG_DIR", tmp_path)
    assert concepts.load_concepts()["TEST"]
    _write_changed_yaml(macro, {"fred": []})
    with pytest.raises(ValueError, match="unknown macro source EFFR"):
        concepts.load_concepts()
