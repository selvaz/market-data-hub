# -*- coding: utf-8 -*-
"""
backfill_etf_classification.py — one-shot: materialize the config-universe
classification (asset_class/area/category/sub_group/sector/theme/priority,
today only derivable at query time via catalog._classify()) into the
etf_classification table, and backfill listings.currency for symbols
registered before the currency fix (db/identity.py's currency_for_symbol).

Idempotent AND non-destructive on rerun:
- classification columns always reflect the current tickers.yaml +
  _classify() mapping (re-derived every run);
- benchmark_proxy is curated separately (not derivable from config), so an
  existing value is preserved across reruns instead of being reset to NULL;
- the currency backfill only fills listings rows that are still NULL, for
  the config/Yahoo-provider listing specifically -- it never overwrites an
  already-set currency (e.g. a distinct listing for the same symbol on
  another venue, registered explicitly with its own currency).

Run:  python scripts/backfill_etf_classification.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from market_data_hub.catalog import _classify                      # noqa: E402
from market_data_hub.config_loader import get_yahoo_tickers        # noqa: E402
from market_data_hub.db.connection import get_conn                 # noqa: E402
from market_data_hub.db.identity import currency_for_symbol        # noqa: E402
from market_data_hub.db.upsert import upsert                       # noqa: E402
from market_data_hub.lock import db_write_lock                     # noqa: E402


def main() -> int:
    entries = get_yahoo_tickers()
    symbols = [e["symbol"] for e in entries]
    now = datetime.now(timezone.utc)

    with db_write_lock():
        con = get_conn()
        try:
            # Preserve already-curated benchmark_proxy AND the original
            # created_at: upsert() is a full-row INSERT OR REPLACE, so a
            # rerun must carry both forward rather than clobber them --
            # benchmark_proxy with the hardcoded NULL a classification-only
            # refresh would otherwise write, created_at with "now" (only
            # updated_at should advance on a refresh).
            existing_rows = con.execute(
                f"SELECT symbol, benchmark_proxy, created_at FROM etf_classification "
                f"WHERE symbol IN ({','.join('?' * len(symbols))})",
                symbols).fetchall()
            existing_proxy = {r[0]: r[1] for r in existing_rows}
            existing_created = {r[0]: r[2] for r in existing_rows}

            rows = []
            for e in entries:
                c = _classify(e)
                rows.append({
                    "symbol": c["symbol"],
                    "asset_class": c["asset_class"],
                    "area": c["area"],
                    "category": c["category"],
                    "sub_group": c["group"],
                    "sector": c["sector"],
                    "theme": c["theme"],
                    "benchmark_proxy": existing_proxy.get(c["symbol"]),
                    "priority": c["priority"],
                    "created_at": existing_created.get(c["symbol"], now),
                    "updated_at": now,
                })
            df = pd.DataFrame(rows)
            added, updated = upsert(con, "etf_classification", df)

            # Backfill currency on listings rows that are still NULL for the
            # config/Yahoo-provider listing specifically -- never overwrite
            # an already-set currency (e.g. a distinct listing for the same
            # symbol registered explicitly on another venue/currency).
            currency_by_symbol = {s: currency_for_symbol(s) for s in symbols}
            cur_df = pd.DataFrame(
                {"symbol": list(currency_by_symbol.keys()),
                 "currency": list(currency_by_symbol.values())}
            )
            con.register("_cur_src", cur_df)
            update_result = con.execute("""
                UPDATE listings SET currency = _cur_src.currency,
                                     updated_at = ?
                FROM _cur_src
                WHERE listings.symbol = _cur_src.symbol
                  AND listings.provider = 'yahoo'
                  AND listings.currency IS NULL
            """, [now]).fetchall()
            n_updated_listings = update_result[0][0] if update_result else 0
            con.unregister("_cur_src")
        finally:
            con.close()

    print(f"etf_classification: +{added} added, {updated} updated "
          f"({len(entries)} config symbols processed)")
    print(f"listings.currency: {n_updated_listings} rows backfilled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
