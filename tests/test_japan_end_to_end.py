"""End-to-end contracts for newly discovered Japan editions."""
from contextlib import contextmanager
from dataclasses import replace
import hashlib

import pytest

from market_data_hub.macro_releases import asia, jp_discovery
from market_data_hub.macro_releases.http import Document, FetchError
from market_data_hub.macro_releases.schedule import RUN_INSTANT, PENDING_EDITIONS


FAMILIES = (
    "jp_industrial_production", "jp_unemployment", "jp_household_spending",
    "jp_tankan", "jp_cpi", "jp_gdp", "jp_oecd_cli",
)
INDICATORS = {
    "jp_industrial_production": "jp_industrial_production_mm",
    "jp_unemployment": "jp_unemployment_rate",
    "jp_household_spending": "jp_household_spending_yy",
    "jp_tankan": "jp_tankan_large_manufacturers_di",
    "jp_cpi": "jp_cpi_yy", "jp_gdp": "jp_gdp_qq", "jp_oecd_cli": "JP_OECD_CLI",
}
PERIODS = {
    "jp_industrial_production": "2027-03", "jp_unemployment": "2027-03",
    "jp_household_spending": "2027-03", "jp_tankan": "2027-Q1",
    "jp_cpi": "2027-03", "jp_gdp": "2027-Q1", "jp_oecd_cli": "2027-03",
}


class NextMonthClient:
    """Issuer latest page and its moving data file, both changed in-memory."""
    def __init__(self, period):
        self.period = period
        self.data_url = "https://issuer.example/new-edition.data"

    def get(self, provider, url, params=None):
        # The body represents the modified next-month file; no checked-in future fixture.
        body = (self.period + ",1.25").encode()
        return Document(body, url, "2027-04-30T00:01:00Z", hashlib.sha256(body).hexdigest())


def fake_parser(document, family, since, *, archives):
    period, printed = document.body.decode().split(",")
    edition = next(d for d, url in archives.items() if url == document.url)
    expected = asia.reviewed(family, edition)[0]
    if period != expected:
        raise FetchError("moving publication " + jp_discovery.moving_status(expected, period))
    row = asia.observation(document, family, edition, INDICATORS[family], printed, "fixture")
    return [replace(row, precision="observed", published_at=document.retrieved_at)
            if family == "jp_oecd_cli" else row]


@contextmanager
def run_at(instant):
    run = RUN_INSTANT.set(instant)
    pending = {}
    notices = PENDING_EDITIONS.set(pending)
    try:
        yield pending
    finally:
        PENDING_EDITIONS.reset(notices)
        RUN_INSTANT.reset(run)


@pytest.mark.parametrize("family", FAMILIES)
def test_next_month_latest_page_and_data_file_produce_observation(monkeypatch, family):
    period = PERIODS[family]
    client = NextMonthClient(period)
    precision = "observed" if family == "jp_oecd_cli" else "exact"
    edition = jp_discovery.Edition("2027-04-30", period, "final", "08:30",
                                   client.data_url, precision)
    monkeypatch.setattr(jp_discovery, "discover", lambda unused, selected: [edition])
    with run_at("2027-05-01T00:00:00Z"):
        rows = asia.fetch_archives(client, family, "2027-01", "issuer", {}, fake_parser)
    assert len(rows) == 1
    assert (rows[0].period, rows[0].edition, rows[0].precision) == (
        period, "2027-04-30", precision)


@pytest.mark.parametrize("today,reason", [
    ("2027-04-30T12:00:00Z", "not_yet_published"),
    ("2027-05-03T12:00:00Z", "overdue"),
])
def test_old_period_from_moving_file_stays_pending(monkeypatch, today, reason):
    family = "jp_industrial_production"
    client = NextMonthClient("2027-02")
    edition = jp_discovery.Edition("2027-04-30", "2027-03", "preliminary", "08:30",
                                   client.data_url)
    monkeypatch.setattr(jp_discovery, "discover", lambda unused, selected: [edition])
    with run_at(today) as pending:
        rows = asia.fetch_archives(client, family, "2027-01", "issuer", {}, fake_parser)
        assert rows == []
        assert pending[family, "2027-04-30"] == reason
