# -*- coding: utf-8 -*-
"""Economic calendar tests: catalogue, ingestion, consolidation."""
from calendar import monthrange
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
from market_data_hub.econ_calendar.catalog import (
    available_series,
    catalogue_vocabulary,
    to_iso3,
)
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
        _obs("myfxbook", actual="3.4%", consensus="2.7%", previous="3.5%", impact="high"),
        _obs("nasdaq", actual="3.4%", consensus="2.9%"),
    ])
    assert outcome["observations"] == 2 and outcome["events"] == 1
    assert outcome["revised"] == 0
    assert outcome["rejected_by_alias"] == 0 and outcome["redirected_by_alias"] == 0
    e = con.execute(
        "SELECT actual, actual_num, consensus, consensus_source, n_sources, "
        "values_agree, status FROM calendar_events").fetchone()
    assert e[0] == "3.4%" and e[1] == 3.4
    assert e[2] == "2.7%" and e[3] == "myfxbook"   # consensus from ONE named source
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


def test_seed_file_carries_the_per_source_decisions(con):
    """A ruling that evaporates when the database is rebuilt is not a ruling,
    so the decisions live in a file beside the catalogue."""
    upsert_indicators(con, load_catalog_rows())
    assert load_seed(con) > 0
    # myfxbook's bare 'Capacity Utilization' was proposed for us_fomc by name
    # reconciliation alone -- capacity utilisation has nothing to do with the
    # FOMC decision.
    assert is_rejected(con, "myfxbook", "USA", "Capacity Utilization")
    assert not is_rejected(con, "myfxbook", "USA", "Inflation Rate YoY")
    # Event-link country routing now separates national EUR releases from
    # the genuine Euro Area aggregate, so these aggregate events are valid.
    assert not is_rejected(con, "myfxbook", "EMU", "GDP Growth Rate QoQ")
    assert not is_rejected(con, "myfxbook", "EMU", "Unemployment Rate")
    assert not is_rejected(con, "myfxbook", "EMU", "Industrial Production MoM")
    assert not is_rejected(con, "myfxbook", "EMU", "Industrial Sales MoM")
    assert not is_rejected(con, "myfxbook", "EMU", "Retail Sales MoM")
    # MyFXBook spells these CPI-family releases as inflation rates.  The
    # explicit, country-scoped aliases preserve headline/core/Tokyo distinctions.
    assert {
        (country, name): resolve(con, "myfxbook", country, name)
        for country, name in {
            ("BRA", "Inflation Rate YoY"), ("BRA", "Inflation Rate MoM"),
            ("DEU", "Inflation Rate YoY"), ("IND", "Inflation Rate YoY"),
            ("JPN", "Inflation Rate YoY"), ("JPN", "Core Inflation Rate YoY"),
            ("JPN", "Tokyo CPI YoY"), ("KOR", "Inflation Rate YoY"),
            ("MEX", "Inflation Rate YoY"), ("MEX", "Core Inflation Rate YoY"),
            ("TWN", "Inflation Rate YoY"), ("USA", "Core Inflation Rate MoM"),
        }
    } == {
        ("BRA", "Inflation Rate YoY"): "br_ipca_yy",
        ("BRA", "Inflation Rate MoM"): "br_ipca_mm",
        ("DEU", "Inflation Rate YoY"): "de_cpi_yy",
        ("IND", "Inflation Rate YoY"): "in_cpi_yy",
        ("JPN", "Inflation Rate YoY"): "jp_cpi_yy",
        ("JPN", "Core Inflation Rate YoY"): "jp_core_cpi",
        ("JPN", "Tokyo CPI YoY"): "jp_tokyo_cpi",
        ("KOR", "Inflation Rate YoY"): "kr_cpi_yy",
        ("MEX", "Inflation Rate YoY"): "mx_cpi_yy",
        ("MEX", "Core Inflation Rate YoY"): "mx_core_cpi",
        ("TWN", "Inflation Rate YoY"): "tw_cpi_yy",
        ("USA", "Core Inflation Rate MoM"): "us_core_cpi_mm",
    }
    # rejection is not the same as never seen: resolve() returns None for both
    assert resolve(con, "myfxbook", "USA", "Capacity Utilization") is None
    assert ("myfxbook", "USA", "capacity utilization") in load_rejections(con)


def test_reseeding_drops_a_rejection_removed_from_the_file(con, tmp_path):
    """A decision reversed in the file must stop applying, not linger.

    Caught live: PR #73 rejected four myfxbook/EMU names wholesale, #74
    lifted the rejection once the real fix landed -- but load_seed() only
    ever upserted what the file currently said, never deleted what it used
    to say. On a database that had run #73, the lifted rejection kept
    discarding real observations forever, because the table never heard the
    file changed its mind.
    """
    upsert_indicators(con, load_catalog_rows())
    seed_v1 = tmp_path / "aliases_v1.yaml"
    seed_v1.write_text(
        "rejections:\n"
        "- source: myfxbook\n"
        "  country_iso3: EMU\n"
        "  name: GDP Growth Rate QoQ\n"
        "  decided_by: test-v1\n"
        "bindings: []\n",
        encoding="utf-8",
    )
    load_seed(con, seed_v1)
    assert is_rejected(con, "myfxbook", "EMU", "GDP Growth Rate QoQ")

    seed_v2 = tmp_path / "aliases_v2.yaml"
    seed_v2.write_text("rejections: []\nbindings: []\n", encoding="utf-8")
    load_seed(con, seed_v2)
    assert not is_rejected(con, "myfxbook", "EMU", "GDP Growth Rate QoQ")
    assert resolve(con, "myfxbook", "EMU", "GDP Growth Rate QoQ") is None


