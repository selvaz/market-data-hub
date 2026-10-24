"""Offline certification of unchanged, hashed central-bank statements."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest
import requests

from market_data_hub.macro_releases import open_reader, open_writer
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.http import Document, FetchError
from market_data_hub.macro_releases.policy import due_editions
from market_data_hub.macro_releases.schedule import RUN_AT, RUN_DATE

ROOT = Path(__file__).parent / "fixtures/macro_releases/m9c"
GOLDEN = json.loads((ROOT / "golden-set.json").read_text())["cases"]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network forbidden in M9c tests")
    monkeypatch.setattr(requests.Session, "request", forbidden)
    date_token = RUN_DATE.set("2026-10-24")
    time_token = RUN_AT.set(datetime(2026, 10, 24, 18, tzinfo=timezone.utc))
    yield
    RUN_DATE.reset(date_token)
    RUN_AT.reset(time_token)


def document(name):
    body = (ROOT / name).read_bytes()
    meta = json.loads((ROOT / (name + ".meta.json")).read_text())
    assert hashlib.sha256(body).hexdigest() == meta["sha256"]
    return Document(body, meta["url"], meta["retrieved_at"], meta["sha256"])


def parse(case):
    from market_data_hub.macro_releases import fomc
    return {"fomc": fomc}[case["family"]].parse(document(case["fixture"]))[0]


@pytest.mark.parametrize("case", GOLDEN, ids=lambda c: c["family"] + ":" + c["edition"])
def test_golden_official_card(case, tmp_path):
    obs = parse(case)
    assert obs.edition == obs.period == case["edition"]
    assert obs.document.url == case["source_url"]
    assert obs.policy_decision["rates"] == case["rates"]
    assert obs.policy_decision["vote_split"] == case["vote_split"]
    assert obs.policy_decision["publication_precision"] == case["precision"]
    db = tmp_path / "golden.sqlite"
    with open_writer(db) as store:
        persist(store, [obs])
    with open_reader(db) as store:
        cards = store.release_cards("2026-01-01", "2026-12-31")["cards"]
        assert len(cards) == 1
        assert cards[0]["resolution"]["status"] == "official"
        assert cards[0]["policy_decision"] == obs.policy_decision
        assert cards[0]["resolution"]["policy_decision"] == obs.policy_decision


def test_fomc_issuer_unknown_edition_and_format_fail_closed():
    from market_data_hub.macro_releases.fomc import parse
    doc = document("fed-sept.html")
    for before, after in ((b"September 16, 2026", b"September 17, 2026"),
                          (b"target range for the federal funds rate", b"unexpected rate convention"),
                          (b"2:00 p.m.", b"1:00 p.m.")):
        assert before in doc.body
        with pytest.raises(FetchError, match="needs review"):
            parse(replace(doc, body=doc.body.replace(before, after)))
    with pytest.raises(FetchError, match="needs review"):
        parse(replace(doc, url="https://news.example/statement"))


def test_fomc_due_at_exact_issuer_instant():
    date = RUN_DATE.set("2026-09-16")
    before = RUN_AT.set(datetime(2026, 9, 16, 17, 59, 59, tzinfo=timezone.utc))
    try:
        assert "2026-09-16" not in due_editions("fomc", "2026-09")
        RUN_AT.set(datetime(2026, 9, 16, 18, tzinfo=timezone.utc))
        assert "2026-09-16" in due_editions("fomc", "2026-09")
    finally:
        RUN_DATE.reset(date)
        RUN_AT.reset(before)
