"""National headlines (DE, FR, IT, ES, PL) from Eurostat JSON-stat: parsing, mapping, provenance."""
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
                   "ea_big4_industrial_production": "sts_inpr_m", "ea_big4_retail": "sts_trtu_m",
                   "ea_big4_hicp": "prc_hicp_minr", "ea_big4_esi": "ei_bssi_m_r2"}


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
        ("es_gdp_qq", "2026-Q2"): 0.7, ("es_gdp_qq", "2026-Q3"): 0.6,
        ("fr_gdp_qq", "2026-Q1"): -0.1, ("fr_gdp_qq", "2026-Q2"): 0.2,
        ("it_gdp_qq", "2026-Q1"): 0.3, ("it_gdp_qq", "2026-Q2"): 0.2,
        ("pl_gdp_qq", "2026-Q2"): 1.1,
    }
    de = next(o for o in rows if o.indicator_id == "de_gdp_qq" and o.period == "2026-Q2")
    assert de.series_key == "namq_10_gdp:DE:CLV_PCH_PRE:SCA:B1GQ"
    assert (de.source, de.family, de.edition, de.published_at) == (
        "Eurostat", "ea_big4_gdp", "2026-11-06", "2026-11-06T00:00:00Z")
    assert (de.precision, de.revision, de.stage, de.method) == ("date_only", "provider_history", "regular", "api")
    assert de.provider_vintage == "2026-11-06T23:00:00+0100"
    assert de.composition_id is None


def test_unemployment_parses_monthly_ilo_rates():
    rows = parse(document("une_rt_m"), "ea_big4_unemployment")
    assert values(rows) == {("it_unemployment_rate", "2026-07"): 6.0, ("it_unemployment_rate", "2026-08"): 6.2,
                            ("de_unemployment_ilo_rate", "2026-09"): 4.1,
                            ("fr_unemployment_ilo_rate", "2026-09"): 8.5,
                            ("es_unemployment_ilo_rate", "2026-09"): 10.3,
                            ("pl_unemployment_ilo_rate", "2026-09"): 3.4}
    assert {o.series_key for o in rows} == {f"une_rt_m:{geo}:SA:TOTAL:PC_ACT:T"
                                            for geo in ("DE", "ES", "FR", "IT", "PL")}
    assert {o.edition for o in rows} == {"2026-11-02"}


def test_industrial_production_parses_five_countries():
    rows = parse(document("sts_inpr_m"), "ea_big4_industrial_production")
    assert values(rows) == {
        ("de_industrial_production_mm", "2026-09"): 1.3,
        ("es_industrial_production_mm", "2026-08"): -0.8, ("es_industrial_production_mm", "2026-09"): 0.9,
        ("fr_industrial_production_mm", "2026-07"): -0.7, ("fr_industrial_production_mm", "2026-08"): -0.3,
        ("it_industrial_production_mm", "2026-07"): 0.6, ("it_industrial_production_mm", "2026-08"): -1.3,
        ("pl_industrial_production_mm", "2026-09"): 2.9,
    }
    assert {o.series_key for o in rows if o.indicator_id.startswith("fr_")} == {
        "sts_inpr_m:FR:PRD:B-D:SCA:PCH_PRE"}


def test_since_cuts_by_period_end():
    rows = parse(document("namq_10_gdp"), "ea_big4_gdp", since="2026-04")
    assert {o.period for o in rows} == {"2026-Q2", "2026-Q3"}
    assert {o.period for o in parse(document("namq_10_gdp"), "ea_big4_gdp", since="2026-07")} == {"2026-Q3"}
    assert parse(document("sts_inpr_m"), "ea_big4_industrial_production", since="2026-11") == []


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
    ("une_rt_m", "ea_big4_unemployment", lambda p: p.update(status={next(iter(p["value"])): "s"}), "status"),
    ("sts_trtu_m", "ea_big4_retail",
     lambda p: p["dimension"]["indic_bt"]["category"].update(index={"NETTUR": 0}), "coverage"),
    ("ei_bssi_m_r2", "ea_big4_esi",
     lambda p: p["dimension"]["indic"]["category"].update(index={"BS-CSMCI": 0}), "coverage"),
    ("prc_hicp_minr", "ea_big4_hicp",
     lambda p: p["dimension"]["coicop18"]["category"].update(index={"CP01": 0}), "coverage"),
    ("prc_hicp_minr", "ea_big4_hicp",
     lambda p: p["dimension"]["unit"]["category"].update(index={"RCH_A": 0, "I25": 1}), "units"),
    ("namq_10_gdp", "ea_big4_gdp",
     lambda p: p.update(value={k: v for k, v in p["value"].items() if int(k) >= 4}), "missing"),
    ("namq_10_gdp", "ea_big4_gdp", lambda p: p.update(updated="yesterday"), "timestamp"),
])
def test_definition_drift_fails_closed(name, family, mutate, message):
    with pytest.raises(FetchError, match=message):
        parse(document(name, mutate), family)


