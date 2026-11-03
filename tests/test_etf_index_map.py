from copy import deepcopy

from market_data_hub.config_loader import get_etf_index_map
from market_data_hub.reader import read_etf_index_map
from validate_config import validate_etf_index_map


def test_index_map_valid_and_reader_filters():
    rows = get_etf_index_map()
    assert not validate_etf_index_map({"funds": rows})
    result = read_etf_index_map(["AGG", "SPAB"])
    assert set(result.symbol) == {"AGG", "SPAB"}
    assert result.underlying_index.nunique() == 1
    assert read_etf_index_map("not-a-symbol").empty


def test_index_map_rejects_invalid_rows():
    row = deepcopy(next(row for row in get_etf_index_map() if row["symbol"] == "AGG"))
    row["hedged"] = True
    errors = validate_etf_index_map({"funds": [row, deepcopy(row)]})
    assert any("requires hedge_currency" in error for error in errors)
    assert any("duplicate symbol" in error for error in errors)