def test_reseeding_leaves_proposed_rows_alone(con):
    """Reconciliation only ever touches what load_seed itself writes.

    A 'proposed' row -- a name the regex matched that nobody has ruled on
    yet -- is a different lifecycle (bare_alias / seed_from_observations),
    not a stale seed entry. Reseeding must not sweep it away just because
    it isn't in the file: a proposal awaiting a human is not the same thing
    as a decision that was reversed.
    """
    upsert_indicators(con, load_catalog_rows())
    propose(con, source="tradays", country_iso3="USA",
            source_name="Some Unruled Name", indicator_key="us_earnings",
            note="proposed by regex")
    load_seed(con)
    # Still proposed, not swept away for being absent from the seed file --
    # and still not resolve()-able, since a proposal is not a decision.
    assert any(r["source"] == "tradays" and r["source_name"] == "Some Unruled Name"
               for r in unmapped(con))
    assert resolve(con, "tradays", "USA", "Some Unruled Name") is None


def test_reseeding_leaves_observation_seeded_bindings_alone(con):
    """seed_from_observations() writes 'confirmed' rows too -- reconciliation
    must not treat every confirmed row as its own to delete.

    Found by Codex review on the first version of this reconciliation: it
    swept the whole table by status alone, so a binding bootstrapped from
    observed data (never recorded in the YAML at all) would have been
    deleted the moment anyone ran load_seed() again -- the very next regular
    pipeline run. Ownership is explicit rather than inferred from the
    caller-supplied decided_by field, so this remains true for any reviewer.
    """
    upsert_indicators(con, load_catalog_rows())
    ingest_observations(con, [CalendarObservation(
        indicator_key="us_earnings", country_iso3="USA", source="tradays",
        provenance="aggregator", source_event_name="Bootstrapped Name",
        release_utc=datetime(2026, 8, 14, 12, 30), actual="3.5%",
    )])
    assert seed_from_observations(con, decided_by="alice") > 0
    assert resolve(con, "tradays", "USA", "Bootstrapped Name") == "us_earnings"

    upsert_alias(
        con, source="tradays", country_iso3="USA", source_name="Direct Name",
        indicator_key="us_earnings", decided_by="alice",
    )
    assert resolve(con, "tradays", "USA", "Direct Name") == "us_earnings"

    load_seed(con)  # the file says nothing about this triple
    assert resolve(con, "tradays", "USA", "Bootstrapped Name") == "us_earnings"
    assert resolve(con, "tradays", "USA", "Direct Name") == "us_earnings"


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


def test_holdout_validation_reports_what_the_rule_is_worth(con):
    """The rule gets applied where no truth exists, so the only honest accuracy
    comes from hiding a known period, relearning without it, and comparing."""
    upsert_indicators(con, load_catalog_rows())
    for mese in (5, 6, 7, 8):
        fine = date(2026, mese - 1, monthrange(2026, mese - 1)[1])
        _rilascio(con, "us_cpi_yy", datetime(2026, mese, 12, 12, 30), ref=fine)

    esito = validate_lags(con)
    assert esito["tested"] == 4
    assert esito["accuracy"] == 1.0 and esito["wrong"] == 0

    # an indicator whose lag contradicts itself is where the errors land
    _rilascio(con, "ez_unemp", datetime(2026, 6, 2, 9, 0), ref=date(2026, 4, 30))
    _rilascio(con, "ez_unemp", datetime(2026, 7, 2, 9, 0), ref=date(2026, 6, 30))
    _rilascio(con, "ez_unemp", datetime(2026, 8, 4, 9, 0), ref=date(2026, 6, 30))
    dopo = validate_lags(con)
    assert dopo["wrong"] > 0
    assert {e["indicator_key"] for e in dopo["errors"]} == {"ez_unemp"}


# --- the point-in-time bridge: when did that value become public ------------
def test_bridge_dates_a_macro_panel_value(con):
    """The reason the calendar exists. macro_panel carries a month-end policy
    rate; the calendar knows which meeting set it and when that meeting spoke."""
    upsert_indicators(con, load_catalog_rows())
    con.execute(
        "INSERT INTO macro_panel (date, country_iso3, indicator_id, value, frequency) "
        "VALUES (?, 'USA', 'bis_policy_rate', 3.625, 'M')", [date(2026, 7, 31)])
    _rilascio(con, "us_fomc", datetime(2026, 7, 29, 18, 0))

    assert infer_reference_dates(con)["policy_events_dated"] == 1
    riga = con.execute(
        "SELECT reference_date, value, known_from, calendar_indicator_key "
        "FROM v_macro_panel_asof WHERE country_iso3 = 'USA'").fetchone()
    assert riga[0] == date(2026, 7, 31) and riga[1] == 3.625
    assert riga[2] == datetime(2026, 7, 29, 18, 0)
    assert riga[3] == "us_fomc"


def test_bridge_leaves_a_month_without_a_meeting_undated(con):
    """A rate that simply persisted was published by nobody that month, and the
    bridge has to say so rather than attribute it to the previous decision."""
    upsert_indicators(con, load_catalog_rows())
    for giorno in (date(2026, 7, 31), date(2026, 8, 31)):
        con.execute(
            "INSERT INTO macro_panel (date, country_iso3, indicator_id, value, frequency) "
            "VALUES (?, 'USA', 'bis_policy_rate', 3.625, 'M')", [giorno])
    _rilascio(con, "us_fomc", datetime(2026, 7, 29, 18, 0))
    infer_reference_dates(con)

    righe = dict(con.execute(
        "SELECT reference_date, known_from FROM v_macro_panel_asof").fetchall())
    assert righe[date(2026, 7, 31)] is not None
    assert righe[date(2026, 8, 31)] is None


