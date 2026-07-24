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


def _install_optional_stack_stubs(monkeypatch) -> None:
    """Make ``run_regime_daily`` importable without the optional regime add-on
    stack (``lazystats.regimes`` + ``matplotlib``), so this wiring test runs in CI too.

    The lock-timeout path never fits an HMM or renders a chart, so trivial
    stubs suffice: ``regime.estimate`` binds two names from ``lazystats.regimes`` at
    import, and ``regime.report`` does ``import matplotlib; matplotlib.use(...);
    import matplotlib.pyplot`` at import.
    """
    # Force a fresh import under the stubs (and restore the module table at
    # teardown, so a stubbed regime module never leaks to another test under
    # pytest-randomly's randomized order).
    for _name in list(sys.modules):
        if _name == "run_regime_daily" or _name.startswith("market_data_hub.regime"):
            monkeypatch.delitem(sys.modules, _name, raising=False)

    lazystats = types.ModuleType("lazystats")
    regimes = types.ModuleType("lazystats.regimes")
    regimes.MSRegimeEngine = object
    regimes.RegimeRun = object
    lazystats.regimes = regimes
    monkeypatch.setitem(sys.modules, "lazystats", lazystats)
    monkeypatch.setitem(sys.modules, "lazystats.regimes", regimes)

    mpl = types.ModuleType("matplotlib")
    mpl.use = lambda *a, **k: None
    pyplot = types.ModuleType("matplotlib.pyplot")
    mpl.pyplot = pyplot
    monkeypatch.setitem(sys.modules, "matplotlib", mpl)
    monkeypatch.setitem(sys.modules, "matplotlib.pyplot", pyplot)


def test_regime_daily_skips_cleanly_on_writer_lock_timeout(monkeypatch):
    _install_optional_stack_stubs(monkeypatch)
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


def test_regime_daily_imports_dblocktimeout_symbol(monkeypatch):
    """Guard against the import regressing: the handler needs the symbol bound."""
    _install_optional_stack_stubs(monkeypatch)
    import importlib

    rrd = importlib.import_module("run_regime_daily")
    assert hasattr(rrd, "DBLockTimeout"), "run_regime_daily must import DBLockTimeout to handle it"
