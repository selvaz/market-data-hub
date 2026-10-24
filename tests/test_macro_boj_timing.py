"""A rolling RSS feed enriches BOJ clocks without revising decision PDFs."""
from dataclasses import replace
import hashlib
import sqlite3

import pytest
import requests

from market_data_hub.macro_releases import open_writer
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.boj_policy import RSS, parse
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
