"""End-to-end contracts for newly discovered Japan editions."""
from contextlib import contextmanager
from dataclasses import replace
import hashlib
from io import BytesIO
import json
from pathlib import Path

import pytest

from market_data_hub.macro_releases import asia, jp_discovery, open_writer, run_daily
from market_data_hub.macro_releases import japan_macro
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


def fake_parser(document, family, since, *, archives, edition, **unused):
    period, printed = document.body.decode().split(",")
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


def test_overdue_discovered_meti_edition_is_visible_in_daily_report(tmp_path):
    fixture = Path(__file__).parent / "fixtures/japan_tier1"
    page = (fixture / "discovery/meti_iip_latest.html").read_bytes()
    workbook = (fixture / "meti_iip_gsm1e.xlsx").read_bytes()

    class FixtureClient:
        def get(self, provider, url, params=None):
            body = page if url == jp_discovery.METI else workbook
            return Document(body, url, "2026-12-03T00:00:00Z",
                            hashlib.sha256(body).hexdigest())

    family = "jp_industrial_production"
    result = run_daily.collect_family(
        lambda: open_writer(tmp_path / "macro.sqlite"), FixtureClient(), "daily", family,
        {family: {"fetch_since": "2026-09", "fetch_until": "2026-12-03T00:00:00Z"}},
        dry_run=True,
    )[family]

    assert result["status"] == "needs_review"
    assert any(
        item["edition"] == "2026-11-30" and item["reason"] == "overdue"
        for item in result["scheduled_editions"]
    )
    assert "edition_overdue:2026-11-30" in result["review_reasons"]


@pytest.mark.parametrize("family", ["jp_industrial_production", "jp_household_spending"])
def test_next_edition_wins_when_moving_url_is_shared(monkeypatch, family):
    client = NextMonthClient("2027-03")
    moving_url = client.data_url
    edition = jp_discovery.Edition(
        "2027-04-30", "2027-03", "final", "08:30", moving_url
    )
    monkeypatch.setattr(jp_discovery, "discover", lambda unused, selected: [edition])
    archives = {"2027-03-31": moving_url}

    with run_at("2027-05-01T00:00:00Z") as pending:
        rows = asia.fetch_archives(client, family, "2027-03", "issuer", archives, fake_parser)

    assert [(row.edition, row.period) for row in rows] == [("2027-04-30", "2027-03")]
    assert pending == {}


def test_next_meti_edition_uses_html_rate_and_workbook_validation():
    from openpyxl import load_workbook

    fixture = Path(__file__).parent / "fixtures/japan_tier1"
    page = (fixture / "discovery/meti_iip_latest.html").read_bytes()
    page = page.replace(b"September 2026", b"October 2026")
    page = page.replace(b"October 30, 2026", b"November 30, 2026")
    page = page.replace(b"<td>2.2</td>", b"<td>2.3</td>", 1)
    workbook = load_workbook(fixture / "meti_iip_gsm1e.xlsx")
    sheet = workbook["Production"]
    new_column = sheet.max_column + 1
    sheet.cell(3, new_column).value = "p 202610"
    production = next(row for row in range(1, sheet.max_row + 1)
                      if sheet.cell(row, 1).value == 1000000000)
    sheet.cell(production, new_column).value = 106.5
    output = BytesIO()
    workbook.save(output)
    workbook.close()

    class FixtureClient:
        def get(self, provider, url, params=None):
            body = page if url == jp_discovery.METI else output.getvalue()
            return Document(body, url, "2026-11-30T00:01:00Z", hashlib.sha256(body).hexdigest())

    with run_at("2026-11-30T00:01:00Z"):
        rows = japan_macro.fetch(FixtureClient(), "jp_industrial_production", "2026-10")

    assert len(rows) == 1
    row = rows[0]
    assert (row.edition, row.period, row.printed, row.value) == (
        "2026-11-30", "2026-10", "2.3", 2.3
    )
    assert row.method == "html_table"
    assert row.document.url == jp_discovery.METI