def test_unmapped_country_in_payload_is_rejected():
    def add_nl(payload):
        index = payload["dimension"]["geo"]["category"]["index"]
        index["NL"] = len(index)
        payload["size"][-2] = len(index)
    with pytest.raises(FetchError, match="geography"):
        parse(document("une_rt_m", add_nl), "ea_big4_unemployment")


def test_poland_is_not_accepted_in_the_euro_area_flash():
    def add_pl(payload):
        index = payload["dimension"]["geo"]["category"]["index"]
        index["PL"] = len(index)
        payload["size"][-2] = len(index)
    with pytest.raises(FetchError, match="geography"):
        parse(document("prc_hicp_fpd", add_pl), "ea_big4_hicp", flash=True)


def test_retail_volume_parses_five_countries():
    rows = parse(document("sts_trtu_m"), "ea_big4_retail")
    assert values(rows) == {("de_retail_sales_mm", "2026-09"): -2.0, ("fr_retail_sales_mm", "2026-09"): -0.6,
                            ("it_retail_sales_mm", "2026-09"): 0.1, ("es_retail_sales_mm", "2026-09"): -0.4,
                            ("pl_retail_sales_mm", "2026-09"): -0.2}
    assert {o.series_key for o in rows if o.indicator_id.startswith("de_")} == {
        "sts_trtu_m:DE:VOL_SLS:G47:SCA:PCH_PRE"}


def test_esi_parses_five_countries_with_dg_ecfin_as_issuer():
    rows = parse(document("ei_bssi_m_r2"), "ea_big4_esi")
    assert values(rows) == {("de_economic_sentiment_indicator", "2026-10"): 94.1,
                            ("fr_economic_sentiment_indicator", "2026-10"): 96.1,
                            ("it_economic_sentiment_indicator", "2026-10"): 100.2,
                            ("es_economic_sentiment_indicator", "2026-10"): 105.1,
                            ("pl_economic_sentiment_indicator", "2026-10"): 99.0}
    assert {INDICATORS[o.indicator_id].issuer_id for o in rows} == {"ECFIN"}
    assert {o.edition for o in rows} == {"2026-10-30"}


def test_hicp_splits_annual_and_monthly_rates_per_country():
    rows = parse(document("prc_hicp_minr"), "ea_big4_hicp")
    got = values(rows)
    assert got[("de_hicp_yy", "2026-09")] == 2.4 and got[("de_hicp_mm", "2026-09")] == 0.2
    assert got[("it_hicp_yy", "2026-08")] == 1.7 and got[("fr_hicp_mm", "2026-09")] == -0.9
    assert got[("es_hicp_yy", "2026-10")] == 4.9 and got[("pl_hicp_yy", "2026-09")] == 4.0
    assert {i for i, _ in got} == {f"{cc}_hicp_{b}" for cc in ("de", "fr", "it", "es", "pl") for b in ("yy", "mm")}
    assert {o.stage for o in rows} == {"regular"}
    de = next(o for o in rows if o.indicator_id == "de_hicp_yy" and o.period == "2026-09")
    assert de.series_key == "prc_hicp_minr:DE:TOTAL:RCH_A"


def test_hicp_flash_fills_only_months_after_the_regular_edition():
    client = FakeClient()
    rows = EurostatNationalAdapter(client).fetch(["ea_big4_hicp"], "2026-08")
    assert [c[1].rsplit("/", 1)[-1] for c in client.calls] == ["prc_hicp_minr", "prc_hicp_fpd"]
    assert client.calls[0][2] == {"lang": "EN", "coicop18": "TOTAL", "unit": ["RCH_A", "RCH_M"],
                                  "geo": ["DE", "ES", "FR", "IT", "PL"], "sinceTimePeriod": "2026-08"}
    assert client.calls[1][2]["release"] == "FLS"
    # Poland has no flash estimate (outside the euro area): never requested.
    assert client.calls[1][2]["geo"] == ["DE", "ES", "FR", "IT"]
    flash = [o for o in rows if o.stage == "flash"]
    assert values(flash) == {("de_hicp_yy", "2026-10"): 2.3, ("de_hicp_mm", "2026-10"): 0.3,
                             ("fr_hicp_yy", "2026-10"): 0.9, ("fr_hicp_mm", "2026-10"): 0.1,
                             ("it_hicp_yy", "2026-10"): 1.6, ("it_hicp_mm", "2026-10"): -0.2}
    assert {o.series_key for o in flash if o.indicator_id == "de_hicp_yy"} == {"prc_hicp_fpd:DE:TOTAL:FLS:RCH_A"}
    assert {o.edition for o in flash} == {"2026-10-31"}
    # September is already in the regular edition: the flash copy is not re-emitted.
    assert not any(o.period == "2026-09" for o in flash)
    # Spain's October is already in the regular edition too.
    assert not any(o.indicator_id.startswith(("es_", "pl_")) for o in flash)


