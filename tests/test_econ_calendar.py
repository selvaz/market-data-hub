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
from market_data_hub.econ_calendar.aliases import (
    cadence_violations,
    is_rejected,
    load_rejections,
    load_seed,
    normalize_name,
    propose,
    resolve,
    seed_from_observations,
    unmapped,
    upsert_alias,
)
from market_data_hub.econ_calendar.audit import (
    disagreeing_bindings,
    suspect_matches,
)
from market_data_hub.econ_calendar.catalog import to_iso3
from market_data_hub.econ_calendar.reference import (
    infer_reference_dates,
    learn_lags,
    validate_lags,
)
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
    assert tables == {"calendar_indicators", "calendar_indicator_aliases",
                      "calendar_events", "calendar_observations",
                      "calendar_event_notes"}


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


# --- aliases: what a source means by a name, decided once and recorded -------
def test_normalization_keeps_what_separates_indicators():
    """Gentler than the audit's: 'm/m' and 'y/y' are noise there and meaning
    here -- Mexico's core CPI m/m is not its core CPI y/y."""
    assert normalize_name("CPI y/y") != normalize_name("CPI m/m")
    # ... and drops only typography: revision marks, case, invisible spaces
    assert normalize_name("GDP Revised QQ *") == normalize_name("gdp  revised qq")
    assert normalize_name("Trade\u200bBalance\u00a0") == "trade balance"


def test_alias_resolves_only_what_was_confirmed(con):
    upsert_indicators(con, load_catalog_rows())
    upsert_alias(con, source="tradays", country_iso3="USA",
                 source_name="Real Earnings m/m", indicator_key="us_earnings",
                 status="proposed")
    # a proposal is a work queue, not a binding
    assert resolve(con, "tradays", "USA", "Real Earnings m/m") is None
    upsert_alias(con, source="tradays", country_iso3="USA",
                 source_name="Average Hourly Earnings y/y",
                 indicator_key="us_earnings")
    assert resolve(con, "tradays", "USA", "Average Hourly Earnings y/y") == "us_earnings"
    # and it cannot generalise the way the regex did
    assert resolve(con, "tradays", "USA", "Real Weekly Earnings MM") is None


def test_alias_is_keyed_on_the_country_too(con):
    """'CPI y/y' on Tradays means eleven different indicators, one per country.
    Without the country in the key the table would be unusable."""
    upsert_indicators(con, load_catalog_rows())
    upsert_alias(con, source="tradays", country_iso3="USA",
                 source_name="CPI y/y", indicator_key="us_cpi_yy")
    upsert_alias(con, source="tradays", country_iso3="IND",
                 source_name="CPI y/y", indicator_key="in_cpi_yy")
    assert resolve(con, "tradays", "USA", "CPI y/y") == "us_cpi_yy"
    assert resolve(con, "tradays", "IND", "CPI y/y") == "in_cpi_yy"


def test_unknown_status_is_rejected(con):
    with pytest.raises(ValueError, match="unknown alias status"):
        upsert_alias(con, source="tradays", country_iso3="USA",
                     source_name="CPI y/y", indicator_key="us_cpi_yy",
                     status="maybe")


def test_proposal_does_not_overwrite_a_decision(con):
    """A rejection that quietly turned back into a proposal would be the same
    silent drift the alias table exists to stop."""
    upsert_indicators(con, load_catalog_rows())
    upsert_alias(con, source="tradays", country_iso3="USA",
                 source_name="Real Earnings m/m", indicator_key=None,
                 status="rejected", note="CPI-deflated earnings, a different series")
    assert propose(con, source="tradays", country_iso3="USA",
                   source_name="Real Earnings m/m",
                   indicator_key="us_earnings") is False
    riga = con.execute("SELECT status, indicator_key FROM calendar_indicator_aliases"
                       ).fetchone()
    assert riga == ("rejected", None)


def test_unmapped_names_are_reported_not_dropped(con):
    """An unknown name yields no event instead of a wrong one. That is the
    better failure, not a harmless one, so it has to be visible."""
    upsert_indicators(con, load_catalog_rows())
    propose(con, source="tradays", country_iso3="USA",
            source_name="Some Brand New Print", indicator_key="us_cpi_yy")
    righe = unmapped(con)
    assert len(righe) == 1
    assert righe[0]["source_name"] == "Some Brand New Print"
    assert righe[0]["suggested"] == "us_cpi_yy"


