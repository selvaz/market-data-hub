"""NBP reference rate from the issuer's interest-rate table: parsing, wiring, provenance."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest
import requests

from market_data_hub.indicator_cards import indicator_card
from market_data_hub.macro_releases import open_writer
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.catalogue import EXPECTED_LAG_DAYS, FAMILIES, INDICATORS, NBP_FAMILIES
from market_data_hub.macro_releases.http import Document, FetchError
from market_data_hub.macro_releases.nbp_policy import URL, NBPAdapter, parse
from market_data_hub.macro_releases.read_api import FAMILY_INDICATORS
from market_data_hub.macro_releases.report import POLICY_INDICATORS
from market_data_hub.macro_releases.run_daily import ADAPTERS, DISCOVERY_ADAPTERS
from market_data_hub.macro_releases.verification import SANITY_BANDS

FIXTURE = Path(__file__).parent / "fixtures" / "macro_releases" / "nbp" / "stopy_procentowe.xml"
RETRIEVED = "2026-11-12T08:00:00Z"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network forbidden in NBP fixtures")
    monkeypatch.setattr(requests.Session, "request", forbidden)


def document(body=None, url=URL):
    body = FIXTURE.read_bytes() if body is None else body
    return Document(body, url, RETRIEVED, hashlib.sha256(body).hexdigest())


def edited(old, new):
    return document(FIXTURE.read_bytes().replace(old.encode(), new.encode()))


class FakeClient:
    def __init__(self):
        self.calls = []

    def get(self, provider, url, params=None):
        self.calls.append((provider, url, params))
        return document()


def test_reference_rate_parses_decimal_comma_and_effective_date():
    [row] = parse(document())
    assert (row.indicator_id, row.family, row.period, row.value, row.printed) == (
        "nbp_reference_rate_decision", "nbp_policy", "2026-03-05", 3.75, "3,75")
    assert (row.source, row.edition, row.published_at, row.precision) == (
        "NBP", "2026-03-05", "2026-03-05T00:00:00Z", "date_only")
    assert row.series_key == "nbp_reference_rate_decision:ref"
    facts = row.policy_decision
    assert facts["decision_date"] == "2026-03-05" and facts["rates"] == {"reference_rate": {"rate": 3.75}}
    assert facts["source_url"] == URL and facts["table_published"] == "2026-03-05"


def test_other_rates_in_the_table_are_not_read():
    # The lombard row is not the reference rate.
    [row] = parse(edited('oprocentowanie="4,25"', 'oprocentowanie="9,99"'))
    assert row.value == 3.75


def test_single_byte_encoded_table_parses():
    body = FIXTURE.read_text(encoding="utf-8").replace('encoding="UTF-8"', 'encoding="ISO-8859-2"')
    [row] = parse(document(body.encode("iso-8859-2")))
    assert row.value == 3.75


def test_since_drops_an_older_decision():
    assert parse(document(), since="2026-04") == []


@pytest.mark.parametrize("old,new", [
    ('pozycja id="ref"', 'pozycja id="xref"'),          # ref row missing
    ('oprocentowanie="3,75"', 'oprocentowanie=""'),      # empty rate
    ('oprocentowanie="3,75"', 'oprocentowanie="3.75%"'),  # not a decimal-comma rate
    ('obowiazuje_od="2026-03-05" />\n<pozycja id="lom"', 'obowiazuje_od="05-03-2026" />\n<pozycja id="lom"'),
    ('tabela id="stoproc"', 'tabela id="other"'),
    ('<stopy_procentowe data_publikacji="2026-03-05">', '<stopy data_publikacji="2026-03-05">'),
    ('data_publikacji="2026-03-05"', 'data_publikacji="2026-03-04"'),  # effective after publication
])
def test_schema_drift_fails_closed(old, new):
    body = FIXTURE.read_text(encoding="utf-8").replace(old, new)
    if old.startswith("<stopy_procentowe"):
        body = body.replace("</stopy_procentowe>", "</stopy>")
    with pytest.raises(FetchError, match="NBP"):
        parse(document(body.encode()))


def test_duplicate_ref_rows_fail_closed():
    body = FIXTURE.read_text(encoding="utf-8")
    ref = next(line for line in body.splitlines() if 'id="ref"' in line)
    with pytest.raises(FetchError, match="NBP"):
        parse(document(body.replace(ref, ref + "\n" + ref).encode()))


def test_wrong_origin_and_entities_are_rejected():
    with pytest.raises(FetchError, match="origin"):
        parse(document(url="https://example.com/dane/stopy/stopy_procentowe.xml"))
    with pytest.raises(FetchError, match="schema"):
        parse(document(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><stopy_procentowe/>'))
    with pytest.raises(FetchError, match="malformed"):
        parse(document(b"<html>not xml"))


def test_adapter_reads_one_issuer_file():
    client = FakeClient()
    rows = NBPAdapter(client).fetch(["nbp_policy", "ea_big4_gdp"], "2026-01")
    assert client.calls == [("nbp", URL, None)]
    assert [r.value for r in rows] == [3.75]


def test_family_is_wired_as_a_tier1_decision():
    assert "nbp_policy" in FAMILIES and EXPECTED_LAG_DAYS["nbp_policy"] == 0
    assert ADAPTERS["nbp_policy"] is DISCOVERY_ADAPTERS["nbp_policy"] is NBPAdapter
    measure = INDICATORS["nbp_reference_rate_decision"]
    assert (measure.issuer_id, measure.geography, measure.frequency, measure.tier) == ("NBP", "POL", "E", "T1")
    assert FAMILY_INDICATORS["nbp_policy"] == {m.id for m in NBP_FAMILIES["nbp_policy"]}
    assert "nbp_reference_rate_decision" in SANITY_BANDS and "nbp_reference_rate_decision" in POLICY_INDICATORS
    assert indicator_card("nbp_reference_rate_decision").key == "pl_nbp"


def test_release_concept_is_the_issuer_rate():
    from market_data_hub.concepts import load_concepts
    spec = load_concepts()["nbp_reference_rate_decision"]
    [source] = spec["sources"]
    assert (source["kind"], source["id"], source["role"]) == ("release", "nbp_reference_rate_decision", "primary")
    assert "evidence" not in source and spec["calendar"] == "country:PL"


def test_persisted_as_verified_issuer_evidence(tmp_path):
    rows = parse(document())
    with open_writer(tmp_path / "macro.sqlite") as store:
        persist(store, rows)
        series = store.conn.execute(
            "SELECT indicator_id, issuer_id, source_id, evidence_role FROM source_series").fetchall()
        assert [tuple(r) for r in series] == [("nbp_reference_rate_decision", "NBP", "NBP", "issuer")]
        statuses = {r[0] for r in store.conn.execute("SELECT verification_status FROM value_vintages")}
        assert statuses == {"verified"}
        before = store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0]
        persist(store, [replace(o, document=replace(o.document, retrieved_at="2026-11-12T09:00:00Z")) for o in rows])
        assert store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] == before


def test_cascade_reads_the_rate_held_since_march(tmp_path):
    from market_data_hub import latest
    path = tmp_path / "macro.sqlite"
    with open_writer(path) as store:
        persist(store, parse(document()))
    result = latest.read_latest("nbp_reference_rate_decision", as_of="2026-11-12", releases_db=str(path),
                                as_known_at="2026-11-12T12:00:00Z")
    assert (result["status"], result["source_kind"]) == ("primary", "release"), result
    assert (result["reference_period"], result["value"]) == ("2026-03-05", 3.75)


def test_manual_collect_persists_the_rate(tmp_path, monkeypatch, capsys):
    from market_data_hub.macro_releases import collect
    monkeypatch.setattr(collect, "HttpClient", lambda *a, **k: FakeClient())
    assert collect.main(["--db", str(tmp_path / "macro.sqlite"), "--families", "nbp_policy",
                         "--since", "2026-01"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["families"]["NBPAdapter"]["new_vintages"] == 1
