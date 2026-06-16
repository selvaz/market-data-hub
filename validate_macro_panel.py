# -*- coding: utf-8 -*-
"""
validate_macro_panel.py — verifica che OGNI codice indicatore del panel
cross-country restituisca davvero dati dalle API live, prima di fidarsene.

Per ogni indicatore prova un piccolo set di paesi-sonda (default: USA, ITA, BRA,
IND, CHN). Riporta:
  OK       — almeno un paese restituisce osservazioni
  PARTIAL  — alcuni paesi vuoti
  EMPTY    — nessun dato per nessun paese sonda
  FALLBACK — il codice primario fallisce ma il fallback funziona
  FAIL     — eccezione/codice non valido

Uso:
    python validate_macro_panel.py                 # tutti gli indicatori
    python validate_macro_panel.py --probes USA ITA JPN
    python validate_macro_panel.py --full          # tutti i paesi del catalogo
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from market_data_hub.config_loader import get_countries, get_macro_panel_specs  # noqa: E402
from market_data_hub.sources import worldbank as wb, imf as im, bis as bs  # noqa: E402

PROBE_DEFAULT = ["USA", "ITA", "BRA", "IND", "CHN"]


import time


def _fetch(spec, countries):
    if spec["source"] == "IMF":
        time.sleep(3)  # spaziare le chiamate IMF: il WAF blocca i burst
        return im.fetch_imf(spec, countries, start_year=2015, retries=3)
    if spec["source"] == "BIS":
        return bs.fetch_bis(spec, countries, start_year=2015, retries=3)
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
                detail = f"{n_obs} obs, {n_countries} paesi, ultimo {latest}"
            else:
                # prova fallback se definito
                fb = spec.get("fallback")
                if fb:
                    fbspec = {**spec, **fb}
                    fdf = _fetch(fbspec, probe_countries)
                    if not fdf.empty:
                        status = "FALLBACK"
                        latest = int(pd.to_datetime(fdf["date"]).dt.year.max())
                        detail = (f"primario vuoto; fallback {fb['source']}/"
                                  f"{fb['code']} ok ({len(fdf)} obs, ultimo {latest})")
                    else:
                        status = "EMPTY"
                        detail = "primario e fallback vuoti"
                else:
                    status = "EMPTY"
                    detail = "nessuna osservazione"
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
    print("RIEPILOGO:", dict(rep["status"].value_counts()))
    bad = rep[rep["status"].isin(["EMPTY", "FAIL"])]
    if not bad.empty:
        print("\nDA CORREGGERE/RIMUOVERE:")
        print(bad[["indicator_id", "source", "code", "status", "detail"]].to_string(index=False))
    print(f"\nReport completo: {out}")
    return rep


def main():
    p = argparse.ArgumentParser(description="Valida i codici del macro panel")
    p.add_argument("--probes", nargs="+", default=PROBE_DEFAULT)
    p.add_argument("--full", action="store_true", help="usa tutti i paesi")
    args = p.parse_args()
    validate(args.probes, full=args.full)


if __name__ == "__main__":
    main()