def test_policy_dates_are_only_given_where_a_bridge_exists(con):
    """A period invented for an event nothing joins to is just a wrong field.
    The ECB decision has no macro_indicator_id -- macro_panel carries DEU, not
    EMU -- so it keeps no reference_date."""
    upsert_indicators(con, load_catalog_rows())
    ingest_observations(con, [CalendarObservation(
        indicator_key="ez_ecb", country_iso3="EMU", source="tradays",
        provenance="aggregator", source_event_name="ECB Rate Decision",
        release_utc=datetime(2026, 7, 23, 12, 15), actual="2.4%",
        vintage_date=date(2026, 8, 14))])
    infer_reference_dates(con)
    assert con.execute(
        "SELECT reference_date FROM calendar_events "
        "WHERE indicator_key = 'ez_ecb'").fetchone()[0] is None


# --- what the Codex review found -------------------------------------------
def test_consensus_survives_the_release_that_overwrites_it(con):
    """Providers replace the forecast with the printed value once a release
    lands, and vintage_date has day granularity, so both captures are the same
    row. Losing the earlier one makes every later surprise zero."""
    upsert_indicators(con, load_catalog_rows())
    ingest_observations(con, [_obs("myfxbook", consensus="2.7%")])           # before
    ingest_observations(con, [_obs("myfxbook", actual="3.4%", consensus=None)])  # after

    e = con.execute("SELECT actual, consensus FROM calendar_events").fetchone()
    assert e == ("3.4%", "2.7%")


def test_consensus_comes_from_the_oldest_version_not_the_newest(con):
    """Same defect one day later: the provider's post-release row carries the
    print in the consensus field, and reading the newest version takes that
    replacement for an expectation."""
    upsert_indicators(con, load_catalog_rows())
    ingest_observations(con, [_obs("myfxbook", consensus="2.7%",
                                   vintage_date=date(2026, 8, 11))])
    ingest_observations(con, [_obs("myfxbook", actual="3.4%", consensus="3.4%",
                                   vintage_date=date(2026, 8, 12))])
    assert con.execute("SELECT consensus FROM calendar_events").fetchone()[0] == "2.7%"


def test_a_known_minute_outranks_a_day_only_placeholder(con):
    """A source publishing only a date arrives as midnight. Taking the earliest
    timestamp would record midnight as the release instant, and the bridge then
    reports a figure as public hours before it was."""
    upsert_indicators(con, load_catalog_rows())
    ingest_observations(con, [
        _obs("nasdaq", release_utc=datetime(2026, 8, 12, 0, 0),
             release_precision="day", actual="3.4%"),
        _obs("tradays", release_utc=datetime(2026, 8, 12, 12, 30),
             release_precision="minute", actual="3.4%"),
    ])
    e = con.execute(
        "SELECT release_utc, release_precision FROM calendar_events").fetchone()
    assert e[0] == datetime(2026, 8, 12, 12, 30) and e[1] == "minute"


def test_day_only_stays_day_when_nobody_knows_the_minute(con):
    upsert_indicators(con, load_catalog_rows())
    ingest_observations(con, [_obs("nasdaq", release_utc=datetime(2026, 8, 12, 0, 0),
                                   release_precision="day", actual="3.4%")])
    assert con.execute(
        "SELECT release_precision FROM calendar_events").fetchone()[0] == "day"


def test_surprise_view_withholds_a_disputed_point(con):
    """consensus_disputed exists to stop a fabricated surprise; the view that
    publishes surprises has to honour it, or the flag protects nothing."""
    upsert_indicators(con, load_catalog_rows())
    ingest_observations(con, [
        _obs("myfxbook", actual="3.4%", consensus="2.7%"),
        _obs("nasdaq", actual="3.4%", consensus="3.4%"),
    ])
    r = con.execute(
        "SELECT consensus_disputed, surprise, surprise_rel, consensus_low, "
        "consensus_high FROM v_calendar_surprise").fetchone()
    assert r[0] is True
    assert r[1] is None and r[2] is None       # no point surprise
    assert r[3] == 2.7 and r[4] == 3.4         # but the range is still there


def test_ingestion_enforces_the_alias_decisions(con):
    """Matching lives in the collector, so a wrong indicator_key arrives
    already chosen. The alias table has to bite in the one place every writer
    passes through, or it documents a rule nothing applies."""
    upsert_indicators(con, load_catalog_rows())
    upsert_alias(con, source="nasdaq", country_iso3="USA",
                 source_name="Housing Starts", indicator_key=None,
                 status="rejected")
    upsert_alias(con, source="tradays", country_iso3="USA",
                 source_name="Real Earnings m/m", indicator_key="us_core_pce")

    esito = ingest_observations(con, [
        _obs("nasdaq", indicator_key="us_housing",
             source_event_name="Housing Starts", actual="1.177M"),
        _obs("tradays", indicator_key="us_earnings",
             source_event_name="Real Earnings m/m", actual="0.0%"),
    ])
    assert esito["rejected_by_alias"] == 1 and esito["redirected_by_alias"] == 1
    chiavi = {r[0] for r in con.execute(
        "SELECT indicator_key FROM calendar_events").fetchall()}
    assert chiavi == {"us_core_pce"}            # rejected row wrote nothing


