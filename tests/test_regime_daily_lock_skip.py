# -*- coding: utf-8 -*-
"""Wiring regression: the scheduled regime job skips cleanly on lock contention.

``run_regime_daily.py`` (Windows Task MarketData_HMMRegime, 30 min after US
close) takes the single-writer DB lock around the whole HMM estimation. If the
USClose/EU18 refresh or a manual backfill still holds that lock, ``db_write_lock``
raises ``DBLockTimeout`` after the timeout. ``runner.py`` catches this and skips
cleanly; the regime entry point did not, so a benign, expected contention
crashed the task with a traceback (red Scheduler task, silently-missed regime
report + Telegram). This test pins the "skip cleanly, exit 0" contract.

Unlike the ``lazyhmm``-gated regime tests (which ``importorskip`` and are hence
skipped in CI), this stubs ``lazyhmm`` so it exercises the wiring everywhere —
the lock-handling path never touches the HMM engine.
"""
from __future__ import annotations

import sys
import types
from contextlib import contextmanager


def _install_lazyhmm_stub(monkeypatch) -> None:
    """Make ``run_regime_daily`` importable without the optional lazyhmm add-on.

    Only the two names ``regime.estimate`` binds at import time are needed; the
    engine is never invoked on the lock-timeout path under test.
    """
    fake = types.ModuleType("lazyhmm")
    fake.MSRegimeEngine = object
    fake.RegimeRun = object
    monkeypatch.setitem(sys.modules, "lazyhmm", fake)


def test_regime_daily_skips_cleanly_on_writer_lock_timeout(monkeypatch):
    _install_lazyhmm_stub(monkeypatch)
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
    _install_lazyhmm_stub(monkeypatch)
    import importlib

    rrd = importlib.import_module("run_regime_daily")
    assert hasattr(rrd, "DBLockTimeout"), "run_regime_daily must import DBLockTimeout to handle it"
