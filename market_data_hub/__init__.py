# -*- coding: utf-8 -*-
"""market_data_hub — unified market data downloader with a DuckDB database."""
__version__ = "0.1.1"

# Configure SSL verification (networks with corporate MITM/proxy) before any
# import of curl_cffi/requests in the submodules.
from market_data_hub._ssl_bootstrap import ensure_ssl as _ensure_ssl  # noqa: E402
_ensure_ssl()

# Public read / discovery / extraction API for downstream tools and LLMs.
# These pull pandas/duckdb, so they are loaded lazily (PEP 562) rather than at
# package-import time: eagerly importing them here defeated the "pandas-free
# leaf" contract for market_data_hub.lazydatacore in practice, because
# `import market_data_hub.lazydatacore` always runs this __init__.py first —
# and since pandas/duckdb/numpy/pyarrow are hub *base* dependencies (always
# installed for a normal `pip install market-data-hub`), the old try/except
# ModuleNotFoundError guard never actually triggered; it only helped a
# hypothetical install missing those deps entirely, which no real install is.
# `from market_data_hub import reader` (used throughout the codebase) is
# unaffected: Python resolves that via the normal submodule-import machinery,
# not through this module's namespace. This __getattr__ only lazily backs the
# `import market_data_hub; market_data_hub.reader....` attribute-access style.
_LAZY_SUBMODULES = ("catalog", "custom", "extract", "reader")
__all__ = list(_LAZY_SUBMODULES)


def __getattr__(name: str):
    if name in _LAZY_SUBMODULES:
        import importlib
        module = importlib.import_module(f"market_data_hub.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
