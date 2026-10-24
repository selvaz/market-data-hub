"""Reviewed FF measures and calendar linking; no network or production writes."""
import pytest

from market_data_hub.macro_releases import consensus, open_writer
from market_data_hub.macro_releases.catalogue import INDICATORS


@pytest.mark.parametrize("currency,title", [
    ("USD", "Core PPI m/m"), ("USD", "PPI q/q"),
    ("USD", "Retail Sales y/y"), ("USD", "Core CPI q/q"),
    ("USD", "Existing Home Sales"), ("GBP", "Retail Sales y/y"),
    ("GBP", "CPI m/m"), ("EUR", "Main Refinancing Rate"),
    ("CNY", "5-y Loan Prime Rate"), ("JPY", "Trade Balance"),
    ("EUR", "German Industrial Production m/m"),
    ("EUR", "Flash Manufacturing PMI"), ("USD", "Final Services PMI"),
])
def test_wrong_measure_transform_geography_and_private_pmi_do_not_map(tmp_path, currency, title):
    with open_writer(tmp_path / "macro.sqlite") as store:
        for indicator in INDICATORS.values():
            store.put(indicator)
        mapping, reason = consensus.Catalogue(store).match({"country": currency}, title)
        assert mapping is None and reason == "unmapped"
