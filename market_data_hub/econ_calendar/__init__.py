# -*- coding: utf-8 -*-
"""econ_calendar -- the macroeconomic release calendar.

Why it lives in the hub rather than next to the scrapers: ``macro_panel`` knows
which period a figure refers to, not when that figure became public. The
``*_vintage`` tables record when ingestion first saw a value, which is a
different thing -- it depends on when the job runs. This module supplies the
missing axis, the moment of publication, and with it the consensus, which the
issuing agencies do not publish at all.

Division of responsibility: the schema, the write path and the consolidation
live here. *Collection* stays outside, because some sources need a driven
browser and dragging Selenium into a library that runs unattended would be the
wrong trade. Collectors call :func:`ingest_observations` with normalised rows.

The three provenances never mix:

===================  ============================================
``macro_panel``      multilateral institutions (IMF, World Bank)
``provenance``       ``'official'``   issuing agencies (BLS, BEA)
``provenance``       ``'aggregator'`` third-party calendars
===================  ============================================
"""
from market_data_hub.econ_calendar.aliases import (
    cadence_violations,
    load_aliases,
    normalize_name,
    resolve,
    seed_from_observations,
    unmapped,
    upsert_alias,
)
from market_data_hub.econ_calendar.catalog import (
    load_catalog_rows,
    upsert_indicators,
)
from market_data_hub.econ_calendar.ingest import (
    CalendarObservation,
    consolidate_events,
    ingest_observations,
    make_event_id,
)

__all__ = [
    "CalendarObservation",
    "cadence_violations",
    "consolidate_events",
    "ingest_observations",
    "load_aliases",
    "load_catalog_rows",
    "make_event_id",
    "normalize_name",
    "resolve",
    "seed_from_observations",
    "unmapped",
    "upsert_alias",
    "upsert_indicators",
]
