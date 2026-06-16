# -*- coding: utf-8 -*-
"""market_data_hub — downloader unificato di dati di mercato con database DuckDB."""
__version__ = "0.1.0"

# yfinance 0.2.x usa internamente pd.Timestamp.utcnow(), deprecata in pandas 3.x:
# centinaia di Pandas4Warning per ogni ticker. Sono di yfinance, non nostre.
import warnings  # noqa: E402
warnings.filterwarnings(
    "ignore", message=".*Timestamp.utcnow is deprecated.*")
warnings.filterwarnings(
    "ignore", message=".*auto_adjust default to True.*")

# Configura la verifica SSL (rete con MITM/proxy aziendale) prima di qualunque
# import di yfinance/requests nei sottomoduli.
from market_data_hub._ssl_bootstrap import ensure_ssl as _ensure_ssl  # noqa: E402
_ensure_ssl()
