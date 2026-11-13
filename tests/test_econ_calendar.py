# -*- coding: utf-8 -*-
"""Economic calendar remnants: catalogue, frozen tables, raw FF archive.

The hub collector (collection, ingestion, validation, macro bridge) was
retired on 2026-11-11; the calendar is collected by LazyCommittee. What these
tests keep in place is what live consumers still read: the catalogue and its
discovery functions over the frozen ``calendar_*`` tables, the schema that
holds them, and the raw Forex Factory archive with its reader.
"""
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest

from market_data_hub.db import connection as cx
from market_data_hub.econ_calendar import load_catalog_rows, upsert_indicators
from market_data_hub.econ_calendar.catalog import (
    AREA_ISO3,
    available_series,
    catalogue_vocabulary,
    to_iso3,
)


@pytest.fixture()
def con():
    c = duckdb.connect(":memory:")
    cx.migrate(c)
    yield c
    c.close()



@pytest.fixture()
def raw_archive(monkeypatch, tmp_path):
    import sys
    import run_econ_calendar as runner
    from market_data_hub.econ_calendar import ff_archive as forexfactory
    from market_data_hub import lock

    def forbidden(*args, **kwargs):
        pytest.fail('archive mode opened a DB or acquired the market_data lock')

    monkeypatch.setattr(cx, 'get_conn', forbidden)
    monkeypatch.setattr(duckdb, 'connect', forbidden)
    monkeypatch.setattr(lock, 'db_write_lock', forbidden)
    monkeypatch.setattr(sys, 'argv', [
        'run_econ_calendar.py', '--archive-only', '--raw-dir', str(tmp_path / 'raw'),
    ])

    class LocalClock:
        @classmethod
        def now(cls):
            # Local fetch day, even though UTC is already the following day.
            return datetime(2026, 9, 5, 23, 30, 12,
                            tzinfo=timezone(timedelta(hours=-7)))

    monkeypatch.setattr(forexfactory, 'datetime', LocalClock)
    # Never really wait out the retry backoff; record what was asked for.
    forexfactory.sleeps = sleeps = []
    monkeypatch.setattr(forexfactory, '_sleep', sleeps.append, raising=False)
    return runner, forexfactory, tmp_path / 'raw'


def _snapshots(raw):
    return sorted(p.name for p in raw.glob('2*.json'))


def _manifest(raw):
    return [json.loads(line) for line in
            (raw / 'manifest.jsonl').read_text(encoding='utf-8').splitlines()]


def test_runner_refuses_the_retired_collection_mode(monkeypatch, capsys):
    """Only --archive-only survives; anything else names the retirement."""
    import sys
    import run_econ_calendar as runner

    monkeypatch.setattr(sys, 'argv', ['run_econ_calendar.py'])
    with pytest.raises(SystemExit) as exc:
        runner.main()
    assert exc.value.code == 2
    assert 'retired' in capsys.readouterr().err


def test_raw_archive_first_unchanged_changed_and_unchanged_revision(raw_archive, monkeypatch):
    runner, ff, raw = raw_archive
    recorded = (Path(__file__).parent / 'fixtures' /
                'ff_calendar_thisweek_2026-09-05.json').read_bytes()
    revised = recorded.replace(b'G20 Meetings', b'G20 Revised Meetings')
    assert revised != recorded
    calls = []
    contents = iter([recorded, recorded, revised, revised])

    def fetch(url, **kwargs):
        calls.append(url)
        response = type('Response', (), {'status_code': 200, 'content': next(contents)})()
        return response

    monkeypatch.setattr(ff.requests, 'get', fetch)
    assert runner.main() == 0
    assert (raw / '2026-09-05.json').read_bytes() == recorded
    assert runner.main() == 3
    assert len(_snapshots(raw)) == 1
    assert runner.main() == 0
    assert (raw / '2026-09-05.233012.json').read_bytes() == revised
    assert (raw / '2026-09-05.json').read_bytes() == recorded
    assert runner.main() == 3
    assert len(_snapshots(raw)) == 2
    assert calls == [ff.URL] * 4
    assert not (raw.parent / 'unused').exists()


