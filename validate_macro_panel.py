# -*- coding: utf-8 -*-
"""
validate_macro_panel.py — verify that EVERY indicator code in the
cross-country panel really returns data from the live APIs, before trusting it.

For each indicator it tries a small set of probe countries (default: USA, ITA, BRA,
IND, CHN). Reports:
  OK       — at least one country returns observations
  PARTIAL  — some countries empty
  EMPTY    — no data for any probe country
  FALLBACK — the primary code fails but the fallback works
  FAIL     — exception/invalid code

Usage:
    python validate_macro_panel.py                 # all indicators
    python validate_macro_panel.py --probes USA ITA JPN
    python validate_macro_panel.py --full          # all countries in the catalog
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from market_data_hub.config_loader import get_countries, get_macro_panel_specs  # noqa: E402
from market_data_hub.sources import worldbank as wb, imf as im, bis as bs, ecb as ec, imf_sdmx as ims  # noqa: E402

PROBE_DEFAULT = ["USA", "ITA", "BRA", "IND", "CHN"]


import time


def _fetch(spec, countries):
    if spec["source"] == "IMF":
        time.sleep(3)  # space out IMF calls: the WAF blocks bursts
        return im.fetch_imf(spec, countries, start_year=2015, retries=3)
    if spec["source"] == "BIS":
        return bs.fetch_bis(spec, countries, start_year=2015, retries=3)
    if spec["source"] == "ECB":
        return ec.fetch_ecb(spec, countries, start_year=2015, retries=3)
    if spec["source"] == "IMF_SDMX":
        return ims.fetch_imf_sdmx(spec, countries, start_year=2015, retries=3)
    return wb.fetch_worldbank(spec, countries, start_year=2015, retries=2)


def validate(probes, full=False):
    cmap = {c["iso3"]: c for c in get_countries()}
    probe_countries = (list(cmap.values()) if full
                       else [cmap[p] for p in probes if p in cmap])
    specs = get_macro_panel_specs()

    rows = []
    for i, spec in enumerate(specs, 1):
        sid = spec["id"]
        print(f"[{i}/{len(specs)}] {sid:32s} {spec['source']}/{spec['dataset']} "
              f"{spec['code']:18s}", end=" ", flush=True)
        status, detail, latest = "FAIL", "", None
        try:
            df = _fetch(spec, probe_countries)
            n_countries = df["country_iso3"].nunique() if not df.empty else 0
            n_obs = len(df)
            if n_obs > 0:
                status = "OK" if n_countries >= max(1, len(probe_countries) // 2) else "PARTIAL"
                latest = int(pd.to_datetime(df["date"]).dt.year.max())
                detail = f"{n_obs} obs, {n_countries} countries, latest {latest}"
            else:
                # try fallback if defined
                fb = spec.get("fallback")
                if fb:
                    fbspec = {**spec, **fb}
                    fdf = _fetch(fbspec, probe_countries)
                    if not fdf.empty:
                        status = "FALLBACK"
                        latest = int(pd.to_datetime(fdf["date"]).dt.year.max())
                        detail = (f"primary empty; fallback {fb['source']}/"
                                  f"{fb['code']} ok ({len(fdf)} obs, latest {latest})")
                    else:
                        status = "EMPTY"
                        detail = "primary and fallback empty"
                else:
                    status = "EMPTY"
                    detail = "no observations"
        except Exception as e:
            status = "FAIL"
            detail = f"{type(e).__name__}: {e}"

        print(f"-> {status}  {detail}")
        rows.append({"indicator_id": sid, "source": spec["source"],
                     "dataset": spec["dataset"], "code": spec["code"],
                     "pillar": spec["pillar"], "status": status,
                     "latest_year": latest, "detail": detail})

    rep = pd.DataFrame(rows)
    out = Path(__file__).parent / "macro_panel_validation.csv"
    rep.to_csv(out, index=False)

    print("\n" + "=" * 60)
    print("SUMMARY:", dict(rep["status"].value_counts()))
    bad = rep[rep["status"].isin(["EMPTY", "FAIL"])]
    if not bad.empty:
        print("\nTO FIX/REMOVE:")
        print(bad[["indicator_id", "source", "code", "status", "detail"]].to_string(index=False))
    print(f"\nFull report: {out}")
    return rep


def main():
    p = argparse.ArgumentParser(description="Validate the macro panel codes")
    p.add_argument("--probes", nargs="+", default=PROBE_DEFAULT)
    p.add_argument("--full", action="store_true", help="use all countries")
    args = p.parse_args()
    validate(args.probes, full=args.full)


if __name__ == "__main__":
    main()
