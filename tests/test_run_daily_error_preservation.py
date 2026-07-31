# -*- coding: utf-8 -*-
"""``run_daily.py`` must not lose the download error when the report that
runs afterward (because ``--report`` was passed) also fails -- the catalog
used to record only the secondary report-generation error, hiding the
actual download failure that triggered it.
"""
from __future__ import annotations

import importlib
import sys


def test_run_daily_preserves_download_error_when_report_also_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("LAZYTOOLS_OPERATIONS_DB", str(tmp_path / "operations.sqlite"))
    monkeypatch.setenv("LAZYTOOLS_ARTIFACTS_DIR", str(tmp_path / "artifacts"))

    rd = importlib.import_module("run_daily")

    def _broken_run(**kwargs):
        raise RuntimeError("download failed")

    monkeypatch.setattr(rd, "run", _broken_run)

    import make_report

    def _broken_collect(con):
        raise RuntimeError("report failed")

    monkeypatch.setattr(make_report, "collect", _broken_collect)

    monkeypatch.setattr(
        sys, "argv",
        ["run_daily.py", "--report", "--db", str(tmp_path / "market_data.duckdb")],
    )

    rc = rd.main()
    assert rc == 1

    from lazytools.operations import OperationsCatalog
    runs = OperationsCatalog().list_runs(task_name="market_data_full")
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert "download failed" in runs[0].error
    assert "report failed" in runs[0].error