def test_discovered_november_oecd_edition_fetches_csv_observation():
    fixture = Path(__file__).parent / "fixtures/japan_tier1/oecd_cli_jpn.csv"
    csv_body = fixture.read_bytes().replace(b"2026-10", b"2026-11")
    discovery = {
        "data": {
            "dataSets": [{"observations": {"0": [100.3]}}],
            "structure": {"dimensions": {"observation": [{"values": [{"id": "2026-11"}]}]}},
        }
    }

    class OecdClient:
        def __init__(self):
            self.requests = []

        def get(self, provider, url, params=None):
            self.requests.append((url, params))
            body = json.dumps(discovery).encode() if params else csv_body
            # Match HttpClient: request parameters are not retained in Document.url.
            return Document(body, url, "2026-11-15T00:01:00Z", hashlib.sha256(body).hexdigest())

    client = OecdClient()
    with run_at("2026-11-15T00:01:00Z"):
        rows = japan_macro.fetch(client, "jp_oecd_cli", "2026-11")

    assert [(row.edition, row.period, row.value) for row in rows] == [
        ("2026-11-15", "2026-11", 100.2095)
    ]
    assert client.requests == [
        (jp_discovery.OECD, {"lastNObservations": "1", "format": "jsondata"}),
        (jp_discovery.OECD + "?format=csvfilewithlabels", None),
    ]


def test_gdp_schedule_editions_follow_archive_links():
    schedule = (Path(__file__).parent /
                "fixtures/japan_tier1/discovery/esri_gdp_schedule.html").read_bytes()
    archive = b'''<table>
      <tr><td>Nov 16, 2026 <a href="qe263/gdemenuea.html">Quarterly Estimates of GDP for Jul.-Sep.2026 (The First preliminary Estimates)</a></td></tr>
      <tr><td>Dec 8, 2026 <a href="qe263_2/gdemenuea.html">Quarterly Estimates of GDP for Jul.-Sep.2026 (The Second preliminary Estimates)</a></td></tr>
    </table>'''
    csv_body = (
        'x\n"Real, Seasonally Adjusted Series (Quarter-to-Quarter Percent Change)",(%)\n'
        "x\nx\nx\n,GDP(Expenditure Approach)\nx\n2026/ 7- 9.,0.5\n"
    ).encode("cp932")

    class GdpClient:
        def get(self, provider, url, params=None):
            if url == jp_discovery.GDP:
                body = schedule
            elif url == japan_macro.GDP_ARCHIVE:
                body = archive
            elif url.endswith("gdemenuea.html"):
                suffix = "1" if "/qe263/" in url else "2"
                body = (f'<a href="../../../../../../../../jp/sna/data/data_list/sokuhou/files/2026/'
                        f'qe263{"" if suffix == "1" else "_2"}/tables/ritu-jk263{suffix}.csv">'
                        'Real, Seasonally Adjusted Series (Quarter-to-Quarter) (csv: 1KB)</a>').encode()
            else:
                body = csv_body
            return Document(body, url, "2026-12-08T00:01:00Z", hashlib.sha256(body).hexdigest())

    with run_at("2026-12-08T00:01:00Z") as pending:
        rows = japan_macro.fetch(GdpClient(), "jp_gdp", "2026-07")

    assert pending == {}
    assert [(row.edition, row.period, row.value) for row in rows] == [
        ("2026-11-16", "2026-Q3", 0.5),
        ("2026-12-08", "2026-Q3", 0.5),
    ]


