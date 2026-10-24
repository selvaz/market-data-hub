"""Approved evidence, consumer semantics, mappings and offline coverage."""
from __future__ import annotations

import csv
import hashlib
import json
import re

import pytest
from pydantic import ValidationError

from market_data_hub.indicator_cards import cards_for, dataset, indicator_card, render_card, unmapped
from market_data_hub.indicator_cards.build import (
    ALIASES_PATH, JSON_PATH, PSV_PATH, TIMING_PATH, build_dataset, build_json, extract_timing, parse_screen,
)
from market_data_hub.indicator_cards.coverage import (
    DRAFT_PSV_PATH, DRAFT_TIMING_PATH, coverage_report, load_draft_cards,
    mapping_ambiguities, tier1_coverage, tier1_ids,
)
from market_data_hub.indicator_cards.models import IndicatorCard, PredictiveScreen
from market_data_hub.indicator_cards.render import MAX_CARD_CHARS, short_pair


def test_generated_json_freshness_and_provenance():
    assert JSON_PATH.read_text(encoding="utf-8") == build_json()
    assert dataset().classification_version == "v2.1"
    assert dataset().source_table_sha256 == hashlib.sha256(PSV_PATH.read_bytes()).hexdigest()
    assert dataset().timing_extract_sha256 == hashlib.sha256(TIMING_PATH.read_bytes()).hexdigest()
    assert dataset().aliases_sha256 == hashlib.sha256(ALIASES_PATH.read_bytes()).hexdigest()
    timing = json.loads(TIMING_PATH.read_text(encoding="utf-8"))
    assert timing["source_table_sha256"] == dataset().source_table_sha256
    assert len(timing["source_guide_sha256"]) == 64


def test_every_approved_row_preserved():
    with PSV_PATH.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="|"))
    assert len(rows) == len(dataset().cards) == 270
    for row in rows:
        card = indicator_card(row["key"])
        assert card is not None
        assert IndicatorCard.model_validate_json(card.model_dump_json()) == card
        for field in ("concept", "country", "theme", "nature", "flag", "rationale", "source"):
            assert getattr(card, field) == row[field]
        assert card.raw_ids == row["ids"]
        assert card.timing.timing_class == row["timing_class"]
        assert card.timing.reference == row["reference"]
        assert card.timing.basis == (None if row["timing_basis"] == "—" else row["timing_basis"])
        assert {s.relationship_id for s in card.timing.predictive_screen} <= set(row["vkeys"].split(";"))


@pytest.mark.parametrize("identity,key", [
    ("ms:PAYEMS", "us_payrolls"), ("es:nonfarm_payrolls", "us_payrolls"),
    ("cal:us_nfp", "us_payrolls"), ("cat:us_payrolls_change", "us_payrolls"),
    ("PAYEMS", "us_payrolls"), ("us_payrolls", "us_payrolls"),
    ("cal:us_gdp_qq_third", "us_gdp_real"), ("cat:ea21_hicp_yy", "ea_hicp"),
    ("cat:us_cpi_core_mm", "us_cpi_core"), ("cal:us_import_prices_mm", "us_import_prices"),
    ("hub:treasury_cash_balance", "us_tga"),
])
def test_id_resolution(identity, key):
    assert indicator_card(identity).key == key
    assert indicator_card(f" {identity} ").key == key


def test_resolves_all_store_ids():
    for card in dataset().cards:
        for store in ("ms", "es", "cal", "cat"):
            for identity in getattr(card.ids, store):
                resolved = indicator_card(f"{store}:{identity}")
                assert resolved is not None
                assert resolved.key == card.key


def test_batch_deduplication_and_nonfatal_missing():
    ids = ["ms:PAYEMS", "missing", "es:nonfarm_payrolls", "cal:us_claims", "missing"]
    assert [c.key for c in cards_for(iter(ids))] == ["us_payrolls", "us_claims_initial"]
    assert unmapped(iter(ids)) == ["missing", "missing"]
    assert indicator_card("missing") is None
    assert indicator_card("wrong:PAYEMS") is None
    assert cards_for([]) == unmapped([]) == []


def test_derived_ingredients_are_not_spread_aliases():
    assert indicator_card("ms:DGS10").key != "us_curve_10y3m"
    assert indicator_card("ms:BAA").key not in {"us_spread_baa10y", "us_spread_baa_aaa"}
    assert indicator_card("us_curve_10y3m").ids.derived_ms == ("DGS10", "DGS3MO")
    assert indicator_card("us_curve_10y3m").ids.ms == ()
    assert indicator_card("ms:ICSA") is None  # explicitly not stored, not an approved id