def test_reseeding_does_not_resurrect_a_corrected_alias(con):
    """Re-seeding reads the OLD observations, still carrying the binding that
    was since corrected. An unconditional upsert would undo the decision."""
    upsert_indicators(con, load_catalog_rows())
    ingest_observations(con, [_obs("tradays", actual="3.4%")])
    seed_from_observations(con)
    upsert_alias(con, source="tradays", country_iso3="USA",
                 source_name="CPI y/y", indicator_key=None, status="rejected",
                 note="checked and refused")

    assert seed_from_observations(con) == 0
    riga = con.execute("SELECT status, indicator_key, note FROM "
                       "calendar_indicator_aliases").fetchone()
    assert riga[0] == "rejected" and riga[1] is None and riga[2] == "checked and refused"
    # ... unless somebody asks for it
    assert seed_from_observations(con, overwrite=True) == 1


def test_alias_status_actually_changes(con):
    """Guards the duckdb 1.4.x trap the repo has already paid for once: with a
    secondary index on a column, INSERT OR REPLACE keeps the OLD value there.
    An index on `status` therefore leaves a rejection reading 'confirmed', and
    every decision this table records would silently fail to land."""
    upsert_alias(con, source="tradays", country_iso3="USA",
                 source_name="CPI y/y", indicator_key="us_cpi_yy")
    upsert_alias(con, source="tradays", country_iso3="USA",
                 source_name="CPI y/y", indicator_key=None, status="rejected")
    assert con.execute(
        "SELECT status, indicator_key FROM calendar_indicator_aliases"
    ).fetchone() == ("rejected", None)
    assert resolve(con, "tradays", "USA", "CPI y/y") is None


def test_reconsolidation_can_change_an_indexed_event_column(con):
    """calendar_events is written with INSERT OR REPLACE and carries secondary
    indexes on release_utc, indicator_key and (country_iso3, reference_date).
    On duckdb 1.4.x that combination keeps the OLD value of an indexed column,
    so this asserts the ones a re-consolidation is expected to move actually
    move -- the alias table lost two decisions to exactly this."""
    upsert_indicators(con, load_catalog_rows())
    ingest_observations(con, [_obs("tradays", actual="3.4%",
                                   reference_date=date(2026, 7, 31))])
    ingest_observations(con, [_obs("tradays", actual="3.4%",
                                   reference_date=date(2026, 6, 30),
                                   vintage_date=date(2026, 9, 1))])
    assert con.execute(
        "SELECT reference_date FROM calendar_events").fetchone()[0] == date(2026, 6, 30)

    # and the release instant, which the precision rule has to be able to move
    ingest_observations(con, [_obs("nasdaq", actual="3.4%",
                                   release_utc=datetime(2026, 8, 12, 0, 0),
                                   release_precision="day",
                                   vintage_date=date(2026, 9, 2))])
    ingest_observations(con, [_obs("yahoo", actual="3.4%",
                                   release_utc=datetime(2026, 8, 12, 6, 0),
                                   release_precision="minute",
                                   vintage_date=date(2026, 9, 2))])
    assert con.execute("SELECT release_utc FROM calendar_events").fetchone()[0]         == datetime(2026, 8, 12, 6, 0)


# --- classification and discovery -------------------------------------------
def test_catalogue_carries_the_classification(con):
    upsert_indicators(con, load_catalog_rows())
    # a survey is soft, a price is hard, a decision is neither
    tipi = dict(con.execute(
        "SELECT indicator_key, data_type FROM calendar_indicators "
        "WHERE indicator_key IN ('ez_pmi_mfg', 'us_cpi_yy', 'us_fomc')").fetchall())
    assert tipi == {"ez_pmi_mfg": "soft", "us_cpi_yy": "hard", "us_fomc": None}
    # the two misfilings the classification pass corrected
    posti = dict(con.execute(
        "SELECT indicator_key, category FROM calendar_indicators "
        "WHERE indicator_key IN ('uk_psnb', 'cn_m2', 'us_retail')").fetchall())
    assert posti == {"uk_psnb": "Fiscal", "cn_m2": "Credit & money",
                     "us_retail": "Consumption"}


def test_cadence_ignores_indicators_that_publish_twice_by_design(con):
    """Euro-area HICP prints a flash and a final every month. Flagging that was
    the largest single source of noise in the cadence report -- 106 of 221
    findings -- and it was correct behaviour being reported as a fault."""
    upsert_indicators(con, load_catalog_rows())
    base = dict(country_iso3="EMU", source="tradays", provenance="aggregator",
                source_event_name="HICP", vintage_date=date(2026, 8, 14),
                actual="2.0%")
    ingest_observations(con, [
        CalendarObservation(indicator_key="ez_hicp_yy",
                            release_utc=datetime(2026, 7, 31, 9, 0), **base),
        CalendarObservation(indicator_key="ez_hicp_yy",
                            release_utc=datetime(2026, 7, 17, 9, 0), **base),
    ])
    assert cadence_violations(con, indicator_keys=["ez_hicp_yy"]) == []


def test_a_third_release_is_still_caught_on_a_flash_final_indicator(con):
    """The tag buys one extra release, not silence.

    Skipping tagged indicators entirely made seventeen of them unauditable: a
    third print in one period -- the shape a bad alias binding takes, and the
    reason this check exists -- would have passed unseen on every one. Two is
    the flash and the final; three is a question.
    """
    upsert_indicators(con, load_catalog_rows())
    base = dict(country_iso3="EMU", source="tradays", provenance="aggregator",
                source_event_name="HICP", vintage_date=date(2026, 8, 14),
                actual="2.0%", indicator_key="ez_hicp_yy")
    ingest_observations(con, [
        CalendarObservation(release_utc=datetime(2026, 7, 17, 9, 0), **base),
        CalendarObservation(release_utc=datetime(2026, 7, 31, 9, 0), **base),
        CalendarObservation(release_utc=datetime(2026, 7, 24, 9, 0), **base),
    ])
    fuori = cadence_violations(con, indicator_keys=["ez_hicp_yy"])
    assert len(fuori) == 1, "a third release on a flash/final indicator went unreported"
    assert fuori[0]["indicator_key"] == "ez_hicp_yy"


