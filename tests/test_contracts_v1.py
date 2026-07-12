# -*- coding: utf-8 -*-
"""Round-trip validation of contracts/v1/*.json against the lazydatacore
models/validators that own each shape (ecosystem stabilization plan,
ECO-010). This is the producer-side half of the contract: market-data-hub
validates its own fixtures here; LazyStats/LazyRay validate the SAME files
as consumers (Train B, PR B3).

"Round-trip": parse -> validate -> re-serialize -> compare, so a fixture
that merely happens to *parse* but silently drops or reshapes data on
serialization is still caught.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from market_data_hub.lazydatacore import (
    AnalysisResult,
    InstrumentId,
    Provenance,
    validate_long_prices,
    validate_wide_prices,
)

CONTRACTS_V1 = Path(__file__).resolve().parent.parent / "contracts" / "v1"


def _load(name: str):
    return json.loads((CONTRACTS_V1 / name).read_text(encoding="utf-8"))


def test_analysis_result_fixture_round_trips():
    raw = _load("analysis_result.json")
    res = AnalysisResult.model_validate(raw)
    again = json.loads(res.model_dump_json())
    assert again == raw
    assert res.schema_version == "1.0"
    assert res.producer == "market-data-hub"


def test_provenance_fixture_round_trips():
    raw = _load("provenance.json")
    prov = Provenance.model_validate(raw)
    assert json.loads(prov.model_dump_json()) == raw


def test_instrument_id_fixture_round_trips():
    raw = _load("instrument_id.json")
    assert isinstance(raw, list) and raw, "fixture must be a non-empty array"
    for canonical in raw:
        iid = InstrumentId.model_validate(canonical)
        assert iid.model_dump(mode="json") == canonical


def test_price_series_fixture_validates_as_long_frame():
    pd = pytest.importorskip("pandas")
    raw = _load("price_series.json")
    rows = raw["rows"]
    assert rows, "fixture must carry at least one row"
    df = pd.DataFrame(rows)
    assert validate_long_prices(df) is df
    for col in ("symbol", "date", "open", "high", "low", "close", "adj_close", "volume"):
        assert col in df.columns


def test_return_series_fixture_validates_as_wide_frame():
    pd = pytest.importorskip("pandas")
    raw = _load("return_series.json")
    rows = raw["rows"]
    assert rows, "fixture must carry at least one row"
    df = pd.DataFrame(rows).set_index("date")
    df.index = pd.to_datetime(df.index)
    assert validate_wide_prices(df) is df
    # Columns are keyed by the BARE symbol, exactly as extract_returns emits
    # them; `instruments` carries the canonical ids for the same set and
    # `symbols` their bare forms. A consumer maps bare -> canonical itself.
    assert set(df.columns) == set(raw["symbols"])
    from market_data_hub.lazydatacore import InstrumentId

    assert [InstrumentId.parse(i).key for i in raw["instruments"]] == raw["symbols"]


def test_all_five_fixtures_present():
    expected = {
        "analysis_result.json",
        "provenance.json",
        "instrument_id.json",
        "price_series.json",
        "return_series.json",
    }
    present = {p.name for p in CONTRACTS_V1.glob("*.json")}
    assert expected <= present
