"""Systemic persisted-store regression for Japan calendar rollovers."""
from datetime import datetime
import hashlib

from market_data_hub.macro_releases import asia, jp_discovery, open_writer
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.catalogue import ASIA_FAMILIES
from market_data_hub.macro_releases.http import Document, FetchError
from market_data_hub.macro_releases.run_daily import DEFAULT_LOOKBACK_MONTHS, fetch_windows
from market_data_hub.macro_releases.schedule import PENDING_EDITIONS, RUN_INSTANT


JAPAN_FAMILIES = tuple(sorted(family for family in ASIA_FAMILIES if family.startswith("jp_")))
QUARTERLY = {"jp_gdp", "jp_tankan"}
INDICATOR = {family: indicators[0].id for family, indicators in ASIA_FAMILIES.items()
             if family.startswith("jp_")}


def month(year, number):
    absolute = year * 12 + number - 1
    return f"{absolute // 12:04d}-{absolute % 12 + 1:02d}"


def test_all_japan_families_survive_thirteen_live_months(monkeypatch, tmp_path):
    """Advance every issuer, retain the store, and cross a calendar year."""
    assert DEFAULT_LOOKBACK_MONTHS == 24
    since, _ = fetch_windows(JAPAN_FAMILIES, DEFAULT_LOOKBACK_MONTHS,
                             datetime.fromisoformat("2027-12-20T12:00:00+00:00"))
    assert since == "2025-12"

    # The simulation owns its future calendar.  Existing reviewed anchors would
    # only add unrelated fixture traffic to this rollover contract.
    for family in JAPAN_FAMILIES:
        monkeypatch.setitem(asia.EDITIONS, family, {
            "2025-01-01": ("2024-12", "final", "08:30")
        })
    # Trade has edition-specific archives in production; include it in the same
    # live continuation harness so the systemic contract covers every JP family.
    monkeypatch.setattr(jp_discovery, "FAMILIES", frozenset(JAPAN_FAMILIES))

    advertised = {}
    documents = {}
    delayed = ("jp_industrial_production", "2027-05-15")

    def discover(unused_client, family):
        return advertised[family]

    monkeypatch.setattr(jp_discovery, "discover", discover)

    class Client:
        def get(self, provider, url, params=None):
            period = documents.get(url, "discovery")
            body = period.encode()
            return Document(body, url, current + "-20T12:00:00Z", hashlib.sha256(body).hexdigest())

    def parser(document, family, since, *, edition, **unused):
        period = document.body.decode()
        expected = asia.reviewed(family, edition)[0]
        if period == "withheld":
            raise FetchError("moving publication not_yet_published")
        assert period == expected
        row = asia.observation(document, family, edition, INDICATOR[family], "1.0", "simulation")
        return [row]

    archives = {family: {} for family in JAPAN_FAMILIES}
    expected_editions = {family: [] for family in JAPAN_FAMILIES}
    path = tmp_path / "japan-rollover.sqlite"

    for offset in range(13):
        current = month(2026, 12 + offset)
        edition = current + "-15"
        for family in JAPAN_FAMILIES:
            due_now = family not in QUARTERLY or int(current[-2:]) in {3, 6, 9, 12}
            items = []
            if due_now:
                period = (current[:4] + "-Q" + str((int(current[-2:]) - 1) // 3 + 1)
                          if family in QUARTERLY else current)
                url = "https://issuer.example/latest/" + family
                item = jp_discovery.Edition(edition, period, "final", "08:30", url)
                items.append(item)
                documents[url] = "withheld" if (family, edition) == delayed else period
                archives[family].setdefault(edition, url)
            # The withheld edition is finally published beside next month's
            # release at an edition-specific URL.
            if family == delayed[0] and current == "2027-06":
                old_url = "https://issuer.example/archive/" + delayed[1]
                old = jp_discovery.Edition(delayed[1], "2027-05", "final", "08:30", old_url)
                items.insert(0, old)
                documents[old_url] = old.period
                archives[family][old.edition_date] = old_url
            advertised[family] = items

        token = RUN_INSTANT.set(current + "-20T12:00:00Z")
        pending = {}
        pending_token = PENDING_EDITIONS.set(pending)
        try:
            rows = []
            for family in JAPAN_FAMILIES:
                family_rows = asia.fetch_archives(
                    Client(), family, since, "issuer", archives[family], parser
                )
                rows.extend(family_rows)
                expected = [item.edition_date for item in advertised[family]
                            if current != "2027-05" or (family, item.edition_date) != delayed]
                assert [row.edition for row in family_rows] == expected
                expected_editions[family].extend(expected)
        finally:
            PENDING_EDITIONS.reset(pending_token)
            RUN_INSTANT.reset(token)

        if current == "2027-05":
            assert pending[delayed] == "overdue"
        else:
            assert all(reason != "needs_review" for reason in pending.values())

        with open_writer(path) as store:
            before = store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0]
            persist(store, rows)
            after = store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0]
            assert after - before == len(rows)

    with open_writer(path) as store:
        stored = store.conn.execute(
            "SELECT e.family, e.edition FROM value_vintages v "
            "JOIN release_events e ON e.id=v.release_event_id ORDER BY e.family, e.edition"
        ).fetchall()
    actual = {family: [] for family in JAPAN_FAMILIES}
    for family, edition in stored:
        actual[family].append(edition)
    assert actual == expected_editions


