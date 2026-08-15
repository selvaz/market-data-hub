# -*- coding: utf-8 -*-
"""Economic calendar tests: catalogue, ingestion, consolidation."""
from datetime import date, datetime

import duckdb
import pytest

from market_data_hub.db import connection as cx
from market_data_hub.econ_calendar import (
    CalendarObservation,
    ingest_observations,
    load_catalog_rows,
    make_event_id,
    upsert_indicators,
)
from market_data_hub.econ_calendar.audit import suspect_matches
from market_data_hub.econ_calendar.catalog import to_iso3
from market_data_hub.econ_calendar.ingest import parse_number


@pytest.fixture()
def con():
    c = duckdb.connect(":memory:")
    cx.migrate(c)
    yield c
    c.close()


def _obs(source, provenance="aggregator", **kw):
    base = dict(
        indicator_key="us_cpi_yy",
        country_iso3="USA",
        source=source,
        provenance=provenance,
        source_event_name="CPI y/y",
        release_utc=datetime(2026, 8, 12, 12, 30),
        reference_period="Jul",
        reference_date=date(2026, 7, 31),
        vintage_date=date(2026, 8, 12),
    )
    base.update(kw)
    return CalendarObservation(**base)


# ------------------------------------------------------------------ schema --
def test_migration_creates_the_tables(con):
    tables = {r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_name LIKE 'calendar%'").fetchall()}
    assert tables == {"calendar_indicators", "calendar_events",
                       "calendar_observations", "calendar_event_notes"}


# ----------------------------------------------------------------- paesi ----
def test_euro_area_becomes_emu():
    assert to_iso3("EZ") == "EMU"
    assert to_iso3("EU") == "EMU"
    assert to_iso3("US") == "USA"


def test_unmapped_country_raises_instead_of_passing():
    # an unmapped country would break the macro_panel join without saying so
    with pytest.raises(ValueError, match="no ISO3 mapping"):
        to_iso3("ZZ")


# --------------------------------------------------------------- catalogo --
def test_catalogue_loads_and_flattens_archetypes(con):
    rows = load_catalog_rows()
    assert len(rows) > 100
    n = upsert_indicators(con, rows)
    assert n == len(rows)
    # the description comes from the archetype, not from the row itself
    d = con.execute(
        "SELECT description, criticality, country_iso3 FROM calendar_indicators "
        "WHERE indicator_key = 'us_cpi_yy'").fetchone()
    assert d[0] and "consumer inflation" in d[0]
    assert d[1] == "T1"
    assert d[2] == "USA"
    # and the euro area is EMU
    assert con.execute(
        "SELECT DISTINCT country_iso3 FROM calendar_indicators WHERE area = 'EZ' "
        "AND country_iso2 = 'EU'").fetchone()[0] == "EMU"


def test_catalogue_upsert_is_idempotent(con):
    rows = load_catalog_rows()
    upsert_indicators(con, rows)
    upsert_indicators(con, rows)
    assert con.execute("SELECT count(*) FROM calendar_indicators").fetchone()[0] == len(rows)


# ------------------------------------------------------------------ numeri --
@pytest.mark.parametrize("testo, atteso", [
    ("-4.9%", -4.9), ("213 K", 213_000.0), ("$-73.261 B", -73.261e9),
    ("2.448%", 2.448), ("N/D", None), ("", None), (None, None),
])
def test_parse_number(testo, atteso):
    assert parse_number(testo) == atteso


def test_scale_does_not_confuse_millions_and_billions():
    assert parse_number("2.5 M") != parse_number("2.5 B")


# ------------------------------------------------------------- ingestione --
def test_event_id_is_stable_across_collectors():
    # two collectors seeing the same release minutes apart must produce the
    # same key, otherwise the event splits in two
    a = make_event_id("us_cpi_yy", datetime(2026, 8, 12, 12, 30))
    b = make_event_id("us_cpi_yy", datetime(2026, 8, 12, 12, 33))
    assert a == b
    assert a != make_event_id("us_cpi_yy", datetime(2026, 8, 13, 12, 30))


def test_ingestion_writes_observations_and_event(con):
    upsert_indicators(con, load_catalog_rows())
    outcome = ingest_observations(con, [
        _obs("tradays", actual="3.4%", consensus="2.7%", previous="3.5%", impact="high"),
        _obs("nasdaq", actual="3.4%", consensus="2.9%"),
    ])
    assert outcome == {"observations": 2, "events": 1, "revised": 0}
    e = con.execute(
        "SELECT actual, actual_num, consensus, consensus_source, n_sources, "
        "values_agree, status FROM calendar_events").fetchone()
    assert e[0] == "3.4%" and e[1] == 3.4
    assert e[2] == "2.7%" and e[3] == "tradays"   # consensus from ONE named source
    assert e[4] == 2 and e[5] is True and e[6] == "released"


def test_issuing_agency_beats_aggregator_on_the_value(con):
    upsert_indicators(con, load_catalog_rows())
    ingest_observations(con, [
        _obs("tradays", actual="3.4%"),
        _obs("bls", provenance="official", source_event_name="CPI", actual="3.41%"),
    ])
    e = con.execute(
        "SELECT actual, actual_source, actual_provenance FROM calendar_events").fetchone()
    assert e == ("3.41%", "bls", "official")


def test_consensus_does_not_fall_back_to_another_provider(con):
    # if the canonical source has no consensus the field stays empty: mixing
    # survey providers manufactures surprises
    upsert_indicators(con, load_catalog_rows())
    ingest_observations(con, [_obs("nasdaq", actual="3.4%", consensus="2.9%")])
    e = con.execute("SELECT consensus, consensus_source FROM calendar_events").fetchone()
    assert e == (None, None)


def test_revision_does_not_overwrite_the_previous_version(con):
    upsert_indicators(con, load_catalog_rows())
    ingest_observations(con, [_obs("tradays", actual="3.4%")])
    outcome = ingest_observations(con, [
        _obs("tradays", actual="3.5%", vintage_date=date(2026, 9, 10))])
    assert outcome["revised"] == 1
    history = con.execute(
        "SELECT vintage_date, actual, change_type, prior_actual FROM calendar_observations "
        "ORDER BY vintage_date").fetchall()
    assert len(history) == 2
    assert history[0][1] == "3.4%" and history[0][2] == "new"
    assert history[1][1] == "3.5%" and history[1][2] == "revised" and history[1][3] == "3.4%"
    # the consolidated event reflects the latest version
    assert con.execute("SELECT actual FROM calendar_events").fetchone()[0] == "3.5%"


def test_scheduled_event_has_a_period_but_no_value(con):
    upsert_indicators(con, load_catalog_rows())
    ingest_observations(con, [
        _obs("yahoo", release_utc=datetime(2026, 9, 10, 12, 30),
             vintage_date=date(2026, 8, 14), previous="3.4%")])
    e = con.execute(
        "SELECT status, reference_period, actual, previous FROM calendar_events").fetchone()
    assert e[0] == "scheduled"
    assert e[1] == "Jul" and e[2] is None and e[3] == "3.4%"


def test_source_disagreement_is_flagged_not_smoothed(con):
    upsert_indicators(con, load_catalog_rows())
    ingest_observations(con, [
        _obs("tradays", actual="3.4%"),
        _obs("nasdaq", actual="10.2%"),
    ])
    assert con.execute("SELECT values_agree FROM calendar_events").fetchone()[0] is False


def test_unknown_provenance_is_rejected():
    with pytest.raises(ValueError, match="unknown provenance"):
        _obs("tradays", provenance="made_up")

# --- cases found by the integration run over the five real sources ---------
def test_sign_is_not_lost_to_the_currency_symbol():
    # '-$101.5B': searching for the number alone skips the minus, and a trade
    # balance with a flipped sign is caught by nothing downstream
    assert parse_number("-$101.5B") == -101.5e9
    assert parse_number("$​-101.461 B") == pytest.approx(-101.461e9)


def test_invisible_spaces_inside_numbers():
    # sources embed non-breaking and zero-width spaces in the values
    assert parse_number("¥​1.610 T") == pytest.approx(1.610e12)


def test_a_different_scale_is_not_a_disagreement(con):
    # Tradays writes '75.1 K' where Yahoo writes '75.1': same figure
    upsert_indicators(con, load_catalog_rows())
    ingest_observations(con, [
        _obs("tradays", actual="75.1 K"),
        _obs("yahoo", actual="75.1"),
    ])
    assert con.execute("SELECT values_agree FROM calendar_events").fetchone()[0] is True


def test_opposite_signs_remain_a_disagreement(con):
    upsert_indicators(con, load_catalog_rows())
    ingest_observations(con, [
        _obs("tradays", actual="1.5"),
        _obs("yahoo", actual="-1.5"),
    ])
    assert con.execute("SELECT values_agree FROM calendar_events").fetchone()[0] is False


def test_reference_date_comes_from_the_source_that_has_one(con):
    # Tradays non pubblica il periodo, Yahoo si': l'evento deve prendere quello
    # di Yahoo invece di ereditare il vuoto dalla prima osservazione vista
    upsert_indicators(con, load_catalog_rows())
    ingest_observations(con, [
        _obs("tradays", reference_period=None, reference_date=None, actual="3.4%"),
        _obs("yahoo", reference_period="Jul", reference_date=date(2026, 7, 31)),
    ])
    e = con.execute(
        "SELECT reference_period, reference_date FROM calendar_events").fetchone()
    assert e == ("Jul", date(2026, 7, 31))


def test_one_release_seen_at_different_hours_stays_one_event(con):
    """Sources disagree on the time: when the gap straddles midnight the same
    release becomes two events, each enriched separately. Real case: an RBA
    decision seen at 21:30, at 04:30 and at 00:30."""
    upsert_indicators(con, load_catalog_rows())
    base = dict(indicator_key="au_rba", country_iso3="AUS",
                source_event_name="RBA Interest Rate Decision",
                vintage_date=date(2026, 8, 14))
    ingest_observations(con, [
        CalendarObservation(source="myfxbook", provenance="aggregator",
                            release_utc=datetime(2026, 8, 10, 21, 30),
                            actual="4.35%", **base),
        CalendarObservation(source="tradays", provenance="aggregator",
                            release_utc=datetime(2026, 8, 11, 4, 30),
                            actual="4.35", **base),
        CalendarObservation(source="nasdaq", provenance="aggregator",
                            release_utc=datetime(2026, 8, 11, 12, 30),
                            actual="4.35%", **base),
    ])
    events = con.execute(
        "SELECT count(*), max(n_sources) FROM calendar_events "
        "WHERE indicator_key = 'au_rba'").fetchone()
    assert events == (1, 3)


def test_distinct_releases_stay_distinct(con):
    """The tolerance window must not merge two real releases: US jobless claims
    come out weekly, and two weeks are two events."""
    upsert_indicators(con, load_catalog_rows())
    base = dict(indicator_key="us_claims", country_iso3="USA",
                source_event_name="Initial Jobless Claims",
                provenance="aggregator", source="tradays",
                vintage_date=date(2026, 8, 14))
    ingest_observations(con, [
        CalendarObservation(release_utc=datetime(2026, 8, 6, 12, 30), actual="199K", **base),
        CalendarObservation(release_utc=datetime(2026, 8, 13, 12, 30), actual="209K", **base),
    ])
    n = con.execute("SELECT count(*) FROM calendar_events "
                    "WHERE indicator_key = 'us_claims'").fetchone()[0]
    assert n == 2


# --- audit: is the matched row actually naming the indicator it was bound to --
def _bind(con, indicator_key, source, name, day):
    """One observation filed under `indicator_key` under the name `name`."""
    ingest_observations(con, [CalendarObservation(
        indicator_key=indicator_key, country_iso3="USA", source=source,
        provenance="aggregator", source_event_name=name,
        release_utc=datetime(2026, 8, day, 12, 30), actual="0.1%",
        vintage_date=date(2026, 8, 14))])


def test_audit_stays_quiet_when_the_names_agree(con):
    upsert_indicators(con, load_catalog_rows())
    for i, src in enumerate(("tradays", "nasdaq", "yahoo")):
        _bind(con, "us_cpi_yy", src, "CPI y/y", 10 + i)
    assert suspect_matches(con) == []


def test_audit_catches_a_different_indicator_wearing_a_similar_name(con):
    """The case this module exists for: BLS 'Real Earnings' rides out with the
    CPI and was filed as Average Hourly Earnings, inheriting its T1 tier."""
    upsert_indicators(con, load_catalog_rows())
    for day in (3, 4, 5, 6):
        _bind(con, "us_earnings", "tradays", "Average Hourly Earnings y/y", day)
    _bind(con, "us_earnings", "tradays", "Real Earnings m/m", 12)

    trovati = suspect_matches(con)
    assert [s["indicator_key"] for s in trovati] == ["us_earnings"]
    assert trovati[0]["source_names"] == ["Real Earnings m/m"]
    assert "real" in trovati[0]["distinctive_words"]


def test_audit_does_not_flag_the_indicator_s_ordinary_name(con):
    """'HSBC India Manufacturing PMI' carries words the catalogue name lacks,
    but it is 4 observations in 5: it is what the source calls the indicator,
    not an intruder. Without the share ceiling the list drowns in these."""
    upsert_indicators(con, load_catalog_rows())
    for day in (3, 4, 5, 6):
        _bind(con, "in_pmi_mfg", "nasdaq", "HSBC India Manufacturing PMI", day)
    _bind(con, "in_pmi_mfg", "nasdaq", "Manufacturing PMI", 12)
    assert suspect_matches(con) == []


def test_audit_reads_through_the_abbreviations_sources_use(con):
    """'Initial Jobless Clm' is the same words shortened to fit a column.
    Flagging those buried the real errors under a hundred spelling variants."""
    upsert_indicators(con, load_catalog_rows())
    for day in (3, 4, 5, 6):
        _bind(con, "us_claims", "tradays", "Initial Jobless Claims", day)
    _bind(con, "us_claims", "tradays", "Initial Jobless Clm *", 12)
    assert suspect_matches(con) == []
