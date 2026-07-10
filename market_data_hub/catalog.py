# -*- coding: utf-8 -*-
"""
catalog.py — discovery / "map" of what the hub contains.

This is the layer an LLM (or another tool) queries FIRST, before extracting, to
learn *what* is available and with *which* semantic cuts: by asset class, by
geographic area, by sector (e.g. US equity sectors), by macro pillar, by country,
by frequency and by data quality/freshness.

It combines the STATIC universe declared in the YAML config (config_loader.py)
with the DYNAMIC coverage metrics stored in the DB (reader.get_coverage() /
get_macro_panel_coverage()). The coverage join is best-effort: if the DB is not
populated yet, the static catalog is still returned (coverage columns are NaN).

Everything returns plain pandas DataFrames / dicts so the output is trivially
serializable for an agent.

Examples:
    from market_data_hub import catalog
    catalog.list_symbols(asset_class="EQUITY", area="Emerging Markets")
    catalog.list_symbols(asset_class="EQUITY", area="USA", sector="Energy")
    catalog.list_sectors()                 # US (and EU) equity sectors
    catalog.list_macro_indicators(pillar="growth")
    catalog.search("emerging markets bonds")
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from market_data_hub import reader
from market_data_hub.config_loader import (
    get_countries,
    get_fred_series,
    get_macro_panel_specs,
    get_settings,
    get_yahoo_tickers,
)

# ---------------------------------------------------------------------------
# Taxonomy enrichment
# ---------------------------------------------------------------------------
# Area aliases: the config uses "US" and "USA" interchangeably (e.g. the VIX
# term-structure symbols carry area "US"). Normalize so a single area filter works.
_AREA_ALIASES = {
    "US": "USA",
}

# Sector classification for the sector ETFs. The sector is NOT a structured field
# in the config — it lives inside the free-text `name` — so we map it explicitly
# by symbol. Covers the US SPDR "Select Sector" set (+ Vanguard Info Tech) and the
# STOXX Europe 600 sector sleeves.
_SECTOR_BY_SYMBOL = {
    # US — GICS sectors
    "XLB": "Materials",
    "XLC": "Communication Services",
    "XLE": "Energy",
    "XLF": "Financials",
    "XLI": "Industrials",
    "XLP": "Consumer Staples",
    "XLU": "Utilities",
    "XLV": "Health Care",
    "XLY": "Consumer Discretionary",
    "VGT": "Information Technology",
    # Europe — STOXX 600 sector sleeves
    "EXV1.DE": "Financials",        # Banks
    "EXV3.DE": "Information Technology",
    "EXV4.DE": "Health Care",
    "EXH4.DE": "Industrials",
    "EXH1.DE": "Energy",            # Oil & Gas
    "EXH9.DE": "Utilities",
}


def _norm_area(area: Optional[str]) -> Optional[str]:
    if area is None:
        return None
    return _AREA_ALIASES.get(area, area)


def _name_tokens(name: str) -> List[str]:
    """Split the canonical `TYPE | group | description` name into its parts."""
    return [t.strip() for t in str(name or "").split("|")]


def _classify(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Derive extra semantic fields from a raw config entry.

    Adds: area_norm, category (name token 0), group (name token 1),
    sector (GICS sector for sector ETFs, else None), theme (for ALTERNATIVES).
    """
    symbol = entry.get("symbol")
    tokens = _name_tokens(entry.get("name", ""))
    category = tokens[0] if len(tokens) >= 1 and tokens[0] else None
    group = tokens[1] if len(tokens) >= 2 and tokens[1] else None
    asset_class = entry.get("asset_class")

    out = {
        "symbol": symbol,
        "asset_class": asset_class,
        "area": entry.get("area"),
        "area_norm": _norm_area(entry.get("area")),
        "name": entry.get("name"),
        "category": category,
        "group": group,
        "sector": _SECTOR_BY_SYMBOL.get(symbol) if isinstance(symbol, str) else None,
        "theme": category if asset_class == "ALTERNATIVES" else None,
        "priority": entry.get("priority"),
    }
    return out


def _coverage_lookup(db_path: Optional[str]) -> pd.DataFrame:
    """coverage_report indexed by symbol (best-effort, empty on any failure)."""
    try:
        cov = reader.get_coverage(db_path=db_path)
    except Exception:
        return pd.DataFrame()
    if cov is None or cov.empty:
        return pd.DataFrame()
    # one row per symbol: keep the best-covered source if duplicated
    cov = cov.sort_values("coverage_score", ascending=False).drop_duplicates("symbol")
    return cov.set_index("symbol")