def test_ambiguity_resolution_prefers_the_ids_own_row_then_country_concept_key():
    report = mapping_ambiguities()
    assert report["pan:bis_policy_rate"]["selected"] == "pan_policy_rate_bis"
    assert report["bis_policy_rate"]["selected"] == "pan_policy_rate_bis"
    assert report["pan:imf_policy_rate"]["selected"] == "pan_policy_rate_imf"
    assert indicator_card("pan:bis_policy_rate").key == "pan_policy_rate_bis"
    for identity, detail in report.items():
        cards = [indicator_card(key) for key in detail["candidates"]]
        bare = identity.split(":", 1)[-1]
        own = [c for c in cards if [v for s in ("ms", "es", "cal", "cat", "pan", "hub")
                                    for v in getattr(c.ids, s)] == [bare]]
        expected = own[0] if own else min(cards, key=lambda c: (c.country, c.concept, c.key))
        assert indicator_card(identity) == expected


def test_none_and_bare_string_inputs():
    assert indicator_card(None) is None
    for fn in (unmapped, cards_for):
        with pytest.raises(TypeError):
            fn("abc")
    assert unmapped(["abc"]) == ["abc"]


@pytest.mark.parametrize("card", dataset().cards, ids=lambda c: c.key)
def test_render_bound_and_timing_semantics(card):
    rendered = render_card(card)
    assert rendered == render_card(card)
    assert len(rendered) <= MAX_CARD_CHARS == 600
    if card.timing.descriptive_peak:
        assert "not a validated lead" in rendered
        assert card.timing.descriptive_peak.meaning == "not a validated lead"
    if card.timing.timing_class == "UNKNOWN":
        assert "timing not classified" in rendered
    if card.timing.timing_class == "CTX":
        assert "context; no timing claim" in rendered
    for screen in card.timing.predictive_screen:
        if screen.status == "PRED-PASS":
            assert screen.pass_horizons
            assert ",".join(screen.pass_horizons) in rendered
            assert "no real-time validation" in rendered


def test_every_screen_names_its_pair_even_with_many_screens():
    card = indicator_card("us_unrate")
    rendered = render_card(card)
    assert len(card.timing.predictive_screen) > 2 and len(rendered) <= MAX_CARD_CHARS
    for screen in card.timing.predictive_screen:
        assert f"{screen.relationship_id} {short_pair(screen.pair)}:{screen.status}" in rendered
    assert "Q17 IP->unrate:PRED-PASS 3m" in rendered
    assert "Q20 unrate->wages:PRED-PASS 3m,6m,12m" in rendered
    for c in dataset().cards:
        text = render_card(c)
        for screen in c.timing.predictive_screen:
            label = short_pair(screen.pair)
            assert re.fullmatch(r"[\w.\-]+(->|~)[\w.\-]+", label), (c.key, label)
            assert screen.relationship_id in text
            assert label in text or screen.pair in text


def test_hypothesis_is_said_in_words_and_caveats_are_never_clipped():
    for key in ("us_curve_10y2y", "us_vix", "uk_credit_conditions"):
        assert "hypothesis" in render_card(indicator_card(key))
    for c in dataset().cards:
        caveat = " ".join("; ".join(filter(None, (c.flag, c.rationale))).split()).rstrip(".")
        assert f"Caveat: {caveat}." in render_card(c), c.key
    assert "whose screen is partial only" in render_card(indicator_card("us_nfci"))


def test_predictive_direction_and_horizons_are_not_inferred_from_class():
    claims = indicator_card("us_claims_initial")
    a1, a2 = claims.timing.predictive_screen
    assert a1.pair == "Initial claims y/y -> unemployment rate"
    assert a1.pass_horizons == ("3m", "6m", "12m")
    assert a2.pair == "Initial claims y/y -> payrolls"
    assert a2.pass_horizons == ("12m",)
    assert a2.partial_horizons == ("3m", "6m")
    payrolls = indicator_card("us_payrolls")
    assert payrolls.timing.predictive_screen[0].pair == "IP y/y -> payrolls (both coincident)"
    assert payrolls.timing.predictive_screen[0].pass_horizons == ()
    assert payrolls.timing.descriptive_peak.source_text == "Q18: +5m [5-95%: 3..18]"
    assert "not a validated lead" in render_card(payrolls)
    permits = indicator_card("us_building_permits")
    assert permits.timing.predictive_screen[0].pass_horizons == ("12m",)
    assert "A3: +0m" in permits.timing.descriptive_peak.source_text


