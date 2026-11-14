"""A rolling RSS feed enriches BOJ clocks without revising decision PDFs."""
from dataclasses import replace
from datetime import datetime
import hashlib
import sqlite3

import pytest
import requests

from market_data_hub.macro_releases import open_writer, run_daily
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.boj_policy import HOST, RSS, fetch, parse
from market_data_hub.macro_releases.schedule import PENDING_DETAILS, PENDING_EDITIONS, RUN_INSTANT
from market_data_hub.macro_releases.http import Document
from market_data_hub.macro_releases.models import utc
from test_macro_m9c import GOLDEN, document, parse as parse_case


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("BOJ timestamp tests must not use the network")
    monkeypatch.setattr(requests.Session, "request", forbidden)


def observations():
    july = document("boj-july.pdf")
    june = document("boj-june.pdf")
    body = (b'<rss><channel><item><link>https://www.boj.or.jp/en/mopo/mpmdeci/mpr_2026/k260731a.pdf</link>'
            b'<pubDate>Fri, 31 Jul 2026 12:09:30 +0900</pubDate></item></channel></rss>')
    feed = Document(body, RSS, "2026-10-25T18:00:01Z", hashlib.sha256(body).hexdigest())
    return (parse(july, june)[0],
            parse(replace(july, retrieved_at="2026-10-25T18:00:00Z"), june, feed=feed)[0])


def card(store, cutoff="2026-10-28T00:00:00Z"):
    result, = store.release_cards("2026-07-30", "2026-07-31",
        indicators=["boj_policy_rate_decision"], as_known_at=cutoff)["cards"]
    return result


def assert_exact(result):
    assert result["publication_precision"] == "exact"
    assert result["released_at"] == utc("2026-07-31T03:09:30Z")
    assert result["policy_decision"]["published_at"] == "2026-07-31T03:09:30+00:00"
    assert result["policy_decision"]["time_missing_reason"] is None
    assert any(s["source_url"] == RSS for s in result["policy_decision"]["supporting_sources"])


def test_date_only_pdf_gains_exact_rss_time_without_rewriting_history(tmp_path):
    original, exact = observations()
    with open_writer(tmp_path / "upgrade.sqlite") as store:
        with store.batch():
            first, = persist(store, [original])
        before = dict(store.conn.execute("SELECT * FROM value_vintages").fetchone())
        with store.batch():
            upgraded, = persist(store, [exact])
            # Timestamp enrichment cannot abort the rest of the family batch.
            persist(store, parse(document("boj-sept.pdf"), document("boj-july.pdf")))
        assert upgraded == first
        assert dict(store.conn.execute("SELECT * FROM value_vintages WHERE id=?", (first,)).fetchone()) == before
        saved = store.conn.execute("SELECT * FROM boj_publication_times").fetchone()
        assert saved["recorded_at"] == utc("2026-10-25T18:00:01Z")
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 2
        historical = card(store, "2026-10-24T23:59:59Z")
        assert historical["publication_precision"] == "date_only"
        assert historical["policy_decision"]["published_at"] is None
        # Do not backdate the RSS evidence to the earlier PDF retrieval.
        assert card(store, "2026-10-25T18:00:00Z")["publication_precision"] == "date_only"
        assert_exact(card(store))
        persist(store, [exact, original, exact])
        assert store.conn.execute("SELECT COUNT(*) FROM boj_publication_times").fetchone()[0] == 1
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            store.conn.execute("UPDATE boj_publication_times SET recorded_at=recorded_at")


def test_rss_rollover_keeps_an_already_exact_pdf_and_allows_later_decisions(tmp_path):
    original, exact = observations()
    rollover = parse(replace(original.document, retrieved_at="2026-10-27T00:00:00Z"),
        document("boj-june.pdf"), feed=document("boj-rss.xml"))[0]
    assert rollover.precision == "date_only"
    with open_writer(tmp_path / "rollover.sqlite") as store:
        first, = persist(store, [exact])
        saved = dict(store.conn.execute("SELECT * FROM value_vintages").fetchone())
        with store.batch():
            reread, = persist(store, [rollover])
            persist(store, parse(document("boj-sept.pdf"), document("boj-july.pdf")))
        assert reread == first
        assert dict(store.conn.execute("SELECT * FROM value_vintages WHERE id=?", (first,)).fetchone()) == saved
        assert_exact(card(store))
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 2


@pytest.mark.parametrize("changed_fact", ["value", "vote"])
def test_same_boj_pdf_still_rejects_changed_decision_facts(tmp_path, changed_fact):
    original, exact = observations()
    changed = (replace(exact, value=exact.value + 0.25) if changed_fact == "value" else
        replace(exact, policy_decision=dict(exact.policy_decision, vote_split={"for": 9, "against": 0})))
    with open_writer(tmp_path / "conflict.sqlite") as store:
        persist(store, [original])
        with pytest.raises(ValueError, match="same publication hash has conflicting value semantics"):
            with store.batch():
                persist(store, [changed])
        assert store.conn.execute("SELECT COUNT(*) FROM boj_publication_times").fetchone()[0] == 0
        assert card(store)["resolution"]["value"] == original.value