_COVERAGE_COLS = [
    "first_date", "last_date", "obs_count", "freq_detected",
    "lag_days", "stalled", "coverage_score", "status",
]


def _attach_coverage(df: pd.DataFrame, db_path: Optional[str]) -> pd.DataFrame:
    cov = _coverage_lookup(db_path)
    for col in _COVERAGE_COLS:
        if not cov.empty and col in cov.columns:
            df[col] = df["symbol"].map(cov[col])
        else:
            df[col] = pd.NA
    return df


# ---------------------------------------------------------------------------
# Datasets overview
# ---------------------------------------------------------------------------
def list_datasets() -> List[Dict[str, Any]]:
    """The domains available in the hub, with the table and how to query them."""
    settings = get_settings()
    crypto = settings.get("crypto", {})
    return [
        {
            "domain": "prices",
            "table": "prices_daily",
            "primary_key": ["date", "symbol"],
            "frequency": "D",
            "n_series": len(get_yahoo_tickers()),
            "description": "Daily OHLCV for equities/ETFs/FX/VIX. Use extract_series(domain='prices').",
            "discovery": "list_symbols()",
        },
        {
            "domain": "macro",
            "table": "macro_series",
            "primary_key": ["date", "series_id"],
            "frequency": "D/M/Q/A",
            "n_series": len(get_fred_series()),
            "description": "Single-value FRED macro series (rates, CPI, GDP, credit, liquidity).",
            "discovery": "list_macro_series()",
        },
        {
            "domain": "macro_panel",
            "table": "macro_panel",
            "primary_key": ["date", "country_iso3", "indicator_id"],
            "frequency": "A/Q",
            "n_series": len(get_macro_panel_specs()),
            "description": "Cross-country macro panel (WorldBank/IMF/BIS) by pillar.",
            "discovery": "list_macro_indicators(); list_countries()",
        },
        {
            "domain": "crypto",
            "table": "crypto_ohlcv",
            "primary_key": ["ts", "symbol", "timeframe"],
            "frequency": ",".join(crypto.get("timeframes", ["1h", "4h", "1d"])),
            "n_series": len(crypto.get("symbols", [])),
            "description": "Binance intraday OHLCV. Use extract_series(domain='crypto').",
            "discovery": "list_crypto_symbols()",
        },
        {
            "domain": "factors",
            "table": "factor_returns",
            "primary_key": ["date", "factor_set", "factor"],
            "frequency": "D/M",
            "n_series": None,
            "description": "Fama-French / momentum factor returns (decimal).",
            "discovery": "list_factor_sets()",
        },
    ]


# ---------------------------------------------------------------------------
# Symbols (prices universe) — with semantic filters
# ---------------------------------------------------------------------------
def _symbol_universe() -> pd.DataFrame:
    rows = [_classify(e) for e in get_yahoo_tickers()]
    return pd.DataFrame(rows)


def list_symbols(asset_class: Optional[str] = None, area: Optional[str] = None,
                 sector: Optional[str] = None, group: Optional[str] = None,
                 with_coverage: bool = True,
                 db_path: Optional[str] = None) -> pd.DataFrame:
    """The price-universe symbols, filterable by semantic cuts.

    asset_class : EQUITY | FIXED_INCOME | COMMODITIES | REAL_ESTATE | ALTERNATIVES | FX
    area        : geographic area (e.g. "Emerging Markets", "USA", "Europe", "China").
                  Matched against the normalized area (US == USA).
    sector      : GICS sector for sector ETFs (e.g. "Energy", "Health Care").
                  Pass "*" to keep ONLY symbols that have a sector (the US/EU sector ETFs).
    group       : the middle token of the name (e.g. "EM", "Energy", "Metals").

    Returns symbols enriched with coverage (first/last date, obs, freq, lag,
    stalled, coverage_score) when with_coverage=True and the DB is populated.
    """
    df = _symbol_universe()
    if asset_class:
        df = df[df["asset_class"] == asset_class]
    if area:
        df = df[df["area_norm"] == _norm_area(area)]
    if sector:
        if sector == "*":
            df = df[df["sector"].notna()]
        else:
            df = df[df["sector"] == sector]
    if group:
        df = df[df["group"] == group]
    df = df.reset_index(drop=True)
    if with_coverage:
        df = _attach_coverage(df, db_path)
    return df


