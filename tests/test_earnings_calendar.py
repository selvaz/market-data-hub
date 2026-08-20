# -*- coding: utf-8 -*-
"""Earnings calendar tests: identity, vintages, consolidation, read surface."""
import os
from datetime import date, datetime, timedelta, timezone

import duckdb
import pytest

from market_data_hub.db import connection as cx
from market_data_hub.earnings_calendar import (
    REGIONS,
    EarningsObservation,
    aggregate,
    audit,
    consolidate_events,
    events_between,
    ingest_observations,
    make_event_id,
    region_of,
    resolve_event_id,
    theme_of,
    vocabulary,
)
from market_data_hub.earnings_calendar import ingest as ing
from market_data_hub.earnings_calendar.collect.tradingview import leggi


@pytest.fixture()
def con():
    c = duckdb.connect(":memory:")
    cx.migrate(c)
    yield c
    c.close()


def _oss(**kw):
    """One observation, with everything a consolidated event needs."""
    base = dict(
        symbol="AAPL",
        exchange="NASDAQ",
        source="tradingview",
        status="estimated",
        release_ts_utc=datetime(2026, 7, 30, 20, 30),
        company_name="Apple Inc.",
        country="United States",
        sector="Electronic Technology",
        industry="Semiconductors",
        market_cap=3.4e12,
        currency="USD",
        vintage_date=date(2026, 7, 1),
    )
    base.update(kw)
    return EarningsObservation(**base)


def _evento(con, campi, dove="TRUE"):
    return con.execute(f"SELECT {campi} FROM earnings_events WHERE {dove}").fetchone()


def _quanti(con) -> int:
    return con.execute("SELECT count(*) FROM earnings_events").fetchone()[0]