# --- cadence, grouped by reference_date instead of the release calendar -----
def test_cadence_no_longer_confuses_two_periods_released_close_together(con):
    """The false positive this fix exists for: a revision of an OLDER period
    landing, release-wise, beside the fresh release of the NEXT one. Bucketing
    by release date merges them into one calendar-month bucket and reports two
    releases where each period individually only had one -- exactly the shape
    the EZ GDP q/q, EZ Unemployment Rate and South Korea Exports/Trade Balance
    false positives took in production."""
    upsert_indicators(con, load_catalog_rows())
    base = dict(indicator_key="us_cpi_yy", country_iso3="USA", source="myfxbook",
                provenance="aggregator", source_event_name="CPI y/y",
                vintage_date=date(2026, 8, 14), actual="3.1%")
    ingest_observations(con, [
        # June's data, released (late) on 10 July
        CalendarObservation(release_utc=datetime(2026, 7, 10, 12, 30),
                            reference_date=date(2026, 6, 30), **base),
        # July's data, released on 28 July -- same release month as above,
        # different reference period, more than 18h apart so a distinct event
        CalendarObservation(release_utc=datetime(2026, 7, 28, 12, 30),
                            reference_date=date(2026, 7, 31), **base),
    ])
    assert cadence_violations(con, indicator_keys=["us_cpi_yy"]) == []


def test_cadence_still_catches_a_real_violation_among_other_periods(con):
    """The fix must not hide a genuine violation behind a decoy period: two
    releases for the SAME reference period is still a contradiction, even
    when a different, legitimately single-released period sits beside it."""
    upsert_indicators(con, load_catalog_rows())
    base = dict(indicator_key="us_cpi_yy", country_iso3="USA", source="myfxbook",
                provenance="aggregator", source_event_name="CPI y/y",
                vintage_date=date(2026, 8, 14), actual="3.1%")
    ingest_observations(con, [
        # June's data, released twice more than 18h apart -- a genuine
        # duplicate/wrong-binding shape, not a flash/final (untagged indicator)
        CalendarObservation(release_utc=datetime(2026, 7, 5, 12, 30),
                            reference_date=date(2026, 6, 30), **base),
        CalendarObservation(release_utc=datetime(2026, 7, 20, 12, 30),
                            reference_date=date(2026, 6, 30), **base),
        # July's data, released once -- fine on its own
        CalendarObservation(release_utc=datetime(2026, 8, 3, 12, 30),
                            reference_date=date(2026, 7, 31), **base),
    ])
    fuori = cadence_violations(con, indicator_keys=["us_cpi_yy"])
    assert len(fuori) == 1
    assert fuori[0]["releases"] == 2 and fuori[0]["period"] == "2026-06-30"


def test_cadence_falls_back_to_the_release_bucket_without_a_reference_date(con):
    """Weekly indicators (and anything else the inference module cannot
    reach) never get a reference_date, so the release-date bucket -- the only
    check that existed before this fix -- has to keep working for them."""
    upsert_indicators(con, load_catalog_rows())
    base = dict(indicator_key="us_claims", country_iso3="USA", source="myfxbook",
                provenance="aggregator", source_event_name="Initial Jobless Claims",
                vintage_date=date(2026, 8, 14), actual="220K")
    ingest_observations(con, [
        # two releases in the same ISO week, no reference_date on either --
        # us_claims is weekly, excluded from reference-date inference
        CalendarObservation(release_utc=datetime(2026, 8, 3, 12, 30), **base),
        CalendarObservation(release_utc=datetime(2026, 8, 5, 12, 30), **base),
    ])
    assert con.execute(
        "SELECT count(*) FROM calendar_events WHERE reference_date IS NOT NULL"
    ).fetchone()[0] == 0
    fuori = cadence_violations(con, indicator_keys=["us_claims"])
    assert len(fuori) == 1 and fuori[0]["releases"] == 2


def test_discovery_answers_without_knowing_an_indicator_key(con):
    """The point of the function: an agent asks in the terms it thinks in."""
    upsert_indicators(con, load_catalog_rows())
    ingest_observations(con, [_obs("tradays", actual="3.4%")])

    inflazione = available_series(con, country="IND", category="Inflation")
    assert {s["indicator_key"] for s in inflazione} == {"in_cpi_yy", "in_wpi_yy"}
    # tracked but with no event: an answer, not a hidden row
    assert all(s["events"] == 0 for s in inflazione)

    giorno = available_series(con, day="2026-08-12", released_only=True)
    assert [s["indicator_key"] for s in giorno] == ["us_cpi_yy"]
    assert giorno[0]["events"] == 1 and giorno[0]["with_reference_date"] == 1

    soft = available_series(con, data_type="soft")
    assert soft and all(s["category"] == "Surveys" for s in soft)


def test_discovery_tag_filter_matches_whole_tags(con):
    """'hard' must not match 'hardship': the tags are pipe-delimited and the
    filter has to respect the delimiters, or a filter silently over-selects."""
    upsert_indicators(con, load_catalog_rows())
    flash = available_series(con, tags=["flash_final"])
    assert flash and all("flash_final" in (s["tags"] or "") for s in flash)
    assert available_series(con, tags=["flash"]) == []


