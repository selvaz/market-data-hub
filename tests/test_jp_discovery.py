"""Offline contracts for live Japan edition discovery."""
from datetime import date
import hashlib
from pathlib import Path

import pytest

from market_data_hub.macro_releases.http import Document, FetchError
from market_data_hub.macro_releases import jp_discovery as discovery
from market_data_hub.macro_releases.schedule import due, release_instant

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
    assert (meti.edition_date, meti.period, meti.release_time, meti.published_value) == (
        "2026-10-30", "2026-09", "08:50", "2.2"
    )
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


def test_lfs_resolves_per_edition_estat_workbook_and_preserves_unresolved():
    scheduled = [
        discovery.Edition("2026-10-30", "2026-09", "final", "08:30", "https://www.e-stat.go.jp/en/stat-search/files", "date"),
        discovery.Edition("2026-11-27", "2026-10", "final", "08:30", "https://www.e-stat.go.jp/en/stat-search/files", "date"),
    ]
    body = b'''<ul><li>2026 October Update date 2026-11-27</li>
      <a href="/en/stat-search/file-download?statInfId=000031999999&amp;fileKind=0">Excel</a></ul>'''
    doc = Document(body, "https://www.e-stat.go.jp/en/stat-search/files?x=1", "2026-11-27T00:00:00Z", "hash")
    result = discovery.resolve_lfs_workbooks(doc, scheduled)
    assert result[0].document_url == "https://www.e-stat.go.jp/en/stat-search/files"
    assert result[1].document_url == "https://www.e-stat.go.jp/en/stat-search/file-download?statInfId=000031999999&fileKind=0"


def test_oecd_reuses_first_seen_date_for_same_period():
    token = discovery.OECD_FIRST_SEEN.set({"2026-10": "2026-11-09"})
    try:
        edition = discovery.discover(Client(), "jp_oecd_cli")[0]
    finally:
        discovery.OECD_FIRST_SEEN.reset(token)
    assert (edition.edition_date, edition.period) == ("2026-11-09", "2026-10")


def test_meti_missing_workbook_fails_closed():
    original = (FIX / "meti_iip_latest.html").read_bytes()
    body = original.replace(b"b2020_gsm1e.xlsx", b"missing.xlsx")
    doc = Document(body, discovery.METI, "2026-10-30T00:00:00Z", "hash")
    with pytest.raises(FetchError, match="workbook link needs review"):
        discovery.parse_meti(doc)


def test_meti_fixture_clock_schedules_the_next_discovered_edition():
    latest, next_edition = discovery.discover(Client(), "jp_industrial_production")
    editions = {
        item.edition_date: (item.period, item.stage, item.release_time)
        for item in (latest, next_edition)
    }

    assert next_edition.release_time == "08:50"
    assert release_instant(
        next_edition.edition_date,
        editions[next_edition.edition_date],
        family="jp_industrial_production",
    ).isoformat() == "2026-11-29T23:50:00+00:00"
    assert list(due(
        editions,
        "2026-11-29T23:50:00Z",
        family="jp_industrial_production",
    )) == ["2026-10-30", "2026-11-30"]