def list_sectors(area: Optional[str] = None) -> pd.DataFrame:
    """Available equity sectors with their symbols (sector ETFs).

    area=None -> all; area="USA" -> US sectors only; area="Europe" -> EU sleeves.
    """
    df = _symbol_universe()
    df = df[df["sector"].notna()]
    if area:
        df = df[df["area_norm"] == _norm_area(area)]
    out = (df.groupby(["sector"])
             .agg(symbols=("symbol", lambda s: sorted(s)),
                  areas=("area_norm", lambda a: sorted(set(a))))
             .reset_index()
             .sort_values("sector")
             .reset_index(drop=True))
    return out


# ---------------------------------------------------------------------------
# Macro series (FRED)
# ---------------------------------------------------------------------------
def list_macro_series(frequency: Optional[str] = None, category: Optional[str] = None,
                      with_coverage: bool = True,
                      db_path: Optional[str] = None) -> pd.DataFrame:
    """FRED single-value macro series. `category` filters the name prefix
    (RATES/MACRO/CREDIT/RISK/LIQ/FX/...). Coverage is matched on series_id."""
    rows = []
    for e in get_fred_series():
        c = _classify(e)
        c["series_id"] = c.pop("symbol")
        c["country"] = e.get("country")
        rows.append(c)
    df = pd.DataFrame(rows)
    if category:
        df = df[df["category"] == category]
    df = df.reset_index(drop=True)
    if with_coverage:
        cov = _coverage_lookup(db_path)
        for col in ("first_date", "last_date", "obs_count", "freq_detected", "lag_days", "stalled"):
            df[col] = df["series_id"].map(cov[col]) if (not cov.empty and col in cov.columns) else pd.NA
    if frequency and "freq_detected" in df.columns:
        df = df[df["freq_detected"] == frequency].reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Macro panel indicators (cross-country)
# ---------------------------------------------------------------------------
def list_macro_indicators(pillar: Optional[str] = None,
                          db_path: Optional[str] = None) -> pd.DataFrame:
    """Cross-country panel indicators, filterable by pillar
    (growth/liquidity/external/debt_cycle/sovereign/banking/governance/geopolitical).
    Enriched with availability (n_countries, coverage_pct, last_date) when present."""
    specs = get_macro_panel_specs()
    df = pd.DataFrame([{
        "indicator_id": s.get("id"),
        "name": s.get("name"),
        "pillar": s.get("pillar"),
        "frequency": s.get("freq"),
        "unit": s.get("unit"),
        "source": s.get("source"),
        "dataset": s.get("dataset"),
        "orientation": s.get("orientation"),
        "priority": s.get("priority"),
    } for s in specs])
    if pillar:
        df = df[df["pillar"] == pillar]
    df = df.reset_index(drop=True)
    try:
        cov = reader.get_macro_panel_coverage(db_path=db_path)
    except Exception:
        cov = pd.DataFrame()
    if cov is not None and not cov.empty:
        cov = cov.set_index("indicator_id")
        for col in ("n_countries", "n_countries_total", "coverage_pct",
                    "first_date", "last_date", "lag_days", "stalled"):
            df[col] = df["indicator_id"].map(cov[col]) if col in cov.columns else pd.NA
    return df


# ---------------------------------------------------------------------------
# Countries
# ---------------------------------------------------------------------------
def list_countries(region: Optional[str] = None, income: Optional[str] = None,
                   g7: Optional[bool] = None) -> pd.DataFrame:
    """The macro_panel country universe, filterable by region / income / G7 flag.

    `region` matches either region_group (G7/EU/EM/...) or region_geo (the
    geographic region), so both "G7" and "East Asia & Pacific" work.
    """
    df = pd.DataFrame(get_countries())
    if region:
        cols = [c for c in ("region_group", "region_geo") if c in df.columns]
        mask = pd.Series(False, index=df.index)
        for c in cols:
            mask = mask | (df[c] == region)
        df = df[mask]
    if income and "income" in df.columns:
        df = df[df["income"] == income]
    if g7 is not None and "g7" in df.columns:
        df = df[df["g7"] == g7]
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Crypto & factors (DB-driven, fall back to config)
# ---------------------------------------------------------------------------
def list_crypto_symbols(db_path: Optional[str] = None) -> pd.DataFrame:
    """Crypto symbols × timeframes actually present in crypto_ohlcv,
    falling back to the configured universe if the DB is empty."""
    try:
        con = reader._con(db_path)
        try:
            df = con.execute(
                "SELECT symbol, timeframe, COUNT(*) AS obs_count, "
                "MIN(ts) AS first_ts, MAX(ts) AS last_ts "
                "FROM crypto_ohlcv GROUP BY symbol, timeframe "
                "ORDER BY symbol, timeframe").fetch_df()
        finally:
            con.close()
        if not df.empty:
            return df
    except Exception:
        pass
    crypto = get_settings().get("crypto", {})
    return pd.DataFrame([
        {"symbol": s, "timeframe": tf, "obs_count": pd.NA,
         "first_ts": pd.NA, "last_ts": pd.NA}
        for s in crypto.get("symbols", []) for tf in crypto.get("timeframes", [])
    ])


