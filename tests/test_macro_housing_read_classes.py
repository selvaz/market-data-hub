"""Private ownership follows resolutions through every public macro read path."""
from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import json

import pytest

from market_data_hub.macro_releases import ReadAPI, open_reader, open_writer, resolve
from market_data_hub.macro_releases.__main__ import main
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.catalogue import INDICATORS
from market_data_hub.macro_releases.housing import PARSERS, observation
from market_data_hub.macro_releases.http import FetchError
from market_data_hub.macro_releases.models import ConsensusReading
from market_data_hub.macro_releases.read_api import latest_period
from market_data_hub.macro_releases.resolve import Snapshot, resolve_evidence
from test_macro_m9f import document, GOLDEN, offline as offline
from test_macro_read_api import evidence

AS_OF = "2026-11-01T00:00:00Z"
WINDOW = ("2026-08-01", "2026-10-24")
FAMILIES = [("us_existing_home_sales", "private"), ("us_pending_home_sales", "private"),
            ("us_case_shiller", "private"), ("us_construction_spending", "official"), ("us_fhfa_hpi", "official"),
            ("us_fannie_pali", "official")]


def publications(family):
    rows = []
    for name in dict.fromkeys(c["fixture"] for c in GOLDEN if c["family"] == family):
        for row in PARSERS[family](document(name), family):
            # Counterfactual timely captures exercise nested previous fields;
            # the saved issuer bodies, values and publication times stay intact.
            seen = (datetime.fromisoformat(row.published_at) + timedelta(minutes=1)).isoformat()
            rows.append(replace(row, document=replace(row.document, retrieved_at=seen)))
    return rows


def forecasts(store, row):
    info = INDICATORS[row.indicator_id]
    captured = (datetime.fromisoformat(row.published_at) - timedelta(hours=1)).isoformat()
    for provider in ("FF", "synthetic-survey"):
        store.put(ConsensusReading(provider=provider, survey_identity="source-class regression",
            indicator_id=row.indicator_id, release_event_id=f"{row.source}:{row.family}:{row.stage}:{row.edition}",
            measure=info.measure, reference_period=row.period, estimate_stage=row.stage,
            value=row.value - 0.1, unit=info.unit, scale=info.scale, published_at=captured,
            captured_at=captured, release_cutoff_at=row.published_at, cutoff_precision="exact",
            cutoff_basis="actual", content_hash=hashlib.sha256((provider + row.indicator_id).encode()).hexdigest()))


def assert_nonofficial(payload):
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in {"status", "resolution_status", "source_class"}:
                assert value != "official", payload
            assert_nonofficial(value)
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            assert_nonofficial(value)


@pytest.mark.parametrize("family,kind", FAMILIES)
def test_every_public_read_function_preserves_class_and_official_controls(tmp_path, family, kind):
    path = tmp_path / "classes.sqlite"
    rows = publications(family)
    row = max(rows, key=lambda r: (r.edition, r.indicator_id))
    with open_writer(path) as store:
        persist(store, rows)
        forecasts(store, row)
    with open_reader(path) as store:
        resolution = resolve(store, row.indicator_id, row.period, as_known_at=AS_OF)
        snapshot = Snapshot.load(store, AS_OF)
        direct = resolve_evidence(snapshot, row.indicator_id, row.period, row.stage)
        assert direct == resolution
        assert resolution.value == row.value
        assert resolution.status == resolution.source_class == kind
        assert resolution.issuer_id == row.source
        assert resolution.source["source_class"] == kind
        assert all(e.official == (kind == "official") and e.verified_issuer for e in snapshot.evidence)

        payloads = [resolution.to_dict(), latest_period(snapshot, row.indicator_id, row.period)]
        # M4 view names are historical; every returned row carries the class.
        for result in (store.first_release(row.indicator_id), store.latest_official(row.indicator_id),
                       store.as_known_at(AS_OF, row.indicator_id)):
            assert result
            assert all(r["source_class"] == r["resolution_status"] == kind and r["issuer_id"] == row.source for r in result)
            payloads.append(result)
        for api in (store, ReadAPI(store)):
            cards = api.release_cards(*WINDOW, indicators=[row.indicator_id], as_known_at=AS_OF)
            current = next(c for c in cards["cards"] if c["resolution"]["edition"] == row.edition)
            assert current["source_class"] == current["resolution"]["source_class"] == kind
            assert current["consensus"] and current["surprise"]
            assert {c["resolution_status"] for c in current["consensus"]} == {kind}
            assert {c["resolution_status"] for c in current["surprise"]} == {kind}
            if family == "us_case_shiller":
                assert current["previous"] and current["previous_revised"]
            counts = api.coverage(*WINDOW, as_known_at=AS_OF)["families"][family]
            assert counts[kind] == counts["captured"] > 0
            if kind == "private":
                assert counts["official"] == 0
            payloads.extend([cards, counts,
                api.explain_empty(row.indicator_id, row.period, as_known_at=AS_OF),
                api.explain_empty(row.indicator_id, WINDOW, as_known_at=AS_OF)])
        if kind == "private":
            for payload in payloads:
                assert_nonofficial(payload)