@pytest.mark.parametrize('failure', ['network', 'http'])
def test_raw_archive_fetch_failure_leaves_no_partial_file(raw_archive, monkeypatch, failure):
    runner, ff, raw = raw_archive
    calls = []

    def fetch(*args, **kwargs):
        calls.append(args)
        if failure == 'network':
            raise ff.requests.RequestException('recorded offline failure')
        return type('Response', (), {'status_code': 503, 'content': b'error'})()

    monkeypatch.setattr(ff.requests, 'get', fetch)
    assert runner.main() == 1
    assert len(calls) == 2  # one retry, never more
    assert ff.sleeps == [ff.RETRY_BACKOFF_S]
    assert _snapshots(raw) == []


@pytest.mark.parametrize('body', [b'', b'<html>Request denied</html>', b'{"error": "rate limited"}'])
def test_raw_archive_rejects_a_non_feed_body(raw_archive, monkeypatch, body):
    # An HTTP 200 block page or empty body must not become the day's snapshot.
    runner, ff, raw = raw_archive
    monkeypatch.setattr(ff.requests, 'get', lambda *a, **k:
                        type('Response', (), {'status_code': 200, 'content': body})())
    assert runner.main() == 1
    assert _snapshots(raw) == []


def test_raw_archive_rename_failure_cleans_temp(raw_archive, monkeypatch):
    runner, ff, raw = raw_archive
    monkeypatch.setattr(ff.requests, 'get', lambda *a, **k:
                        type('Response', (), {'status_code': 200, 'content': b'[]\n'})())

    def fail_rename(*args):
        raise OSError('recorded rename failure')

    monkeypatch.setattr(Path, 'rename', fail_rename)
    assert runner.main() == 1
    assert [p.name for p in raw.iterdir()] == ['manifest.jsonl']  # no temp file left
    assert _manifest(raw)[0]['outcome'] == 'failed'


def _response(status=200, content=b'[]\n'):
    return type('Response', (), {'status_code': status, 'content': content})()


def test_manifest_lines_for_written_unchanged_and_failed(raw_archive, monkeypatch):
    runner, ff, raw = raw_archive
    recorded = (Path(__file__).parent / 'fixtures' /
                'ff_calendar_thisweek_2026-09-05.json').read_bytes()
    replies = iter([_response(200, recorded), _response(200, recorded),
                    _response(404, b'gone')])
    monkeypatch.setattr(ff.requests, 'get', lambda *a, **k: next(replies))
    assert [runner.main() for _ in range(3)] == [0, 3, 1]
    written, unchanged, failed = _manifest(raw)
    sha = hashlib.sha256(recorded).hexdigest()
    days = sorted(e['date'][:10] for e in json.loads(recorded))
    # 23:30:12 at UTC-7 is 06:30:12Z the next day; local_date stays the local one.
    assert written == {
        'captured_at_utc': '2026-09-06T06:30:12.000000Z', 'local_date': '2026-09-05',
        'outcome': 'written', 'file': str(raw / '2026-09-05.json'),
        'sha256': sha, 'bytes': len(recorded), 'http_status': 200, 'error': None,
        'feed_week_start': days[0], 'feed_week_end': days[-1],
    }
    assert unchanged['outcome'] == 'unchanged'
    assert unchanged['file'] == str(raw / '2026-09-05.json')
    assert unchanged['sha256'] == sha
    assert failed['outcome'] == 'failed'
    assert failed['file'] is None
    assert failed['http_status'] == 404
    assert 'HTTP 404' in failed['error']
    assert len(failed['error']) <= 200
    assert _snapshots(raw) == ['2026-09-05.json']
    assert len((raw / 'manifest.jsonl').read_text().splitlines()) == 3


def test_429_retries_once_after_backoff_then_succeeds(raw_archive, monkeypatch):
    runner, ff, raw = raw_archive
    replies = iter([_response(429, b'slow down'), _response(200)])
    calls = []
    monkeypatch.setattr(ff.requests, 'get', lambda *a, **k: calls.append(1) or next(replies))
    assert runner.main() == 0
    assert len(calls) == 2
    assert ff.sleeps == [ff.RETRY_BACKOFF_S] and ff.RETRY_BACKOFF_S >= 60
    (line,) = _manifest(raw)
    assert line['outcome'] == 'written' and line['http_status'] == 200


