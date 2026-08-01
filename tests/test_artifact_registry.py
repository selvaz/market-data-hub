# -*- coding: utf-8 -*-
"""market_data_hub/artifact_registry.py — best-effort cataloging of report
artifacts into the shared LazyTools registry (lazytools.registry).

Registration must never break the calling scheduled script (run_daily.py /
run_regime_daily.py): every failure mode here -- lazytools missing,
MARKET_DATA_ARTIFACTS_DB unset, or the registry call itself raising -- has to
degrade to a silent no-op, never an exception.
"""
from __future__ import annotations

import pytest

pytest.importorskip("lazytools", reason="artifact registry wraps the optional lazytools.registry module")

from lazytools.registry import search_artifacts  # noqa: E402

from market_data_hub.artifact_registry import register_report_artifact  # noqa: E402


def test_register_report_artifact_inserts_queryable_row(tmp_path, monkeypatch):
    db_path = tmp_path / "artifacts.sqlite"
    monkeypatch.setenv("MARKET_DATA_ARTIFACTS_DB", str(db_path))

    artifact_id = register_report_artifact(
        title="Market data report 20260801_0800",
        summary="1,234 rows | 56 series | score 92.3 | 2 stalled",
        tags=["daily"],
        content_uri=str(tmp_path / "market_data_report_20260801_0800.html"),
    )

    assert artifact_id is not None

    results = search_artifacts(str(db_path), kind="report")
    assert len(results) == 1
    row = results[0]
    assert row["repo"] == "market-data-hub"
    assert row["kind"] == "report"
    assert row["title"] == "Market data report 20260801_0800"
    assert "daily" in row["tags"]


def test_register_report_artifact_skips_silently_when_env_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("MARKET_DATA_ARTIFACTS_DB", raising=False)

    result = register_report_artifact(
        title="Market data report 20260801_0800",
        summary="whatever",
        tags=["daily"],
        content_uri=str(tmp_path / "report.html"),
    )

    assert result is None
    # No DB should have been created anywhere -- there was nowhere to write to.
    assert list(tmp_path.glob("*.sqlite")) == []


def test_register_report_artifact_swallows_registry_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_DATA_ARTIFACTS_DB", str(tmp_path / "artifacts.sqlite"))

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated lazytools.registry failure")

    monkeypatch.setattr("market_data_hub.artifact_registry.register_artifact", _boom)

    result = register_report_artifact(
        title="Market data report 20260801_0800",
        summary="whatever",
        tags=["daily"],
        content_uri=str(tmp_path / "report.html"),
    )

    assert result is None