def test_no_default_revision_claims_or_fabricated_defects():
    assert "annual benchmark revisions" in indicator_card("us_payrolls").revision_behaviour
    assert indicator_card("ea_hicp").revision_behaviour == "Not specified in approved row."
    assert indicator_card("us_claims_initial").known_failure_modes == ("defect D3", "gap G1")
    assert "vintage mix" in indicator_card("us_retail_sales").rationale


def test_models_are_strict_closed_and_immutable():
    payload = indicator_card("us_payrolls").model_dump(mode="json", by_alias=True)
    payload["country"] = 123
    with pytest.raises(ValidationError):
        IndicatorCard.model_validate_json(json.dumps(payload))
    payload["country"] = "USA"
    payload["market_view"] = "buy"
    with pytest.raises(ValidationError):
        IndicatorCard.model_validate_json(json.dumps(payload))
    with pytest.raises(ValidationError):
        indicator_card("us_payrolls").timing.basis = "HYPOTHESIS"
    with pytest.raises(ValidationError):
        PredictiveScreen(relationship_id="A1", pair="X -> Y", status="PRED-PASS", source_text="")
    with pytest.raises(ValueError, match="Unparsed"):
        parse_screen("A1 [X -> Y]: PRED-PASS at h=99years")


def test_timing_extraction_and_mismatched_provenance(tmp_path):
    guide = tmp_path / "guide.md"
    psv = tmp_path / "table.psv"
    psv.write_text("key|concept|ids\nus_test|Test|ms:TEST\n", encoding="utf-8")
    guide.write_text("| us_test | Test | ms:TEST | Issuer | M | Labour | | UNKNOWN | not tested | not estimated | HARD | H | mixed | none | | Test. |\n", encoding="utf-8")
    extracted = extract_timing(guide, psv)
    assert extracted["rows"]["us_test"] == {"predictive_screen": "not tested", "descriptive_peak": "not estimated"}
    psv.write_text("key|concept|ids\nus_test|Different|ms:TEST\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mismatch"):
        extract_timing(guide, psv)
    with pytest.raises(ValueError, match="different PSV"):
        build_dataset(psv_path=psv)


def test_certified_coverage_and_explicit_source_gaps(capsys):
    from market_data_hub.macro_releases.catalogue import FAMILIES, INDICATORS

    report = coverage_report()
    print(json.dumps(report, indent=2))
    assert set(report["certified_families"]) == set(FAMILIES)
    # Every certified family is covered since the owner-approved UK GDP row
    # (18/10/2026); no documented gap remains.
    for family in FAMILIES:
        assert report["certified_families"][family]["unmapped"] == []
    assert report["documented_certified_gaps"] == {}
    # 14 pilot + 16 US monthly (M9a-1) + 7 UK, EA GDP and claims (M9a-2) measures.
    assert report["macro_releases"] == {"mapped": 37, "total": len(INDICATORS), "unmapped": []}
    assert report["tier_1_calendar"]["mapped"] == report["tier_1_calendar"]["total"] == 65
    assert report["macro_series"]["mapped"] == report["macro_series"]["total"] == 107
    assert "macro_releases" in capsys.readouterr().out


@pytest.mark.parametrize("identity,key", [
    ("us_core_cpi_yy", "us_cpi_core"), ("us_core_cpi_mm", "us_cpi_core"),
    ("us_retail_sales_ex_auto_mm", "us_retail_sales"),
    ("us_sp_global_manufacturing_pmi", "us_spglobal_pmi"),
    ("us_sp_global_services_pmi", "us_spglobal_pmi"),
    ("us_new_home_sales", "us_new_home_sales"), ("us_initial_jobless_claims", "us_claims_initial"),
    ("ea_manufacturing_pmi", "ea_pmi"), ("ea_services_pmi", "ea_pmi"),
    ("uk_sp_global_services_pmi", "uk_pmi_svc"),
    ("cn_nbs_manufacturing_pmi", "cn_pmi_nbs"),
    ("cn_caixin_manufacturing_pmi", "cn_pmi_private"),
    ("ca_unemployment_rate", "ca_lfs"), ("ca_employment_change", "ca_lfs"),
])
def test_tier1_aliases_reuse_approved_concepts(identity, key):
    assert indicator_card(identity).key == indicator_card(f"cat:{identity}").key == key
    aliases = json.loads(ALIASES_PATH.read_text(encoding="utf-8"))["aliases"]
    assert any(a["id"] == f"cat:{identity}" and a["key"] == key and a["basis"] for a in aliases)