def test_2027_q4_gdp_editions_resolve_period_derived_csv_links():
    schedule = (Path(__file__).parent /
                "fixtures/japan_tier1/discovery/esri_gdp_schedule.html").read_bytes()
    archive_url = "https://www.esri.cao.go.jp/en/sna/data/sokuhou/files/2026/toukei_2026.html"
    archive = b'''<table>
      <tr><td>Feb 15, 2027 <a href="qe264/gdemenuea.html">Quarterly Estimates of GDP for Oct.-Dec.2026 (The First preliminary Estimates)</a></td></tr>
      <tr><td>Mar 9, 2027 <a href="qe264_2/gdemenuea.html">Quarterly Estimates of GDP for Oct.-Dec.2026 (The Second preliminary Estimates)</a></td></tr>
    </table>'''
    csv_body = (
        'x\n"Real, Seasonally Adjusted Series (Quarter-to-Quarter Percent Change)",(%)\n'
        "x\nx\nx\n,GDP(Expenditure Approach)\nx\n2026/ 10-12.,0.7\n"
    ).encode("cp932")
    csv_urls = {
        "https://www.esri.cao.go.jp/jp/sna/data/data_list/sokuhou/files/2026/qe264/tables/ritu-jk2641.csv",
        "https://www.esri.cao.go.jp/jp/sna/data/data_list/sokuhou/files/2026/qe264_2/tables/ritu-jk2642.csv",
    }

    class GdpRolloverClient:
        def __init__(self):
            self.requested = []

        def get(self, provider, url, params=None):
            self.requested.append(url)
            if url == jp_discovery.GDP:
                body = schedule
            elif url == archive_url:
                body = archive
            elif url.endswith("gdemenuea.html"):
                revised = "_2/" in url
                directory = "qe264_2" if revised else "qe264"
                number = "2" if revised else "1"
                body = (f'<a href="../../../../../../../../jp/sna/data/data_list/sokuhou/files/'
                        f'2026/{directory}/tables/ritu-jk264{number}.csv">'
                        'Real, Seasonally Adjusted Series (Quarter-to-Quarter) (csv: 1KB)</a>').encode()
            else:
                assert url in csv_urls
                body = csv_body
            return Document(body, url, "2027-03-09T00:01:00Z", hashlib.sha256(body).hexdigest())

    client = GdpRolloverClient()
    with run_at("2027-03-09T00:01:00Z") as pending:
        rows = japan_macro.fetch(client, "jp_gdp", "2026-10")

    assert pending == {}
    assert [(row.edition, row.period, row.value) for row in rows] == [
        ("2027-02-15", "2026-Q4", 0.7),
        ("2027-03-09", "2026-Q4", 0.7),
    ]
    assert csv_urls <= set(client.requested)