# ------------------------------------------------------------------ schema --
def test_migration_creates_the_tables(con):
    tables = {r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_name LIKE 'earnings%'").fetchall()}
    assert tables == {"earnings_events", "earnings_observations"}


# ---------------------------------------------------------------- identita --
def test_a_release_nobody_has_seen_yet_is_keyed_on_its_utc_day(con):
    quando = datetime(2026, 9, 30, 20, 30)
    assert (resolve_event_id(con, "NASDAQ", "AAPL", quando)
            == make_event_id("NASDAQ", "AAPL", quando))
    # the day, not the instant
    assert make_event_id("NASDAQ", "AAPL", datetime(2026, 9, 30, 1, 0)) == \
        make_event_id("NASDAQ", "AAPL", quando)
    assert make_event_id("NASDAQ", "AAPL", datetime(2026, 10, 1, 20, 30)) != \
        make_event_id("NASDAQ", "AAPL", quando)
    assert make_event_id("NASDAQ", "MSFT", quando) != \
        make_event_id("NASDAQ", "AAPL", quando)


def test_a_release_that_slips_a_few_days_stays_one_event(con):
    """A date moved from 30 September to 2 October is the same release. Keying
    on the day alone would report a no-show and a surprise appearance."""
    ingest_observations(con, [_oss(release_ts_utc=datetime(2026, 9, 30, 20, 30),
                                   vintage_date=date(2026, 9, 1))])
    ingest_observations(con, [_oss(release_ts_utc=datetime(2026, 10, 2, 20, 30),
                                   vintage_date=date(2026, 9, 25))])
    assert _quanti(con) == 1
    assert _evento(con, "release_ts_utc")[0] == datetime(2026, 10, 2, 20, 30)


def test_two_releases_a_quarter_apart_stay_two_events(con):
    """The defect this replaced a quarter key for: 1 July and 30 September both
    hashed to 2026Q3, so the second release overwrote the first and took its
    published figures with it."""
    ingest_observations(con, [_oss(release_ts_utc=datetime(2026, 7, 1, 20, 30),
                                   status="occurred", eps_actual=1.4,
                                   vintage_date=date(2026, 7, 2))])
    ingest_observations(con, [_oss(release_ts_utc=datetime(2026, 9, 30, 20, 30),
                                   vintage_date=date(2026, 9, 1))])
    assert _quanti(con) == 2
    luglio = _evento(con, "status, eps_actual",
                     "release_ts_utc < TIMESTAMP '2026-08-01'")
    assert luglio == ("occurred", 1.4)


def test_a_slip_beyond_the_tolerance_window_splits_the_event(con):
    """Honest about what the window is: a heuristic. Six weeks out, the module
    cannot tell a long postponement from the next quarter's release, and it
    chooses to keep two rather than merge two real releases into one."""
    ingest_observations(con, [_oss(release_ts_utc=datetime(2026, 9, 30, 20, 30),
                                   vintage_date=date(2026, 9, 1))])
    ingest_observations(con, [_oss(release_ts_utc=datetime(2026, 11, 15, 20, 30),
                                   vintage_date=date(2026, 9, 25))])
    assert _quanti(con) == 2


# ----------------------------------------------------------------- vintage --
def test_re_ingesting_the_same_day_replaces_the_row(con):
    esito = ingest_observations(con, [_oss(eps_estimate=1.0)])
    assert esito == {"observations": 1, "events": 1}
    ingest_observations(con, [_oss(eps_estimate=2.0)])
    righe = con.execute(
        "SELECT vintage_date, eps_estimate FROM earnings_observations").fetchall()
    assert righe == [(date(2026, 7, 1), 2.0)]


def test_a_later_vintage_is_added_beside_the_earlier_one_and_wins(con):
    ingest_observations(con, [_oss(eps_estimate=1.0)])
    ingest_observations(con, [_oss(eps_estimate=2.0, vintage_date=date(2026, 7, 15))])
    assert con.execute("SELECT count(*) FROM earnings_observations").fetchone()[0] == 2
    assert _evento(con, "eps_estimate")[0] == 2.0


def test_an_earlier_vintage_arriving_late_does_not_win(con):
    """Backfills arrive out of order; the newest version still decides."""
    ingest_observations(con, [_oss(eps_estimate=2.0, vintage_date=date(2026, 7, 15))])
    ingest_observations(con, [_oss(eps_estimate=1.0, vintage_date=date(2026, 7, 1))])
    assert _evento(con, "eps_estimate")[0] == 2.0


# ------------------------------------------------------------------ stato ---
def test_occurred_outranks_estimated_even_from_an_older_vintage(con):
    """A forward run collected today still expects a release another source
    already saw happen: 'occurred' cannot be undone by a fresher expectation."""
    ingest_observations(con, [_oss(source="tv_last", status="occurred",
                                   eps_actual=1.4, vintage_date=date(2026, 7, 31))])
    ingest_observations(con, [_oss(source="tv_next", status="estimated",
                                   vintage_date=date(2026, 8, 5))])
    assert _evento(con, "status")[0] == "occurred"


def test_an_unknown_status_is_rejected():
    with pytest.raises(ValueError, match="unknown status"):
        _oss(status="probably")


# ------------------------------------------------------------ campi valore --
def test_an_actual_present_in_one_source_survives_a_source_without_it(con):
    ingest_observations(con, [
        _oss(source="tv_last", status="occurred", eps_actual=1.4, revenue_actual=9.1e10),
        _oss(source="tv_next", status="estimated", eps_estimate=1.3),
    ])
    assert _evento(con, "eps_actual, revenue_actual, eps_estimate") == (1.4, 9.1e10, 1.3)


def test_consolidation_is_idempotent(con):
    ingest_observations(con, [_oss(status="occurred", eps_actual=1.4)])
    campi = "symbol, status, release_ts_utc, region, theme, eps_actual, n_sources"
    prima = _evento(con, campi)
    consolidate_events(con, [make_event_id("NASDAQ", "AAPL", datetime(2026, 7, 30))])
    assert _evento(con, campi) == prima
    assert _quanti(con) == 1


def test_n_sources_counts_sources_not_vintages(con):
    ingest_observations(con, [_oss(source="a"), _oss(source="b")])
    ingest_observations(con, [_oss(source="a", vintage_date=date(2026, 7, 9))])
    assert _evento(con, "n_sources")[0] == 2


# ------------------------------------------------------------- precisione --
def test_a_known_minute_within_a_day_beats_a_midnight_placeholder(con):
    """Being the authority on WHETHER a release happened is not being the
    authority on WHEN. Toyota reports at 15:00 Tokyo -- 06:00 UTC -- and the
    day-only row for the same release arrives as a midnight on the OTHER side
    of UTC midnight, which is why the window is a day and not a UTC date."""
    base = dict(symbol="7203", exchange="TSE", country="Japan",
                vintage_date=date(2026, 8, 2))
    ingest_observations(con, [EarningsObservation(
        source="tv_last", status="occurred", eps_actual=1.4,
        release_ts_utc=datetime(2026, 8, 1, 0, 0), release_precision="day", **base)])
    ingest_observations(con, [EarningsObservation(
        source="altro", status="estimated",
        release_ts_utc=datetime(2026, 7, 31, 23, 0), release_precision="minute", **base)])

    assert _quanti(con) == 1
    assert _evento(con, "release_ts_utc, release_precision") == \
        (datetime(2026, 7, 31, 23, 0), "minute")


def test_the_deterministic_order_not_the_clock_decides_among_minute_rows(con):
    """Two sources both know a minute and disagree. Taking the earliest would
    let any source drag the instant backwards; the consolidation order --
    status first, then source name -- picks, so reruns cannot differ."""
    base = dict(symbol="AAPL", exchange="NASDAQ", country="United States",
                vintage_date=date(2026, 8, 2))
    ingest_observations(con, [EarningsObservation(
        source="aaa", status="occurred", eps_actual=1.4,
        release_ts_utc=datetime(2026, 7, 31, 0, 0), release_precision="day", **base)])
    ingest_observations(con, [
        EarningsObservation(source="zzz", status="occurred", eps_actual=1.4,
                            release_ts_utc=datetime(2026, 7, 30, 20, 30),
                            release_precision="minute", **base),
        EarningsObservation(source="bbb", status="estimated",
                            release_ts_utc=datetime(2026, 7, 30, 14, 0),
                            release_precision="minute", **base),
    ])
    assert _evento(con, "release_ts_utc")[0] == datetime(2026, 7, 30, 20, 30)


def test_a_release_stays_day_only_when_nobody_knows_the_minute(con):
    ingest_observations(con, [_oss(release_ts_utc=datetime(2026, 7, 30, 0, 0),
                                   release_precision="day")])
    assert _evento(con, "release_ts_utc, release_precision") == \
        (datetime(2026, 7, 30, 0, 0), "day")


# ---------------------------------------------------------------- regioni ---
def test_a_country_maps_to_its_region_or_to_unknown():
    assert region_of("United States") == "us"
    assert region_of("Hong Kong") == "china_hk"
    assert region_of("Brazil") == "americas_ex_us"
    # a plausible-looking region would hide the gap the audit exists to report
    assert region_of("Atlantis") == "unknown"
    assert region_of(None) == "unknown" and region_of("") == "unknown"


def test_every_region_written_belongs_to_the_closed_vocabulary(con):
    for paese in ("United States", "Germany", "Japan", "India", "Atlantis", None):
        ingest_observations(con, [_oss(symbol=f"S{paese}", country=paese)])
    prodotte = {r[0] for r in con.execute(
        "SELECT DISTINCT region FROM earnings_events").fetchall()}
    assert prodotte and prodotte <= set(REGIONS)


def test_the_region_follows_the_country_the_event_records(con):
    """The furthest-advanced source can be the one missing the country. Taking
    the region from that row filed an event under Japan while counting it as
    region 'unknown', and the audit then reported a gap that was not one."""
    base = dict(symbol="7203", exchange="TSE")
    ingest_observations(con, [
        _oss(source="tv_last", status="occurred", eps_actual=1.4, country=None, **base),
        _oss(source="tv_next", status="estimated", country="Japan", **base),
    ])
    assert _evento(con, "country, region") == ("Japan", "japan")
    assert audit(con)["unknown_region"] == 0


# ------------------------------------------------------------------- temi ---
def test_a_mapped_industry_carries_its_theme():
    ing._themes_cache = None            # the module caches; start from the file
    try:
        assert theme_of("Semiconductors") == "ai_semis"
        # An industry whose name contains a colon has to be quoted in the YAML
        # or it parses as a mapping and matches nothing, silently.
        assert theme_of("Pharmaceuticals: Major") == "healthcare"
        assert theme_of("Auto Parts: OEM") == "mobility_transport"
        assert theme_of("  sEMIconductors ") == "ai_semis"   # case and padding
    finally:
        ing._themes_cache = None


def test_an_unmapped_industry_has_no_theme():
    assert theme_of("Sorcery") is None
    assert theme_of(None) is None


def test_the_consolidated_event_carries_the_theme(con):
    ingest_observations(con, [_oss(industry="Airlines")])
    assert _evento(con, "industry, theme") == ("Airlines", "mobility_transport")


# ------------------------------------------------------------------- fuso ---
def test_a_tz_aware_release_is_stored_as_naive_utc(con):
    tokyo = datetime(2026, 10, 1, 5, 30, tzinfo=timezone(timedelta(hours=9)))
    o = _oss(release_ts_utc=tokyo)
    assert o.release_ts_utc == datetime(2026, 9, 30, 20, 30)
    assert o.release_ts_utc.tzinfo is None
    # and the identity follows the UTC day, not the local one
    assert o.event_id == make_event_id("NASDAQ", "AAPL", datetime(2026, 9, 30))
    ingest_observations(con, [o])
    assert _evento(con, "release_ts_utc")[0] == datetime(2026, 9, 30, 20, 30)


# ------------------------------------------------------------------ query ---
def _tre_eventi(con):
    ingest_observations(con, [
        _oss(symbol="AAPL", country="United States", sector="Electronic Technology",
             industry="Semiconductors", market_cap=3.4e12, status="occurred",
             eps_estimate=1.3, eps_actual=1.4,
             release_ts_utc=datetime(2026, 7, 30, 20, 30)),
        _oss(symbol="7203", exchange="TSE", country="Japan",
             sector="Producer Manufacturing", industry="Motor Vehicles",
             market_cap=3.0e11, release_ts_utc=datetime(2026, 8, 1, 6, 0)),
        _oss(symbol="SAN", exchange="BME", country="Spain", sector="Finance",
             industry="Major Banks", market_cap=2.5e11,
             release_ts_utc=datetime(2026, 8, 5, 5, 0)),
    ])


def test_the_query_window_is_half_open(con):
    _tre_eventi(con)
    dentro = events_between(con, "2026-08-01 06:00:00", "2026-08-05 05:00:00")
    assert [e["symbol"] for e in dentro] == ["7203"]     # start in, end out


def test_query_filters_are_case_insensitive(con):
    _tre_eventi(con)
    assert len(events_between(con, "2026-07-01", "2026-09-01", region="JAPAN")) == 1
    assert len(events_between(con, "2026-07-01", "2026-09-01", theme="AI_semis")) == 1
    assert len(events_between(con, "2026-07-01", "2026-09-01", status="Occurred")) == 1


def test_events_come_back_biggest_first(con):
    _tre_eventi(con)
    tutti = events_between(con, "2026-07-01", "2026-09-01")
    assert [e["symbol"] for e in tutti] == ["AAPL", "7203", "SAN"]
    grandi = events_between(con, "2026-07-01", "2026-09-01", min_market_cap=1e12)
    assert [e["symbol"] for e in grandi] == ["AAPL"]


def test_aggregate_buckets_and_counts_what_already_happened(con):
    _tre_eventi(con)
    per_regione = {r["bucket"]: r for r in
                   aggregate(con, "2026-07-01", "2026-09-01", by="region")}
    assert set(per_regione) == {"us", "japan", "europe"}
    assert per_regione["us"]["n"] == 1 and per_regione["us"]["occurred"] == 1
    assert per_regione["japan"]["occurred"] == 0
    assert per_regione["europe"]["market_cap_total"] == 2.5e11


def test_aggregate_refuses_a_grouping_it_cannot_serve(con):
    with pytest.raises(ValueError, match="cannot group by"):
        aggregate(con, "2026-07-01", "2026-09-01", by="ceo_mood")


def test_vocabulary_reports_the_counts_and_the_stored_window(con):
    _tre_eventi(con)
    v = vocabulary(con)
    assert {r["value"]: r["n"] for r in v["regions"]} == {"us": 1, "japan": 1, "europe": 1}
    assert {r["value"] for r in v["countries"]} == {"United States", "Japan", "Spain"}
    assert {r["value"]: r["n"] for r in v["statuses"]} == {"occurred": 1, "estimated": 2}
    assert v["stored_from"].startswith("2026-07-30")
    assert v["stored_to"].startswith("2026-08-05")


def test_an_empty_database_answers_with_empty_lists_not_an_error(con):
    assert events_between(con, "2026-07-01", "2026-09-01") == []
    assert aggregate(con, "2026-07-01", "2026-09-01", by="sector") == []
    v = vocabulary(con)
    assert v["regions"] == [] and v["themes"] == []
    assert v["stored_from"] is None and v["stored_to"] is None


# -------------------------------------------------------------- sorpresa ----
def test_surprise_is_the_ratio_of_the_miss_to_the_estimate(con):
    ingest_observations(con, [_oss(status="occurred", eps_estimate=2.0, eps_actual=2.5,
                                   revenue_estimate=100.0, revenue_actual=90.0)])
    r = con.execute(
        "SELECT eps_surprise, revenue_surprise FROM v_earnings_surprise").fetchone()
    assert r[0] == pytest.approx(0.25) and r[1] == pytest.approx(-0.10)


def test_a_release_that_has_not_happened_is_not_a_surprise(con):
    ingest_observations(con, [_oss(status="estimated", eps_estimate=2.0, eps_actual=2.5)])
    assert con.execute("SELECT count(*) FROM v_earnings_surprise").fetchone()[0] == 0


def test_a_zero_or_missing_estimate_yields_no_surprise(con):
    """Dividing by a breakeven forecast would publish an infinity as a beat."""
    ingest_observations(con, [
        _oss(symbol="ZERO", status="occurred", eps_estimate=0.0, eps_actual=0.4),
        _oss(symbol="NONE", status="occurred", eps_estimate=None, eps_actual=0.4),
    ])
    valori = dict(con.execute(
        "SELECT symbol, eps_surprise FROM v_earnings_surprise").fetchall())
    assert valori == {"ZERO": None, "NONE": None}


# --------------------------------------------------------------- lettura ----
_INTESTAZIONE = ("tv_ticker,symbol,company_name,market_cap,release_ts,country,"
                 "exchange,sector,industry,currency,eps_estimate,revenue_estimate,status")


def _unix(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def _riga(istante, simbolo="AAPL", stato="estimated", coda=""):
    return (f"NASDAQ:{simbolo},{simbolo},Apple,3.4e12,{istante},United States,"
            f"NASDAQ,Electronic Technology,Semiconductors,USD,1.3,9e10,{stato}{coda}")


def _csv(tmp_path, righe, intestazione=_INTESTAZIONE, nome="tv.csv"):
    percorso = tmp_path / nome
    percorso.write_text("\n".join([intestazione] + righe) + "\n", encoding="utf-8")
    return percorso


def test_a_midnight_release_is_known_to_the_day_not_to_the_minute(tmp_path):
    """The scanner reports midnight UTC when it has the date but not the hour;
    calling that minute-precise puts an Asian release on the previous day."""
    percorso = _csv(tmp_path, [
        _riga(_unix(datetime(2026, 7, 30))),
        _riga(_unix(datetime(2026, 8, 1, 6, 0)), simbolo="7203"),
    ])
    precisioni = {o.symbol: o.release_precision for o in leggi(percorso)}
    assert precisioni == {"AAPL": "day", "7203": "minute"}


def test_a_row_without_a_release_instant_is_skipped(tmp_path):
    percorso = _csv(tmp_path, [
        _riga(""),
        _riga(_unix(datetime(2026, 8, 5, 5, 0)), simbolo="SAN"),
    ])
    letti = leggi(percorso)
    assert [o.symbol for o in letti] == ["SAN"]
    assert letti[0].source == "tradingview"
    assert letti[0].release_ts_utc == datetime(2026, 8, 5, 5, 0)


def test_the_actual_columns_are_optional(tmp_path):
    senza = _csv(tmp_path, [_riga(_unix(datetime(2026, 7, 30, 20, 30)))])
    assert leggi(senza)[0].eps_actual is None

    con_attuali = _csv(tmp_path, [
        _riga(_unix(datetime(2026, 7, 30, 20, 30)), stato="occurred", coda=",1.4,9.1e10")],
        intestazione=_INTESTAZIONE + ",eps_actual,revenue_actual", nome="tv2.csv")
    o = leggi(con_attuali)[0]
    assert o.eps_actual == 1.4 and o.revenue_actual == 9.1e10 and o.status == "occurred"


def test_the_vintage_comes_from_the_file_not_from_today(tmp_path):
    """Re-ingesting an old CSV must not restamp it as a fresh reading: that
    turns an archived expectation into the newest word on the release."""
    percorso = _csv(tmp_path, [_riga(_unix(datetime(2026, 7, 30, 20, 30)))])
    os.utime(percorso, (_unix(datetime(2026, 8, 3, 12, 0)),
                        _unix(datetime(2026, 8, 3, 12, 0))))
    assert leggi(percorso)[0].vintage_date == date(2026, 8, 3)
    assert leggi(percorso, vintage_date=date(2026, 9, 1))[0].vintage_date == \
        date(2026, 9, 1)


# ----------------------------------------------------------------- audit ----
def test_audit_counts_an_event_no_observation_can_regenerate(con):
    """earnings_events is derived, so a row with no surviving observation is
    unreproducible: it would vanish on the next rebuild without notice."""
    con.execute("INSERT INTO earnings_events (event_id, symbol, exchange, status) "
                "VALUES ('orfano', 'X', 'NASDAQ', 'estimated')")
    ingest_observations(con, [_oss()])
    assert audit(con)["orphan_events"] == 1


def test_audit_counts_unknown_regions_and_unmapped_industries(con):
    ingest_observations(con, [
        _oss(symbol="OK", country="United States", industry="Semiconductors"),
        _oss(symbol="BOH", country="Atlantis", industry="Sorcery"),
    ])
    a = audit(con)
    assert a["unknown_region"] == 1
    assert a["unmapped_industry"] == 1
    assert a["missing_market_cap"] == 0


def test_audit_counts_the_two_contradictory_status_and_actual_pairs(con):
    """Both are one source disagreeing with itself: a release that happened
    with no figure, and one still pending that already carries one."""
    ingest_observations(con, [
        _oss(symbol="MUTO", status="occurred", eps_actual=None, revenue_actual=None),
        _oss(symbol="ANTICIPO", status="estimated", eps_actual=1.4),
        _oss(symbol="SANO", status="occurred", eps_actual=1.4),
    ])
    a = audit(con)
    assert a["occurred_without_actual"] == 1
    assert a["pending_with_actual"] == 1


# ---- storia di un evento --
def test_the_history_shows_the_date_that_moved(con):
    """The event carries the current answer; only the versions say it changed."""
    from market_data_hub.earnings_calendar import event_history

    ingest_observations(con, [_oss(release_ts_utc=datetime(2026, 9, 28, 6, 0),
                                   vintage_date=date(2026, 9, 1))])
    ingest_observations(con, [_oss(release_ts_utc=datetime(2026, 10, 2, 6, 0),
                                   vintage_date=date(2026, 9, 20))])

    eid = con.execute("SELECT event_id FROM earnings_events").fetchone()[0]
    storia = event_history(con, eid)
    assert storia["release_ts_utc"] == datetime(2026, 10, 2, 6, 0)
    assert [v["release_ts_utc"] for v in storia["versions"]] == [
        datetime(2026, 9, 28, 6, 0), datetime(2026, 10, 2, 6, 0)]


def test_an_unknown_event_has_no_history(con):
    from market_data_hub.earnings_calendar import event_history

    assert event_history(con, "nonesiste") == {}
