# -*- coding: utf-8 -*-
"""The calendar's honest-value work: the macro bridge, the validation window,
the catch-up pass, and the row counts the exit code is decided on.

Every fixture builds on the real schema (``cx.migrate``) rather than a
hand-written CREATE TABLE, so a column that moves breaks these tests instead of
letting them pass against a table the production database does not have.
"""
import json
from datetime import date, datetime, time, timedelta, timezone

import duckdb
import pytest

from market_data_hub.db import connection as cx
from market_data_hub.econ_calendar import (
    CalendarObservation,
    ingest_observations,
    load_catalog_rows,
    upsert_indicators,
)
from market_data_hub.econ_calendar.collect.consolidate import raccogli
from market_data_hub.econ_calendar.macro_bridge import (
    bridged_indicators,
    fill_from_macro_series,
)


@pytest.fixture()
def con():
    c = duckdb.connect(":memory:")
    cx.migrate(c)
    yield c
    c.close()


# ------------------------------------------------------------------ helpers --
def _serie(con, series_id, righe, *, source="fred"):
    """Write a macro series and its vintages, the way the FRED job does.

    ``macro_series`` carries the latest revision, ``macro_series_vintage`` every
    version with the day it was collected. ``righe`` is
    (observation date, value, vintage date) and later vintages overwrite the
    current table exactly as the real ingest does.
    """
    for giorno, valore, vintage in righe:
        con.execute(
            "INSERT OR REPLACE INTO macro_series_vintage "
            "(date, series_id, value, vintage_date, source) VALUES (?, ?, ?, ?, ?)",
            [giorno, series_id, valore, vintage, source])
        con.execute(
            "INSERT OR REPLACE INTO macro_series "
            "(date, series_id, value, series_name, source, country) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [giorno, series_id, valore, f"TEST | {series_id}", source, "US"])


def _evento(con, indicator_key, *, release_utc, reference_date, actual=None,
            country_iso3="USA", source="forexfactory", provenance="aggregator"):
    ingest_observations(con, [CalendarObservation(
        indicator_key=indicator_key, country_iso3=country_iso3, source=source,
        provenance=provenance, source_event_name=indicator_key,
        release_utc=release_utc, reference_period="Aug",
        reference_date=reference_date, actual=actual,
        vintage_date=release_utc.date())])


def _catalogo(con):
    upsert_indicators(con, load_catalog_rows())


# ------------------------------------------------------- 1. the bridge map --
def test_the_catalogue_declares_only_level_to_level_bridges():
    """The mapping is the part a wrong entry makes dangerous, so it is asserted
    by name rather than by count: every declared bridge must be a pure level in
    the series' own unit, because that is the only case where the FRED number
    IS the number the calendar prints. A change here should have to be typed
    out, not slip in."""
    dichiarati = {r["indicator_key"]: r["macro_series_id"]
                  for r in load_catalog_rows() if r["macro_series_id"]}
    assert dichiarati == {"us_unemp": "UNRATE",
                          "ez_unemp": "LRHUTTTTEZM156S"}
    per_chiave = {r["indicator_key"]: r for r in load_catalog_rows()}
    for chiave in dichiarati:
        assert per_chiave[chiave]["value_type"] == "%"
        assert per_chiave[chiave]["frequency"] == "M"


def test_a_change_indicator_cannot_be_bridged_even_if_someone_maps_it(con):
    """The YAML comment is not the enforcement. A y/y CPI pointed at the CPI
    index would produce '323.05%' -- a credible-looking, entirely wrong figure --
    so the refusal lives in code and is reported, not silent."""
    _catalogo(con)
    con.execute("UPDATE calendar_indicators SET macro_series_id = 'CPIAUCSL' "
                "WHERE indicator_key = 'us_cpi_yy'")
    _serie(con, "CPIAUCSL", [(date(2026, 8, 1), 323.05, date(2026, 9, 11))])
    _evento(con, "us_cpi_yy", release_utc=datetime(2026, 9, 10, 12, 30),
            reference_date=date(2026, 8, 31))

    rifiutati = {i["indicator_key"]: i for i in bridged_indicators(con)
                 if not i["usable"]}
    assert "us_cpi_yy" in rifiutati
    assert "value_type" in rifiutati["us_cpi_yy"]["reason"]

    esito = fill_from_macro_series(
        con, now_utc=datetime(2026, 9, 12, tzinfo=timezone.utc))
    assert esito["filled"] == 0
    assert con.execute(
        "SELECT actual, actual_num FROM calendar_events "
        "WHERE indicator_key = 'us_cpi_yy'").fetchone() == (None, None)


def test_an_unmapped_indicator_stays_null_and_invents_nothing(con):
    """us_nfp has no bridge on purpose (PAYEMS is a level in thousands, the
    calendar prints the monthly change). It must come out of a bridge run
    exactly as it went in."""
    _catalogo(con)
    _serie(con, "PAYEMS", [(date(2026, 8, 1), 159_300.0, date(2026, 9, 4))])
    _evento(con, "us_nfp", release_utc=datetime(2026, 9, 4, 12, 30),
            reference_date=date(2026, 8, 31))

    esito = fill_from_macro_series(
        con, now_utc=datetime(2026, 9, 6, tzinfo=timezone.utc))

    assert esito["filled"] == 0
    assert con.execute(
        "SELECT actual, actual_num, actual_source FROM calendar_events "
        "WHERE indicator_key = 'us_nfp'").fetchone() == (None, None, None)
    assert con.execute("SELECT count(*) FROM calendar_event_notes").fetchone()[0] == 0


# ------------------------------------------------------ 2. the bridge fill --
def test_the_bridge_fills_only_when_the_period_matches(con):
    """One event for August, one for July; the series holds August only. The
    August event is filled from the August observation and the July event is
    left alone -- the near miss is the dangerous case, because a value from the
    wrong month is still a plausible unemployment rate."""
    _catalogo(con)
    _serie(con, "UNRATE", [(date(2026, 8, 1), 4.1, date(2026, 9, 4))])
    _evento(con, "us_unemp", release_utc=datetime(2026, 9, 4, 12, 30),
            reference_date=date(2026, 8, 31))
    _evento(con, "us_unemp", release_utc=datetime(2026, 8, 7, 12, 30),
            reference_date=date(2026, 7, 31))

    esito = fill_from_macro_series(
        con, now_utc=datetime(2026, 9, 6, tzinfo=timezone.utc), run_id="r1")

    assert esito["filled"] == 1
    assert esito["no_series_observation"] == 1
    per_periodo = dict(con.execute(
        "SELECT reference_date, actual FROM calendar_events "
        "WHERE indicator_key = 'us_unemp'").fetchall())
    # the string carries the unit, matching what the aggregator and the web
    # fill already store for this column; actual_num parses back to 4.1
    assert per_periodo == {date(2026, 8, 31): "4.1%", date(2026, 7, 31): None}
    riga = con.execute(
        "SELECT actual_num, actual_source, actual_provenance, status "
        "FROM calendar_events WHERE reference_date = ?",
        [date(2026, 8, 31)]).fetchone()
    assert riga == (4.1, "macro_series:UNRATE", "derived", "released")


def test_the_bridge_writes_a_note_naming_the_series_and_its_vintage(con):
    """A number in this column must never be anonymous: the note says which
    series it came from, which observation, and which vintage of it."""
    _catalogo(con)
    _serie(con, "UNRATE", [(date(2026, 8, 1), 4.1, date(2026, 9, 4))])
    _evento(con, "us_unemp", release_utc=datetime(2026, 9, 4, 12, 30),
            reference_date=date(2026, 8, 31))

    fill_from_macro_series(con, now_utc=datetime(2026, 9, 6, tzinfo=timezone.utc),
                           run_id="r1")

    nota, fonte, run = con.execute(
        "SELECT commentary_json, technical_source, run_id "
        "FROM calendar_event_notes").fetchone()
    contenuto = json.loads(nota)
    assert contenuto["check"] == "macro_series_fill"
    assert contenuto["macro_series_id"] == "UNRATE"
    assert contenuto["series_observation_date"] == "2026-08-01"
    assert contenuto["series_first_vintage"] == "2026-09-04"
    assert (fonte, run) == ("macro_series:UNRATE", "r1")


def test_the_bridge_never_overwrites_a_value_a_source_supplied(con):
    """A figure from a real source is the print. The bridge holds a possibly
    revised series value, so it must not touch it -- not even when the two
    disagree, which is exactly when a silent overwrite would do most damage."""
    _catalogo(con)
    _serie(con, "UNRATE", [(date(2026, 8, 1), 4.3, date(2026, 9, 4))])
    _evento(con, "us_unemp", release_utc=datetime(2026, 9, 4, 12, 30),
            reference_date=date(2026, 8, 31), actual="4.1%")

    esito = fill_from_macro_series(
        con, now_utc=datetime(2026, 9, 6, tzinfo=timezone.utc))

    assert esito["candidates"] == 0 and esito["filled"] == 0
    assert con.execute(
        "SELECT actual, actual_num, actual_source FROM calendar_events"
    ).fetchone() == ("4.1%", 4.1, "forexfactory")


def test_the_bridge_takes_the_first_vintage_not_the_revision(con):
    """calendar_events.actual is what was PUBLISHED. The series has since been
    revised from 4.1 to 4.4; taking the current table would quietly rewrite the
    print as though the agency had said 4.4 on the day."""
    _catalogo(con)
    _serie(con, "UNRATE", [(date(2026, 8, 1), 4.1, date(2026, 9, 4)),
                           (date(2026, 8, 1), 4.4, date(2026, 10, 2))])
    assert con.execute("SELECT value FROM macro_series WHERE series_id = 'UNRATE'"
                       ).fetchone() == (4.4,)
    _evento(con, "us_unemp", release_utc=datetime(2026, 9, 4, 12, 30),
            reference_date=date(2026, 8, 31))

    fill_from_macro_series(con, now_utc=datetime(2026, 10, 5, tzinfo=timezone.utc))

    assert con.execute("SELECT actual_num FROM calendar_events").fetchone() == (4.1,)


def test_the_bridge_refuses_an_event_whose_period_is_unknown(con):
    """No reference_date means the period was never published and never
    inferred. Guessing it from the release day is how a July print ends up
    filed as August."""
    _catalogo(con)
    _serie(con, "UNRATE", [(date(2026, 8, 1), 4.1, date(2026, 9, 4))])
    ingest_observations(con, [CalendarObservation(
        indicator_key="us_unemp", country_iso3="USA", source="forexfactory",
        provenance="aggregator", source_event_name="Unemployment Rate",
        release_utc=datetime(2026, 9, 4, 12, 30), vintage_date=date(2026, 9, 4))])

    esito = fill_from_macro_series(
        con, now_utc=datetime(2026, 9, 6, tzinfo=timezone.utc))

    assert esito["candidates"] == 0 and esito["filled"] == 0
    assert con.execute("SELECT actual FROM calendar_events").fetchone() == (None,)


def test_the_bridge_does_not_fill_a_release_that_has_not_happened(con):
    """A scheduled release with a past reference period is a revision or a
    later estimate. Filling it from the series would claim a print that has
    not been made."""
    _catalogo(con)
    _serie(con, "UNRATE", [(date(2026, 8, 1), 4.1, date(2026, 9, 4))])
    _evento(con, "us_unemp", release_utc=datetime(2026, 9, 20, 12, 30),
            reference_date=date(2026, 8, 31))

    esito = fill_from_macro_series(
        con, now_utc=datetime(2026, 9, 6, tzinfo=timezone.utc))

    assert esito["skipped_future"] == 1 and esito["filled"] == 0


def test_the_bridge_ignores_a_series_observation_with_no_vintage(con):
    """macro_series alone gives a number with no publication date attached.
    That is not enough to say it was published on the day the calendar claims."""
    _catalogo(con)
    con.execute(
        "INSERT INTO macro_series (date, series_id, value, source, country) "
        "VALUES (?, 'UNRATE', 4.1, 'fred', 'US')", [date(2026, 8, 1)])
    _evento(con, "us_unemp", release_utc=datetime(2026, 9, 4, 12, 30),
            reference_date=date(2026, 8, 31))

    esito = fill_from_macro_series(
        con, now_utc=datetime(2026, 9, 6, tzinfo=timezone.utc))

    assert esito["no_series_observation"] == 1 and esito["filled"] == 0


# ------------------------------------------------------------ 3. the window --
@pytest.mark.parametrize("ora_di_lancio", [0, 6, 13, 23])
def test_a_1230_release_on_the_boundary_day_is_inside_the_window(con, ora_di_lancio):
    """The bug this fixes, stated as arithmetic. The job runs at 13:00 UTC and
    the window was ``now - 3 days`` = 13:00 UTC on day D-3; US payrolls publish
    at 12:30 UTC, so on the boundary day they fell out by thirty minutes.
    Parameterised over the launch hour because the second half of the bug was
    that the answer depended on it at all."""
    from market_data_hub.econ_calendar import validate

    _catalogo(con)
    oggi = date(2026, 9, 7)
    bordo = datetime.combine(oggi - timedelta(days=3), time(12, 30))
    _evento(con, "us_nfp", release_utc=bordo, reference_date=date(2026, 8, 31))

    eventi = validate._t1_events_for_window(
        con,
        now_utc=datetime.combine(oggi, time(ora_di_lancio), tzinfo=timezone.utc),
        lookback_days=3)

    assert [e["indicator_key"] for e in eventi] == ["us_nfp"]


def test_the_window_start_is_midnight_of_the_boundary_day():
    from market_data_hub.econ_calendar.validate import window_start

    lancio = datetime(2026, 9, 7, 13, 0)
    assert window_start(lancio, 3) == datetime(2026, 9, 4, 0, 0)
    # and it does not move when the job starts late
    assert window_start(datetime(2026, 9, 7, 17, 42), 3) == datetime(2026, 9, 4, 0, 0)


def test_the_window_still_excludes_the_future(con):
    from market_data_hub.econ_calendar import validate

    _catalogo(con)
    adesso = datetime(2026, 9, 7, 13, 0, tzinfo=timezone.utc)
    _evento(con, "us_nfp", release_utc=datetime(2026, 9, 7, 13, 20),
            reference_date=date(2026, 8, 31))

    assert validate._t1_events_for_window(
        con, now_utc=adesso, lookback_days=3) == []


# ----------------------------------------------------------- 3b. the catch-up --
def _valida(con, monkeypatch, risposta, **kw):
    from market_data_hub.econ_calendar import validate
    monkeypatch.setattr(validate, "_ask", lambda *a, **k: risposta)
    return validate.run_validation(con, **kw)


def test_the_catchup_picks_up_an_old_event_the_window_left_empty(con, monkeypatch):
    """Without this pass an event that aged out of the window unfilled stayed
    empty forever: measured on production on 07/09/2026, 13 of 77 events had no
    actual_num and nothing was ever going to look at them again."""
    _catalogo(con)
    oggi = date(2026, 9, 7)
    vecchio = datetime.combine(oggi - timedelta(days=12), time(12, 30))
    _evento(con, "us_nfp", release_utc=vecchio, reference_date=date(2026, 8, 31))

    esito = _valida(con, monkeypatch,
                    "STATUS: FOUND\nACTUAL: 162K\nNOTE: BLS.\n"
                    "SOURCES:\nhttps://www.bls.gov/x",
                    day=oggi, lookback_days=3, catchup_days=30)

    assert esito["checked"] == 0
    assert esito["rechecked"] == 1 and esito["refilled"] == 1
    assert con.execute(
        "SELECT actual, actual_provenance FROM calendar_events"
    ).fetchone() == ("162K", "web")


def test_the_catchup_does_not_look_at_events_that_already_have_a_value(con, monkeypatch):
    _catalogo(con)
    oggi = date(2026, 9, 7)
    _evento(con, "us_nfp",
            release_utc=datetime.combine(oggi - timedelta(days=12), time(12, 30)),
            reference_date=date(2026, 8, 31), actual="162K")

    esito = _valida(con, monkeypatch, "STATUS: UNVERIFIED",
                    day=oggi, lookback_days=3, catchup_days=30)

    assert esito["rechecked"] == 0 and esito["checked"] == 0


def test_the_catchup_gives_up_after_the_configured_number_of_attempts(con, monkeypatch):
    """An event nobody can find must stop costing a web search every morning.
    Three runs look, the fourth does not."""
    _catalogo(con)
    oggi = date(2026, 9, 7)
    _evento(con, "us_nfp",
            release_utc=datetime.combine(oggi - timedelta(days=12), time(12, 30)),
            reference_date=date(2026, 8, 31))

    visti = []
    for _ in range(4):
        esito = _valida(con, monkeypatch, "STATUS: UNVERIFIED",
                        day=oggi, lookback_days=3, catchup_days=30,
                        max_attempts=3)
        visti.append(esito["rechecked"])

    assert visti == [1, 1, 1, 0]
    assert con.execute(
        "SELECT count(*), max(review_attempts) FROM calendar_event_notes "
        "WHERE json_extract_string(commentary_json, '$.check') = 'actual_catchup'"
    ).fetchone() == (3, 3)
    assert esito["attempts_exhausted"] == 0   # no longer queried, so not counted
    assert con.execute("SELECT actual FROM calendar_events").fetchone() == (None,)


def test_an_engine_failure_does_not_burn_a_catchup_attempt(con, monkeypatch):
    """A missing claude-agent-sdk is not the event's fault. If errors consumed
    attempts, one broken week would exhaust the whole backlog without a single
    search ever having run."""
    from market_data_hub.econ_calendar import validate

    _catalogo(con)
    oggi = date(2026, 9, 7)
    _evento(con, "us_nfp",
            release_utc=datetime.combine(oggi - timedelta(days=12), time(12, 30)),
            reference_date=date(2026, 8, 31))

    def esplode(*a, **k):
        raise RuntimeError("EngineError: call failed")

    monkeypatch.setattr(validate, "_ask", esplode)
    for _ in range(4):
        esito = validate.run_validation(con, oggi, lookback_days=3,
                                        catchup_days=30, max_attempts=3)
    assert esito["rechecked"] == 1 and esito["errors"] == 1
    assert con.execute("SELECT count(*) FROM calendar_event_notes").fetchone()[0] == 0


def test_the_catchup_does_not_reach_beyond_its_depth(con, monkeypatch):
    _catalogo(con)
    oggi = date(2026, 9, 7)
    _evento(con, "us_nfp",
            release_utc=datetime.combine(oggi - timedelta(days=45), time(12, 30)),
            reference_date=date(2026, 7, 31))

    esito = _valida(con, monkeypatch, "STATUS: UNVERIFIED",
                    day=oggi, lookback_days=3, catchup_days=30)

    assert esito["rechecked"] == 0


# ------------------------------------------------------------ 4. row counts --
_INTESTAZIONE = ('Data_Rilascio,Orario,Paese,Importanza,Evento,'
                 'Periodo_Riferimento,Attuale,Previsto,Precedente,Revisione,Fonte')


def _feed(tmp_path, righe):
    (tmp_path / 'forexfactory.csv').write_text(
        _INTESTAZIONE + '\n' + '\n'.join(righe) + '\n', encoding='utf-8')


def _tre_righe(tmp_path):
    """One row per verdict, on purpose.

    * 'Unemployment Rate' matches us_unemp's rules -> matched.
    * 'CPI y/y' is ruled out by an explicit rejection recorded below.
    * 'ANZ Business Confidence' from NZ: no catalogue entry covers NZ, so no
      rule was ever evaluated against it -- the invisible category.
    """
    _feed(tmp_path, [
        '2026-09-04,12:30,US,high,Unemployment Rate,Aug,,4.2%,4.1%,,ForexFactory',
        '2026-09-04,12:30,US,high,CPI y/y,Aug,,2.9%,2.8%,,ForexFactory',
        '2026-08-31,21:00,NZ,low,ANZ Business Confidence,Aug,,,49.7,,ForexFactory',
    ])


def test_the_three_row_counts_partition_the_feed(con, tmp_path, monkeypatch):
    from market_data_hub.econ_calendar.aliases import (
        load_aliases, load_rejections, upsert_alias,
    )

    catalogo = [r for r in load_catalog_rows()
                if r['indicator_key'] in ('us_unemp', 'us_cpi_yy')]
    upsert_indicators(con, catalogo)
    _tre_righe(tmp_path)
    upsert_alias(con, source='forexfactory', country_iso3='USA',
                 source_name='CPI y/y', indicator_key='us_cpi_yy',
                 status='rejected', note='test')
    monkeypatch.chdir(tmp_path)

    osservazioni, per_fonte, conteggi = raccogli(
        catalogo, load_rejections(con), load_aliases(con))

    assert [o.indicator_key for o in osservazioni] == ['us_unemp']
    assert conteggi['rows'] == 3
    assert (conteggi['matched'], conteggi['ruled_out'],
            conteggi['unseen']) == (1, 1, 1)
    assert conteggi['unseen_uncovered_country'] == 1
    assert per_fonte == {'forexfactory': 1}


def test_a_rejected_row_is_counted_once_not_once_per_catalogue_entry(con, tmp_path,
                                                                    monkeypatch):
    """The artefact this replaces: the counter lived inside the
    indicator x row double loop, so ONE rejected US row was counted once for
    every US catalogue entry. Production printed '60 righe respinte' for 5
    rows on 07/09/2026."""
    from market_data_hub.econ_calendar.aliases import (
        load_aliases, load_rejections, upsert_alias,
    )

    catalogo = [r for r in load_catalog_rows() if r['country_iso3'] == 'USA']
    assert len(catalogo) > 10, 'servono piu\' indicatori USA perche\' il doppio ciclo morda'
    upsert_indicators(con, catalogo)
    _feed(tmp_path, [
        '2026-09-04,12:30,US,high,CPI y/y,Aug,,2.9%,2.8%,,ForexFactory'])
    upsert_alias(con, source='forexfactory', country_iso3='USA',
                 source_name='CPI y/y', indicator_key='us_cpi_yy',
                 status='rejected', note='test')
    monkeypatch.chdir(tmp_path)

    _, _, conteggi = raccogli(catalogo, load_rejections(con), load_aliases(con))

    assert conteggi == {'rows': 1, 'matched': 0, 'ruled_out': 1, 'unseen': 0,
                        'unseen_uncovered_country': 0,
                        'per_source': {'forexfactory': {
                            'rows': 1, 'matched': 0, 'ruled_out': 1,
                            'unseen': 0, 'unseen_uncovered_country': 0}}}


def test_an_excluded_row_counts_as_unseen_not_as_ruled_out(con, tmp_path, monkeypatch):
    """'ADP Non-Farm Employment Change' is kept off us_nfp by match_excludes.
    That is a catalogue entry saying 'not me', not a decision about the row:
    nobody has ruled on ADP, and calling it 'rejected' would hide it in the
    bucket that means 'somebody looked'."""
    catalogo = [r for r in load_catalog_rows() if r['indicator_key'] == 'us_nfp']
    upsert_indicators(con, catalogo)
    _feed(tmp_path, [
        '2026-09-03,12:15,US,high,ADP Non-Farm Employment Change,Aug,,68K,104K,,ForexFactory'])
    monkeypatch.chdir(tmp_path)

    osservazioni, _, conteggi = raccogli(catalogo)

    assert osservazioni == []
    assert (conteggi['ruled_out'], conteggi['unseen']) == (0, 1)
    # the country IS covered: the rules ran and none of them claimed the row
    assert conteggi['unseen_uncovered_country'] == 0


# ------------------------------------------------------------ 4b. exit code --
def test_exit_code_is_clean_when_the_feed_is_mostly_understood():
    from run_econ_calendar import EXIT_OK, exit_code

    conteggi = {'rows': 100, 'matched': 70, 'ruled_out': 5, 'unseen': 25}
    assert exit_code(conteggi, 70) == EXIT_OK


def test_degraded_coverage_does_not_change_the_exit_code():
    """A standing condition is not an alarm, and 2 was not ours to spend.

    The production shape on 07/09/2026: 89 of 126 rows met no rule. It has
    been like that every day this calendar has existed, so wiring it to the
    exit code made the task red every morning -- and `run_marketdata_job.ps1`,
    the wrapper that launches this job, already reads 2 as "configuration
    refused", so a degraded run wrote a false sentence into its own log.
    The number is said in the log instead, in full, on every run.
    """
    from run_econ_calendar import EXIT_OK, exit_code

    conteggi = {'rows': 126, 'matched': 32, 'ruled_out': 5, 'unseen': 89}
    assert exit_code(conteggi, 32) == EXIT_OK


def test_exit_code_fails_when_nothing_was_hooked_at_all():
    from run_econ_calendar import EXIT_FAILED, exit_code

    conteggi = {'rows': 126, 'matched': 0, 'ruled_out': 5, 'unseen': 121}
    assert exit_code(conteggi, 0) == EXIT_FAILED


def test_exit_code_separates_an_empty_feed_from_a_broken_one():
    """3 is the ecosystem's 'nothing to do, do not go red' (LazyRay's
    run_stress_monitor.py and run_dalio_v2.py); 1 is a fault. An empty in-tray
    and a download that failed are not the same event."""
    from run_econ_calendar import EXIT_FAILED, EXIT_NOTHING_TO_DO, exit_code

    vuoto = {'rows': 0, 'matched': 0, 'ruled_out': 0, 'unseen': 0}
    assert exit_code(vuoto, 0) == EXIT_NOTHING_TO_DO
    assert exit_code(vuoto, 0, collezione_fallita=True) == EXIT_FAILED


def test_the_runner_says_degraded_out_loud_while_still_exiting_clean(
        tmp_path, monkeypatch, capsys):
    """End to end through main(): the sentence must be there, the red must not.

    Reporting it and alarming on it are different jobs. A reader who opens the
    log has to find the three counts; a scheduler that goes red every morning
    teaches the same reader to stop opening it.
    """
    import run_econ_calendar as runner

    db = tmp_path / 'hub.duckdb'
    con = cx.get_conn(str(db))
    con.close()
    righe = ['2026-09-04,12:30,US,high,Unemployment Rate,Aug,,4.2%,4.1%,,ForexFactory']
    righe += [f'2026-08-31,21:00,NZ,low,Regional Survey {i},Aug,,,49.7,,ForexFactory'
              for i in range(9)]
    _feed(tmp_path, righe)
    monkeypatch.setattr('sys.argv', [
        'run_econ_calendar.py', '--db', str(db), '--work-dir', str(tmp_path),
        '--no-collect', '--no-validate', '--no-bridge'])

    assert runner.main() == runner.EXIT_OK
    assert 'DEGRADED' in capsys.readouterr().err


def test_the_runner_reports_nothing_to_do_when_there_is_no_feed(tmp_path, monkeypatch):
    import run_econ_calendar as runner

    db = tmp_path / 'hub.duckdb'
    con = cx.get_conn(str(db))
    con.close()
    monkeypatch.setattr('sys.argv', [
        'run_econ_calendar.py', '--db', str(db), '--work-dir', str(tmp_path),
        '--no-collect', '--no-validate', '--no-bridge'])

    assert runner.main() == runner.EXIT_NOTHING_TO_DO


def test_the_runner_fills_from_the_bridge_before_paying_for_a_web_search(tmp_path,
                                                                        monkeypatch):
    """The bridge runs inside main() and its fill is visible to the validation
    pass that follows, which is the point of the ordering: an event the hub can
    answer for free must not be sent to an LLM."""
    import run_econ_calendar as runner

    db = tmp_path / 'hub.duckdb'
    con = cx.get_conn(str(db))
    _serie(con, "UNRATE", [(date(2026, 8, 1), 4.1, date(2026, 9, 4))])
    con.close()
    _feed(tmp_path, [
        '2026-09-04,12:30,US,high,Unemployment Rate,Aug,,4.2%,4.1%,,ForexFactory'])
    monkeypatch.setattr('sys.argv', [
        'run_econ_calendar.py', '--db', str(db), '--work-dir', str(tmp_path),
        '--no-collect', '--no-validate'])

    runner.main()

    con = cx.get_conn(str(db), read_only=True)
    try:
        assert con.execute(
            "SELECT actual, actual_source FROM calendar_events "
            "WHERE indicator_key = 'us_unemp'").fetchone() == (
                "4.1%", "macro_series:UNRATE")
    finally:
        con.close()


def test_a_backfilled_vintage_is_refused_because_it_is_a_revision(con):
    """The earliest vintage is only the print if the hub was there to see it.

    Measured on production: UNRATE holds 319 observations back to 2000 and
    every one before July 2026 carries the SAME vintage date, 2026-07-10 --
    the day of the historical backfill, by which time each figure had already
    been revised. Taking the earliest vintage there records a 2026 revision as
    what came out years earlier, and `derived` outranks `web`, so the later
    cross-check could never correct it.
    """
    _catalogo(con)
    # Collected years after the fact, exactly as a backfill does.
    _serie(con, "UNRATE", [(date(2020, 4, 1), 14.7, date(2026, 7, 10))])
    _evento(con, "us_unemp", release_utc=datetime(2020, 5, 8, 12, 30),
            reference_date=date(2020, 4, 30))

    esito = fill_from_macro_series(con, now_utc=datetime(2026, 9, 7, tzinfo=timezone.utc))

    assert esito["filled"] == 0
    assert con.execute("SELECT actual_num FROM calendar_events").fetchone() == (None,)


def test_a_vintage_collected_days_after_the_release_is_still_the_print(con):
    """The window is generous on purpose: a collection that fell behind by a
    few days is still the first print, while a backfill is years late."""
    _catalogo(con)
    _serie(con, "UNRATE", [(date(2026, 8, 1), 4.1, date(2026, 9, 7))])
    _evento(con, "us_unemp", release_utc=datetime(2026, 9, 4, 12, 30),
            reference_date=date(2026, 8, 31))

    fill_from_macro_series(con, now_utc=datetime(2026, 9, 10, tzinfo=timezone.utc))

    assert con.execute("SELECT actual_num FROM calendar_events").fetchone() == (4.1,)


def test_a_bridged_value_is_not_sent_to_the_paid_web_check(con, monkeypatch):
    """The bridge exists to replace the paid lookup, not to run alongside it.

    The primary pass deliberately includes events that already have an actual,
    so a wrong figure can be caught. But a value taken from a Federal Reserve
    series in this same file has nothing to gain from a language model reading
    a news page about it, and without the exclusion every bridged release paid
    for its search anyway, on every qualifying run.
    """
    from market_data_hub.econ_calendar.validate import _t1_events_for_window

    _catalogo(con)
    _serie(con, "UNRATE", [(date(2026, 8, 1), 4.1, date(2026, 9, 4))])
    _evento(con, "us_unemp", release_utc=datetime(2026, 9, 4, 12, 30),
            reference_date=date(2026, 8, 31))
    fill_from_macro_series(con, now_utc=datetime(2026, 9, 5, tzinfo=timezone.utc))
    assert con.execute(
        "SELECT actual_provenance FROM calendar_events").fetchone() == ("derived",)

    restanti = _t1_events_for_window(
        con, now_utc=datetime(2026, 9, 5, tzinfo=timezone.utc), lookback_days=3)

    assert [e["indicator_key"] for e in restanti] == []