@pytest.mark.parametrize("family", ["fomc", "ecb_policy", "boe_policy", "boc_policy", "pboc_lpr"])
def test_other_families_retain_strict_same_pdf_timestamp_semantics(tmp_path, family):
    original = parse_case(next(c for c in GOLDEN if c["family"] == family))
    changed = replace(original, precision="date_only", policy_decision=dict(original.policy_decision,
        published_at=None, publication_precision="date_only", time_missing_reason="test"))
    with open_writer(tmp_path / "other.sqlite") as store:
        persist(store, [original])
        with pytest.raises(ValueError, match="same publication hash has conflicting value semantics"):
            with store.batch():
                persist(store, [changed])
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 1
        assert store.conn.execute("SELECT COUNT(*) FROM boj_publication_times").fetchone()[0] == 0


def test_contradictory_exact_boj_time_still_conflicts(tmp_path):
    original, exact = observations()
    changed = replace(exact, published_at="2026-07-31T03:10:00Z",
        issuer_local_time="2026-07-31T12:10:00+09:00", policy_decision=dict(exact.policy_decision,
            published_at="2026-07-31T03:10:00Z"))
    with open_writer(tmp_path / "time-conflict.sqlite") as store:
        persist(store, [original, exact])
        with pytest.raises(ValueError, match="conflicting exact time evidence"):
            with store.batch():
                persist(store, [changed])
        assert_exact(card(store))


def test_january_listing_uses_previous_year_statement_only_as_baseline():
    current_listing = b'<a href="/en/mopo/mpmdeci/mpr_2028/k280123a.htm">January</a>'
    previous_listing = b'<a href="/en/mopo/mpmdeci/mpr_2027/k271218a.htm">December</a>'

    def statement_body(stamp, rate):
        return (f'''<html>{stamp} Bank of Japan Statement on Monetary Policy
          Policy Board of the Bank of Japan decided, by a unanimous vote, to set the following guideline
          The Bank will encourage the uncollateralized overnight call rate to remain at around {rate} percent
        </html>''').encode()

    urls = {
        f"{HOST}/en/mopo/mpmdeci/mpr_2028/index.htm": current_listing,
        f"{HOST}/en/mopo/mpmdeci/mpr_2027/index.htm": previous_listing,
        f"{HOST}/en/mopo/mpmdeci/mpr_2028/k280123a.htm": statement_body("January 23, 2028", "0.75"),
        f"{HOST}/en/mopo/mpmdeci/mpr_2027/k271218a.htm": statement_body("December 18, 2027", "0.5"),
        RSS: b"<rss><channel></channel></rss>",
    }

    class JanuaryClient:
        def __init__(self):
            self.requested = []

        def get(self, provider, url, params=None):
            self.requested.append(url)
            body = urls[url]
            return Document(body, url, "2028-01-23T08:00:00Z", hashlib.sha256(body).hexdigest())

    client = JanuaryClient()
    token = RUN_INSTANT.set("2028-01-23T08:00:00Z")
    try:
        # The daily runner uses a 24-month lookback, which includes December.
        rows = fetch(client, "boj_policy", "2025-01")
    finally:
        RUN_INSTANT.reset(token)

    assert len(rows) == 1
    assert (rows[0].edition, rows[0].value) == ("2028-01-23", 0.75)
    assert f"{HOST}/en/mopo/mpmdeci/mpr_2027/k271218a.htm" in client.requested
    assert rows[0].policy_decision["supporting_sources"][0]["decision_date"] == "2027-12-18"


def test_narrow_window_still_uses_current_year_statement_as_baseline():
    listing = (
        b'<a href="/en/mopo/mpmdeci/mpr_2027/k270123a.htm">January</a>'
        b'<a href="/en/mopo/mpmdeci/mpr_2027/k270319a.htm">March</a>'
    )

    def statement_body(stamp, rate):
        return (f'''<html>{stamp} Bank of Japan Statement on Monetary Policy
          Policy Board of the Bank of Japan decided, by a unanimous vote, to set the following guideline
          The Bank will encourage the uncollateralized overnight call rate to remain at around {rate} percent
        </html>''').encode()

    urls = {
        f"{HOST}/en/mopo/mpmdeci/mpr_2027/index.htm": listing,
        f"{HOST}/en/mopo/mpmdeci/mpr_2026/index.htm": (
            b'<a href="/en/mopo/mpmdeci/mpr_2026/k261218a.htm">December</a>'
        ),
        f"{HOST}/en/mopo/mpmdeci/mpr_2027/k270123a.htm": statement_body("January 23, 2027", "0.5"),
        f"{HOST}/en/mopo/mpmdeci/mpr_2027/k270319a.htm": statement_body("March 19, 2027", "0.75"),
        RSS: b"<rss><channel></channel></rss>",
    }

    class Client:
        def get(self, provider, url, params=None):
            body = urls[url]
            return Document(body, url, "2027-03-19T08:00:00Z", hashlib.sha256(body).hexdigest())

    token = RUN_INSTANT.set("2027-03-19T08:00:00Z")
    try:
        rows = fetch(Client(), "boj_policy", "2027-03")
    finally:
        RUN_INSTANT.reset(token)

    assert [(row.edition, row.value) for row in rows] == [("2027-03-19", 0.75)]
    decision = rows[0].policy_decision
    assert decision["rates"]["overnight_call_rate"]["change_bp"] == 25.0
    assert decision["supporting_sources"][0]["decision_date"] == "2027-01-23"


