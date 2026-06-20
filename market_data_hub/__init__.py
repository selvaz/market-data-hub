# -*- coding: utf-8 -*-
"""market_data_hub — unified market data downloader with a DuckDB database."""
__version__ = "0.1.0"

# yfinance 0.2.x internally uses pd.Timestamp.utcnow(), deprecated in pandas 3.x:
# hundreds of Pandas4Warning per ticker. They come from yfinance, not from us.
import warnings  # noqa: E402
warnings.filterwarnings(
    "ignore", message=".*Timestamp.utcnow is deprecated.*")
warnings.filterwarnings(
    "ignore", message=".*auto_adjust default to True.*")

# Configure SSL verification (networks with corporate MITM/proxy) before any
# import of yfinance/requests in the submodules.
from market_data_hub._ssl_bootstrap import ensure_ssl as _ensure_ssl  # noqa: E402
_ensure_ssl()

# Public read / discovery / extraction API for downstream tools and LLMs.
# These pull pandas/duckdb. The import is guarded so that the pandas-free shared
# data contract (market_data_hub.lazydatacore) stays importable on tools that do
# not install the full data-core stack (e.g. LazyFin, which has no pandas).
try:
    from market_data_hub import catalog, extract, reader  # noqa: E402,F401
    __all__ = ["catalog", "extract", "reader"]
except ImportError:  # pragma: no cover - exercised only without pandas/duckdb
    __all__ = []
