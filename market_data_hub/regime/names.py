"""Symbol display-name lookup -- pulled out into its own module (rather than
living in ``report.py`` or ``daily_payload.py``) so both can import it
without a circular dependency between them.
"""

from __future__ import annotations

from typing import Dict

from market_data_hub import catalog

__all__ = ["display_names"]


def display_names() -> Dict[str, str]:
    """symbol -> human-readable name from tickers.yaml (best-effort, one lookup)."""
    try:
        df = catalog.list_symbols(with_coverage=False)
        return {s: str(n) for s, n in zip(df["symbol"], df["name"]) if n}
    except Exception:
        return {}