@pytest.mark.parametrize('first', ['timeout', 'connection', 500, 503, 429])
def test_retry_happens_once_and_only_once(raw_archive, monkeypatch, first):
    runner, ff, raw = raw_archive
    calls = []

    def fetch(*args, **kwargs):
        calls.append(1)
        if first == 'timeout':
            raise ff.requests.Timeout('read timed out')
        if first == 'connection':
            raise ff.requests.ConnectionError('refused https://nfs.faireconomy.media/x?k=secret')
        return _response(first, b'nope')

    monkeypatch.setattr(ff.requests, 'get', fetch)
    assert runner.main() == 1  # both attempts fail: non-zero for the watchdog
    assert len(calls) == 2  # never a third call
    assert len(ff.sleeps) == 1
    (line,) = _manifest(raw)  # one line per run, not per HTTP call
    assert line['outcome'] == 'failed'
    assert 'secret' not in line['error'] and 'faireconomy' not in line['error']
    assert line['http_status'] == (first if isinstance(first, int) else None)


def test_non_retryable_status_is_not_retried(raw_archive, monkeypatch):
    runner, ff, raw = raw_archive
    calls = []
    monkeypatch.setattr(ff.requests, 'get', lambda *a, **k: calls.append(1) or _response(403))
    assert runner.main() == 1
    assert len(calls) == 1 and ff.sleeps == []
    assert _manifest(raw)[0]['http_status'] == 403


@pytest.mark.parametrize('body', [b'<html>blocked</html>', b'', b'{"a": 1}'])
def test_unparsable_body_no_retry_and_failed_line(raw_archive, monkeypatch, body):
    runner, ff, raw = raw_archive
    calls = []
    monkeypatch.setattr(ff.requests, 'get',
                        lambda *a, **k: calls.append(1) or _response(200, body))
    assert runner.main() == 1
    assert len(calls) == 1 and ff.sleeps == []
    (line,) = _manifest(raw)
    assert line['outcome'] == 'failed' and line['file'] is None
    assert line['http_status'] == 200
    assert 'not a JSON list' in line['error']
    assert line['bytes'] == len(body)
    assert line['feed_week_start'] is None
    assert _snapshots(raw) == []


def test_at_most_two_http_calls_per_run(raw_archive, monkeypatch):
    runner, ff, raw = raw_archive
    calls = []
    monkeypatch.setattr(ff.requests, 'get', lambda *a, **k: calls.append(1) or _response(429))
    for _ in range(3):
        assert runner.main() == 1
    assert len(calls) == 6  # 2 per run
    assert len(_manifest(raw)) == 3


def test_load_snapshots_orders_by_time_and_falls_back_to_mtime(tmp_path):
    from market_data_hub.econ_calendar.ff_archive import load_snapshots
    raw = tmp_path / 'raw'
    raw.mkdir()
    for name in ('2026-09-05.json', '2026-09-05.233012.json', '2026-09-04.json',
                 '2026-09-03.json'):
        (raw / name).write_text('[]')
    # Manifest order differs from time order; only 'written' lines count; a torn
    # line is skipped; 09-03 and 09-04 are not in the manifest -> mtime.
    lines = [
        {'captured_at_utc': '2026-09-05T11:00:00Z', 'outcome': 'written',
         'file': str(raw / '2026-09-05.233012.json')},
        {'captured_at_utc': '2026-09-05T10:00:00Z', 'outcome': 'unchanged',
         'file': str(raw / '2026-09-05.json')},
        {'captured_at_utc': '2026-09-05T09:00:00Z', 'outcome': 'written',
         'file': str(raw / '2026-09-05.json')},
        {'captured_at_utc': '2026-09-05T12:00:00Z', 'outcome': 'failed', 'file': None},
    ]
    (raw / 'manifest.jsonl').write_text(
        '\n'.join(json.dumps(x) for x in lines) + '\nnot json{\n', encoding='utf-8')
    for name, when in (('2026-09-04.json', '2026-09-04T08:00:00'),
                       ('2026-09-03.json', '2026-09-03T08:00:00')):
        stamp = datetime.fromisoformat(when).replace(tzinfo=timezone.utc).timestamp()
        os.utime(raw / name, (stamp, stamp))

    snaps = load_snapshots(raw)
    assert [(t.isoformat(), p.name) for t, p in snaps] == [
        ('2026-09-03T08:00:00+00:00', '2026-09-03.json'),
        ('2026-09-04T08:00:00+00:00', '2026-09-04.json'),
        ('2026-09-05T09:00:00+00:00', '2026-09-05.json'),
        ('2026-09-05T11:00:00+00:00', '2026-09-05.233012.json'),
    ]
    assert [s.captured_at_source for s in snaps] == ['mtime', 'mtime', 'manifest', 'manifest']
    # The consumer's rule: last snapshot captured strictly before the release.
    release = datetime(2026, 9, 5, 11, 0, 0, tzinfo=timezone.utc)
    assert [p.name for t, p in snaps if t < release][-1] == '2026-09-05.json'


