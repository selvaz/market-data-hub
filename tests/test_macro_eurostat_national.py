"""Big-four national headlines from Eurostat JSON-stat: parsing, mapping, provenance."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest
import requests

from market_data_hub.indicator_cards import indicator_card
from market_data_hub.indicator_cards.coverage import tier1_ids
from market_data_hub.macro_releases import open_writer
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.catalogue import EA_BIG4_FAMILIES, EXPECTED_LAG_DAYS, FAMILIES, INDICATORS
from market_data_hub.macro_releases.eurostat_national import (
    BASE, MAPPED, SPECS, EurostatNationalAdapter, parse,
)
from market_data_hub.macro_releases.http import Document, FetchError
from market_data_hub.macro_releases.read_api import FAMILY_INDICATORS
from market_data_hub.macro_releases.run_daily import ADAPTERS, DISCOVERY_ADAPTERS
from market_data_hub.macro_releases.verification import SANITY_BANDS

FIXTURES = Path(__file__).parent / "fixtures" / "macro_releases" / "eurostat_national"
RETRIEVED = "2026-11-12T08:00:00Z"
DATASET_FIXTURE = {"ea_big4_gdp": "namq_10_gdp", "ea_big4_unemployment": "une_rt_m",
                   "ea_big4_industrial_production": "sts_inpr_m"}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network forbidden in Eurostat national fixtures")
    monkeypatch.setattr(requests.Session, "request", forbidden)


def document(name, mutate=None):
    payload = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    if mutate:
        mutate(payload)
    body = json.dumps(payload).encode()
    return Document(body, BASE + name, RETRIEVED, hashlib.sha256(body).hexdigest())


class FakeClient:
    def __init__(self):
        self.calls = []

    def get(self, provider, url, params=None):
        self.calls.append((provider, url, params))
        return document(url.rsplit("/", 1)[-1])


def values(rows):
    return {(o.indicator_id, o.period): o.value for o in rows}


def test_gdp_parses_latest_two_quarters_per_country():
    rows = parse(document("namq_10_gdp"), "ea_big4_gdp")
    assert values(rows) == {
        ("de_gdp_qq", "2026-Q1"): 0.4, ("de_gdp_qq", "2026-Q2"): 0.2,
        ("es_gdp_qq", "2026-Q1"): 0.6, ("es_gdp_qq", "2026-Q2"): 0.7,
        ("fr_gdp_qq", "2026-Q1"): -0.1, ("fr_gdp_qq", "2026-Q2"): 0.2,
        ("it_gdp_qq", "2026-Q1"): 0.3, ("it_gdp_qq", "2026-Q2"): 0.2,
    }
    de = next(o for o in rows if o.indicator_id == "de_gdp_qq" and o.period == "2026-Q2")
    assert de.series_key == "namq_10_gdp:DE:CLV_PCH_PRE:SCA:B1GQ"
    assert (de.source, de.family, de.edition, de.published_at) == (
        "Eurostat", "ea_big4_gdp", "2026-08-14", "2026-08-14T00:00:00Z")
    assert (de.precision, de.revision, de.stage, de.method) == ("date_only", "provider_history", "regular", "api")
    assert de.provider_vintage == "2026-08-14T23:00:00+0200"
    assert de.composition_id is None


def test_unemployment_parses_italy_ilo_rate():
    rows = parse(document("une_rt_m"), "ea_big4_unemployment")
    assert values(rows) == {("it_unemployment_rate", "2026-07"): 6.0, ("it_unemployment_rate", "2026-08"): 6.2}
    assert {o.series_key for o in rows} == {"une_rt_m:IT:SA:TOTAL:PC_ACT:T"}
    assert {o.edition for o in rows} == {"2026-10-01"}


def test_industrial_production_parses_france_italy_spain():
    rows = parse(document("sts_inpr_m"), "ea_big4_industrial_production")
    assert values(rows) == {
        ("es_industrial_production_mm", "2026-07"): 0.6, ("es_industrial_production_mm", "2026-08"): -0.8,
        ("fr_industrial_production_mm", "2026-07"): -0.7, ("fr_industrial_production_mm", "2026-08"): -0.3,
        ("it_industrial_production_mm", "2026-07"): 0.6, ("it_industrial_production_mm", "2026-08"): -1.3,
    }
    assert {o.series_key for o in rows if o.indicator_id.startswith("fr_")} == {
        "sts_inpr_m:FR:PRD:B-D:SCA:PCH_PRE"}


def test_since_cuts_by_period_end():
    rows = parse(document("namq_10_gdp"), "ea_big4_gdp", since="2026-04")
    assert {o.period for o in rows} == {"2026-Q2"}
    assert parse(document("sts_inpr_m"), "ea_big4_industrial_production", since="2026-09") == []


@pytest.mark.parametrize("name,family,mutate,message", [
    ("namq_10_gdp", "ea_big4_gdp",
     lambda p: p["dimension"]["unit"]["category"].update(index={"CP_MEUR": 0}), "units"),
    ("une_rt_m", "ea_big4_unemployment",
     lambda p: p["dimension"]["age"]["category"].update(index={"Y15-24": 0}), "coverage"),
    ("sts_inpr_m", "ea_big4_industrial_production",
     lambda p: p["dimension"]["nace_r2"]["category"].update(index={"B-E": 0}), "coverage"),
    ("sts_inpr_m", "ea_big4_industrial_production",
     lambda p: p.update(extension=dict(p["extension"], id="STS_INPR_Q")), "identity"),
    ("namq_10_gdp", "ea_big4_gdp", lambda p: p["id"].append("extra"), "dimensions"),
    ("une_rt_m", "ea_big4_unemployment", lambda p: p.update(status={"3": "s"}), "status"),
    ("namq_10_gdp", "ea_big4_gdp",
     lambda p: p.update(value={k: v for k, v in p["value"].items() if int(k) >= 4}), "missing"),
    ("namq_10_gdp", "ea_big4_gdp", lambda p: p.update(updated="yesterday"), "timestamp"),
])
def test_definition_drift_fails_closed(name, family, mutate, message):
    with pytest.raises(FetchError, match=message):
        parse(document(name, mutate), family)


def test_unmapped_country_in_payload_is_rejected():
    def add_de(payload):
        payload["dimension"]["geo"]["category"]["index"] = {"DE": 0, "IT": 1}
        payload["size"][-2] = 2
    with pytest.raises(FetchError, match="geography"):
        parse(document("une_rt_m", add_de), "ea_big4_unemployment")


def test_adapter_requests_exact_filters_without_network():
    client = FakeClient()
    rows = EurostatNationalAdapter(client).fetch(list(EA_BIG4_FAMILIES) + ["ea_hicp"], "2026-05")
    assert {c[1] for c in client.calls} == {BASE + d for d in DATASET_FIXTURE.values()}
    by_dataset = {c[1].rsplit("/", 1)[-1]: c[2] for c in client.calls}
    assert by_dataset["namq_10_gdp"] == {"lang": "EN", "unit": "CLV_PCH_PRE", "s_adj": "SCA", "na_item": "B1GQ",
                                         "geo": ["DE", "ES", "FR", "IT"], "sinceTimePeriod": "2026-Q2"}
    assert by_dataset["une_rt_m"] == {"lang": "EN", "s_adj": "SA", "age": "TOTAL", "unit": "PC_ACT", "sex": "T",
                                      "geo": ["IT"], "sinceTimePeriod": "2026-05"}
    assert by_dataset["sts_inpr_m"]["nace_r2"] == "B-D" and by_dataset["sts_inpr_m"]["geo"] == ["ES", "FR", "IT"]
    assert {o.indicator_id for o in rows} == set(MAPPED)


def test_mapping_covers_only_definition_matched_tier1_cards():
    assert MAPPED == {
        "de_gdp_qq": ("ea_big4_gdp", "DE"), "fr_gdp_qq": ("ea_big4_gdp", "FR"),
        "it_gdp_qq": ("ea_big4_gdp", "IT"), "es_gdp_qq": ("ea_big4_gdp", "ES"),
        "it_unemployment_rate": ("ea_big4_unemployment", "IT"),
        "fr_industrial_production_mm": ("ea_big4_industrial_production", "FR"),
        "it_industrial_production_mm": ("ea_big4_industrial_production", "IT"),
        "es_industrial_production_mm": ("ea_big4_industrial_production", "ES"),
    }
    cards = {"de_gdp_qq": "de_gdp_real", "fr_gdp_qq": "fr_gdp_real", "it_gdp_qq": "it_gdp_real",
             "es_gdp_qq": "es_gdp_real", "it_unemployment_rate": "it_unemployment",
             "fr_industrial_production_mm": "fr_indpro", "it_industrial_production_mm": "it_indpro",
             "es_industrial_production_mm": "es_indpro"}
    issuers = {"de": "Destatis", "fr": "INSEE", "it": "Istat", "es": "INE"}
    tier1 = set(tier1_ids())
    for indicator, (family, geo) in MAPPED.items():
        assert indicator in tier1
        assert indicator_card(indicator).key == cards[indicator]
        measure = INDICATORS[indicator]
        assert measure.issuer_id == issuers[indicator[:2]] != "Eurostat"
        assert (measure.tier, measure.catalogue_status, measure.active) == ("T1", "supported", True)
        assert indicator in FAMILY_INDICATORS[family] and indicator in SANITY_BANDS
    # Card concepts that differ from the Eurostat measure stay unmapped.
    for indicator, card in (("de_unemployment_rate", "de_unemployment_ba"),
                            ("fr_unemployment_rate", "fr_unemployment"),
                            ("es_unemployment_rate", "es_unemployment")):
        assert indicator_card(indicator).key == card
        assert indicator not in INDICATORS and indicator not in MAPPED
    # No German industrial production card is in the Tier-1 snapshot.
    assert not any(i.startswith("de_industrial_production") for i in tier1)


def test_families_are_wired_for_daily_and_manual_collection():
    for family in EA_BIG4_FAMILIES:
        assert family in FAMILIES and family in SPECS and family in EXPECTED_LAG_DAYS
        assert ADAPTERS[family] is DISCOVERY_ADAPTERS[family] is EurostatNationalAdapter


def test_persisted_as_unverified_eurostat_mirror_of_national_issuer(tmp_path):
    rows = parse(document("namq_10_gdp"), "ea_big4_gdp")
    with open_writer(tmp_path / "macro.sqlite") as store:
        persist(store, rows)
        series = store.conn.execute(
            "SELECT DISTINCT indicator_id, issuer_id, distributor_id, source_id, evidence_role "
            "FROM source_series ORDER BY indicator_id").fetchall()
        assert [tuple(r) for r in series] == [
            ("de_gdp_qq", "Destatis", "Eurostat", "Eurostat", "mirror"),
            ("es_gdp_qq", "INE", "Eurostat", "Eurostat", "mirror"),
            ("fr_gdp_qq", "INSEE", "Eurostat", "Eurostat", "mirror"),
            ("it_gdp_qq", "Istat", "Eurostat", "Eurostat", "mirror"),
        ]
        statuses = {r[0] for r in store.conn.execute("SELECT verification_status FROM value_vintages")}
        assert statuses == {"unverified"}
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 8
        events = {r[0] for r in store.conn.execute("SELECT id FROM release_events")}
        assert "Destatis:ea_big4_gdp:regular:2026-08-14" in events
        # The same snapshot re-read adds nothing.
        before = store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0]
        persist(store, [replace(o, document=replace(o.document, retrieved_at="2026-11-12T09:00:00Z")) for o in rows])
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == before


def test_manual_collect_isolates_a_failing_family(tmp_path, monkeypatch, capsys):
    from market_data_hub.macro_releases import collect

    class DriftedUnemployment(FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__()

        def get(self, provider, url, params=None):
            if url.endswith("une_rt_m"):
                # The live failure: a wrong age filter yields another age dimension.
                return document("une_rt_m", lambda p: p["dimension"]["age"]["category"].update(index={"Y15-24": 0}))
            return super().get(provider, url, params)

    monkeypatch.setattr(collect, "HttpClient", DriftedUnemployment)
    families = ",".join(EA_BIG4_FAMILIES)
    assert collect.main(["--db", str(tmp_path / "macro.sqlite"), "--families", families, "--since", "2026-01"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["family_failures"] == {"ea_big4_unemployment": ["EurostatNationalAdapter:ea_big4_unemployment"]}
    assert set(result["families"]) == {"EurostatNationalAdapter:ea_big4_gdp",
                                       "EurostatNationalAdapter:ea_big4_industrial_production"}
    assert result["families"]["EurostatNationalAdapter:ea_big4_gdp"]["new_vintages"] == 8
    assert result["families"]["EurostatNationalAdapter:ea_big4_industrial_production"]["new_vintages"] == 6
