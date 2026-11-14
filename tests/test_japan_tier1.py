"""Offline Japan tier-1 source contracts captured on 2026-11-13."""
import json
from pathlib import Path

import pytest
import requests

from market_data_hub.macro_releases.http import Document, FetchError
from market_data_hub.macro_releases import japan_macro

FIX = Path(__file__).parent / "fixtures/japan_tier1"
MANIFEST = {row["file"]: row for row in json.loads((FIX / "MANIFEST.json").read_text())}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(requests.Session, "request", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network forbidden")))


def doc(name):
    row = MANIFEST[name]
    body = (FIX / name).read_bytes()
    return Document(body, row["url"], row["retrieved_at"], row["sha256"])


def test_esri_gdp_published_rate():
    row = japan_macro.parse(doc("esri_gdp_qq_ritu-jk2622.csv"), "jp_gdp")[0]
    assert (row.indicator_id, row.period, row.value, row.stage) == ("jp_gdp_qq", "2026-Q2", 0.4, "revised")


def test_meti_iip_workbook_preliminary_rate_and_schema():
    assert japan_macro.parse_meti_iip_workbook((FIX / "meti_iip_gsm1e.xlsx").read_bytes()) == ("2026-09", 2.2, True)
    with pytest.raises(FetchError, match="workbook"):
        japan_macro.parse_meti_iip_workbook(b"not an xlsx")


def test_oecd_exact_series_keys_and_latest_values():
    cli = {"STRUCTURE_ID": "DSD_STES@DF_CLI(4.1)", "REF_AREA": "JPN", "FREQ": "M", "MEASURE": "LI", "UNIT_MEASURE": "IX", "ACTIVITY": "_Z", "ADJUSTMENT": "AA", "TRANSFORMATION": "IX", "TIME_HORIZ": "_Z", "METHODOLOGY": "H"}
    unemp = {"STRUCTURE_ID": "DSD_KEI@DF_KEI(4.0)", "REF_AREA": "JPN", "FREQ": "M", "MEASURE": "UNEMP", "UNIT_MEASURE": "PT_LF", "ACTIVITY": "_T", "ADJUSTMENT": "Y", "TRANSFORMATION": "_Z"}
    cpi = {"STRUCTURE_ID": "DSD_PRICES_COICOP2018@DF_PRICES_C2018_ALL(1.0)", "REF_AREA": "JPN", "FREQ": "M", "METHODOLOGY": "N", "MEASURE": "CPI", "UNIT_MEASURE": "PA", "EXPENDITURE": "_T", "ADJUSTMENT": "N", "TRANSFORMATION": "GY"}
    assert japan_macro.parse_oecd_sdmx((FIX / "oecd_cli_jpn.csv").read_bytes(), cli) == ("2026-10", 100.2095)
    assert japan_macro.parse_oecd_sdmx((FIX / "oecd_unemp_kei_jpn.csv").read_bytes(), unemp) == ("2026-09", 2.4)
    assert japan_macro.parse_oecd_sdmx((FIX / "oecd_cpi_c2018_jpn.csv").read_bytes(), cpi) == ("2026-09", 2.1)
    broken = (FIX / "oecd_cli_jpn.csv").read_bytes().replace(b",AA,", b",N,", 1)
    with pytest.raises(FetchError, match="identity"):
        japan_macro.parse_oecd_sdmx(broken, cli)



def test_statistics_bureau_cpi_and_lfs_workbooks():
    cpi_rows = japan_macro.parse(doc("statjp_fies_file_000040513539.xlsx"), "jp_cpi")
    assert [(row.indicator_id, row.period, row.value) for row in cpi_rows] == [
        ("jp_cpi_yy", "2026-09", 2.1), ("jp_core_cpi_yy", "2026-09", 2.1)]
    assert japan_macro.parse_statjp_lfs_workbook((FIX / "statjp_cpi_file_000031831358.xlsx").read_bytes()) == ("2026-09", 2.4)



def test_household_fixture_manifest_does_not_mislabel_cpi_as_fies():
    # The captured file bearing the FIES filename is manifest-bound to e-Stat
    # CPI file 000040513539. Fail closed rather than report CPI cell I91 as FIES.
    with pytest.raises(FetchError, match="FIES"):
        japan_macro.parse_statjp_fies_workbook((FIX / "statjp_fies_file_000040513539.xlsx").read_bytes())
