# -*- coding: utf-8 -*-
"""
backfill_listings_from_tickers.py — one-shot: populate instruments/listings/
identifier_aliases from the config price universe (tickers.yaml), so every
symbol already in prices_daily gets identity rows (plan v3.1, Fase 2).

Idempotent: ids are deterministic and rows are only inserted when missing.
Run:  python scripts/backfill_listings_from_tickers.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from market_data_hub.config_loader import get_yahoo_tickers          # noqa: E402
from market_data_hub.db.connection import get_conn                   # noqa: E402
from market_data_hub.db.identity import currency_for_symbol          # noqa: E402
from market_data_hub.lock import db_write_lock                       # noqa: E402
from market_data_hub.services.prices import (                        # noqa: E402
    _KIND_BY_ASSET_CLASS, _register_listing)


def main() -> int:
    entries = get_yahoo_tickers()
    with db_write_lock():
        con = get_conn()
        try:
            before = con.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
            for e in entries:
                _register_listing(con, {
                    "symbol": e["symbol"],
                    "kind": _KIND_BY_ASSET_CLASS.get(e.get("asset_class", ""), "OTHER"),
                    "name": e.get("name"),
                    "exchange": None,
                    "currency": currency_for_symbol(e["symbol"]),
                    "provider": "yahoo",
                })
            after = con.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
        finally:
            con.close()
    print(f"listings: {before} -> {after} (+{after - before}), "
          f"{len(entries)} config symbols processed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
