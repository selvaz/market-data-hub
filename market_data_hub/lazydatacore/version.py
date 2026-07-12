# -*- coding: utf-8 -*-
"""Schema version and producer identity stamped on every contract payload.

``SCHEMA_VERSION`` governs the shape of :class:`~market_data_hub.lazydatacore.
result.AnalysisResult` and the ``contracts/v1/`` fixtures — not the
``market-data-hub`` package version, which changes independently.

Compatibility rule (stabilization plan, ECO-010): additive, optional fields
are compatible within the same major (``"1.x"``); a removal or rename of an
existing field requires a new major and a new ``contracts/v{N}/`` fixture
set. Never change a field's meaning, unit or timezone convention silently.
"""
from __future__ import annotations

import importlib.metadata

SCHEMA_VERSION = "1.0"

PRODUCER_NAME = "market-data-hub"


def _producer_version() -> str:
    try:
        return importlib.metadata.version(PRODUCER_NAME)
    except importlib.metadata.PackageNotFoundError:
        # Running from a source checkout without an installed distribution
        # (e.g. a fresh clone before `pip install -e .`).
        return "0.0.0-dev"


PRODUCER_VERSION = _producer_version()
