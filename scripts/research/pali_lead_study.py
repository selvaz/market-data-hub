"""Deterministic PALI monthly-average study; statistics only, no DB or network.

Use the free issuer workbook and the frozen FRED snapshot from the mortgage
study. Weekly dates are Friday period ends. No historic release vintages are
inferred. Both PALI series are NSA; the method is exploratory, not a v2 screen.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from market_data_hub.macro_releases.fannie_pali import workbook
from scripts.research.mortgage_lead_study import load_snapshot, render_tables, run_study

PREDICTORS = ("PALI_UPB", "PALI_COUNT")
TARGETS = ("HSN1F", "HOUST", "EXHOSLUSM495S")
PERIODS = {"full": (None, None), "2004_2014": ("2004-01", "2014-12"),
           "2015_onward": ("2015-01", None), "2009_2019": ("2009-01", "2019-12"),
           "2020_onward": ("2020-01", None)}


def study(workbook_path, snapshot_path, end):
    cutoff = pd.Timestamp(end)
    if cutoff != cutoff + pd.offsets.MonthEnd(0):
        raise ValueError("Month-end cutoff required")
    body = workbook_path.read_bytes()
    history, _ = workbook(body)
    weekly = pd.DataFrame([(pd.Timestamp(day), upb, count) for day, upb, count in history],
                          columns=["date", *PREDICTORS]).set_index("date")
    targets, provenance = load_snapshot(snapshot_path)
    if provenance["end"] != end:
        raise ValueError("Snapshot and PALI cutoffs must agree")
    provenance.update(pali_workbook_sha256=hashlib.sha256(body).hexdigest(),
                      pali_history_start=str(weekly.index.min().date()),
                      pali_history_end=str(weekly.index.max().date()),
                      pali_source="Fannie Mae; current revised free PALI history",
                      pali_adjustment="NSA; no seasonal adjustment inferred",
                      monthly_rule="mean of available weekly levels by Friday date; first month begins 2004-01-09")
    # Do not let target-only pre-2004 rows change any training-window length.
    raw = pd.concat([weekly, targets[[c for c in TARGETS if c in targets]]], axis=1, sort=True)
    raw = raw.loc[weekly.index.min().replace(day=1):cutoff]
    result = run_study(raw, provenance, predictors=PREDICTORS, targets=TARGETS, periods=PERIODS)
    result["method"].update(pali_transform="100 * first difference log monthly mean weekly NSA level",
                            limitation="NSA predictor vs SA targets; calendar seasonality and DU share can confound; revised-data pseudo-OOS")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", required=True, type=Path)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--end", default="2026-09-30")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--tables", required=True, type=Path)
    args = parser.parse_args(argv)
    result = study(args.workbook, args.snapshot, args.end)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.tables.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    args.tables.write_text(render_tables(result), encoding="utf-8")
    print("PALI study complete; statistics only.")


if __name__ == "__main__":
    main()