def test_seed_takes_the_bindings_already_in_the_calendar(con):
    upsert_indicators(con, load_catalog_rows())
    ingest_observations(con, [_obs("tradays", actual="3.4%"),
                              _obs("nasdaq", actual="3.4%")])
    assert seed_from_observations(con, decided_by="test") == 2
    assert resolve(con, "tradays", "USA", "CPI y/y") == "us_cpi_yy"


def test_cadence_catches_what_source_agreement_cannot(con):
    """The Real Earnings case: all three sources named it the same way and all
    three agreed on the value, so cross-checking them would have confirmed the
    wrong binding. Two monthly prints in one August is a contradiction no
    amount of agreement excuses."""
    upsert_indicators(con, load_catalog_rows())
    base = dict(indicator_key="us_earnings", country_iso3="USA",
                source_event_name="Average Hourly Earnings", provenance="aggregator",
                source="tradays", vintage_date=date(2026, 8, 14))
    ingest_observations(con, [
        CalendarObservation(release_utc=datetime(2026, 8, 7, 12, 30), actual="3.2%", **base),
        CalendarObservation(release_utc=datetime(2026, 8, 12, 12, 30), actual="0.0%", **base),
    ])
    fuori = cadence_violations(con, indicator_keys=["us_earnings"])
    assert len(fuori) == 1
    assert fuori[0]["releases"] == 2 and fuori[0]["expected"] == 1
    assert fuori[0]["period"] == "2026-08-01"


def test_cadence_quiet_when_the_indicator_behaves(con):
    upsert_indicators(con, load_catalog_rows())
    base = dict(indicator_key="us_earnings", country_iso3="USA",
                source_event_name="Average Hourly Earnings", provenance="aggregator",
                source="tradays", vintage_date=date(2026, 8, 14))
    ingest_observations(con, [
        CalendarObservation(release_utc=datetime(2026, 7, 3, 12, 30), actual="3.5%", **base),
        CalendarObservation(release_utc=datetime(2026, 8, 7, 12, 30), actual="3.2%", **base),
    ])
    assert cadence_violations(con, indicator_keys=["us_earnings"]) == []


# --- the strongest binding detector: sources that disagree on the number -----
def test_disagreement_names_both_readings(con):
    """46 disagreements in the first full load, 46 of them binding errors.
    The report has to show the names beside the values, because it is reading
    them together that says which series each source actually filed."""
    upsert_indicators(con, load_catalog_rows())
    base = dict(indicator_key="au_wages", country_iso3="AUS",
                provenance="aggregator", release_utc=datetime(2026, 5, 13, 1, 30),
                vintage_date=date(2026, 8, 14))
    ingest_observations(con, [
        CalendarObservation(source="tradays", source_event_name="Wage Price Index y/y",
                            actual="3.3%", **base),
        CalendarObservation(source="nasdaq", source_event_name="Wage Price Index",
                            actual="0.8%", **base),
    ])
    fuori = disagreeing_bindings(con)
    assert len(fuori) == 1
    assert fuori[0]["distinct_names"] == 2      # two names: a binding problem
    letture = {r["source"]: (r["source_name"], r["actual"]) for r in fuori[0]["readings"]}
    assert letture["nasdaq"] == ("Wage Price Index", "0.8%")


def test_disagreement_under_one_name_is_marked_differently(con):
    """One name and two numbers is a data problem, not a binding one, and the
    caller must not go looking for a series that is not there."""
    upsert_indicators(con, load_catalog_rows())
    ingest_observations(con, [_obs("tradays", actual="3.4%"),
                              _obs("nasdaq", actual="9.9%")])
    fuori = disagreeing_bindings(con)
    assert len(fuori) == 1 and fuori[0]["distinct_names"] == 1


def test_agreeing_sources_produce_no_finding(con):
    upsert_indicators(con, load_catalog_rows())
    ingest_observations(con, [_obs("tradays", actual="3.4%"),
                              _obs("nasdaq", actual="3.4%")])
    assert disagreeing_bindings(con) == []


def test_seed_file_carries_the_per_source_decisions(con):
    """A ruling that evaporates when the database is rebuilt is not a ruling,
    so the decisions live in a file beside the catalogue."""
    upsert_indicators(con, load_catalog_rows())
    assert load_seed(con) > 0
    # Nasdaq publishes housing starts as a level where the indicator is m/m
    assert is_rejected(con, "nasdaq", "USA", "Housing Starts")
    assert not is_rejected(con, "tradays", "USA", "Housing Starts m/m")
    # rejection is not the same as never seen: resolve() returns None for both
    assert resolve(con, "nasdaq", "USA", "Housing Starts") is None
    assert ("nasdaq", "USA", "housing starts") in load_rejections(con)


