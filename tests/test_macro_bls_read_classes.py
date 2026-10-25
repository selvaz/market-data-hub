"""ALFRED remains a redistributor through every public BLS read surface."""
from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import json

import pytest

from market_data_hub.macro_releases import ReadAPI, open_reader, open_writer, resolve
from market_data_hub.macro_releases.__main__ import main
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.bls import shift_month
from market_data_hub.macro_releases.catalogue import INDICATORS
from market_data_hub.macro_releases.discovery import fetch_daily
from market_data_hub.macro_releases.models import ConsensusReading
from market_data_hub.macro_releases.read_api import latest_period
from market_data_hub.macro_releases.resolve import Snapshot, resolve_evidence
from test_macro_bls_fallback import FAMILIES, Outage, offline as offline
from test_macro_housing_read_classes import assert_nonofficial
from test_macro_read_api import evidence

AS_OF = "2026-10-26T00:00:00Z"
WINDOW = ("2026-08-01", "2026-10-25")


@pytest.mark.parametrize("family", FAMILIES)
def test_all_reads_keep_redistributor_class(tmp_path, capsys, family):
    path = tmp_path / "reads.sqlite"
    rows = fetch_daily(Outage(), family, "2026-06").observations
    row = max(rows, key=lambda r: (r.edition, r.indicator_id))
    info = INDICATORS[row.indicator_id]
    with open_writer(path) as store:
        persist(store, rows)
        # Explicit counterfactual earlier captures exercise nested previous
        # fields; no claim that these synthetic values are real publications.
        earlier = replace(row, period=shift_month(row.period, -1), edition="2026-08-01",
                          published_at="2026-08-01T12:30:00Z", provider_vintage="2026-08-01",
                          document=replace(row.document, retrieved_at="2026-08-01T13:00:00Z"))
        revised = replace(earlier, edition=row.edition, published_at=row.published_at,
                          value=0.3, revision="provider_history", provider_vintage=row.edition,
                          document=row.document)
        persist(store, [earlier, revised])
        captured = (datetime.fromisoformat(row.published_at) - timedelta(hours=1)).isoformat()
        for provider in ("FF", "synthetic-survey"):
            store.put(ConsensusReading(provider=provider, survey_identity="BLS source-class regression",
                indicator_id=row.indicator_id, release_event_id=f"BLS:{family}:regular:{row.edition}",
                measure=info.measure, reference_period=row.period, estimate_stage="regular", value=0.1,
                unit=info.unit, scale=info.scale, published_at=captured, captured_at=captured,
                release_cutoff_at=row.published_at, cutoff_precision="exact", cutoff_basis="actual",
                content_hash=hashlib.sha256((family + provider).encode()).hexdigest()))
    with open_reader(path) as store:
        snapshot = Snapshot.load(store, AS_OF)
        result = resolve(store, row.indicator_id, row.period, as_known_at=AS_OF)
        assert resolve_evidence(snapshot, row.indicator_id, row.period, "regular") == result
        assert (result.status, result.source_class, result.issuer_id) == ("provisional", "redistributor", "BLS")
        assert latest_period(snapshot, row.indicator_id, row.period)["source_class"] == "redistributor"
        assert result.source["source_id"] == "ALFRED"
        assert all(e.source_class == "redistributor" and not e.verified_issuer for e in snapshot.evidence)
        # These M4 views intentionally select only verified issuer rows.
        assert store.first_release() == store.latest_official() == store.as_known_at(AS_OF) == []
        for api in (store, ReadAPI(store)):
            cards = api.release_cards(*WINDOW, indicators=[row.indicator_id], as_known_at=AS_OF)
            card = next(c for c in cards["cards"] if c["resolution"]["edition"] == row.edition)
            assert card["source_class"] == card["resolution"]["source_class"] == "redistributor"
            for field in ("previous", "previous_revised"):
                assert card[field]["source_class"] == "redistributor"
            assert card["consensus"] and card["surprise"]
            assert {c["resolution_status"] for c in card["consensus"] + card["surprise"]} == {"provisional"}
            assert_nonofficial(cards)
            counts = api.coverage(*WINDOW, as_known_at=AS_OF)["families"][family]
            assert counts["official"] == counts["private"] == 0
            assert counts["provisional"] == counts["captured"] > 0
            assert_nonofficial(api.explain_empty(row.indicator_id, row.period, as_known_at=AS_OF))
            assert_nonofficial(api.explain_empty(row.indicator_id, WINDOW, as_known_at=AS_OF))
    for command in ("cards", "coverage"):
        assert main([command, "--db", str(path), "--since", WINDOW[0], "--until", WINDOW[1], "--as-known-at", AS_OF]) == 0
        payload = json.loads(capsys.readouterr().out)
        if command == "cards":
            assert_nonofficial(payload)
        else:
            assert payload["families"][family]["official"] == 0


@pytest.mark.parametrize("source", ["FRED", "ALFRED"])
def test_wrong_stored_issuer_role_never_promotes_a_redistributor(tmp_path, source):
    with open_writer(tmp_path / "wrong-role.sqlite") as store:
        evidence(store, indicator="us_unemployment_rate", source=source, role="issuer", verified=True)
        result = resolve(store, "us_unemployment_rate", "2026-09", as_known_at=AS_OF)
        assert (result.status, result.source_class, result.issuer_id) == ("provisional", "redistributor", "BLS")
        for rows in (store.first_release(), store.latest_official(), store.as_known_at(AS_OF)):
            assert rows and {r["source_class"] for r in rows} == {"redistributor"}
            assert_nonofficial(rows)
