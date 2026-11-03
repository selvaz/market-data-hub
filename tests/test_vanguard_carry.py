from pathlib import Path
from market_data_hub.vanguard_carry import parse_pdf


def test_bndx_recorded_fund_not_index_characteristics():
    data = (
        Path(__file__).parent / "fixtures/vanguard_bndx_fact_sheet.pdf"
    ).read_bytes()
    date, values = parse_pdf(data)
    assert date.isoformat() == "2026-09-30"
    assert values["average_duration"][0] == 6.4
    assert values["holdings_count"][0] == 6837