def test_every_tier1_id_resolves_to_an_approved_or_strict_draft_card(monkeypatch):
    import socket

    def no_network(*args, **kwargs):
        raise AssertionError("Tier-1 coverage must remain offline")

    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    ids = tier1_ids()
    assert len(ids) == len(set(ids)) == 86
    approved = tier1_coverage()
    report = coverage_report(with_drafts=True)["tier_1_measures"]
    drafts = load_draft_cards()
    assert approved["mapped"] == approved["approved"] == report["approved"] == 62
    assert approved["draft"] == 0 and approved["with_drafts"] is False
    assert report["mapped"] == report["total"] == 86
    assert report["draft"] == len(drafts) == 24
    assert report["unmapped"] == [] and report["with_drafts"] is True
    draft_index = {identity: card for card in drafts for identity in card.ids.cat}
    assert set(approved["unmapped"]) == set(draft_index)
    for identity in ids:
        card = indicator_card(identity) or draft_index.get(identity)
        assert card is not None
        assert IndicatorCard.model_validate_json(card.model_dump_json()) == card
        assert report["resolutions"][identity]["key"] == card.key


def test_draft_rows_and_timing_are_conservative_and_never_shipped():
    shipped = dataset()
    with PSV_PATH.open(encoding="utf-8-sig", newline="") as f:
        header = next(csv.reader(f, delimiter="|"))
    with DRAFT_PSV_PATH.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f, delimiter="|"))
    assert rows[0] == header and all(len(row) == 19 for row in rows)
    timing = json.loads(DRAFT_TIMING_PATH.read_text(encoding="utf-8"))
    for card in load_draft_cards():
        assert card.key not in {c.key for c in shipped.cards}
        for identity in (card.key, *card.ids.cat, *(f"cat:{i}" for i in card.ids.cat)):
            assert indicator_card(identity) is None
        assert card.flag == "draft v2.2, pending owner approval"
        assert card.timing.timing_class == "UNKNOWN" and card.timing.basis is None
        assert card.timing.predictive_screen == () and card.timing.descriptive_peak is None
        assert card.timing.screen_note == "not tested"
        assert card.nature == ("SOFT" if "pmi" in card.key else "HARD")
        assert "Source: https://" in card.rationale and "not fetched" in card.rationale
        assert timing["rows"][card.key] == {"descriptive_peak": "not estimated", "predictive_screen": "not tested"}
    assert dataset() is shipped and len(shipped.cards) == 270
    assert "ea_composite_pmi" in tier1_coverage()["unmapped"]


def test_draft_loader_rejects_stale_timing_and_approved_overlap(tmp_path):
    psv = tmp_path / "draft.psv"
    timing = tmp_path / "timing.json"
    psv.write_bytes(DRAFT_PSV_PATH.read_bytes() + b"\n")
    timing.write_bytes(DRAFT_TIMING_PATH.read_bytes())
    with pytest.raises(ValueError, match="different PSV"):
        load_draft_cards(psv, timing)
    psv.write_text(DRAFT_PSV_PATH.read_text(encoding="utf-8").replace(
        "cat:us_existing_home_sales", "cat:us_new_home_sales",
    ), encoding="utf-8")
    evidence = json.loads(timing.read_text(encoding="utf-8"))
    evidence["source_table_sha256"] = hashlib.sha256(psv.read_bytes()).hexdigest()
    timing.write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(ValueError, match="overlaps an approved card"):
        load_draft_cards(psv, timing)


def test_coverage_cli_requires_explicit_draft_opt_in(monkeypatch, capsys):
    import sys
    from market_data_hub.indicator_cards.coverage import main

    for args, mapped in (([], 62), (["--with-drafts"], 86)):
        monkeypatch.setattr(sys, "argv", ["coverage", *args])
        main()
        report = json.loads(capsys.readouterr().out)
        assert report["tier_1_measures"]["mapped"] == mapped
        assert report["cards"] == 270
