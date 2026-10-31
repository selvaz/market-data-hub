"""Offline ECY2 identity, publication and precision-transition regressions."""
from dataclasses import replace
import hashlib
import json

import pytest

from market_data_hub.macro_releases import open_writer
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.discovery import fetch_daily
from market_data_hub.macro_releases.http import FetchError
from market_data_hub.macro_releases import ons
from market_data_hub.macro_releases.run_daily import persist_counts
from market_data_hub.macro_releases.verification import reviewed_anchor
from test_macro_official_adapters import document


def changed(payload):
    doc = document("ons/ecy2-mgdp.json")
    body = json.dumps(payload).encode()
    return replace(doc, body=body, document_hash=hashlib.sha256(body).hexdigest())


def test_index_uk_publication_and_separate_precision_history(tmp_path):
    rows = ons.ONSAdapter.parse_index(document("ons/ecy2-mgdp.json"), "2026-06")
    assert {o.edition for o in rows} == {"2026-10-15"}
    assert {o.published_at for o in rows} == {"2026-10-15T00:00:00Z"}
    assert all(o.precision == "date_only" and reviewed_anchor(o) for o in rows)
    assert rows[-1].period == "2026-08" and rows[-1].value == 103.1
    old = ons.ONSAdapter.parse_levels(document("ons/gdp69-csv.csv"),
                                     document("ons/gdp69.json").json(), "2026-06")
    with open_writer(tmp_path / "macro.sqlite") as store:
        persist(store, old)
        old_values = [dict(r) for r in store.conn.execute("SELECT * FROM value_vintages")]
        persist(store, rows)
        assert [dict(store.conn.execute("SELECT * FROM value_vintages WHERE id=?", (r["id"],)).fetchone())
                for r in old_values] == old_values
        new = store.conn.execute("SELECT * FROM value_vintages WHERE parser_version=?",
                                 (ons.INDEX_PARSER,)).fetchall()
        assert {r["revision_kind"] for r in new} == {"backfill"}
        assert {r["source_series_id"] for r in new}.isdisjoint(r["source_series_id"] for r in old_values)
        assert {r["extraction_method"] for r in new} == {"json"}
        assert not store.first_release("uk_gdp_index")
        payload = document("ons/ecy2-mgdp.json").json()
        payload["description"]["nextRelease"] = "transport metadata changed"
        reread = ons.ONSAdapter.parse_index(changed(payload), "2026-06")
        assert persist_counts(store, reread) == {
            "vintages_added": 0, "readings_added": 0, "readings_skipped": len(rows)}
        result = store.release_cards("2026-10-15", "2026-10-15", indicators="uk_gdp_index",
                                     as_known_at="2026-10-31T23:59:59Z")
        assert len(result["cards"]) == 1
        assert result["cards"][0]["reference_period"] == "2026-08"
        assert result["cards"][0]["revisions"] == []
        assert store.explain_empty("uk_gdp_index", ("2026-10-20", "2026-10-21"),
                                   as_known_at="2026-10-31T23:59:59Z")["reason"] == "no_release_in_window"
        later = replace(rows[0], edition="2026-11-12", published_at="2026-11-12T00:00:00Z", value=102.7)
        persist(store, [later])
        assert store.conn.execute("SELECT revision_kind FROM value_vintages ORDER BY id DESC LIMIT 1").fetchone()[0] == "routine"
        assert not store.first_release("uk_gdp_index")


@pytest.mark.parametrize("mutation", ["months", "dataset", "series", "uri", "title", "value", "date", "update", "timezone", "duplicate"])
def test_index_fails_closed_on_shape_or_identity_change(mutation):
    payload = document("ons/ecy2-mgdp.json").json()
    if mutation == "months":
        payload["months"] = {}
    elif mutation == "dataset":
        payload["months"][0]["sourceDataset"] = "OTHER"
    elif mutation == "series":
        payload["description"]["cdid"] = "OTHER"
    elif mutation == "uri":
        payload["uri"] = "/other"
    elif mutation == "title":
        payload["description"]["title"] = "Other index"
    elif mutation == "value":
        payload["months"][0]["value"] = "102.1234"
    elif mutation == "date":
        payload["months"][0]["date"] = "2026-Q2"
    elif mutation == "timezone":
        payload["months"][0]["updateDate"] = "2026-10-15T00:00:00"
    elif mutation == "duplicate":
        payload["months"].append(payload["months"][0])
    else:
        payload["months"][0].pop("updateDate")
    with pytest.raises(FetchError, match="ECY2"):
        ons.ONSAdapter.parse_index(changed(payload), "2026-08")


def test_both_collectors_retire_beta_fetches():
    calls = []
    class Replay:
        def get(self, provider, url, params=None):
            calls.append(url)
            paths = {ons.INDEX_URL: "ons/ecy2-mgdp.json",
                     ons.BULLETIN_INDEX_URL: "ons/current-bulletins.response.json",
                     **{ons.BULLETIN_BASE + name + "2026": f"ons/{name}-content.html"
                        for name in ("july", "august")}}
            return document(paths[url])
    assert any(o.indicator_id == "uk_gdp_index" for o in ons.ONSAdapter(Replay()).fetch(["uk_gdp"], "2026-06"))
    assert not fetch_daily(Replay(), "uk_gdp", "2026-06").issues
    assert all("api.beta.ons" not in url and "four-decimal" not in url for url in calls)