@pytest.mark.parametrize("family,kind", FAMILIES[:4])
@pytest.mark.parametrize("command", ["cards", "coverage"])
def test_public_cli_carries_private_classes(tmp_path, capsys, family, kind, command):
    path = tmp_path / "cli.sqlite"
    with open_writer(path) as store:
        persist(store, publications(family))
    assert main([command, "--db", str(path), "--since", WINDOW[0], "--until", WINDOW[1], "--as-known-at", AS_OF]) == 0
    result = json.loads(capsys.readouterr().out)
    if command == "cards":
        assert {c["resolution"]["status"] for c in result["cards"]} == {kind}
        if kind == "private":
            assert_nonofficial(result)
    else:
        counts = result["families"][family]
        assert counts[kind] == counts["captured"] > 0
        if kind == "private":
            assert counts["official"] == 0


@pytest.mark.parametrize("family", ["us_pending_home_sales", "us_case_shiller", "us_fannie_pali"])
def test_matching_fred_history_remains_redistributor_on_every_read_path(tmp_path, capsys, family):
    path = tmp_path / "mirror.sqlite"
    row = publications(family)[-1]
    with open_writer(path) as store:
        persist(store, [replace(row, source="FRED")])
    with open_reader(path) as store:
        result = resolve(store, row.indicator_id, row.period, as_known_at=AS_OF)
        assert (result.status, result.source_class) == ("provisional", "redistributor")
        assert result.issuer_id == row.source
        snapshot = Snapshot.load(store, AS_OF)
        assert resolve_evidence(snapshot, row.indicator_id, row.period, row.stage) == result
        assert latest_period(snapshot, row.indicator_id, row.period)["source_class"] == "redistributor"
        assert store.first_release() == store.latest_official() == store.as_known_at(AS_OF) == []
        for api in (store, ReadAPI(store)):
            cards = api.release_cards(*WINDOW, as_known_at=AS_OF)
            assert cards["cards"]
            assert {c["source_class"] for c in cards["cards"]} == {"redistributor"}
            assert_nonofficial(cards)
            counts = api.coverage(*WINDOW, as_known_at=AS_OF)["families"][family]
            assert counts["official"] == counts["private"] == 0
            assert counts["provisional"] == counts["captured"] == 1
            assert_nonofficial(api.explain_empty(row.indicator_id, row.period, as_known_at=AS_OF))
    for command in ("cards", "coverage"):
        assert main([command, "--db", str(path), "--since", WINDOW[0], "--until", WINDOW[1], "--as-known-at", AS_OF]) == 0
        payload = json.loads(capsys.readouterr().out)
        if command == "cards":
            assert_nonofficial(payload)
        else:
            assert payload["families"][family]["official"] == 0


def test_verified_fred_with_wrong_stored_issuer_role_cannot_be_official(tmp_path):
    with open_writer(tmp_path / "wrong-role.sqlite") as store:
        evidence(store, indicator="us_pending_home_sales_mm", source="FRED", role="issuer", verified=True)
        result = resolve(store, "us_pending_home_sales_mm", "2026-09", as_known_at=AS_OF)
        assert (result.status, result.source_class) == ("provisional", "redistributor")
        assert not any(e.official for e in Snapshot.load(store, AS_OF).evidence)
        for rows in (store.first_release(), store.latest_official(), store.as_known_at(AS_OF)):
            assert rows
            assert {r["source_class"] for r in rows} == {"redistributor"}
            assert_nonofficial(rows)


def test_private_issuer_still_wins_matching_mirror_and_disputes_equal_authority(tmp_path):
    with open_writer(tmp_path / "authority.sqlite") as store:
        evidence(store, indicator="us_pending_home_sales_mm", source="NAR", value=-7.3)
        evidence(store, indicator="us_pending_home_sales_mm", source="FRED", role="mirror", value=-7.3)
        result = resolve(store, "us_pending_home_sales_mm", "2026-09", as_known_at=AS_OF)
        assert (result.status, result.source_class, result.source["source_id"], result.value) == ("private", "private", "NAR", -7.3)
        evidence(store, indicator="us_pending_home_sales_mm", source="NAR-verified-copy", role="issuer", value=-7.4)
        result = resolve(store, "us_pending_home_sales_mm", "2026-09", as_known_at=AS_OF)
        assert (result.status, result.source_class) == ("disputed", "private")
        assert result.discrepancies


@pytest.mark.parametrize("family", ["us_construction_spending", "us_fhfa_hpi", "us_existing_home_sales"])
def test_prior_period_guard_stays_fail_closed(family):
    row = publications(family)[-1]
    with pytest.raises(FetchError, match="headline reference period needs review"):
        observation(row.document, family, row.edition, row.indicator_id, row.printed, period="2025-01")
