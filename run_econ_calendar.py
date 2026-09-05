# -*- coding: utf-8 -*-
"""run_econ_calendar.py — collect the economic release calendar and ingest it.

Usage:
    python run_econ_calendar.py --db <path> --work-dir <dir>
    python run_econ_calendar.py --db <path> --work-dir <dir> --no-collect   # re-ingest what is on disk
    python run_econ_calendar.py --db <path> --work-dir <dir> --no-validate # skip the T1 web-search pass
    python run_econ_calendar.py --db <path> --audit-only

Two halves that fail for different reasons and are therefore separable:
collection writes myfxbook's CSV into --work-dir, ingestion reads that CSV
and consolidates it into the calendar tables. --no-collect re-runs the second
half alone, which is what you want after changing a matching rule: it costs
no requests and no browser.

A third, optional step follows ingestion: for that day's T1-criticality
releases, an LLM with live web search cross-checks MyFXBook's own
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
from datetime import UTC, datetime, timedelta
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
# hardcoded a second time: {'myfxbook.csv': ('myfxbook', 'aggregator')}.
MYFXBOOK_CSV = next(iter(FONTI))


def exit_code(n_osservazioni: int) -> int:
    """0 clean, 1 failed -- two outcomes, not three.

    The old three-way split (clean / degraded-exit-2 / failed) existed
    because a dead source among five still left a run partially useful: "4
    of 5 succeeded" needed to be distinguishable from "all 5 succeeded", or
    a caller that only reads the exit code -- Task Scheduler is exactly such
    a caller -- could not tell a degraded run from a clean one. Measured on
    2026-08-17 with yahoo down: reference_date coverage came out at 11%,
    against the 44% a clean run got, while the exit code still said 0.

    MyFXBook is the only source left, and that middle case goes with the
    other four: there is no partial credit for one source, only whether it
    produced anything to ingest. Two outcomes cover that completely.

    Decided on `n_osservazioni`, not on whether collection raised: a source
    can "succeed" -- raise nothing -- and still return an empty frame, which
    is a total failure by outcome even though nothing crashed.
    """
    return 0 if n_osservazioni else 1


def collect(work_dir: Path, da: str, a: str) -> bool:
    """Download myfxbook into its CSV. Returns True on success, False on failure.

    No stale-CSV cleanup here, unlike the old multi-source version: myfxbook
    writes incrementally and resumes across runs by design, so a failure
    partway through still leaves whatever it managed to commit on disk, and
    that is exactly what should survive -- there is no other source's file
    that could go stale behind it.
    """
    uscita = work_dir / MYFXBOOK_CSV
    print(f'\n--- myfxbook -> {uscita.name} ---', flush=True)
    try:
        from market_data_hub.econ_calendar.collect.myfxbook import scarica
        df = scarica(da, a, uscita)
    except Exception as e:
        print(f'  FAILED: {type(e).__name__}: {str(e)[:160]}', flush=True)
        return False

    if df is None or df.empty:
        print('  WARNING: MyFXBook returned zero rows for the whole collection '
              'window; no fallback source is configured.', file=sys.stderr,
              flush=True)
        return False
    print(f'  {len(df)} rows -> {uscita}', flush=True)
    return True


def audit(con) -> None:
    """The checks that say whether the run is worth trusting. Never fatal.

    Down to four checks now that myfxbook is the only source: the two that
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
    p = argparse.ArgumentParser(description='economic calendar: collect and ingest (myfxbook)')
    p.add_argument('--db', required=True,
                   help='DuckDB path. Absolute: a relative one resolves inside the repo.')
    p.add_argument('--work-dir', default='.',
                   help='where the myfxbook CSV is written and read')
    p.add_argument('--no-collect', action='store_true',
                   help='skip downloading; ingest the CSV already in --work-dir')
    p.add_argument('--no-validate', action='store_true',
                   help='skip the T1 web-search validation pass after ingest '
                        '(network + LLM cost; useful for fast local iteration)')
    p.add_argument('--audit-only', action='store_true',
                   help='run only the checks against the database')
    p.add_argument('--from', dest='da', default=None,
                   help='start date for myfxbook collection')
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

    oggi = datetime.now(UTC).date()
    collezione_riuscita = True
    if not args.no_collect:
        da = args.da or str(oggi - timedelta(days=7))
        a = args.a or str(oggi)
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
        osservazioni, per_fonte = raccogli(catalogo, respinti, legami)
    finally:
        os.chdir(prima)

    for f, n in sorted(per_fonte.items()):
        print(f'  {f:14} {n:5} observations')
    print(f'  {"TOTAL":14} {len(osservazioni):5}')
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

    if not osservazioni:
        print('\nnothing to ingest.', file=sys.stderr)
        con.close()
        return exit_code(0)

    esito = ingest_observations(
        con, osservazioni,
        run_id=args.run_id or f'econ-calendar-{oggi}')
    print(f'\ningested: {esito}')

    # A period the source published is a fact; one derived from the indicator's
    # learned lag is an inference, and the two are kept apart in
    # reference_date_origin. Without this step only the first kind is ever
    # recorded, and reference_date sits at what the source happens to publish.
    dedotti = infer_reference_dates(con)
    print(f'reference dates inferred: {dedotti}')

    if args.no_validate:
        print('\nvalidate: skipped (--no-validate)')
    else:
        print('\n=== validate (T1, web search) ===')
        try:
            from market_data_hub.econ_calendar.validate import run_validation
            esito_validazione = run_validation(
                con, oggi, run_id=args.run_id or f'econ-calendar-{oggi}')
            print(f'  {esito_validazione}')
        except Exception as e:
            # A validation failure costs the cross-check, not the ingest:
            # everything above this point is already written and stands.
            print(f'  could not run ({type(e).__name__}: {str(e)[:160]})')

    audit(con)
    con.close()
    return exit_code(len(osservazioni))


if __name__ == '__main__':
    raise SystemExit(main())