def test_hicp_flash_definition_drift_fails_closed():
    with pytest.raises(FetchError, match="coverage"):
        parse(document("prc_hicp_fpd", lambda p: p["dimension"]["release"]["category"].update(index={"FRS": 0})),
              "ea_big4_hicp", flash=True)


FIVE = ["DE", "ES", "FR", "IT", "PL"]


def test_adapter_requests_exact_filters_without_network():
    client = FakeClient()
    rows = EurostatNationalAdapter(client).fetch(list(EA_BIG4_FAMILIES) + ["ea_hicp"], "2026-05")
    assert {c[1] for c in client.calls} == {BASE + d for d in DATASET_FIXTURE.values()} | {BASE + "prc_hicp_fpd"}
    by_dataset = {c[1].rsplit("/", 1)[-1]: c[2] for c in client.calls}
    assert by_dataset["namq_10_gdp"] == {"lang": "EN", "unit": "CLV_PCH_PRE", "s_adj": "SCA", "na_item": "B1GQ",
                                         "geo": FIVE, "sinceTimePeriod": "2026-Q2"}
    assert by_dataset["une_rt_m"] == {"lang": "EN", "s_adj": "SA", "age": "TOTAL", "unit": "PC_ACT", "sex": "T",
                                      "geo": FIVE, "sinceTimePeriod": "2026-05"}
    assert by_dataset["sts_inpr_m"]["nace_r2"] == "B-D" and by_dataset["sts_inpr_m"]["geo"] == FIVE
    assert by_dataset["sts_trtu_m"] == {"lang": "EN", "indic_bt": "VOL_SLS", "nace_r2": "G47", "s_adj": "SCA",
                                        "unit": "PCH_PRE", "geo": FIVE, "sinceTimePeriod": "2026-05"}
    assert by_dataset["ei_bssi_m_r2"] == {"lang": "EN", "indic": "BS-ESI-I", "s_adj": "SA",
                                          "geo": FIVE, "sinceTimePeriod": "2026-05"}
    assert {o.indicator_id for o in rows} == set(MAPPED)


