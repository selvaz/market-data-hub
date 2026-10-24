"""Explicit-path, manual official collection. No scheduler or production default."""
from __future__ import annotations

import argparse
from datetime import date, datetime
import json
from pathlib import Path
import re

from .adapters import persist
from .bea import BEAAdapter
from .bls import BLSAdapter, shift_month
from .catalogue import FAMILIES, REMAINING_FAMILIES, CENTRAL_BANKS
from .policy import PolicyAdapter
from .tier1_publications import Tier1Adapter
from .eurostat import EurostatAdapter
from .fred import FREDAdapter, USRedistributorAdapter
from .dol import DOLAdapter
from .http import FetchError, HttpClient
from .ons import ONSAdapter
from .store import open_writer


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--families", required=True)
    parser.add_argument("--since", default=shift_month(date.today().strftime("%Y-%m"), -4))
    parser.add_argument("--with-redistributors", action="store_true",
                        help="Also collect unverified FRED/ALFRED evidence")
    args = parser.parse_args(argv)
    families = list(dict.fromkeys(args.families.split(",")))
    if not families or any(f not in FAMILIES for f in families):
        parser.error("--families must contain comma-separated names: " + ",".join(FAMILIES))
    if not re.fullmatch(r"\d{4}-\d{2}", args.since):
        parser.error("--since must be YYYY-MM")
    try:
        datetime.strptime(args.since, "%Y-%m")
    except ValueError:
        parser.error("--since must be a real month")
    target = args.db.resolve()
    if any(p.lower() == "programdata" for p in target.parts):
        parser.error("This manual development collector refuses ProgramData paths")
    client = HttpClient()
    result = {"db": str(target), "families": {}, "failures": {}, "family_failures": {}}
    adapters = [(BLSAdapter(client), [f for f in families if f in ("us_cpi", "us_payrolls", "us_ppi", "us_jolts")]),
                (BEAAdapter(client), [f for f in families if f in ("us_gdp", "us_pce")]),
                (USRedistributorAdapter(client), [f for f in families if f in ("us_retail_sales", "us_housing_starts", "us_durable_goods", "us_industrial_production", "us_trade_balance")]),
                (EurostatAdapter(client), [f for f in families if f in ("ea_hicp", "ea_gdp")]),
                (ONSAdapter(client), [f for f in families if f in ("uk_gdp", "uk_cpi", "uk_labour_market")]),
                (DOLAdapter(client), [f for f in families if f == "us_jobless_claims"])]
    if args.with_redistributors:
        adapters.append((FREDAdapter(client), [f for f in families if f.startswith("us_")]))
    adapters.append((Tier1Adapter(client), [f for f in families if f in REMAINING_FAMILIES]))
    adapters.append((PolicyAdapter(client), [f for f in families if f in CENTRAL_BANKS]))
    for adapter, selected in adapters:
        if not selected:
            continue
        name = type(adapter).__name__
        try:
            observations = adapter.fetch(selected, args.since)
            if not observations:
                raise FetchError("No observations in requested window")
            # Fetch/parse completely before the short, atomic provider batch.
            with open_writer(target) as writer:
                before = writer.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0]
                with writer.batch():
                    ids = persist(writer, observations)
                added = writer.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0] - before
            result["families"][name] = {"families": selected, "observations": len(observations),
                                        "new_vintages": added, "reused_vintages": len(ids) - added,
                                        "unavailable": [{"indicator": o.indicator_id, "period": o.period,
                                                         "reason": o.printed} for o in observations if o.value is None]}
        except Exception as exc:
            # Unexpected exceptions can contain request URLs/credentials. Report
            # their type; controlled adapter errors already carry safe messages.
            result["failures"][name] = str(exc) if isinstance(exc, (FetchError, ValueError)) else type(exc).__name__
            for family in selected:
                result["family_failures"].setdefault(family, []).append(name)
    print(json.dumps(result, indent=2))
    return 1 if result["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
