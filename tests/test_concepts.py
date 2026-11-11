"""Offline H1 contract: catalogued sources and unchanged legacy rate chains."""
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from market_data_hub import concepts, resolved_rates
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
