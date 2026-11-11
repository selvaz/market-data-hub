# -*- coding: utf-8 -*-
"""run_econ_calendar.py — archive the raw Forex Factory calendar feed.

Usage:
    python run_econ_calendar.py --archive-only [--raw-dir <dir>]

The hub economic-calendar collector (collection, ingestion into the
``calendar_*`` tables, web validation, macro bridge) was retired on
2026-11-11: the calendar is collected by LazyCommittee. Only the raw-archive
mode survives, because the scheduled task ``InvestmentCommittee_EconRawArchive``
still runs it daily and two live readers consume the archive
(``macro_releases.consensus`` and ``econ_calendar.archive_forecasts``).

Archive mode opens no database and takes no lock. Exit codes: 0 written,
3 unchanged (the ecosystem's "nothing to do"), 1 failed.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

EXIT_OK = 0
EXIT_FAILED = 1
# 2 is NOT free: `run_marketdata_job.ps1` reads it as "configuration refused".
EXIT_NOTHING_TO_DO = 3


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--archive-only', action='store_true',
                   help='archive the raw Forex Factory feed (the only supported mode)')
    p.add_argument('--raw-dir', default=None,
                   help=r'raw archive directory (default: C:\ProgramData\InvestmentCommittee\econ_calendar\raw)')
    args = p.parse_args()

    if not args.archive_only:
        p.error('the hub economic-calendar collector was retired on 2026-11-11 '
                '(the calendar is collected by LazyCommittee); only --archive-only '
                'is supported')

    from market_data_hub.econ_calendar.ff_archive import DEFAULT_RAW_DIR, archive_raw
    try:
        code = archive_raw(args.raw_dir if args.raw_dir is not None else DEFAULT_RAW_DIR)
    except (RuntimeError, OSError) as e:
        print(f'raw archive FAILED: {e}', file=sys.stderr, flush=True)
        return EXIT_FAILED
    print('raw archive: ' + ('written' if code == EXIT_OK else 'unchanged'), flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