def test_boj_thirteen_month_rollover_uses_consecutive_statements(tmp_path):
    from market_data_hub.macro_releases.boj_policy import RSS, fetch

    editions = [month(2027, 1 + offset) + "-15" for offset in range(13)]
    baseline = "2026-12-18"
    rates = {stamp: 0.25 + index * 0.25
             for index, stamp in enumerate([baseline] + editions)}
    visible = []

    def statement(stamp):
        date = datetime.strptime(stamp, "%Y-%m-%d").strftime("%B %d, %Y")
        return (f"<html>{date} Bank of Japan Statement on Monetary Policy "
                "Policy Board of the Bank of Japan decided, by a unanimous vote, "
                "to set the following guideline The Bank will encourage the "
                "uncollateralized overnight call rate to remain at around "
                f"{rates[stamp]} percent</html>").encode()

    class Client:
        def get(self, provider, url, params=None):
            if url == RSS:
                body = b"<rss><channel></channel></rss>"
            elif url.endswith("/index.htm"):
                year = url.split("mpr_")[1][:4]
                available = ([baseline] if year == "2026" else
                             [stamp for stamp in visible if stamp.startswith(year)])
                body = b"".join(
                    (f'<a href="/en/mopo/mpmdeci/mpr_{year}/k{stamp[2:4]}{stamp[5:7]}'
                     f'{stamp[8:10]}a.htm">statement</a>').encode()
                    for stamp in available
                )
            else:
                digits = url.rsplit("/k", 1)[1][:6]
                stamp = "20" + digits[:2] + "-" + digits[2:4] + "-" + digits[4:]
                body = statement(stamp)
            return Document(body, url, visible[-1] + "T12:00:00Z",
                            hashlib.sha256(body).hexdigest())

    path = tmp_path / "boj-rollover.sqlite"
    for stamp in editions:
        visible.append(stamp)
        token = RUN_INSTANT.set(stamp + "T12:00:00Z")
        try:
            rows = fetch(Client(), "boj_policy", "2025-01")
        finally:
            RUN_INSTANT.reset(token)
        assert rows[-1].edition == stamp
        assert all(row.policy_decision["rates"]["overnight_call_rate"]["change_bp"] == 25.0
                   for row in rows)
        with open_writer(path) as store:
            before = store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0]
            persist(store, rows)
            after = store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0]
            assert after - before == 1

    with open_writer(path) as store:
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 13
