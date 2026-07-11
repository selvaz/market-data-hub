# -*- coding: utf-8 -*-
"""Enforcement tests (plan v3.1, Fase 7).

Three families:
  1. static import boundary — read modules never import provider clients;
  2. runtime no-network — every read-only service/tool works with the network
     hard-blocked at the socket layer; only ensure_* may reach out;
  3. tool profiles — read and write bundles are disjoint, every write tool is
     gated by allow_write, every list-returning read tool is bounded.

These are the CI tripwires the plan's §7 acceptance asks for: a bypass (a
read path that fetches, an ungated write) fails here before it ships.
"""
from __future__ import annotations

import ast
import inspect
import json
import socket
from pathlib import Path

import pandas as pd
import pytest

from market_data_hub import agent_tools as at

ROOT = Path(__file__).resolve().parents[1] / "market_data_hub"

# Read-side modules: they must be importable and usable with no provider
# client and no network. sources/ and runner.py are the only fetch layers.
_READ_MODULES = ["reader.py", "extract.py", "catalog.py"]
_PROVIDER_ROOTS = {"requests", "curl_cffi", "yfinance", "httpx", "urllib3"}


# ------------------------------------------------------------- import boundary
@pytest.mark.parametrize("module", _READ_MODULES)
def test_read_modules_have_no_provider_imports(module):
    tree = ast.parse((ROOT / module).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        for name in names:
            root = name.split(".")[0]
            assert root not in _PROVIDER_ROOTS, (
                f"{module} imports provider client {name!r}")
            assert not name.startswith("market_data_hub.sources"), (
                f"{module} imports the fetch layer {name!r}")


def test_services_do_not_import_sources_at_module_level():
    """services.* may call sources only inside ensure_* bodies (lazy import),
    so that importing the service layer never drags in provider clients."""
    for path in (ROOT / "services").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:      # top level only — lazy imports are fine
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert not name.startswith("market_data_hub.sources"), (
                    f"services/{path.name} imports {name!r} at module level")


# ------------------------------------------------------------------ no-network
@pytest.fixture()
def no_network(monkeypatch):
    """Hard-block the network at the socket layer for the duration of a test."""
    def _blocked(*args, **kwargs):
        raise AssertionError("network access attempted by a read-only path")
    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


def _seed(tmp_db):
    """Populate prices + SEC facts through the ensure_* capabilities with
    offline stub fetchers (network never involved)."""
    from market_data_hub.services import financials as fin
    from market_data_hub.services import prices as svc

    def fetch_prices(symbols, start, end):
        return {symbols[0]: pd.DataFrame({
            "date": pd.date_range(start, periods=5, freq="B").date,
            "close": [100.0 + i for i in range(5)],
            "adj_close": [100.0 + i for i in range(5)],
        })}

    svc.ensure_price_history("SPY", start="2024-01-01", end="2024-01-31",
                             db_path=tmp_db, fetch=fetch_prices)
    fin.ensure_filings_and_facts(
        "AAPL", db_path=tmp_db,
        resolve=lambda q: {"cik": "0000320193", "name": "Apple Inc.",
                           "ticker": "AAPL"},
        fetch_submissions=lambda cik: {"cik": 320193, "filings": {"recent": {
            "accessionNumber": ["0000320193-24-000123"], "form": ["10-K"],
            "filingDate": ["2024-11-01"], "reportDate": ["2024-09-28"],
            "primaryDocument": ["aapl.htm"]}}},
        fetch_facts=lambda cik: {"cik": 320193, "facts": {"us-gaap": {
            "Assets": {"units": {"USD": [
                {"end": "2024-09-28", "val": 1.0, "fy": 2024, "fp": "FY",
                 "form": "10-K", "filed": "2024-11-01", "accn": "a"}]}}}}})


def test_all_read_paths_work_with_network_blocked(tmp_db, no_network):
    """Every read-only service and LLM tool must answer from the DB alone.
    (The seed itself runs with the network blocked too: the stub fetchers
    prove ensure_* is the only layer that would need it.)"""
    from market_data_hub.services import financials as fin
    from market_data_hub.services import prices as svc

    _seed(tmp_db)

    assert svc.resolve_instrument("SPY", db_path=tmp_db)[0]["registered"]
    assert svc.get_price_summary("SPY", db_path=tmp_db)["n_obs"] == 5
    assert fin.resolve_issuer("AAPL", db_path=tmp_db)["cik"] == "0000320193"
    assert fin.get_facts("AAPL", db_path=tmp_db)["n_returned"] == 1
    assert fin.get_statement("AAPL", db_path=tmp_db)["lines"]["assets"]
    assert fin.get_financials_coverage("AAPL", db_path=tmp_db)

    # the read-only LLM tool layer, end to end
    for call in (lambda: at.tool_resolve_instrument("SPY"),
                 lambda: at.tool_get_price_summary("SPY"),
                 lambda: at.tool_get_financial_facts("AAPL"),
                 lambda: at.tool_get_statement("AAPL"),
                 lambda: at.tool_get_financials_coverage(),
                 lambda: at.tool_list_datasets(),
                 lambda: at.tool_list_symbols(),
                 lambda: at.tool_get_coverage(),
                 lambda: at.tool_get_ingestion_health()):
        out = json.loads(call())
        assert "network access attempted" not in json.dumps(out)


# -------------------------------------------------------------------- health
def test_ingestion_health_reports_jobs_errors_and_is_bounded(tmp_db):
    from market_data_hub.services import health
    from market_data_hub.services import prices as svc

    _seed(tmp_db)
    with pytest.raises(RuntimeError):
        svc.ensure_price_history(
            "QQQ", start="2024-01-01", end="2024-01-31", db_path=tmp_db,
            fetch=lambda s, a, b: (_ for _ in ()).throw(RuntimeError("boom")))

    out = health.get_ingestion_health(db_path=tmp_db)
    by_status = {(j["kind"], j["status"]): j["n"] for j in out["jobs"]}
    assert by_status[("price_history", "completed")] == 1
    assert by_status[("price_history", "error")] == 1
    assert by_status[("sec_facts", "completed")] == 1
    assert len(out["recent_errors"]) == 1
    assert "boom" in out["recent_errors"][0]["error_msg"]
    providers = {r["provider"] for r in out["runs_by_provider"]}
    assert {"yahoo", "sec_edgar"} <= providers
    assert out["sec_coverage"][0]["n_facts"] == 1


def test_ingestion_health_on_empty_db(tmp_db):
    from market_data_hub.services import health
    out = health.get_ingestion_health(db_path=tmp_db)
    assert out["jobs"] == [] and out["recent_errors"] == []
    assert out["stalled_prices_total"] == 0


# --------------------------------------------------------------- tool profiles
def test_read_and_write_bundles_are_disjoint_and_unique():
    read = [f.__name__ for f in at.TOOL_FUNCTIONS]
    write = [f.__name__ for f in at.WRITE_TOOL_FUNCTIONS]
    assert len(read) == len(set(read))
    assert len(write) == len(set(write))
    assert not set(read) & set(write)


def test_every_write_tool_is_gated_by_allow_write():
    for fn in at.WRITE_TOOL_FUNCTIONS:
        sig = inspect.signature(fn)
        assert "allow_write" in sig.parameters, (
            f"{fn.__name__} lacks the allow_write gate")
        assert sig.parameters["allow_write"].default is False, (
            f"{fn.__name__} must default allow_write to False")
        # calling without the gate returns an error, touching nothing
        args = {"symbols": "SPY"} if "symbols" in sig.parameters else \
               {"query": "SPY"}
        out = json.loads(fn(**args))
        assert "allow_write" in out.get("error", "")


def test_write_tools_never_in_default_bundle():
    """The standard (read-only) profile must not contain any ensure/refresh
    capability — plan §5.1/§5.2."""
    for fn in at.TOOL_FUNCTIONS:
        assert "allow_write" not in inspect.signature(fn).parameters
        assert not fn.__name__.startswith(("tool_ensure", "tool_refresh"))
