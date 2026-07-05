# -*- coding: utf-8 -*-
"""market_data_hub — unified market data downloader with a DuckDB database."""
__version__ = "0.1.0"

# Configure SSL verification (networks with corporate MITM/proxy) before any
# import of curl_cffi/requests in the submodules.
from market_data_hub._ssl_bootstrap import ensure_ssl as _ensure_ssl  # noqa: E402
_ensure_ssl()

# Public read / discovery / extraction API for downstream tools and LLMs.
# These pull pandas/duckdb. The import is guarded so that the pandas-free shared
# data contract (market_data_hub.lazydatacore) stays importable on tools that do
# not install the full data-core stack (e.g. LazyFin, which has no pandas).
try:
    from market_data_hub import catalog, custom, extract, reader  # noqa: E402,F401
    __all__ = ["catalog", "custom", "extract", "reader"]
except ModuleNotFoundError as _exc:  # pragma: no cover - only without the heavy stack
    # Tolerate *only* the heavy optional stack being absent (so the pandas-free
    # lazydatacore contract still imports); re-raise any other import failure so
    # real breakage in catalog/extract/reader is not silently masked.
    if _exc.name not in {"pandas", "duckdb", "numpy", "pyarrow"}:
        raise
    __all__ = []