def test_due_december_decision_missing_from_listing_is_reported_pending():
    listing = b'<a href="/en/mopo/mpmdeci/mpr_2026/k261030a.pdf">October</a>'

    class Client:
        def get(self, provider, url, params=None):
            body = b"<rss><channel></channel></rss>" if url == RSS else listing
            return Document(body, url, "2026-12-19T00:00:00Z",
                            hashlib.sha256(body).hexdigest())

    run_token = RUN_INSTANT.set("2026-12-19T00:00:00Z")
    pending_token = PENDING_EDITIONS.set({})
    details_token = PENDING_DETAILS.set({})
    try:
        assert fetch(Client(), "boj_policy", "2026-12") == []
        assert PENDING_EDITIONS.get()["boj_policy", "2026-12-18"] == "needs_review"
        assert PENDING_DETAILS.get()["boj_policy", "2026-12-18"]["category"] == "not_yet_listed"
    finally:
        PENDING_DETAILS.reset(details_token)
        PENDING_EDITIONS.reset(pending_token)
        RUN_INSTANT.reset(run_token)


def test_january_collection_does_not_report_collected_prior_year_as_unlisted(tmp_path):
    decisions = [
        "2026-01-23", "2026-03-19", "2026-04-28", "2026-06-16",
        "2026-07-31", "2026-09-18", "2026-10-30", "2026-12-18",
    ]
    rates = {"2025-12-19": 0.5, **{stamp: 0.5 for stamp in decisions},
             "2027-01-22": 0.75}

    def statement_body(stamp):
        printed = datetime.strptime(stamp, "%Y-%m-%d").strftime("%B %d, %Y")
        return (f"<html>{printed} Bank of Japan Statement on Monetary Policy "
                "Policy Board of the Bank of Japan decided, by a unanimous vote, "
                "to set the following guideline The Bank will encourage the "
                "uncollateralized overnight call rate to remain at around "
                f"{rates[stamp]} percent</html>").encode()

    def listing(year, stamps):
        return b"".join(
            (f'<a href="/en/mopo/mpmdeci/mpr_{year}/k{stamp[2:4]}'
             f'{stamp[5:7]}{stamp[8:10]}a.htm">statement</a>').encode()
            for stamp in stamps
        )

    class Client:
        def get(self, provider, url, params=None):
            if url == RSS:
                body = b"<rss><channel></channel></rss>"
            elif url.endswith("mpr_2027/index.htm"):
                body = listing(2027, ["2027-01-22"])
            elif url.endswith("mpr_2026/index.htm"):
                body = listing(2026, decisions)
            elif url == "https://www.boj.or.jp/en/mopo/mpmdeci/mpr_2025/k251219a.pdf":
                body = statement_body("2025-12-19")
            else:
                digits = url.rsplit("/k", 1)[1][:6]
                body = statement_body(
                    f"20{digits[:2]}-{digits[2:4]}-{digits[4:6]}"
                )
            return Document(body, url, "2027-01-22T08:00:00Z",
                            hashlib.sha256(body).hexdigest())

    path = tmp_path / "boj-2027.sqlite"
    token = RUN_INSTANT.set("2026-12-19T00:00:00Z")
    try:
        old_rows = fetch(Client(), "boj_policy", "2025-01")
    finally:
        RUN_INSTANT.reset(token)
    with open_writer(path) as store:
        persist(store, old_rows)

    family = "boj_policy"
    result = run_daily.collect_family(
        lambda: open_writer(path), Client(), "2027-rollover", family,
        {family: {"fetch_since": "2025-01", "fetch_until": "2027-01-22T08:00:00Z"}},
        dry_run=True,
    )[family]

    assert result["status"] == "ok", result
    assert result["vintages_added"] == 1
    assert result.get("review_details", []) == []
    with open_writer(path) as store:
        editions = [row[0] for row in store.conn.execute(
            "SELECT e.edition FROM value_vintages v "
            "JOIN release_events e ON e.id=v.release_event_id "
            "WHERE e.family='boj_policy' ORDER BY e.edition"
        )]
    assert editions == decisions + ["2027-01-22"]
