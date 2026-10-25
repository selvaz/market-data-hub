"""docs/macro-indicators.md Part B is generated; a stale page must fail the suite."""
import re
import sys

import pytest

from market_data_hub.indicator_cards import dataset
from market_data_hub.indicator_cards import docs
from market_data_hub.macro_releases.catalogue import FAMILIES

CELL_SPLIT = re.compile(r"(?<!\\)\|")


def generated_block(text):
    return docs.split_document(text)[1]


def test_part_b_is_fresh():
    text = docs.DOC_PATH.read_text(encoding="utf-8")
    assert text == docs.updated_document(text), (
        "docs/macro-indicators.md Part B is stale; run python -m market_data_hub.indicator_cards.docs")
    assert text.count(docs.BEGIN) == 1 and text.count(docs.END) == 1


def test_check_flag_fails_on_stale_part_b(tmp_path, monkeypatch, capsys):
    page = tmp_path / "macro-indicators.md"
    fresh = docs.DOC_PATH.read_text(encoding="utf-8")
    page.write_text(fresh, encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["docs", "--check", "--path", str(page)])
    docs.main()
    assert "up to date" in capsys.readouterr().out
    # Any change inside the generated block, however small, is detected.
    page.write_text(fresh.replace("| yes |", "| no |", 1), encoding="utf-8")
    with pytest.raises(SystemExit, match="stale"):
        docs.main()
    # Hand-written text outside the markers is not part of the check.
    page.write_text(fresh.replace("Part A", "Part A (edited)", 1), encoding="utf-8")
    docs.main()


def test_write_mode_repairs_stale_block_and_keeps_hand_written_text(tmp_path, monkeypatch):
    page = tmp_path / "macro-indicators.md"
    fresh = docs.DOC_PATH.read_text(encoding="utf-8")
    before, block, after = docs.split_document(fresh)
    page.write_text(before + docs.BEGIN + "\nold\n" + docs.END + "\n" + after, encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["docs", "--path", str(page)])
    docs.main()
    assert page.read_text(encoding="utf-8") == fresh


def test_render_is_deterministic_and_marked():
    first = docs.render()
    assert first == docs.render()
    assert first.startswith(docs.BEGIN) and first.rstrip().endswith(docs.END)


def test_summary_counts_match_the_cards():
    rows = docs.build_rows()
    assert len(rows) == len(dataset().cards)
    block = docs.render()
    summary = block.split("### Summary by geography", 1)[1].split("### Coverage notes", 1)[0]
    lines = [row for row in summary.splitlines() if row.startswith("| ") and "---" not in row][1:]
    total = [c.strip() for c in CELL_SPLIT.split(lines[-1])[1:-1]]
    assert total[0] == "**All**"
    assert int(total[1]) == len(rows)
    assert int(total[2]) == sum(1 for r in rows if r.families)
    assert int(total[3]) == sum(1 for r in rows if r.consensus)
    assert sum(int(x) for x in total[4:]) == len(rows)
    per_geo = {}
    for line in lines[:-1]:
        cells = [c.strip() for c in CELL_SPLIT.split(line)[1:-1]]
        per_geo[cells[0].split(" ")[0]] = int(cells[1])
    assert per_geo == {code: sum(1 for c in dataset().cards if c.country == code)
                       for code in {c.country for c in dataset().cards}}


def test_tables_are_well_formed_and_cover_every_card_once():
    block = docs.render()
    keys = []
    for line in block.splitlines():
        if line.startswith("| ") and "(`" in line.split(" | ")[0]:
            assert len(CELL_SPLIT.split(line)) - 2 == 11, line
            keys.append(re.match(r"\| .*\(`([^`]+)`\) \|", line)[1])
    assert sorted(keys) == sorted(c.key for c in dataset().cards)


def test_official_cards_come_from_certified_families_only():
    rows = docs.build_rows()
    official = [r for r in rows if r.families]
    assert official
    for row in official:
        assert set(row.families) <= set(FAMILIES)
        assert row.indicators
    # Every certified family is reached by at least one card, or reported as such.
    reached = {f for r in official for f in r.families}
    notes = docs._coverage_notes(rows)[1]
    for family in FAMILIES:
        assert family in reached or f"`{family}`" in notes


@pytest.mark.parametrize("source,issuers,via", [
    ("ICE via FRED", ["ICE"], ["FRED"]),
    ("FRED / Fed Board", ["Fed Board"], ["FRED"]),
    ("FRED", [], ["FRED"]),
    ("FRED / Sahm", [], ["FRED"]),
    ("Census/BEA", ["Census", "BEA"], []),
    ("Fed Board H.15; OECD MEI", ["Fed Board H.15", "OECD MEI"], []),
    ("", [], []),
])
def test_split_source_never_calls_a_redistributor_the_issuer(source, issuers, via):
    assert docs.split_source(source) == (issuers, via)


def test_issuer_column_has_no_redistributor_and_names_it_as_feed():
    for row in docs.build_rows():
        issuer = row.issuer_line.split("<br>")[0]
        assert "FRED" not in issuer, row.card.key
    by_key = {r.card.key: r for r in docs.build_rows()}
    line = by_key["us_breakeven_5y"].issuer_line
    assert line.startswith("issuer: Fed Board") and "FRED (redistributor)" in line


def test_release_times_and_consensus_are_derived():
    rows = {r.card.key: r for r in docs.build_rows()}
    assert rows["us_payrolls"].release_time == "08:30 America/New_York"
    assert rows["us_jolts_openings"].release_time == "10:00 America/New_York"
    assert rows["us_payrolls"].families == ("us_payrolls",)
    # M2b (#128) aliased payrolls to FF; UK regular pay has no exact FF title.
    assert rows["us_cpi_headline"].consensus and rows["us_payrolls"].consensus
    assert rows["uk_awe_regular"].families and not rows["uk_awe_regular"].consensus
    assert rows["us_sahm"].release_time == docs.DASH and not rows["us_sahm"].families