def test_vocabulary_tells_a_caller_what_it_can_filter_on(con):
    """A filter over a closed vocabulary is unusable by someone who does not
    know the vocabulary, and a wrong guess returns an empty list that looks
    exactly like a legitimate 'nothing matched'."""
    upsert_indicators(con, load_catalog_rows())
    v = catalogue_vocabulary(con)
    assert v["data_type"] == {"hard": 111, "soft": 18}
    assert set(v["side"]) == {"demand", "supply"}
    assert v["category"]["Inflation"] == 29
    # tags are pipe-packed in the column and counted individually here
    assert "flash_final" in v["tags"] and "|" not in "".join(v["tags"])


def test_filters_are_case_insensitive(con):
    """An agent writing 'inflation' should get the answer, not silence."""
    upsert_indicators(con, load_catalog_rows())
    assert (len(available_series(con, category="inflation"))
            == len(available_series(con, category="Inflation")) == 29)
    assert len(available_series(con, country="usa")) == \
        len(available_series(con, country="USA"))


# ---------------------------------------------------------------------------
# Collection: the five defects Codex found on PR #59, each with the case that
# reproduces it. Every test below fails on the code as it was.
# ---------------------------------------------------------------------------


def test_anchor_utc_hour_follows_daylight_saving():
    """A US 08:30 ET print is 12:30 UTC in summer and 13:30 in winter.

    The anchors used to carry a fixed UTC hour, so a winter batch measured a
    one-hour offset against a correct source: tradays and yahoo were refused
    outright, and myfxbook took an offset one hour wrong -- silently, which is
    the failure this module was written to end.
    """
    from market_data_hub.econ_calendar.collect.timezones import ora_utc_ancora

    estate = ora_utc_ancora(8.5, 'America/New_York', date(2026, 8, 13))
    inverno = ora_utc_ancora(8.5, 'America/New_York', date(2026, 12, 10))
    assert estate == 12.5
    assert inverno == 13.5
    assert inverno - estate == 1.0


def test_winter_batch_measures_zero_offset_for_a_utc_source():
    """The regression in full: a correct UTC batch dated in December.

    With a fixed 12.5 anchor this measured +1.00 and `measure` refused the
    batch for a source that was right all along.
    """
    import pandas as pd

    from market_data_hub.econ_calendar.collect.timezones import measure

    inverno = pd.DataFrame({
        'Paese': ['US'] * 3,
        'Evento': ['CPI YY', 'Initial Jobless Claims', 'CPI YY'],
        # 08:30 New York in December is 13:30 UTC, and yahoo publishes UTC
        'Orario': ['13:30', '13:30', '13:30'],
        'Data_Rilascio': ['2026-12-10', '2026-12-17', '2026-12-24'],
    })
    assert measure(inverno, 'yahoo') == 0.0


def test_tradays_year_is_derived_not_fixed():
    """Tradays writes '13 August' with no year.

    It was forced to 2026, so from 2027 every newly collected release would be
    dated a year early, and a January batch -- which spans two years by
    construction -- misdated whichever half the constant did not name.
    """
    from market_data_hub.econ_calendar.collect.timezones import giorno_di

    # a January run reaching back thirteen weeks crosses into the year before
    gennaio = date(2027, 1, 15)
    assert giorno_di('15 January', gennaio) == date(2027, 1, 15)
    assert giorno_di('20 December', gennaio) == date(2026, 12, 20)
    # and the year is never hardwired to 2026
    assert giorno_di('13 August', date(2028, 9, 1)) == date(2028, 8, 13)

    # The symmetric case, and the one a "not far in the future" rule gets
    # wrong: read on New Year's Eve, '1 January' is tomorrow, not 364 days
    # back. Both directions have to work, so distance decides rather than a
    # rule about which way to lean.
    vigilia = date(2026, 12, 31)
    assert giorno_di('1 January', vigilia) == date(2027, 1, 1)
    assert giorno_di('30 December', vigilia) == date(2026, 12, 30)
    # 29 February exists only in a leap year: 2028 is one, 2027 is not
    assert giorno_di('29 February', date(2028, 3, 5)) == date(2028, 2, 29)


def test_observations_are_stamped_with_the_day_they_were_collected(tmp_path, monkeypatch):
    """`vintage_date` was pinned to `date(2026, 8, 14)` inside `raccogli`.

    Every run after that day therefore rewrote the same
    (event_id, source, vintage_date) row instead of adding one, so a revision
    was indistinguishable from the value it replaced and an as-of query could
    return a number from before it had been collected. This goes through
    `raccogli` itself, because the pin was there and not on the dataclass.
    """
    from market_data_hub.econ_calendar.collect.consolidate import raccogli

    monkeypatch.chdir(tmp_path)
    # 'CPI Inflation Rate YoY' does double duty: it is what myfxbook's timezone
    # anchor looks for ('Inflation Rate' at 08:30 America/New_York) AND what
    # the catalogue's match_rules for a *_cpi_yy indicator need ('cpi' and
    # 'yy' both present) -- 12:30 UTC is 08:30 EDT on 13 August 2026.
    (tmp_path / 'myfxbook.csv').write_text(
        'Data_Rilascio,Orario,Paese,Importanza,Evento,Periodo_Riferimento,'
        'Attuale,Previsto,Precedente,Revisione,Fonte\n'
        '2026-08-13,12:30,US,high,CPI Inflation Rate YoY,Jul,3.1,3.0,2.9,,MyFXBook\n',
        encoding='utf-8')

    catalogo = [r for r in load_catalog_rows()
                if r['country_iso3'] == 'USA' and 'cpi' in r['indicator_key'].lower()][:1]
    assert catalogo, 'serve almeno un indicatore USA nel catalogo'

    # Bracket the call: crossing UTC midnight mid-run must not fail the test,
    # but a pinned date outside the bracket still must.
    prima = datetime.utcnow().date()
    osservazioni, _ = raccogli(catalogo)
    dopo = datetime.utcnow().date()

    assert osservazioni, 'il csv di prova deve produrre almeno una osservazione'
    assert all(prima <= o.vintage_date <= dopo for o in osservazioni)


