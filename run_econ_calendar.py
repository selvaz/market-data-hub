# -*- coding: utf-8 -*-
"""
run_econ_calendar.py — collect the economic release calendar and ingest it.

Usage:
    python run_econ_calendar.py --db <path> --work-dir <dir>
    python run_econ_calendar.py --db <path> --work-dir <dir> --sources nasdaq forexfactory
    python run_econ_calendar.py --db <path> --work-dir <dir> --no-collect   # re-ingest what is on disk
    python run_econ_calendar.py --db <path> --audit-only

Two halves that fail for different reasons and are therefore separable:
collection writes one CSV per source into --work-dir, ingestion reads those CSVs
and consolidates them into the calendar tables. --no-collect re-runs the second
half alone, which is what you want after changing a matching rule: it costs no
requests and no browser.

--db is required and should be absolute. market-data-hub resolves a relative
db_path inside its own repository, so a runner invoked from elsewhere with a
relative path silently creates an empty database next to the package and every
reader then answers "nothing found".
"""
import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from market_data_hub.db import connection as cx                       # noqa: E402
from market_data_hub.econ_calendar import (                           # noqa: E402
    ingest_observations, load_catalog_rows, upsert_indicators,
)
from market_data_hub.econ_calendar.aliases import (                   # noqa: E402
    cadence_violations, load_aliases, load_rejections, load_seed, unmapped,
)
from market_data_hub.econ_calendar.audit import (                     # noqa: E402
    disagreeing_bindings, suspect_matches,
)
from market_data_hub.econ_calendar.collect.consolidate import FONTI, raccogli  # noqa: E402
from market_data_hub.econ_calendar.reference import (                 # noqa: E402
    infer_reference_dates, validate_lags,
)

# source name -> the CSV consolidate.py expects it in
FILE_PER_FONTE = {fonte: file for file, (fonte, _) in FONTI.items()}


def exit_code(guasti: int, n_fonti: int, n_osservazioni: int) -> int:
    """0 clean, 1 total failure, 2 degraded -- three outcomes, not two.

    One dead source does not void the others: the run above still ingests
    what the rest collected, and a day covered by four sources is worth
    having. But "some collected" and "all collected" used to both return 0,
    which is indistinguishable to a caller that only reads the exit code --
    and Task Scheduler is exactly such a caller.

    The middle case is not cosmetic. Measured on 2026-08-17 with yahoo down:
    reference_date coverage came out at 11%, against the 44% a clean run
    gets, because yahoo is the only source that publishes the reference
    period.
    """
    if n_osservazioni == 0:
        return 1
    if guasti == 0:
        return 0
    return 2


def collect(fonti, work_dir: Path, da: str, a: str,
            yahoo_da: str, yahoo_a: str, settimane: int) -> int:
    """Download each requested source into its CSV. Returns the failure count."""
    guasti = 0
    for fonte in fonti:
        uscita = work_dir / FILE_PER_FONTE[fonte]
        print(f'\n--- {fonte} -> {uscita.name} ---', flush=True)
        scritto_da_se = False
        try:
            # Imported per source: four of the five need selenium and a browser,
            # and a run limited to nasdaq must not require either.
            if fonte == 'nasdaq':
                from market_data_hub.econ_calendar.collect.nasdaq import scarica
                df = scarica(da, a)
            elif fonte == 'forexfactory':
                from market_data_hub.econ_calendar.collect.forexfactory import scarica
                df = scarica()
            elif fonte == 'myfxbook':
                # Writes as it goes and keeps a resume registry: a day costs
                # about thirteen seconds, so a half-finished run that started
                # over would never finish.
                from market_data_hub.econ_calendar.collect.myfxbook import scarica
                df = scarica(da, a, uscita)
                scritto_da_se = True
            elif fonte == 'tradays':
                from market_data_hub.econ_calendar.collect.tradays import scarica
                df = scarica(settimane)
            elif fonte == 'yahoo':
                from market_data_hub.econ_calendar.collect.yahoo import scarica_intervallo
                df = scarica_intervallo(yahoo_da, yahoo_a)
            else:
                raise ValueError(f'unknown source {fonte!r}')
        except Exception as e:
            # One dead source does not void the others: the consolidation is
            # multi-source precisely so it can survive losing one, and a day
            # covered by four is worth having.
            guasti += 1
            print(f'  FAILED: {type(e).__name__}: {str(e)[:160]}', flush=True)
            continue

        if df is None or df.empty:
            guasti += 1
            print('  no rows', flush=True)
            continue
        if not scritto_da_se:
            df.to_csv(uscita, index=False, encoding='utf-8-sig')
        print(f'  {len(df)} rows -> {uscita}', flush=True)
    return guasti


