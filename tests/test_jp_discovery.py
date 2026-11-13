"""Offline contracts for live Japan edition discovery."""
from dataclasses import replace
from datetime import date
import hashlib
from pathlib import Path

import pytest

from market_data_hub.macro_releases.http import Document, FetchError
from market_data_hub.macro_releases import jp_discovery as discovery

FIX = Path(__file__).parent / "fixtures/japan_tier1/discovery"
URLS = {
    discovery.METI: "meti_iip_latest.html", discovery.LFS: "stat_lfs_latest.html",
    discovery.LFS_SCHEDULE: "stat_lfs_schedule.html", discovery.FIES: "stat_fies_latest.html",
    discovery.CPI: "stat_cpi_latest.html", discovery.CPI_SCHEDULE: "stat_cpi_schedule.html",
    discovery.GDP: "esri_gdp_schedule.html", discovery.TANKAN: "boj_tankan_listing.html",
    discovery.OECD: "oecd_cli_last1.json",
}


class Client:
    def get(self, provider, url, params=None):
        name = URLS[url]
        body = (FIX / name).read_bytes()
        return Document(body, url, "2026-11-13T13:54:03+00:00", hashlib.sha256(body).hexdigest())


def test_new_editions_and_issuer_precision():
    client = Client()
    meti = discovery.discover(client, "jp_industrial_production")[0]
    fies = discovery.discover(client, "jp_household_spending")[0]
    tankan = discovery.discover(client, "jp_tankan")[0]
    oecd = discovery.discover(client, "jp_oecd_cli")[0]
    assert (meti.edition_date, meti.period, meti.release_time) == ("2026-10-30", "2026-09", "8:50")
    assert (fies.edition_date, fies.period, fies.precision) == ("2026-11-10", "2026-09", "date")
    assert (tankan.edition_date, tankan.period) == ("2026-10-01", "2026-Q3")
    assert (oecd.edition_date, oecd.period, oecd.precision) == ("2026-11-13", "2026-10", "observed")


def test_schedule_rollover_to_2027():
    gdp = discovery.discover(Client(), "jp_gdp")
    cpi = discovery.discover(Client(), "jp_cpi")
    assert any(e.edition_date == "2027-02-15" and e.period == "2026-Q4" for e in gdp)
    assert any(e.edition_date == "2027-01-22" and e.period == "2026-12" for e in cpi)


def test_moving_url_pending_rolled_and_overdue():
    assert discovery.moving_status("2026-10", "2026-09") == "not_yet_published"
    assert discovery.moving_status("2026-10", "2026-11") == "needs_review"
    assert discovery.pending_reason("2026-11-10", today=date(2026, 11, 11)) == "not_yet_published"
    assert discovery.pending_reason("2026-11-10", today=date(2026, 11, 12)) == "overdue"


def test_meti_403_is_transport_error():
    class Forbidden(Client):
        def get(self, provider, url, params=None):
            raise FetchError("meti: HTTP 403")
    with pytest.raises(FetchError, match="meti: HTTP 403"):
        discovery.discover(Forbidden(), "jp_industrial_production")
