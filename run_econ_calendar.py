# -*- coding: utf-8 -*-
"""run_econ_calendar.py — collect the economic release calendar and ingest it.

Usage:
    python run_econ_calendar.py --db <path> --work-dir <dir>
    python run_econ_calendar.py --db <path> --work-dir <dir> --no-collect   # re-ingest what is on disk
    python run_econ_calendar.py --db <path> --work-dir <dir> --no-validate # skip the T1 web-search pass
    python run_econ_calendar.py --db <path> --audit-only

Two halves that fail for different reasons and are therefore separable:
collection writes the configured source CSV into --work-dir, ingestion reads that CSV
and consolidates it into the calendar tables. --no-collect re-runs the second
half alone, which is what you want after changing a matching rule: it costs
no requests.

A third, optional step follows ingestion: for that day's T1-criticality
releases, an LLM with live web search cross-checks our calendar's
actual/previous/consensus against what actually published, and flags any
mismatch in ``calendar_event_notes`` -- it does not correct the value.
``--no-validate`` skips it, the way ``--no-collect`` skips downloading: no
network cost, no LLM cost, useful for fast local iteration.

--db is required and should be absolute. market-data-hub resolves a relative
db_path inside its own repository, so a runner invoked from elsewhere with a
relative path silently creates an empty database next to the package and every
reader then answers "nothing found".
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from market_data_hub.db import connection as cx                       # noqa: E402
from market_data_hub.econ_calendar import (                           # noqa: E402
    ingest_observations, load_catalog_rows, upsert_indicators,
)
from market_data_hub.econ_calendar.aliases import (                   # noqa: E402
    cadence_violations, load_aliases, load_rejections, load_seed, unmapped,
)
from market_data_hub.econ_calendar.collect.consolidate import FONTI, raccogli  # noqa: E402
from market_data_hub.econ_calendar.reference import (                 # noqa: E402
    infer_reference_dates, validate_lags,
)

# The single source's CSV name, read off consolidate.FONTI rather than
# hardcoded a second time.
SOURCE_CSV = next(iter(FONTI))
SOURCE_NAME = FONTI[SOURCE_CSV][0]


# How far the collection reaches, in days either side of today.
#
# Backwards, to catch a figure the feed published late or revised. Forwards,
# because a calendar that only records what has already come out is half a
# calendar: the reason to keep one is to know what is due. The window ended at
# `oggi` until now, so the archive's furthest event was always this evening and
# every consumer asking "what is scheduled next week" got an empty answer that
# read like "nothing is scheduled".
#
# Seven days ahead is what a weekly reader needs, but it is a FILTER, not a
# promise: the source publishes one feed, `ff_calendar_thisweek.json`, and
# there is no next-week equivalent -- `ff_calendar_nextweek.json` answers 404,
# checked. So the real horizon is the end of the current week: six days ahead
# on a Monday, two on a Friday. The constant simply stops throwing away
# whatever the feed does offer, which is most of it -- measured live on
# 07/09/2026, one fetch returned 76 rows of which 60 were in the future, with
# their consensus figures attached.
#
# A future release has no published value, which is correct and already
# handled: it is ingested with status 'scheduled', the bridge skips it
# (`skipped_future`) and the web validation's window ends half an hour in the
# past, so nothing goes looking for a number that does not exist yet.
LOOKBACK_DAYS = 7
LOOKAHEAD_DAYS = 7


EXIT_OK = 0
EXIT_FAILED = 1
# 2 is NOT free: `run_marketdata_job.ps1`, the wrapper that launches this job,
# already reads it as "configuration refused" and writes that into the log.
EXIT_NOTHING_TO_DO = 3

# Above this share of feed rows that no rule ever looked at, the run SAYS it is
# degraded -- in the log, in full, every time. It does not change the exit code.
#
# 50%: the catalogue is meant to describe what matters in this feed, and once
# more than half falls through it unexamined the catalogue has stopped
# describing it. Measured on the production feed of 07/09/2026: 89 of 126 rows
# unseen before the coverage pass, 101 of 186 after it -- the pass moved 34
# rows into the catalogue and the forward window then brought a fresh week of
# uncatalogued ones with it.
#
# Which is the point: this is a STANDING CONDITION, not an event. It has been
# true every day this calendar has existed, so wiring it to the exit code made
# the task red every morning, and a task that is always red is one nobody
# reads. The number belongs where a person looks -- the log, and the macro
# document's own coverage line.
SOGLIA_RIGHE_NON_VISTE = 0.50


def exit_code(conteggi: dict, n_osservazioni: int, *,
              collezione_fallita: bool = False,
              soglia: float = SOGLIA_RIGHE_NON_VISTE) -> int:
    """What the run is worth, in the one byte Task Scheduler reads.

    Four outcomes, and the middle two are the point. The previous version
    returned ``0 if n_osservazioni else 1``: ONE observation out of a
    126-row feed exited 0, and ``audit()`` is documented as never fatal, so a
    run in which the calendar had almost entirely stopped understanding its
    source was indistinguishable from a healthy one.

    ``EXIT_NOTHING_TO_DO`` (3) is not invented here: it is the ecosystem's
    convention for 'this run had no work and that is fine, do not go red',
    used by LazyRay in ``run_stress_monitor.py`` ("Exit 3 ('nothing to do'),
    not a red task") and ``run_dalio_v2.py`` (``--if-changed``). It applies
    when the feed held no rows at all and nothing failed to fetch them --
    a re-ingest of an absent CSV, say -- which is an empty in-tray, not a
    fault.

    Degraded coverage is NOT one of them, and that is a reversal of my own
    earlier decision, made after running the thing.

    It was exit 2, on the argument that raising the bar until today's run
    passes would be choosing not to be told. Two facts came out of the first
    real run. The wrapper that launches this job, `run_marketdata_job.ps1`,
    already spends 2 on "configuration refused", so a degraded run wrote a
    false sentence into its own log. And the measurement is a STANDING
    CONDITION, not an event: the catalogue has never described more than half
    this feed, so the task would have been red every morning, which is how a
    reader is taught to stop reading red.

    An exit code is an alarm, and an alarm that is always on is furniture. The
    coverage numbers are printed in full on every run and carried into the
    macro document that a person actually reads; a real fault -- the feed
    spoke and the catalogue understood NONE of it -- is still exit 1.
    """
    if not conteggi.get('rows'):
        return EXIT_FAILED if collezione_fallita else EXIT_NOTHING_TO_DO
    if not n_osservazioni:
        # The feed spoke and the catalogue understood none of it. Not an empty
        # in-tray: a total matching failure, which is a fault.
        return EXIT_FAILED
    return EXIT_OK


def collect(work_dir: Path, da: str, a: str) -> bool:
    """Download the configured source into its CSV. Returns True on success.

    No stale-CSV cleanup occurs: a failed collection must not erase the
    accumulated history already available for ingestion.
    """
    uscita = work_dir / SOURCE_CSV
    print(f'\n--- {SOURCE_NAME} -> {uscita.name} ---', flush=True)
    try:
        from market_data_hub.econ_calendar.collect.forexfactory import scarica
        df = scarica(da, a, uscita)
    except Exception as e:
        print(f'  FAILED: {type(e).__name__}: {str(e)[:160]}', flush=True)
        return False

    if df is None or df.empty:
        print(f'  WARNING: {SOURCE_NAME} returned zero rows for the whole collection '
              'window; no fallback source is configured.', file=sys.stderr,
              flush=True)
        return False
    print(f'  {len(df)} rows -> {uscita}', flush=True)
    return True


def stampa_conteggi(conteggi: dict) -> None:
    """The three row verdicts, separately, because only one of them was visible.

    The old line said '60 righe respinte' and meant nothing: the counter lived
    inside the indicator x row double loop, so one rejected row was counted
    once per catalogue entry that shared its country. Five rows, printed as
    sixty. The category nobody could see at all was the third one -- the rows
    no rule looked at -- and on the production feed it is the majority.
    """
    righe = conteggi.get('rows') or 0
    if not righe:
        return
    quota = 100 * conteggi['unseen'] / righe
    print(f'\n  feed rows            {righe:5}')
    print(f'    matched            {conteggi["matched"]:5}')
    print(f'    ruled out          {conteggi["ruled_out"]:5}   '
          f'(an explicit decision in the alias table)')
    print(f'    never looked at    {conteggi["unseen"]:5}   ({quota:.0f}%; '
          f'{conteggi["unseen_uncovered_country"]} in countries the catalogue '
          f'does not cover)')


def audit(con) -> None:
    """The checks that say whether the run is worth trusting. Never fatal.

    Down to four checks now that there is only one collection source: the two that
    used to run here, disagreeing_bindings and suspect_matches, existed
    specifically to catch CROSS-SOURCE disagreement or name variance -- with
    one source there is nothing left for either to compare, so they were
    deleted along with audit.py rather than kept reporting an empty list
    forever.
    """
    print('\n=== audit ===')
    for etichetta, fn in (('unmapped names     ', unmapped),
                          ('cadence violations ', cadence_violations)):
        try:
            print(f'  {etichetta}: {len(fn(con))}')
        except Exception as e:
            print(f'  {etichetta}: could not run ({type(e).__name__}: {str(e)[:80]})')
    try:
        con.execute("""
            SELECT count(*) FILTER (WHERE reference_date IS NOT NULL), count(*)
            FROM calendar_events WHERE status = 'released'""")
        con_ref, totale = con.fetchone()
        quota = f'{100 * con_ref / totale:.0f}%' if totale else 'n/a'
        print(f'  reference_date     : {con_ref}/{totale} ({quota})')
    except Exception as e:
        print(f'  reference_date     : could not run ({str(e)[:80]})')
    try:
        print(f'  lag validation     : {len(validate_lags(con))} indicators measured')
    except Exception as e:
        print(f'  lag validation     : could not run ({str(e)[:80]})')


def main() -> int:
    p = argparse.ArgumentParser(description=f'economic calendar: collect and ingest ({SOURCE_NAME})')
    p.add_argument('--db', required=True,
                   help='DuckDB path. Absolute: a relative one resolves inside the repo.')
    p.add_argument('--work-dir', default='.',
                   help=f'where the {SOURCE_NAME} CSV is written and read')
    p.add_argument('--no-collect', action='store_true',
                   help='skip downloading; ingest the CSV already in --work-dir')
    p.add_argument('--no-validate', action='store_true',
                   help='skip the T1 web-search validation pass after ingest '
                        '(network + LLM cost; useful for fast local iteration)')
    p.add_argument('--no-bridge', action='store_true',
                   help='skip filling missing actuals from the macro series '
                        'already in this database (no network, no LLM cost)')
    p.add_argument('--validate-lookback-days', type=int, default=3,
                   help='days of safely-past T1 releases to validate/fill (default: 3)')
    p.add_argument('--audit-only', action='store_true',
                   help='run only the checks against the database')
    p.add_argument('--from', dest='da', default=None,
                   help=f'start date for {SOURCE_NAME} collection')
    p.add_argument('--to', dest='a', default=None)
    p.add_argument('--run-id', default=None)
    args = p.parse_args()

    work_dir = Path(args.work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    db = Path(args.db)
    if not db.is_absolute():
        print(f'WARNING: --db {args.db} is relative; resolving to {db.resolve()}',
              file=sys.stderr)

    con = cx.get_conn(str(db.resolve()))
    print(f'db: {db.resolve()}  (schema {cx.get_schema_version(con)})')

    if args.audit_only:
        audit(con)
        con.close()
        return 0

    # `timezone.utc`, not the `datetime.UTC` alias: that alias arrived in
    # 3.11, and pyproject declares `requires-python = ">=3.9"` while CI
    # tests 3.9 and 3.10. This one import failed both of them, on main,
    # before this branch existed -- the whole file could not even be
    # collected there.
    oggi = datetime.now(timezone.utc).date()
    collezione_riuscita = True
    if not args.no_collect:
        da = args.da or str(oggi - timedelta(days=LOOKBACK_DAYS))
        a = args.a or str(oggi + timedelta(days=LOOKAHEAD_DAYS))
        collezione_riuscita = collect(work_dir, da, a)

    print('\n=== consolidation ===')
    catalogo = load_catalog_rows()
    print(f'catalogue: {upsert_indicators(con, catalogo)} indicators')
    n_seed = load_seed(con)
    respinti = load_rejections(con)
    legami = load_aliases(con)
    print(f'per-source decisions: {n_seed} ({len(respinti)} rejected, {len(legami)} bound)')

    # raccogli() reads the CSV by its bare name, so it has to run where it is
    prima = os.getcwd()
    os.chdir(work_dir)
    try:
        osservazioni, per_fonte, conteggi = raccogli(catalogo, respinti, legami)
    finally:
        os.chdir(prima)

    for f, n in sorted(per_fonte.items()):
        print(f'  {f:14} {n:5} observations')
    print(f'  {"TOTAL":14} {len(osservazioni):5}')
    stampa_conteggi(conteggi)
    # Printed even when nothing failed, so a reader does not have to infer a
    # clean run from the absence of a line.
    #
    # Under --no-collect, collection was never attempted, so nothing about
    # it can be reported as having succeeded or failed today -- the CSV on
    # disk could be from a clean run, a failed one, or days ago.
    if args.no_collect:
        print('  collection: not attempted (--no-collect; re-ingesting what is on disk)')
    else:
        print('  collection: ' + ('ok' if collezione_riuscita
                                   else 'FAILED (see the FAILED/no rows line above)'))

    codice = exit_code(conteggi, len(osservazioni),
                       collezione_fallita=not (args.no_collect or collezione_riuscita))
    # Nothing new to ingest is not a reason to skip the rest.
    #
    # The bridge and the catch-up validation below work on events ALREADY in
    # the database and need no fresh observations at all -- the catch-up
    # exists precisely to revisit releases whose value is still missing weeks
    # later. Returning here meant that on any day the feed gave us nothing,
    # the one pass whose whole job is to close old holes did not run, which is
    # the opposite of what a quiet day should cost.
    if osservazioni:
        esito = ingest_observations(
            con, osservazioni,
            run_id=args.run_id or f'econ-calendar-{oggi}')
        print(f'\ningested: {esito}')

        # A period the source published is a fact; one derived from the
        # indicator's learned lag is an inference, and the two are kept apart
        # in reference_date_origin. Without this step only the first kind is
        # ever recorded, and reference_date sits at what the source happens to
        # publish.
        dedotti = infer_reference_dates(con)
        print(f'reference dates inferred: {dedotti}')
    else:
        print('\nnothing to ingest; running the recovery passes over what is '
              'already stored.', file=sys.stderr)

    # Before the web pass, not after: the bridge is deterministic, free and
    # reproducible, so anything it can fill must not be paid for a second time
    # by sending an LLM to look the same number up on the internet.
    if args.no_bridge:
        print('\nmacro bridge: skipped (--no-bridge)')
    else:
        print('\n=== macro bridge (fill from this database) ===')
        try:
            from market_data_hub.econ_calendar.macro_bridge import (
                bridged_indicators, fill_from_macro_series,
            )
            for i in (x for x in bridged_indicators(con) if not x['usable']):
                print(f'  refused {i["indicator_key"]} -> '
                      f'{i["macro_series_id"]}: {i["reason"]}')
            print(f'  {fill_from_macro_series(con, run_id=args.run_id or f"econ-calendar-{oggi}")}')
        except Exception as e:
            # Same contract as the validation pass: losing the bridge costs
            # the fill, not the ingest, which is already committed above.
            print(f'  could not run ({type(e).__name__}: {str(e)[:160]})')

    if args.no_validate:
        print('\nvalidate: skipped (--no-validate)')
    else:
        print('\n=== validate (T1, web search) ===')
        try:
            from market_data_hub.econ_calendar.validate import run_validation
            esito_validazione = run_validation(
                con, oggi, run_id=args.run_id or f'econ-calendar-{oggi}',
                lookback_days=args.validate_lookback_days)
            print(f'  {esito_validazione}')
        except Exception as e:
            # A validation failure costs the cross-check, not the ingest:
            # everything above this point is already written and stands.
            print(f'  could not run ({type(e).__name__}: {str(e)[:160]})')

    audit(con)
    con.close()
    # Said loudly, every run, and deliberately NOT in the exit code: see
    # exit_code's docstring for why an always-on alarm is furniture.
    if conteggi.get('rows') and conteggi['unseen'] > SOGLIA_RIGHE_NON_VISTE * conteggi['rows']:
        print(f'\nDEGRADED: {conteggi["unseen"]} of {conteggi["rows"]} feed rows '
              f'({100 * conteggi["unseen"] / conteggi["rows"]:.0f}%) met no matching '
              f'rule and no ruling, over the {100 * SOGLIA_RIGHE_NON_VISTE:.0f}% '
              f'threshold. What was ingested stands; the catalogue has stopped '
              f'describing the feed.', file=sys.stderr)
    return codice


if __name__ == '__main__':
    raise SystemExit(main())