def test_mapping_covers_only_definition_matched_tier1_cards():
    five = {"DE": "de", "FR": "fr", "IT": "it", "ES": "es", "PL": "pl"}
    assert MAPPED == {
        **{f"{cc}_gdp_qq": ("ea_big4_gdp", geo) for geo, cc in five.items()},
        "it_unemployment_rate": ("ea_big4_unemployment", "IT"),
        **{f"{cc}_unemployment_ilo_rate": ("ea_big4_unemployment", geo) for geo, cc in five.items() if cc != "it"},
        **{f"{cc}_industrial_production_mm": ("ea_big4_industrial_production", geo) for geo, cc in five.items()},
        **{f"{cc}_retail_sales_mm": ("ea_big4_retail", geo) for geo, cc in five.items()},
        **{f"{cc}_hicp_{b}": ("ea_big4_hicp", geo) for geo, cc in five.items() for b in ("yy", "mm")},
        **{f"{cc}_economic_sentiment_indicator": ("ea_big4_esi", geo) for geo, cc in five.items()},
    }
    cards = {"de_gdp_qq": "de_gdp_real", "fr_gdp_qq": "fr_gdp_real", "it_gdp_qq": "it_gdp_real",
             "es_gdp_qq": "es_gdp_real", "pl_gdp_qq": "pl_gdp_real", "it_unemployment_rate": "it_unemployment",
             **{f"{cc}_unemployment_ilo_rate": f"{cc}_unemployment_ilo" for cc in ("de", "fr", "es", "pl")},
             "de_industrial_production_mm": "de_indprod",
             "fr_industrial_production_mm": "fr_indpro", "it_industrial_production_mm": "it_indpro",
             "es_industrial_production_mm": "es_indpro", "pl_industrial_production_mm": "pl_indpro",
             **{f"{cc}_retail_sales_mm": f"{cc}_retail" for cc in five.values()},
             **{f"{cc}_hicp_{b}": f"{cc}_hicp" for cc in five.values() for b in ("yy", "mm")},
             **{f"{cc}_economic_sentiment_indicator": f"{cc}_esi" for cc in five.values()}}
    issuers = {"de": "Destatis", "fr": "INSEE", "it": "Istat", "es": "INE", "pl": "GUS"}
    tier1 = set(tier1_ids())
    original = {"ea_big4_gdp": set(SPECS["ea_big4_gdp"]["indicators"].values()) - {"pl_gdp_qq"},
                "it": {"it_unemployment_rate", "fr_industrial_production_mm", "it_industrial_production_mm",
                       "es_industrial_production_mm"}}
    for indicator, (family, geo) in MAPPED.items():
        # The #192 headlines are in the Tier-1 snapshot; the additions are new cards.
        if indicator in original["ea_big4_gdp"] | original["it"]:
            assert indicator in tier1
        assert indicator_card(indicator).key == cards[indicator]
        measure = INDICATORS[indicator]
        expected = "ECFIN" if family == "ea_big4_esi" else issuers[indicator[:2]]
        assert measure.issuer_id == expected != "Eurostat"
        assert (measure.tier, measure.catalogue_status, measure.active) == ("T1", "supported", True)
        assert indicator in FAMILY_INDICATORS[family] and indicator in SANITY_BANDS
    # Card concepts that differ from the Eurostat measure stay unmapped.
    for indicator, card in (("de_unemployment_rate", "de_unemployment_ba"),
                            ("fr_unemployment_rate", "fr_unemployment"),
                            ("es_unemployment_rate", "es_unemployment"),
                            ("pl_unemployment_rate", "pl_unemployment")):
        assert indicator_card(indicator).key == card
        assert indicator not in INDICATORS and indicator not in MAPPED
    # National HICP is not the national CPI: the CPI cards keep their own ids.
    for indicator, card in (("de_cpi_yy", "de_cpi"), ("fr_cpi_yy", "fr_cpi"), ("it_cpi_yy", "it_cpi_nic"),
                            ("es_cpi_yy", "es_cpi"), ("pl_cpi_yy", "pl_cpi")):
        assert indicator_card(indicator).key == card and indicator not in MAPPED


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
            ("pl_gdp_qq", "GUS", "Eurostat", "Eurostat", "mirror"),
        ]
        statuses = {r[0] for r in store.conn.execute("SELECT verification_status FROM value_vintages")}
        assert statuses == {"unverified"}
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == 9
        events = {r[0] for r in store.conn.execute("SELECT id FROM release_events")}
        assert {"Destatis:ea_big4_gdp:regular:2026-11-06", "GUS:ea_big4_gdp:regular:2026-11-06"} <= events
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
    assert set(result["families"]) == {f"EurostatNationalAdapter:{f}" for f in EA_BIG4_FAMILIES
                                       if f != "ea_big4_unemployment"}
    assert result["families"]["EurostatNationalAdapter:ea_big4_gdp"]["new_vintages"] == 9
    assert result["families"]["EurostatNationalAdapter:ea_big4_industrial_production"]["new_vintages"] == 8
    assert result["families"]["EurostatNationalAdapter:ea_big4_retail"]["new_vintages"] == 5
    assert result["families"]["EurostatNationalAdapter:ea_big4_esi"]["new_vintages"] == 5
    # DE/FR/IT: six regular rates for each of August and September plus six
    # October flashes; ES October and PL September regular rates (2 each).
    assert result["families"]["EurostatNationalAdapter:ea_big4_hicp"]["new_vintages"] == 22


