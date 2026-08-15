"""Regime estimation does not live here, and must not come back.

It did live here. Until August 2026 this repository carried its own HMM fit,
its own report and its own runner, while LazyStats carried the same method for
the same symbols against the same depot. Two copies of a method are two answers
waiting to disagree, and they did: on 13 August the two produced different BIC
for TLT on identical prices.

The split is by subject, not by taste. This repository owns prices: where they
come from, whether they arrived, what is missing. LazyStats owns what a price
series means -- fitting, states, persistence to the ResultDepot. A regime
estimate appearing here again would not be a duplicate file, it would be a
second owner of an answer.

These are cheap and blunt on purpose. The failure they guard against is not
subtle -- someone copies a module back, or a runner is restored from an old
branch -- and it is exactly the kind of thing that goes unnoticed until two
reports disagree.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "market_data_hub"


def test_the_regime_package_is_gone():
    assert not (PACKAGE / "regime").exists(), (
        "market_data_hub/regime/ is back; the fit belongs to LazyStats"
    )


def test_it_is_not_importable_either():
    """A leftover installed copy answers imports even with the source removed."""
    assert importlib.util.find_spec("market_data_hub.regime") is None, (
        "market_data_hub.regime imports from somewhere -- most likely a stale "
        "install still on this machine, which is how a removed module keeps "
        "answering for weeks"
    )


@pytest.mark.parametrize(
    "leaf",
    ["run_regime_daily.py",
     "run_regime_daily_with_telegram.ps1",
     "run_regime_8y.ps1",
     "run_regime_window_comparison_with_telegram.ps1"],
)
def test_no_regime_runner_sits_at_the_repository_root(leaf):
    """Two of these were already missing before the removal, and their scheduled
    tasks pointed at them anyway for five days without anyone noticing. The
    tasks are unregistered now; the files must not reappear without them."""
    assert not (ROOT / leaf).exists(), f"{leaf} is back at the repository root"


FITTING = {"MSRegimeEngine", "RegimeEngine", "fit_autos_Y", "fit_with_auto_S"}


def test_no_module_here_fits_a_regime():
    """Imports and calls, read from the syntax tree -- not raw text.

    `extract.py` ends with a comment saying the frame it returns is ready for
    `MSRegimeEngine(...).fit(df, model="panel")`, and that comment should stay:
    it tells the reader where the fit happens now. A check that cannot tell a
    signpost from the thing it points at would have to be silenced, and a
    silenced check protects nothing.
    """
    offenders = []
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "lazystats.regimes"
            ):
                offenders.append(f"{path.relative_to(ROOT)}: imports {node.module}")
            elif isinstance(node, ast.ImportFrom) and {
                a.name for a in node.names
            } & FITTING:
                offenders.append(f"{path.relative_to(ROOT)}: imports a fit engine")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and (
                node.func.id in FITTING
            ):
                offenders.append(f"{path.relative_to(ROOT)}: calls {node.func.id}()")
    assert not offenders, (
        "regime fitting reappeared in this package: " + ", ".join(offenders)
    )
