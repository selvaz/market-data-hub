# -*- coding: utf-8 -*-
"""econ_calendar -- what remains of the hub's macroeconomic release calendar.

The hub collector (scraping, consolidation, ingestion, web validation, the
macro bridge and the ``econ_calendar`` job) was retired on 2026-11-11 by owner
decision: the calendar is collected by LazyCommittee (``lazycommittee
calendar``: Forex Factory plus verification), one source per concept.
Collection had already stopped on 2026-09-25.

What stays, because live consumers still read it:

- ``catalog``: the indicator catalogue (``config/econ_calendar.yaml``) and the
  read-only discovery functions over the frozen ``calendar_*`` tables
  (``available_series``, ``catalogue_vocabulary``), used by the hub's agent
  tools, LazyTools' ``EconCalendarTools`` and the indicator cards.
- ``ff_archive``: the raw Forex Factory feed archive (writer for the
  ``--archive-only`` task, reader for ``macro_releases.consensus``).
- ``archive_forecasts``: literal FF forecasts with provenance, read by
  LazyCommittee's FF-forecast evidence.

The ``calendar_*`` tables themselves are untouched: historical data, no
migration drops them.
"""
from market_data_hub.econ_calendar.catalog import (
    load_catalog_rows,
    upsert_indicators,
)

__all__ = [
    "load_catalog_rows",
    "upsert_indicators",
]