# --- reference period: derived where no source publishes one ----------------
def _rilascio(con, indicator_key, release, *, period=None, ref=None, source="tradays"):
    ingest_observations(con, [CalendarObservation(
        indicator_key=indicator_key, country_iso3="USA", source=source,
        provenance="aggregator", source_event_name="x", release_utc=release,
        reference_period=period, reference_date=ref, actual="0.1%",
        vintage_date=date(2026, 8, 14))])


def test_lag_is_learned_per_indicator_not_per_frequency(con):
    """US CPI and euro-area industrial production are both monthly and their
    lags are 1 and 2. A frequency-wide default is wrong for one of them every
    month, which is why the lag is learned per indicator."""
    upsert_indicators(con, load_catalog_rows())
    for mese, fine in ((6, date(2026, 5, 31)), (7, date(2026, 6, 30))):
        _rilascio(con, "us_cpi_yy", datetime(2026, mese, 12, 12, 30), ref=fine)
    for mese, fine in ((6, date(2026, 4, 30)), (7, date(2026, 5, 31))):
        _rilascio(con, "ez_indprod", datetime(2026, mese, 14, 9, 0), ref=fine)

    imparati = learn_lags(con)
    assert imparati["us_cpi_yy"]["lag_months"] == 1
    assert imparati["ez_indprod"]["lag_months"] == 2
    assert imparati["us_cpi_yy"]["stable"] and imparati["ez_indprod"]["stable"]


def test_inference_marks_what_it_writes(con):
    """A derived period must never be indistinguishable from a published one:
    a backtest joining on dates nobody published has no way to know."""
    upsert_indicators(con, load_catalog_rows())
    for mese, fine in ((6, date(2026, 5, 31)), (7, date(2026, 6, 30))):
        _rilascio(con, "us_cpi_yy", datetime(2026, mese, 12, 12, 30), ref=fine)
    _rilascio(con, "us_cpi_yy", datetime(2026, 8, 12, 12, 30))   # no period given

    esito = infer_reference_dates(con)
    assert esito["events_filled"] == 1
    righe = dict(con.execute(
        "SELECT reference_date_origin, count(*) FROM calendar_events "
        "WHERE reference_date IS NOT NULL GROUP BY 1").fetchall())
    assert righe == {"source": 2, "inferred": 1}
    # August release, lag 1 -> July, and the period is its LAST day
    assert con.execute(
        "SELECT reference_date FROM calendar_events "
        "WHERE reference_date_origin = 'inferred'").fetchone()[0] == date(2026, 7, 31)


def test_unstable_lag_is_left_alone(con):
    """Where the observed lag contradicts itself the series is usually mixing
    two releases; deriving from a contradiction writes a confident wrong date."""
    upsert_indicators(con, load_catalog_rows())
    _rilascio(con, "ez_unemp", datetime(2026, 6, 2, 9, 0), ref=date(2026, 4, 30))
    _rilascio(con, "ez_unemp", datetime(2026, 7, 2, 9, 0), ref=date(2026, 6, 30))
    _rilascio(con, "ez_unemp", datetime(2026, 8, 4, 9, 0))

    assert learn_lags(con)["ez_unemp"]["stable"] is False
    assert infer_reference_dates(con)["events_filled"] == 0
    assert infer_reference_dates(con, only_stable=False)["events_filled"] == 1


def test_weekly_and_policy_events_are_not_derived(con):
    """Jobless claims refer to a week ending on a given day, which a lag in
    months cannot express; a rate decision describes no period at all."""
    upsert_indicators(con, load_catalog_rows())
    for giorno in (6, 13, 20):
        _rilascio(con, "us_claims", datetime(2026, 8, giorno, 12, 30))
    _rilascio(con, "us_fomc", datetime(2026, 7, 29, 18, 0))
    assert infer_reference_dates(con)["events_filled"] == 0


def test_inference_does_not_learn_from_itself(con):
    """Once a date is inferred it must not become evidence for the next lag,
    or a single wrong derivation propagates into the rule that produced it."""
    upsert_indicators(con, load_catalog_rows())
    for mese, fine in ((6, date(2026, 5, 31)), (7, date(2026, 6, 30))):
        _rilascio(con, "us_cpi_yy", datetime(2026, mese, 12, 12, 30), ref=fine)
    _rilascio(con, "us_cpi_yy", datetime(2026, 8, 12, 12, 30))
    infer_reference_dates(con)
    assert learn_lags(con)["us_cpi_yy"]["events"] == 2      # not 3
