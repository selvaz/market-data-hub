# -*- coding: utf-8 -*-
"""
artifact_registry.py — best-effort cataloging of report artifacts into the
shared LazyTools artifact registry (``lazytools.registry``).

This is entirely optional plumbing: market-data-hub's own DuckDB pipeline and
scheduled jobs work identically whether or not it fires. Three independent
reasons it may be a no-op, all of them fine:

1. ``lazytools`` is not installed in this environment at all (it is not a
   declared dependency of this package — see pyproject.toml/requirements.txt
   — only imported opportunistically, same as the existing Telegram
   integration in send_telegram_run_report.py).
2. ``lazytools.registry.resolve_db("market_data_artifacts")`` returns
   ``None`` because ``MARKET_DATA_ARTIFACTS_DB`` is unset — this DB is
   declared ``required=False`` in LazyTools' ``KNOWN_DBS``, i.e. opt-in per
   deployment.
3. Anything else goes wrong while registering (a locked/corrupt sqlite file,
   an unexpected exception inside lazytools, ...).

In every case the calling scheduled script (run_daily.py)
must keep running: registering a report as an artifact is a nice-to-have
index entry, never a condition for the download/estimation job's success.
"""
from __future__ import annotations

import sys

try:
    from lazytools.registry import register_artifact, resolve_db
except ImportError:
    resolve_db = None  # type: ignore[assignment]
    register_artifact = None  # type: ignore[assignment]


def register_report_artifact(*, title: str, summary: str, tags: list[str], content_uri: str) -> str | None:
    """Catalog one report as a ``market-data-hub``/``report`` artifact.

    Best-effort only: swallows every exception (import errors, missing/unset
    ``MARKET_DATA_ARTIFACTS_DB``, sqlite errors, ...) and prints a warning to
    stderr instead of raising, so callers never need to guard this call.

    Args:
        title: Short human-readable title (e.g. "Market data report ...").
        summary: Cheap-to-read summary of the report's headline numbers.
        tags: Free-text tags (e.g. ``["daily"]`` or ``["regime", "daily"]``).
        content_uri: Path/URI to the actual report file (the HTML report).

    Returns:
        The new artifact's id, or ``None`` if registration was skipped or
        failed.
    """
    if resolve_db is None or register_artifact is None:
        return None
    try:
        db_path = resolve_db("market_data_artifacts")
        if not db_path:
            return None  # MARKET_DATA_ARTIFACTS_DB unset -- optional, skip silently
        return register_artifact(
            db_path,
            repo="market-data-hub",
            kind="report",
            title=title,
            summary=summary,
            tags=tags,
            content_uri=content_uri,
        )
    except Exception as e:
        print(f"WARNING: artifact registration failed (non-fatal): {e}", file=sys.stderr)
        return None