def test_load_snapshots_without_manifest_or_directory(tmp_path):
    from market_data_hub.econ_calendar.ff_archive import load_snapshots
    assert load_snapshots(tmp_path / 'missing') == []
    (tmp_path / 'notes.json').write_text('[]')
    (tmp_path / '2026-09-05.json').write_text('[]')
    (only,) = load_snapshots(tmp_path)
    assert only[1].name == '2026-09-05.json' and only.captured_at_source == 'mtime'


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


def test_retired_calendar_views_are_gone(con):
    """The EconCalendar job was retired; its read views served data frozen
    on 25/09/2026, so schema v28 drops them (the tables stay as history)."""
    views = {r[0] for r in con.execute(
        "SELECT view_name FROM duckdb_views() WHERE NOT internal").fetchall()}
    assert not views & {"v_calendar_surprise", "v_calendar_upcoming"}
    assert con.execute("SELECT count(*) FROM calendar_events").fetchone() == (0,)


def test_v27_database_loses_the_calendar_views_on_migrate():
    c = duckdb.connect(":memory:")
    cx.migrate(c)
    c.execute("CREATE VIEW v_calendar_upcoming AS SELECT * FROM calendar_events")
    c.execute("CREATE VIEW v_calendar_surprise AS SELECT * FROM calendar_events")
    c.execute("UPDATE schema_meta SET value = '27' WHERE key = 'schema_version'")
    assert cx.migrate(c) == cx.SCHEMA_VERSION
    views = {r[0] for r in c.execute(
        "SELECT view_name FROM duckdb_views() WHERE NOT internal").fetchall()}
    c.close()
    assert not views & {"v_calendar_surprise", "v_calendar_upcoming"}


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


def test_discovery_answers_without_knowing_an_indicator_key(con):
    """The point of the function: an agent asks in the terms it thinks in."""
    upsert_indicators(con, load_catalog_rows())
    # One released event, written straight into the frozen table: the hub no
    # longer has a write path, and the discovery reader is what is under test.
    con.execute(
        "INSERT INTO calendar_events (event_id, indicator_key, country_iso3, "
        "release_utc, reference_period, reference_date, status, actual) "
        "VALUES ('e1', 'us_cpi_yy', 'USA', TIMESTAMP '2026-08-12 12:30:00', "
        "'Jul', DATE '2026-07-31', 'released', '3.4%')")

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
    assert v["data_type"] == {"hard": 121, "soft": 26}
    assert set(v["side"]) == {"demand", "supply"}
    assert v["category"]["Inflation"] == 32
    # tags are pipe-packed in the column and counted individually here
    assert "flash_final" in v["tags"] and "|" not in "".join(v["tags"])


def test_filters_are_case_insensitive(con):
    """An agent writing 'inflation' should get the answer, not silence."""
    upsert_indicators(con, load_catalog_rows())
    assert (len(available_series(con, category="inflation"))
            == len(available_series(con, category="Inflation")) == 32)
    assert len(available_series(con, country="usa")) == \
        len(available_series(con, country="USA"))


