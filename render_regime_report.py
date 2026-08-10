#!/usr/bin/env python
"""Re-render the HTML report for any past ``regime_daily_report`` depot row.

Demonstrates (and is the concrete tool for) the guarantee that the report
is always reconstructable from its saved JSON alone: this script only
reads a row back out of ``lazystats_depot`` and calls
``market_data_hub.regime.daily_render.render_html`` -- no live DB access,
no re-fitting.

Usage::

    python render_regime_report.py --result-id res_xxxxxxxxxxxx
    python render_regime_report.py --latest
    python render_regime_report.py --latest --out my_report.html
"""

from __future__ import annotations

import argparse
import os
import sys

import lazytools.registry as lazytools_registry
from lazystats.io.depot import ResultDepot

from market_data_hub.regime.daily_payload import REGIME_REPORT_KIND, REGIME_REPORT_SERIES_KEY
from market_data_hub.regime.daily_render import render_html


def _latest_result_id(depot: ResultDepot) -> str | None:
    results = depot.list(cadence="stable", limit=200)
    for r in results:
        if r["series_key"] == REGIME_REPORT_SERIES_KEY and r["kind"] == REGIME_REPORT_KIND:
            return r["result_id"]
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--result-id", help="A specific lazystats_depot result_id to re-render")
    group.add_argument("--latest", action="store_true", help="Re-render the most recent regime_daily_report run")
    parser.add_argument("--out", help="Output HTML path; default: reports/regime/regime_daily_<as_of>_<result_id>.html")
    args = parser.parse_args()

    depot_path = lazytools_registry.resolve_db("lazystats_depot")
    depot = ResultDepot(depot_path)
    try:
        if args.latest:
            result_id = _latest_result_id(depot)
            if result_id is None:
                print("No regime_daily_report results found in the depot.", file=sys.stderr)
                return 1
        else:
            result_id = args.result_id

        row = depot.load(result_id)
        if row is None:
            print(f"No such result_id: {result_id!r}", file=sys.stderr)
            return 1
    finally:
        depot.close()

    html = render_html(row)
    out_path = args.out or f"reports/regime/regime_daily_{row['payload']['as_of']}_{row['result_id']}.html"
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Re-rendered {result_id} -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
