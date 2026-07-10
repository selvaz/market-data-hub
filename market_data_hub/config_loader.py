# -*- coding: utf-8 -*-
"""
config_loader.py — (cached) loading of the YAML configuration files.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

import yaml

_CONFIG_DIR = Path(__file__).parent / "config"


def _load_yaml(name: str) -> Dict[str, Any]:
    # every config YAML in this project is a top-level mapping (validated
    # by validate_config.py); callers rely on that invariant via .get(...)
    path = _CONFIG_DIR / name
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


@lru_cache(maxsize=1)
def get_settings() -> Dict[str, Any]:
    s = _load_yaml("settings.yaml")
    # Secrets are injected from the environment, never read from the YAML file.
    if os.environ.get("FRED_API_KEY"):
        s["fred_api_key"] = os.environ["FRED_API_KEY"]

    reports = s.setdefault("reports", {})
    if os.environ.get("MARKET_DATA_REPORT_DIR"):
        reports["dir"] = os.environ["MARKET_DATA_REPORT_DIR"]

    return s


@lru_cache(maxsize=1)
def get_yahoo_tickers() -> List[Dict[str, Any]]:
    fred_symbols = {e["symbol"] for e in _load_yaml("macro_series.yaml").get("fred", [])}
    return [e for e in _load_yaml("tickers.yaml").get("yahoo", [])
            if e["symbol"] not in fred_symbols]


@lru_cache(maxsize=1)
def get_fred_series() -> List[Dict[str, Any]]:
    fred: List[Dict[str, Any]] = _load_yaml("macro_series.yaml").get("fred", [])
    return fred


@lru_cache(maxsize=1)
def get_countries() -> List[Dict[str, Any]]:
    countries: List[Dict[str, Any]] = _load_yaml("countries.yaml").get("countries", [])
    return countries


@lru_cache(maxsize=1)
def get_macro_panel_specs() -> List[Dict[str, Any]]:
    specs: List[Dict[str, Any]] = _load_yaml("macro_panel.yaml").get("indicators", [])
    return specs