# ------------------------------------------------------ shape of the file ---
def test_the_catalogue_has_the_shape_the_loader_and_the_matcher_assume(con):
    """There was no test on the shape of econ_calendar.yaml, only on what it
    happens to contain: ``len(rows) > 100`` and three spot checks. Adding
    nineteen entries by hand is exactly the occasion on which a missing field
    or a mistyped archetype gets in, and both fail silently -- an unknown
    archetype flattens to a null value_type, an empty match_rules matches every
    row of its country.
    """
    righe = load_catalog_rows()
    chiavi = [r['indicator_key'] for r in righe]
    assert len(set(chiavi)) == len(chiavi), 'duplicate indicator_key'

    for r in righe:
        dove = r['indicator_key']
        assert r['name'] and r['area'] and r['category'], f'{dove}: empty identity'
        assert r['country_iso2'] in AREA_ISO3, f'{dove}: {r["country_iso2"]!r}'
        assert r['criticality'] in {'T1', 'T2', 'T3'}, f'{dove}: {r["criticality"]!r}'
        assert r['frequency'] in {'W', 'M', 'Q', 'A', 'E'}, f'{dove}: {r["frequency"]!r}'
        assert r['nature'] in {'leading', 'coincident', 'lagging', 'policy'}, dove
        # 'policy' is the nature of a decision, and the only nature a decision
        # can have: the twelve rate entries all carried it and the thirteenth
        # came in as 'leading' until this said so.
        assert (r['nature'] == 'policy') == (r['category'] == 'Monetary policy'), dove
        assert r['data_type'] in {'hard', 'soft', None}, dove
        assert r['side'] in {'demand', 'supply', None}, dove
        # the archetype resolved: a key with no archetype flattens to nulls and
        # the indicator arrives in the database with no unit and no description
        assert r['archetype'] and r['value_type'] and r['description'], \
            f'{dove}: archetype {r["archetype"]!r} did not resolve'
        # an empty rule is not a permissive rule, it is a rule that matches
        # every row of the country
        assert (r['match_rules'] or '').strip(), f'{dove}: empty match_rules'

    # macro_series_id is the bridge to the hub's own history and has rules of
    # its own (the retired macro bridge: pure levels, same unit, no transform). None of
    # the coverage entries claims one.
    nuovi = {'us_adp', 'us_ism_prices', 'us_pmi_mfg', 'us_pmi_svc', 'us_factory',
             'cn_pmi_nbs_svc', 'ez_employment', 'de_cpi_mm', 'de_pmi_mfg',
             'de_pmi_svc', 'de_retail', 'jp_earnings', 'ca_pmi_mfg', 'ch_cpi_mm',
             'ch_cpi_yy', 'ch_gdp', 'ch_unemp', 'ch_pmi_mfg', 'nz_rbnz'}
    assert nuovi <= set(chiavi)
    assert all(r['macro_series_id'] is None for r in righe
               if r['indicator_key'] in nuovi)


def test_load_snapshots_keeps_subsecond_order(tmp_path):
    """Two different feeds captured within one second: the later one wins."""
    from market_data_hub.econ_calendar.ff_archive import load_snapshots
    raw = tmp_path / 'raw'
    raw.mkdir()
    for name in ('2026-09-05.json', '2026-09-05.090000.json'):
        (raw / name).write_text('[]')
    lines = [
        {'captured_at_utc': '2026-09-05T09:00:00.100000Z', 'outcome': 'written',
         'file': str(raw / '2026-09-05.json')},
        {'captured_at_utc': '2026-09-05T09:00:00.900000Z', 'outcome': 'written',
         'file': str(raw / '2026-09-05.090000.json')},
    ]
    (raw / 'manifest.jsonl').write_text('\n'.join(json.dumps(x) for x in lines) + '\n', encoding='utf-8')
    release = datetime(2026, 9, 5, 9, 0, 1, tzinfo=timezone.utc)
    snaps = load_snapshots(raw)
    assert [p.name for t, p in snaps if t < release][-1] == '2026-09-05.090000.json'


def test_load_snapshots_uses_unchanged_evidence_over_mtime(tmp_path):
    """A pre-manifest snapshot later confirmed 'unchanged' keeps that exact time,
    even if a restore moved its mtime after the release."""
    from market_data_hub.econ_calendar.ff_archive import load_snapshots
    raw = tmp_path / 'raw'
    raw.mkdir()
    (raw / '2026-09-05.json').write_text('[]')
    (raw / 'manifest.jsonl').write_text(json.dumps(
        {'captured_at_utc': '2026-09-05T09:00:00Z', 'outcome': 'unchanged',
         'file': str(raw / '2026-09-05.json')}) + '\n', encoding='utf-8')
    late = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc).timestamp()
    os.utime(raw / '2026-09-05.json', (late, late))
    [snap] = load_snapshots(raw)
    assert snap[0] == datetime(2026, 9, 5, 9, 0, tzinfo=timezone.utc)
    assert snap.captured_at_source == 'manifest'


def test_load_snapshots_resolves_windows_paths_on_any_os(tmp_path):
    from market_data_hub.econ_calendar.ff_archive import load_snapshots
    raw = tmp_path / 'raw'
    raw.mkdir()
    (raw / '2026-09-05.json').write_text('[]')
    (raw / 'manifest.jsonl').write_text(json.dumps(
        {'captured_at_utc': '2026-09-05T09:00:00Z', 'outcome': 'written',
         'file': r'C:\ProgramData\InvestmentCommittee\econ_calendar\raw\2026-09-05.json'}) + '\n',
        encoding='utf-8')
    [snap] = load_snapshots(raw)
    assert snap[1].name == '2026-09-05.json' and snap.captured_at_source == 'manifest'