def audit(con) -> None:
    """The checks that say whether the run is worth trusting. Never fatal."""
    print('\n=== audit ===')
    for etichetta, fn in (('disagreeing values ', disagreeing_bindings),
                          ('suspect names      ', suspect_matches),
                          ('cadence violations ', cadence_violations),
                          ('unmapped names     ', unmapped)):
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
    p = argparse.ArgumentParser(description='economic calendar: collect and ingest')
    p.add_argument('--db', required=True,
                   help='DuckDB path. Absolute: a relative one resolves inside the repo.')
    p.add_argument('--work-dir', default='.',
                   help='where the per-source CSVs are written and read')
    p.add_argument('--sources', nargs='+', choices=sorted(FILE_PER_FONTE),
                   help='limit to the given sources (default: all)')
    p.add_argument('--no-collect', action='store_true',
                   help='skip downloading; ingest the CSVs already in --work-dir')
    p.add_argument('--audit-only', action='store_true',
                   help='run only the checks against the database')
    p.add_argument('--yahoo-from', default=None)
    p.add_argument('--yahoo-to', default=None)
    p.add_argument('--from', dest='da', default=None,
                   help='start date for the day-by-day sources (nasdaq, myfxbook)')
    p.add_argument('--to', dest='a', default=None)
    p.add_argument('--settimane', type=int, default=13,
                   help='quante settimane indietro chiedere a tradays')
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

    fonti = args.sources or sorted(FILE_PER_FONTE)
    guasti = 0
    oggi = date.today()
    if not args.no_collect:
        da = args.da or str(oggi - timedelta(days=7))
        a = args.a or str(oggi)
        # Yahoo is the only source that publishes the reference period, and its
        # strength is the future: it is asked for a window reaching past the
        # quarterlies rather than the same week as the others.
        guasti = collect(fonti, work_dir, da, a,
                         args.yahoo_from or da,
                         args.yahoo_to or str(oggi + timedelta(days=45)),
                         args.settimane)

    print('\n=== consolidation ===')
    catalogo = load_catalog_rows()
    print(f'catalogue: {upsert_indicators(con, catalogo)} indicators')
    n_seed = load_seed(con)
    respinti = load_rejections(con)
    legami = load_aliases(con)
    print(f'per-source decisions: {n_seed} ({len(respinti)} rejected, {len(legami)} bound)')

    # raccogli() reads the CSVs by their bare names, so it has to run where they are
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
    # clean run from the absence of a line -- the failure count above it is
    # already easy to miss between two runs of numbers.
    print(f'  sources: {len(fonti) - guasti}/{len(fonti)} succeeded'
          + (f'  ({guasti} failed, see FAILED lines above)' if guasti else ''))

    if not osservazioni:
        print('\nnothing to ingest.', file=sys.stderr)
        con.close()
        return exit_code(guasti, len(fonti), 0)

    esito = ingest_observations(
        con, osservazioni,
        run_id=args.run_id or f'econ-calendar-{oggi}')
    print(f'\ningested: {esito}')

    # A period the source published is a fact; one derived from the indicator's
    # learned lag is an inference, and the two are kept apart in
    # reference_date_origin. Without this step only the first kind is ever
    # recorded, and reference_date sits at what the sources happen to publish --
    # measured here as 20% against 43% with it. only_stable=True is the default
    # and stays: the indicators whose lag varies are mostly the euro-area
    # aggregates, whose bindings are the ones known to be mixed, so filling
    # those would be deriving a date from a contradiction.
    dedotti = infer_reference_dates(con)
    print(f'reference dates inferred: {dedotti}')

    audit(con)
    con.close()
    return exit_code(guasti, len(fonti), len(osservazioni))


if __name__ == '__main__':
    raise SystemExit(main())