@pytest.mark.parametrize("family,edition,period,tokyo", [
    ("jp_cpi", "2026-11-20", "2026-10", False),
    ("jp_cpi", "2027-04-23", "2027-03", False),
    ("jp_tokyo_cpi", "2027-04-30", "2027-04", True),
])
def test_cpi_listing_discovery_resolves_workbook_and_produces_observations(
        monkeypatch, family, edition, period, tokyo):
    """A discovery listing is evidence, never the edition's known data file."""
    from openpyxl import load_workbook
    from market_data_hub.macro_releases.asia_discovery import CPI_INDEX

    root = Path(__file__).parent / "fixtures/macro_releases/m9d1"
    workbook = load_workbook(root / "jp-cpi-sep.xlsx")
    sheet = next(s for s in workbook if s["H8"].value == "Monthly, % Change over the year")
    source_row = next(row for row in range(1, sheet.max_row + 1)
                      if str(sheet.cell(row, 2).value).strip() == "202609"
                      and str(sheet.cell(row, 3).value).strip() == "03")
    sheet.cell(source_row, 2).value = period.replace("-", "")
    sheet["I6"] = ("Table 1-2 Subgroup Index for Ku-area of Tokyo" if tokyo
                   else "Table 1-1 Subgroup Index for Japan")
    if tokyo:
        # The captured national workbook has no preliminary note.
        sheet.cell(1, 1).value = "Note) Latest monthly figures are preliminary ones."
    output = BytesIO()
    workbook.save(output)
    workbook.close()

    listing_url = "https://www.e-stat.go.jp/en/stat-search/files?synthetic=1"
    file_url = ("https://www.e-stat.go.jp/en/stat-search/file-download?"
                "statInfId=999999999999&fileKind=4")
    survey = period
    if tokyo:
        from market_data_hub.macro_releases.bls import shift_month
        survey = shift_month(period, -1)
    label = ("Subgroup Index for Ku-area of Tokyo" if tokyo
             else "Subgroup Index for Japan")
    month_link = (f'<ul class="stat-dataset_list-detail">2025-Base Consumer Price Index '
                  f'{label} Monthly Survey date '
                  f'{__import__("datetime").datetime.strptime(survey, "%Y-%m").strftime("%Y%b.")} '
                  f'Update date {edition}<a data-release_count="{2 if tokyo else 1}" '
                  f'href="{file_url}">Excel</a></ul>').encode()
    index = (f'<a href="{listing_url}&year={survey[:4]}0&month=230709{survey[5:]}&toukei=00200573'
             f'&tstat=000001243876">'
             f'{__import__("datetime").datetime.strptime(survey, "%Y-%m").strftime("%b.")}</a>').encode()

    discovered = jp_discovery.Edition(
        edition, period, "preliminary" if tokyo else "final", "08:30",
        "https://www.e-stat.go.jp/en/stat-search/files",
    )
    monkeypatch.setattr(jp_discovery, "discover", lambda client, selected: [discovered])

    class Client:
        def get(self, provider, url, params=None):
            if url == CPI_INDEX:
                body = index
            elif url.startswith(listing_url):
                body = month_link
            elif url == file_url:
                body = output.getvalue()
            else:
                raise AssertionError(url)
            return Document(body, url, edition + "T00:01:00Z", hashlib.sha256(body).hexdigest())

    with run_at(edition + "T12:00:00Z") as pending:
        rows = japan_macro.fetch(Client(), family, period)

    assert pending == {}
    assert rows
    assert {row.edition for row in rows} == {edition}
    assert {row.period for row in rows} == {period}


def test_march_2027_tankan_discovery_reaches_generalised_pdf_parser(monkeypatch):
    """Exercise Q1 and 2027 through discovery, fetch and the production parser."""
    edition = jp_discovery.Edition(
        "2027-04-01", "2027-Q1", "final", "08:50",
        "https://www.boj.or.jp/en/statistics/tk/gaiyo/2027/tka2703.pdf",
    )
    monkeypatch.setattr(jp_discovery, "discover", lambda client, family: [edition])
    extracted = (
        "Tankan Summary (March 2027) 214th Short-Term Economic Survey of "
        "Enterprises in Japan April 1, 2027 "
        '1. Business Conditions DI ("favorable" - "unfavorable"), % points '
        "Large Enterprises Medium-sized Enterprises Small Enterprises "
        "Changes Changes Changes Changes Changes Changes Manufacturing "
        "10 11 12 2 13 1"
    )

    class Page:
        def extract_text(self):
            return extracted

    class Reader:
        def __init__(self, stream):
            self.pages = [Page()]

    monkeypatch.setattr("pypdf.PdfReader", Reader)

    class Client:
        def get(self, provider, url, params=None):
            body = b"%PDF-synthetic-tankan"
            return Document(body, url, "2027-04-01T00:01:00Z", hashlib.sha256(body).hexdigest())

    with run_at("2027-04-01T12:00:00Z") as pending:
        rows = japan_macro.fetch(Client(), "jp_tankan", "2027-01")

    assert pending == {}
    assert [(row.edition, row.period, row.value) for row in rows] == [
        ("2027-04-01", "2027-Q1", 12.0)
    ]


