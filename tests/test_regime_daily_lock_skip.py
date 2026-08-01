# -*- coding: utf-8 -*-
"""Wiring regression: the scheduled regime job skips cleanly on lock contention.

``run_regime_daily.py`` (Windows Task MarketData_HMMRegime, 30 min after US
close) takes the single-writer DB lock around the whole HMM estimation. If the
USClose/EU18 refresh or a manual backfill still holds that lock, ``db_write_lock``
raises ``DBLockTimeout`` after the timeout. ``runner.py`` catches this and skips
cleanly; the regime entry point did not, so a benign, expected contention
crashed the task with a traceback (red Scheduler task, silently-missed regime
report + Telegram). This test pins the "skip cleanly, exit 0" contract.

Unlike the ``lazystats.regimes``-gated regime tests (which ``importorskip`` and are hence
skipped in CI), this stubs ``lazystats.regimes`` so it exercises the wiring everywhere —
the lock-handling path never touches the HMM engine.
"""
from __future__ import annotations

import sys
import types
from contextlib import contextmanager


def _install_optional_stack_stubs(monkeypatch, request) -> None:
    """Make ``run_regime_daily`` importable without the optional regime add-on
    stack (``lazystats``, ``lazytools`` + ``matplotlib``), so this wiring test
    runs in CI too.

    The lock-timeout path never fits an HMM, persists a result, or renders a
    chart, so trivial stubs suffice: ``regime.estimate`` binds two names from
    ``lazystats.regimes`` at import plus ``ResultDepot`` from
    ``lazystats.io.depot`` and the ``lazytools.registry`` module (both used
    only inside ``run_daily_regime_estimation``, which this test's lock-timeout
    path never reaches); ``regime.report`` does ``import matplotlib;
    matplotlib.use(...); import matplotlib.pyplot`` at import.
    """
    # Force a fresh import under the stubs, and purge those same modules again
    # at teardown. monkeypatch.delitem's own revert only restores a sys.modules
    # entry to what it was *before* this call -- it does not know about (and
    # so cannot undo) the brand-new "market_data_hub.regime.*"/"run_regime_daily"
    # entries that importlib.import_module() below inserts *while* matplotlib/
    # lazystats/lazytools are stubbed. Left alone, those entries (whose module
    # objects hold references to the stubs, e.g. report.py's module-level
    # `plt`) would stay cached for the rest of the test session and silently
    # break any later test that imports the real regime.report/estimate under
    # pytest-randomly's randomized order. Deleting them again on teardown
    # forces the next real import to rebuild against the real dependencies.
    def _purge_regime_modules() -> None:
        for _name in list(sys.modules):
            if _name == "run_regime_daily" or _name.startswith("market_data_hub.regime"):
                del sys.modules[_name]

    _purge_regime_modules()
    request.addfinalizer(_purge_regime_modules)

    lazystats = types.ModuleType("lazystats")
    lazystats.__path__ = []  # type: ignore[attr-defined]  # marks it as a package so "lazystats.io.depot" resolves as a submodule
    regimes = types.ModuleType("lazystats.regimes")
    regimes.MSRegimeEngine = object
    regimes.RegimeRun = object
    lazystats.regimes = regimes
    lazystats_io = types.ModuleType("lazystats.io")
    lazystats_io.__path__ = []  # type: ignore[attr-defined]
    lazystats_io_depot = types.ModuleType("lazystats.io.depot")
    lazystats_io_depot.ResultDepot = object
    lazystats_io.depot = lazystats_io_depot
    lazystats.io = lazystats_io
    monkeypatch.setitem(sys.modules, "lazystats", lazystats)
    monkeypatch.setitem(sys.modules, "lazystats.regimes", regimes)
    monkeypatch.setitem(sys.modules, "lazystats.io", lazystats_io)
    monkeypatch.setitem(sys.modules, "lazystats.io.depot", lazystats_io_depot)

    lazytools = types.ModuleType("lazytools")
    lazytools.__path__ = []  # type: ignore[attr-defined]
    lazytools_registry = types.ModuleType("lazytools.registry")
    lazytools_registry.resolve_db = lambda name: None
    lazytools.registry = lazytools_registry
    monkeypatch.setitem(sys.modules, "lazytools", lazytools)
    monkeypatch.setitem(sys.modules, "lazytools.registry", lazytools_registry)

    mpl = types.ModuleType("matplotlib")
    mpl.use = lambda *a, **k: None
    pyplot = types.ModuleType("matplotlib.pyplot")
    mpl.pyplot = pyplot
    monkeypatch.setitem(sys.modules, "matplotlib", mpl)
    monkeypatch.setitem(sys.modules, "matplotlib.pyplot", pyplot)


def test_regime_daily_skips_cleanly_on_writer_lock_timeout(monkeypatch, request):
    _install_optional_stack_stubs(monkeypatch, request)
    import importlib

    rrd = importlib.import_module("run_regime_daily")
    from market_data_hub.lock import DBLockTimeout

    calls = {"estimate": 0}

    @contextmanager
    def _lock_held_by_other(*args, **kwargs):
        # Simulate another writer holding the lock past the timeout.
        raise DBLockTimeout("Another writer holds the DB lock (x.lock); skipping this run.")
        yield  # pragma: no cover — generator marker; never reached

    def _fail_if_called(**kwargs):
        calls["estimate"] += 1
        raise AssertionError("estimation ran despite the lock being contended")

    monkeypatch.setattr(rrd, "db_write_lock", _lock_held_by_other)
    monkeypatch.setattr(rrd, "run_daily_regime_estimation", _fail_if_called)
    # --tickers supplies the universe directly (no DB hit) so main() reaches the
    # locked section; --dry-run keeps it off the Telegram path.
    monkeypatch.setattr(sys, "argv", ["run_regime_daily.py", "--tickers", "SPY", "--dry-run"])

    rc = rrd.main()

    assert rc == 0, "scheduled regime job must skip cleanly (exit 0) on lock contention"
    assert calls["estimate"] == 0, "estimation must not run when the writer lock is contended"


def test_regime_daily_imports_dblocktimeout_symbol(monkeypatch, request):
    """Guard against the import regressing: the handler needs the symbol bound."""
    _install_optional_stack_stubs(monkeypatch, request)
    import importlib

    rrd = importlib.import_module("run_regime_daily")
    assert hasattr(rrd, "DBLockTimeout"), "run_regime_daily must import DBLockTimeout to handle it"
