# -*- coding: utf-8 -*-
"""DataSpace adapter for this repository's market-data DuckDB.

Makes market-data-hub registrable in a :class:`lazydataspace.DataSpace` so a
workflow spanning several repositories can verify every source's readiness
together, before its first write.

The adapter is deliberately thin. It adds **no** second path resolver and no
second read API: paths come from ``db.connection``'s canonical resolver, and
callers reach the data through ``market_data_hub.reader`` exactly as they do
today. Registering this Source changes nothing about how the repo works
standalone.

``lazydataspace`` is an optional dependency (``pip install
market-data-hub[lazydataspace]``). Nothing else in this package imports this
module, so the repo installs and runs without it. Note that ``lazydataspace``
requires Python 3.11+, above this package's own 3.9 floor.

Example:
    from lazydataspace import DataSpace
    from market_data_hub.dataspace_source import MarketDataSource

    space = DataSpace(MarketDataSource())
    space.require_ready()
    px = read_prices(["SPY", "TLT"])       # the repo's own API, unchanged
"""
from __future__ import annotations

import os
from typing import Optional

from lazydataspace import Health, SourceInfo

from market_data_hub.db.connection import resolve_db_path

#: What this endpoint offers, mirroring ``market_data_hub.reader``'s public
#: read API. DataSpace never interprets these strings; they exist so a caller
#: can ask "who provides market prices?" without hardcoding a source name.
CAPABILITIES = (
    "market.prices",
    "market.macro",
    "market.crypto",
    "market.factors",
    "market.coverage",
)

#: The table whose presence distinguishes "a readable DuckDB file" from
#: "actually this repository's market database" — the failure a health check
#: is worth having is being pointed at the wrong file, not a corrupt one.
_SENTINEL_TABLE = "prices_daily"

#: Columns ``read_prices`` filters and selects on (reader.py: the
#: ``is_live`` clause, the default ``adj_close`` field, the identity keys).
#: A table merely *named* prices_daily is not identity — ready must mean the
#: advertised reader API can actually run against this file.
_SENTINEL_COLUMNS = frozenset({"date", "symbol", "adj_close", "is_live"})


class MarketDataSource:
    """This repository's market-data DuckDB, as a DataSpace ``Source``.

    Satisfies the ``lazydataspace.Source`` protocol structurally — there is no
    base class to inherit, so this stays a plain object that happens to be
    registrable.

    Args:
        db_path: Explicit database path. Omit to use the repository's own
            resolution order (``MARKET_DATA_DB`` env var, then
            ``settings.yaml``, then the repo-local default) — the same one
            every other entry point in this package uses.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path

    @property
    def name(self) -> str:
        return "market"

    @property
    def owner(self) -> str:
        return "market-data-hub"

    @property
    def capabilities(self) -> tuple[str, ...]:
        return CAPABILITIES

    def describe(self) -> SourceInfo:
        """Return the non-sensitive self-description.

        Carries no path: ``SourceInfo`` has no field for one, and the
        description is written to be safe in a log.
        """
        return SourceInfo(
            name=self.name,
            owner=self.owner,
            capabilities=self.capabilities,
            description=(
                "Daily prices, macro series and panels, crypto, factors and "
                "coverage for the Lazy ecosystem (DuckDB). Read via "
                "market_data_hub.reader."
            ),
        )

    def health(self) -> Health:
        """Open the database read-only and confirm it is this repo's.

        A real check, not an environment-variable test: it resolves the
        path, opens the file and queries it.

        Deliberately does **not** call ``get_conn()``: that helper creates
        the database when a read-only caller finds it missing, which is
        right for a reader and wrong for a health check — a readiness probe
        that silently creates an empty database would report ready and hand
        the workflow an empty source.

        Failure details name the configuration knob but never its value:
        this report is logged, and a path is deployment information.
        """
        try:
            path = resolve_db_path(self._db_path)
        except Exception as exc:
            return Health(ready=False, detail=f"path resolution raised {type(exc).__name__}")

        if not os.path.exists(path):
            return Health(
                ready=False,
                detail="database file does not exist (configure MARKET_DATA_DB or settings.yaml)",
            )

        try:
            import duckdb

            con = duckdb.connect(path, read_only=True)
            try:
                rows = con.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = ?",
                    [_SENTINEL_TABLE],
                ).fetchall()
            finally:
                con.close()
        except Exception as exc:
            # Type only: DuckDB errors quote the full file path.
            return Health(ready=False, detail=f"cannot open database: {type(exc).__name__}")

        if not rows:
            return Health(
                ready=False,
                detail=f"database is readable but has no {_SENTINEL_TABLE} table (wrong file?)",
            )
        # Table name alone is not identity: a legacy or foreign DuckDB with
        # any table called prices_daily would pass, and the workflow's first
        # read_prices() would then fail on the columns it filters and selects.
        # Ready must mean the advertised reader API can actually run.
        missing = _SENTINEL_COLUMNS - {r[0] for r in rows}
        if missing:
            return Health(
                ready=False,
                detail=(
                    f"{_SENTINEL_TABLE} exists but lacks required column(s) "
                    f"{sorted(missing)} (legacy or foreign schema?)"
                ),
            )
        return Health(ready=True)


__all__ = ["CAPABILITIES", "MarketDataSource"]