def _fetch_discovered_fixture(monkeypatch, family, edition, body, period, *, published=None):
    url = "https://issuer.example/2027/" + family
    discovered = jp_discovery.Edition(
        edition, period, "preliminary" if family == "jp_industrial_production" else "final",
        "08:50", url, published_value=published,
    )
    monkeypatch.setattr(jp_discovery, "discover", lambda client, selected: [discovered])

    class Client:
        def get(self, provider, requested, params=None):
            payload = b"<html>discovery evidence</html>" if requested == jp_discovery.METI else body
            return Document(payload, requested, edition + "T00:01:00Z",
                            hashlib.sha256(payload).hexdigest())

    with run_at(edition + "T12:00:00Z") as pending:
        rows = japan_macro.fetch(Client(), family, "2027-01")
    assert pending == {}
    assert rows and {row.edition for row in rows} == {edition}
    assert {row.period for row in rows} == {period}
    return rows


def test_2027_industrial_production_rollover_uses_synthesised_workbook(monkeypatch):
    from openpyxl import load_workbook
    fixture = Path(__file__).parent / "fixtures/japan_tier1/meti_iip_gsm1e.xlsx"
    workbook = load_workbook(fixture)
    sheet = workbook["Production"]
    production = next(row for row in range(1, sheet.max_row + 1)
                      if sheet.cell(row, 1).value == 1000000000)
    prior = sheet.cell(production, sheet.max_column).value
    column = sheet.max_column + 1
    sheet.cell(3, column).value = "p 202703"
    sheet.cell(production, column).value = prior * 1.022
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()

    rows = _fetch_discovered_fixture(
        monkeypatch, "jp_industrial_production", "2027-04-30",
        stream.getvalue(), "2027-03", published="2.2",
    )
    assert [(row.indicator_id, row.value) for row in rows] == [
        ("jp_industrial_production_mm", 2.2)
    ]


def test_2027_unemployment_rollover_uses_synthesised_workbook(monkeypatch):
    from openpyxl import load_workbook
    fixture = Path(__file__).parent / "fixtures/japan_tier1/statjp_cpi_file_000031831358.xlsx"
    workbook = load_workbook(fixture)
    sheet = workbook["季節調整値"]
    row = sheet.max_row + 1
    sheet.cell(row, 1).value = 2027
    sheet.cell(row, 2).value = "3月"
    sheet.cell(row, 20).value = 2.6
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()

    rows = _fetch_discovered_fixture(
        monkeypatch, "jp_unemployment", "2027-04-30", stream.getvalue(), "2027-03"
    )
    assert [(row.indicator_id, row.value) for row in rows] == [
        ("jp_unemployment_rate", 2.6)
    ]


@pytest.mark.parametrize("numeric_period,period,edition", [
    (2027.09, "2027-09", "2027-11-10"),
    (2027.10, "2027-10", "2027-12-10"),
    (2027.11, "2027-11", "2028-01-10"),
])
def test_2027_household_spending_rollover_uses_synthesised_workbook(
        monkeypatch, numeric_period, period, edition):
    from openpyxl import Workbook
    workbook = Workbook()
    sheet = workbook.active
    sheet["A1"] = "Two-or-more-person Households"
    sheet["A2"] = "Consumption expenditures"
    sheet["I5"] = numeric_period
    sheet["I91"] = 1.7
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()

    rows = _fetch_discovered_fixture(
        monkeypatch, "jp_household_spending", edition, stream.getvalue(), period
    )
    assert [(row.indicator_id, row.value) for row in rows] == [
        ("jp_household_spending_yy", 1.7)
    ]


def test_2027_oecd_cli_rollover_uses_synthesised_sdmx(monkeypatch):
    fixture = Path(__file__).parent / "fixtures/japan_tier1/oecd_cli_jpn.csv"
    body = fixture.read_bytes().replace(b"2026-10", b"2027-03")
    rows = _fetch_discovered_fixture(
        monkeypatch, "jp_oecd_cli", "2027-04-09", body, "2027-03"
    )
    assert [(row.indicator_id, row.value) for row in rows] == [
        ("JP_OECD_CLI", 100.2095)
    ]