def test_resuming_myfxbook_keeps_iso_codes_already_written():
    """The whole column used to be mapped through a currency-keyed dictionary.

    Rows an earlier run had already converted ('US') matched no currency key
    and became empty, so each resumed run erased the country from everything
    collected before it, and consolidation then skipped those rows entirely.
    """
    import pandas as pd

    from market_data_hub.econ_calendar.collect.myfxbook import paese_iso

    assert paese_iso('USD') == 'US'          # fresh row, still a currency
    assert paese_iso('US') == 'US'           # row from an earlier run, kept
    assert paese_iso('EU') == 'EU'
    assert paese_iso('') == ''

    # the column as it really looks on a resumed run: both kinds at once
    misto = pd.Series(['USD', 'US', 'EUR', 'EU', 'GBP', 'GB'])
    assert list(misto.map(paese_iso)) == ['US', 'US', 'EU', 'EU', 'GB', 'GB']


def test_myfxbook_event_href_refines_eur_currency_to_issuer_country():
    """The visible EUR label must not turn German GDP into an EMU release."""
    pytest.importorskip("bs4", reason="leggi() parses with BeautifulSoup, part of the "
                         "optional [calendar] extra the base CI job does not install")
    from market_data_hub.econ_calendar.collect.myfxbook import leggi

    class Driver:
        page_source = '''
        <table>
          <tr>
            <td>Sep 07, 09:00</td><td></td><td></td><td>EUR</td>
            <td><a href="/forex-economic-calendar/euro-area/gdp-growth-rate-qoq">GDP Growth Rate QoQ (Q2)</a></td>
            <td>Low</td><td>0%</td><td>0.4%</td><td>0.4%</td>
          </tr>
          <tr>
            <td>Sep 25, 07:00</td><td></td><td></td><td>EUR</td>
            <td><a href="/forex-economic-calendar/germany/gdp-growth-rate-qoq">GDP Growth Rate QoQ (Q2)</a></td>
            <td>Low</td><td>0.4%</td><td>0.2%</td><td>0.3%</td>
          </tr>
        </table>'''

    rows = leggi(Driver(), 2026)
    assert [(r['Paese'], r['Evento'], r['Periodo_Riferimento']) for r in rows] == [
        ('EU', 'GDP Growth Rate QoQ', 'Q2'),
        ('DE', 'GDP Growth Rate QoQ', 'Q2'),
    ]


def test_myfxbook_unknown_eur_href_does_not_fall_back_to_emu():
    from market_data_hub.econ_calendar.collect.myfxbook import paese_da_href

    # An unmapped issuer remains visibly unmatched instead of corrupting EMU.
    assert paese_da_href('/forex-economic-calendar/cyprus/retail-sales-mom', 'EUR') == 'cyprus'


def test_recent_days_are_collected_again():
    """The registry skipped a date forever.

    Today is routinely collected before its own releases come out, and recent
    values get revised; with overlapping scheduled windows those observations
    were never refreshed. Only days older than the window stay skipped.
    """
    from datetime import timedelta as td

    from market_data_hub.econ_calendar.collect.myfxbook import da_ricollezionare

    oggi = date(2026, 8, 16)
    registro = {(oggi - td(days=n)).isoformat() for n in (0, 1, 3, 30, 90)}
    saltate, rifare = da_ricollezionare(registro, rifai_giorni=7, oggi=oggi)

    assert oggi.isoformat() in rifare                      # today, still filling in
    assert (oggi - td(days=1)).isoformat() in rifare       # yesterday, revisable
    assert (oggi - td(days=3)).isoformat() in rifare
    assert (oggi - td(days=30)).isoformat() in saltate     # settled, stays skipped
    assert (oggi - td(days=90)).isoformat() in saltate
    assert saltate | rifare == registro and not (saltate & rifare)

    # a registry with junk in it must not crash the split
    saltate, rifare = da_ricollezionare({'non-una-data'}, oggi=oggi)
    assert saltate == {'non-una-data'} and rifare == set()


def test_scarica_actually_uses_the_two_helpers():
    """The helpers being right does not mean the collector calls them.

    Both defects lived inside `scarica`: the whole-column currency map and the
    registry that skipped forever. Testing `paese_iso` and `da_ricollezionare`
    in isolation leaves `scarica` free to stop calling them and stay green, so
    the call sites are checked here -- structurally, since exercising `scarica`
    itself would need a browser.
    """
    import ast
    import inspect

    from market_data_hub.econ_calendar.collect import myfxbook

    sorgente_fn = inspect.getsource(myfxbook.scarica)
    albero = ast.parse(sorgente_fn)
    # Every name the function mentions, however it uses it: `da_ricollezionare`
    # is called, `paese_iso` is handed to `.map()` without being called, and
    # either way its absence is the defect coming back.
    nomi = {n.id for n in ast.walk(albero) if isinstance(n, ast.Name)}

    assert 'da_ricollezionare' in nomi, \
        'scarica non consulta piu da_ricollezionare: il registro torna a saltare per sempre'
    assert 'paese_iso' in nomi, \
        'scarica non converte piu con paese_iso: la ripresa torna a cancellare gli ISO'
    # and the mapping that caused the defect must not come back
    assert '.map(VALUTA_PAESE)' not in sorgente_fn, \
        'la colonna e di nuovo mappata in blocco sul dizionario delle valute'