LEAD_LIVE_VALUES = {
    # Owner's live Eurostat run, 2026-11-12 (the fixtures carry these values).
    "de_industrial_production_mm": ("2026-09", 1.3),
    "de_unemployment_ilo_rate": ("2026-09", 4.1), "fr_unemployment_ilo_rate": ("2026-09", 8.5),
    "de_retail_sales_mm": ("2026-09", -2.0), "fr_retail_sales_mm": ("2026-09", -0.6),
    "it_retail_sales_mm": ("2026-09", 0.1),
    "de_economic_sentiment_indicator": ("2026-10", 94.1), "fr_economic_sentiment_indicator": ("2026-10", 96.1),
    "it_economic_sentiment_indicator": ("2026-10", 100.2),
    # Synthetic October flash (no national HICP value was recorded live).
    "de_hicp_yy": ("2026-10", 2.3), "it_hicp_mm": ("2026-10", -0.2),
    # Spain and Poland, owner's live Eurostat run 2026-11-12.
    "es_gdp_qq": ("2026-Q3", 0.6),
    "es_industrial_production_mm": ("2026-09", 0.9), "pl_industrial_production_mm": ("2026-09", 2.9),
    "es_hicp_yy": ("2026-10", 4.9), "pl_hicp_yy": ("2026-09", 4.0),
    "es_unemployment_ilo_rate": ("2026-09", 10.3), "pl_unemployment_ilo_rate": ("2026-09", 3.4),
    "es_retail_sales_mm": ("2026-09", -0.4), "pl_retail_sales_mm": ("2026-09", -0.2),
    "es_economic_sentiment_indicator": ("2026-10", 105.1), "pl_economic_sentiment_indicator": ("2026-10", 99.0),
}


@pytest.fixture
def national_store(tmp_path, monkeypatch):
    path = tmp_path / "macro.sqlite"
    rows = EurostatNationalAdapter(FakeClient()).fetch(list(EA_BIG4_FAMILIES), "2026-01")
    with open_writer(path) as store:
        persist(store, rows)
    return str(path)


def test_every_national_indicator_has_a_release_concept():
    from market_data_hub.concepts import load_concepts
    registry = load_concepts()
    for indicator, (family, _) in MAPPED.items():
        spec = registry[indicator]
        [source] = spec["sources"]
        assert (source["kind"], source["id"], source["role"]) == ("release", indicator, "primary")
        assert source["evidence"] == "redistributor"
        assert spec["calendar"] == "country:" + indicator[:2].upper()
        period_days = 92 if INDICATORS[indicator].frequency == "Q" else 31
        lag = EXPECTED_LAG_DAYS[family]
        assert source["max_age_business_days"] == -(-(period_days + lag + 7) * 5 // 7)
    for geo, series in (("DE", "DEULOLITOAASTSAM"), ("FR", "FRALOLITOAASTSAM"), ("IT", "ITALOLITOAASTSAM"),
                        ("ES", "ESPLOLITOAASTSAM")):
        cli = registry[f"{geo}_OECD_CLI"]
        assert cli["sources"] == [{"kind": "macro", "id": series, "role": "primary",
                                   "max_age_business_days": 60,
                                   "convention": "OECD amplitude-adjusted composite leading indicator"}]
        assert indicator_card(series).key == f"{geo.lower()}_oecd_cli"
        assert indicator_card(series).timing.timing_class == "LEAD"
    # Poland's FRED CLI stopped in 2022: no concept; its leading indicator is the ESI.
    assert "PL_OECD_CLI" not in registry


def test_cascade_reads_eurostat_mirror_as_current_value(national_store):
    from market_data_hub import latest
    for indicator, (period, value) in LEAD_LIVE_VALUES.items():
        result = latest.read_latest(indicator, as_of="2026-11-12", releases_db=national_store,
                                    as_known_at="2026-11-12T12:00:00Z")
        assert (result["status"], result["source_kind"]) == ("primary", "release"), (indicator, result)
        assert (result["reference_period"], result["value"]) == (period, value), indicator


def test_poland_q2_gdp_is_returned_as_stale_on_2026_11_12(national_store):
    # Q2 + 35d Eurostat lag + 7d grace ends before 12 November: Eurostat had
    # not yet carried the GUS Q3 flash, so the newest reading is stale.
    from market_data_hub import latest
    result = latest.read_latest("pl_gdp_qq", as_of="2026-11-12", releases_db=national_store,
                                as_known_at="2026-11-12T12:00:00Z")
    assert (result["status"], result["reference_period"], result["value"]) == ("stale", "2026-Q2", 1.1)


def test_mirror_readings_need_the_redistributor_opt_in():
    from market_data_hub import latest
    from market_data_hub.concepts import load_concepts
    source = load_concepts()["de_retail_sales_mm"]["sources"][0]
    issuer_only = {k: v for k, v in source.items() if k != "evidence"}
    assert latest._release_statuses(issuer_only) == {"official", "private"}
    assert latest._release_statuses(source) == {"official", "private", "provisional"}


def test_release_evidence_value_is_validated():
    from market_data_hub.concepts import load_concepts, validate_concepts
    spec = dict(load_concepts()["de_retail_sales_mm"])
    spec["sources"] = [dict(spec["sources"][0], evidence="mirror")]
    with pytest.raises(ValueError, match="evidence"):
        validate_concepts([spec])