def list_factor_sets(db_path: Optional[str] = None) -> pd.DataFrame:
    """Factor sets and factors present in factor_returns (DB-driven)."""
    try:
        con = reader._con(db_path)
        try:
            df = con.execute(
                "SELECT factor_set, factor, frequency, COUNT(*) AS obs_count, "
                "MIN(date) AS first_date, MAX(date) AS last_date "
                "FROM factor_returns GROUP BY factor_set, factor, frequency "
                "ORDER BY factor_set, factor").fetch_df()
        finally:
            con.close()
        if not df.empty:
            return df
    except Exception:
        pass
    datasets = get_settings().get("factors", {}).get("datasets", [])
    return pd.DataFrame([{"factor_set": d, "factor": pd.NA, "frequency": pd.NA,
                          "obs_count": pd.NA, "first_date": pd.NA, "last_date": pd.NA}
                         for d in datasets])


# ---------------------------------------------------------------------------
# Single-series description & free-text search
# ---------------------------------------------------------------------------
def describe_series(symbol_or_id: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Resolve an identifier across every domain and return a single info card:
    domain, classification, source/unit and coverage/quality."""
    sym = _symbol_universe()
    hit = sym[sym["symbol"] == symbol_or_id]
    if not hit.empty:
        # pandas' incomplete type stubs make Series.to_dict() resolve to Any
        row: Dict[str, Any] = hit.iloc[0].to_dict()
        row["domain"] = "prices"
        try:
            row["latest"] = reader.get_latest(symbol_or_id, db_path=db_path)
        except Exception:
            pass
        return row

    macro = list_macro_series(with_coverage=True, db_path=db_path)
    hit = macro[macro["series_id"] == symbol_or_id]
    if not hit.empty:
        row = hit.iloc[0].to_dict()
        row["domain"] = "macro"
        return row

    ind = list_macro_indicators(db_path=db_path)
    hit = ind[ind["indicator_id"] == symbol_or_id]
    if not hit.empty:
        row = hit.iloc[0].to_dict()
        row["domain"] = "macro_panel"
        return row

    return {"symbol": symbol_or_id, "domain": None,
            "error": "not found in any domain (prices/macro/macro_panel)"}


def search(query: str, db_path: Optional[str] = None) -> pd.DataFrame:
    """Case-insensitive substring match over symbol/name/sector/area/id across
    the prices universe, the FRED macro series and the cross-country indicators.
    Returns a unified frame: (domain, key, name, asset_class/pillar, area/sector)."""
    q = (query or "").strip().lower()
    out: List[Dict[str, Any]] = []
    if not q:
        return pd.DataFrame(out)

    def _match(*vals) -> bool:
        return any(q in str(v).lower() for v in vals if v is not None)

    for e in (_classify(x) for x in get_yahoo_tickers()):
        if _match(e["symbol"], e["name"], e["sector"], e["area"], e["group"], e["asset_class"]):
            out.append({"domain": "prices", "key": e["symbol"], "name": e["name"],
                        "tag": e["asset_class"], "detail": e["sector"] or e["area"]})
    for e in get_fred_series():
        if _match(e.get("symbol"), e.get("name"), e.get("area")):
            out.append({"domain": "macro", "key": e.get("symbol"), "name": e.get("name"),
                        "tag": e.get("asset_class"), "detail": e.get("area")})
    for s in get_macro_panel_specs():
        if _match(s.get("id"), s.get("name"), s.get("pillar")):
            out.append({"domain": "macro_panel", "key": s.get("id"), "name": s.get("name"),
                        "tag": s.get("pillar"), "detail": s.get("source")})
    return pd.DataFrame(out)