def test_recollected_row_replaces_the_stale_one_even_if_its_time_changed():
    """Dedup must key on the release, not on the time it was said to be at.

    A re-collection can carry a corrected time. Keying on `Orario` kept both
    rows, and the ingest then chose between them by order -- where the stale
    one can win. Two genuinely distinct reference periods must still survive.
    """
    import ast
    import inspect

    import pandas as pd

    from market_data_hub.econ_calendar.collect import myfxbook

    righe = pd.DataFrame([
        # same release, collected twice, time corrected on the second pass
        {'Data_Rilascio': '2026-08-13', 'Orario': '15:00', 'Paese': 'US',
         'Evento': 'CPI YY', 'Periodo_Riferimento': 'Jul', 'Attuale': '3.0'},
        {'Data_Rilascio': '2026-08-13', 'Orario': '14:00', 'Paese': 'US',
         'Evento': 'CPI YY', 'Periodo_Riferimento': 'Jul', 'Attuale': '3.1'},
        # same day and indicator, different period: two observations, not a dup
        {'Data_Rilascio': '2026-08-13', 'Orario': '14:00', 'Paese': 'US',
         'Evento': 'CPI YY', 'Periodo_Riferimento': 'Jun', 'Attuale': '2.9'},
    ])

    # the key is read from `scarica` itself, so changing it there fails here
    sorgente_fn = inspect.getsource(myfxbook.scarica)
    albero = ast.parse(sorgente_fn)
    chiave = None
    for nodo in ast.walk(albero):
        if (isinstance(nodo, ast.Assign)
                and any(getattr(t, 'id', '') == 'chiave' for t in nodo.targets)):
            chiave = [c.value for c in ast.walk(nodo)
                      if isinstance(c, ast.Constant) and isinstance(c.value, str)]
    assert chiave, 'scarica non definisce piu una chiave di deduplicazione'
    assert 'Orario' not in chiave, \
        "l'orario e tornato nella chiave: una release con orario corretto non viene sostituita"
    assert 'Periodo_Riferimento' in chiave, \
        'senza il periodo, due release dello stesso indicatore nello stesso giorno si fondono'

    tenute = righe.drop_duplicates(subset=chiave, keep='last').reset_index(drop=True)

    assert len(tenute) == 2, 'le due letture della stessa release devono fondersi in una'
    luglio = tenute[tenute.Periodo_Riferimento == 'Jul'].iloc[0]
    assert luglio.Attuale == '3.1' and luglio.Orario == '14:00', \
        'deve vincere la riga ricollezionata, con il suo orario corretto'
    assert set(tenute.Periodo_Riferimento) == {'Jul', 'Jun'}


# ---------------------------------------------------------------------------
# The job used to exit 0 whenever anything at all was ingested, even with a
# source down -- indistinguishable to Task Scheduler from a clean run. That
# was a three-way split (clean/degraded/failed) because a dead source among
# several still left a partially useful run. MyFXBook is the only source
# left, so there is no partial-credit case any more: exit_code() is now a
# straight function of whether anything was produced to ingest.
# ---------------------------------------------------------------------------


def test_a_clean_run_exits_zero():
    from run_econ_calendar import exit_code

    assert exit_code(468) == 0


def test_zero_observations_is_a_failure():
    """Collection can 'succeed' -- return an empty frame -- and still leave
    nothing to ingest; that is not a clean run either."""
    from run_econ_calendar import exit_code

    assert exit_code(0) == 1


def test_no_collect_does_not_claim_a_fresh_collection_succeeded():
    """--no-collect skips collection entirely, so the summary must say that
    rather than reporting 'ok'/'FAILED' for a collection that never ran."""
    import inspect

    sorgente = inspect.getsource(__import__("run_econ_calendar").main)
    assert "args.no_collect" in sorgente
    assert "not attempted" in sorgente


def test_collect_never_touches_the_csv_on_failure(tmp_path, monkeypatch):
    """myfxbook writes incrementally and can have committed many resumable
    days to disk before raising midway (a transient browser failure, say).
    With myfxbook as the only source there is no other source's file that
    could go stale behind a failed one, so collect() must never delete or
    truncate it on failure -- whatever it managed to write stands."""
    import market_data_hub.econ_calendar.collect.myfxbook as mod_myfxbook
    from run_econ_calendar import collect

    work_dir = tmp_path
    bersaglio = work_dir / "myfxbook.csv"
    bersaglio.write_text("giorni gia raccolti, prima della chiamata che fallisce\n",
                         encoding="utf-8")

    def scarica_che_fallisce_a_meta(da, a, uscita):
        # emula il comportamento reale: scrive, poi il browser muore
        assert uscita == bersaglio
        raise RuntimeError("browser died mid-collection")

    monkeypatch.setattr(mod_myfxbook, "scarica", scarica_che_fallisce_a_meta)

    riuscita = collect(work_dir, "2026-08-01", "2026-08-16")

    assert riuscita is False
    assert bersaglio.exists(), \
        "il file di myfxbook e' stato toccato da un fallimento dentro la sua stessa chiamata"
    assert "gia raccolti" in bersaglio.read_text(encoding="utf-8")


def test_collect_reports_failure_on_empty_or_missing_frame(tmp_path, monkeypatch):
    import market_data_hub.econ_calendar.collect.myfxbook as mod_myfxbook
    import pandas as pd
    from run_econ_calendar import collect

    monkeypatch.setattr(mod_myfxbook, "scarica",
                        lambda da, a, uscita: pd.DataFrame())
    assert collect(tmp_path, "2026-08-01", "2026-08-16") is False
