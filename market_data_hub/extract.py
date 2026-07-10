# -*- coding: utf-8 -*-
"""
extract.py — analysis-ready extraction on top of reader.py.

reader.py returns the raw stored shape (wide prices, long crypto/panel, ...).
Downstream models (e.g. LazyHMM regime detection) want a clean, transformed
time-series matrix: a DataFrame with a DatetimeIndex and one column per series,
optionally turned into log-returns and resampled to a target frequency.

This module is that adapter. Every function returns ``(df, meta)`` where ``df``
is the analysis matrix and ``meta`` is a JSON-serializable dict describing what
was extracted (and the coverage/quality of the inputs).

Examples:
    from market_data_hub.extract import extract_series, extract_returns
    df, meta = extract_returns(["SPY", "TLT", "^VIX"], start="2010-01-01", frequency="W")
    # df is ready for: MSRegimeEngine(...).fit(df, model="panel")

    macro, m = extract_series(["DGS10", "T10Y2Y"], domain="macro", transform="diff")
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

from market_data_hub import reader

_TRANSFORMS = {"level", "log_return", "pct_change", "diff"}
_FREQ_RULE = {"D": "D", "W": "W-FRI", "M": "ME", "Q": "QE"}


def _as_list(symbols: Union[str, List[str]]) -> List[str]:
    return [symbols] if isinstance(symbols, str) else list(symbols)


def _resample(levels: pd.DataFrame, frequency: Optional[str]) -> pd.DataFrame:
    """Resample a wide LEVEL frame to the target frequency (last observation in
    each bucket). Transforms are applied *after* resampling, so W/M/Q returns
    compound correctly (e.g. weekly log-return = log(last_w / last_w-1)).

    "D" means the native daily grid, NOT a calendar-day resample: reindexing
    trading-day prices onto calendar days would insert NaN weekend rows and
    void every Monday/post-holiday return computed via shift(1)."""
    if not frequency or frequency == "D":
        return levels
    return levels.resample(_FREQ_RULE[frequency]).last()


def _apply_transform(level: pd.DataFrame, transform: str) -> pd.DataFrame:
    if transform == "level":
        return level
    if transform == "log_return":
        return np.log(level / level.shift(1))
    if transform == "pct_change":
        return level.pct_change()
    if transform == "diff":
        return level.diff()
    raise ValueError(f"Invalid transform {transform!r}; allowed: {sorted(_TRANSFORMS)}")


def _fill(df: pd.DataFrame, fillna: str) -> pd.DataFrame:
    if fillna == "none":
        return df
    if fillna == "ffill":
        return df.ffill()
    if fillna == "zero":
        return df.fillna(0.0)
    if fillna == "drop":
        return df.dropna(how="any")
    raise ValueError(f"Invalid fillna {fillna!r}; allowed: ffill|drop|zero|none")


def _quality(symbols: List[str], db_path: Optional[str]) -> Dict[str, dict]:
    """Per-symbol coverage snapshot (best-effort)."""
    try:
        cov = reader.get_coverage(symbols=symbols, db_path=db_path)
    except Exception:
        return {}
    if cov is None or cov.empty:
        return {}
    keep = [c for c in ("last_date", "lag_days", "coverage_score", "stalled",
                        "freq_detected", "status") if c in cov.columns]
    out: Dict[str, dict] = {}
    for _, r in cov.iterrows():
        rec = {k: (None if pd.isna(r[k]) else r[k]) for k in keep}
        # dates / numpy types -> JSON-friendly
        for k, v in rec.items():
            if v is not None and hasattr(v, "isoformat"):
                rec[k] = v.isoformat()
            elif isinstance(v, (np.generic,)):
                rec[k] = v.item()
        out[str(r["symbol"])] = rec
    return out


def _wide_levels(symbols: List[str], start: Optional[str], end: Optional[str],
                 domain: str, field: str, db_path: Optional[str]) -> pd.DataFrame:
    """Pull a wide (date × symbol) level matrix for the requested domain."""
    if domain == "prices":
        return reader.read_prices(symbols, start=start, end=end, field=field,
                                  wide=True, db_path=db_path)
    if domain == "macro":
        return reader.read_macro(symbols, start=start, end=end, wide=True, db_path=db_path)
    if domain == "custom":
        return reader.read_custom(symbols, start=start, end=end, wide=True, db_path=db_path)
    if domain == "factors":
        return reader.read_factors(factors=symbols, start=start, end=end,
                                   wide=True, db_path=db_path)
    if domain == "crypto":
        # crypto is long; pivot close by symbol (single timeframe at a time)
        frames = []
        for sym in symbols:
            raw = reader.read_crypto(sym, timeframe=field,
                                     start=start, end=end, db_path=db_path)
            if raw is None or raw.empty:
                continue
            s = raw.set_index("ts")["close"].rename(sym)
            frames.append(s)
        if not frames:
            return pd.DataFrame()
        out = pd.concat(frames, axis=1)
        out.index = pd.to_datetime(out.index)
        return out.sort_index()
    raise ValueError(
        f"Invalid domain {domain!r}; allowed: prices|macro|custom|crypto|factors")


def extract_series(symbols: Union[str, List[str]], start: Optional[str] = None,
                   end: Optional[str] = None, *, domain: str = "prices",
                   field: str = "adj_close", transform: str = "level",
                   frequency: Optional[str] = None, fillna: str = "none",
                   align: bool = True, db_path: Optional[str] = None
                   ) -> Tuple[pd.DataFrame, dict]:
    """Extract an analysis-ready time-series matrix.

    Parameters
    ----------
    symbols   : one symbol or a list (prices/crypto symbols, FRED series_ids,
                custom series_ids, or factor names).
    domain    : "prices" | "macro" | "custom" | "crypto" | "factors" — "custom"
                reads app-published series (market_data_hub.custom.store_series).
    field     : for prices, the OHLCV field (adj_close default); for crypto, the timeframe.
    transform : "level" | "log_return" | "pct_change" | "diff".
    frequency : None (native) | "D" | "W" (W-FRI) | "M" | "Q" — resampling target.
    fillna    : "none" | "ffill" | "zero" | "drop".
    align     : if True, drop rows that are all-NaN after the transform.

    Returns
    -------
    (df, meta) : df has a DatetimeIndex and one column per symbol; meta is a
                 JSON-serializable description including per-symbol coverage.
    """
    symbols = _as_list(symbols)
    if transform not in _TRANSFORMS:
        raise ValueError(f"Invalid transform {transform!r}; allowed: {sorted(_TRANSFORMS)}")
    if frequency and frequency not in _FREQ_RULE:
        raise ValueError(f"Invalid frequency {frequency!r}; allowed: {sorted(_FREQ_RULE)}")
    if domain == "crypto" and field == "adj_close":
        field = "1d"  # `field` carries the timeframe for crypto; map the price default

    # Fast path: stored daily log-returns via the v_returns view (prices only).
    used_view = False
    if (domain == "prices" and transform == "log_return"
            and field == "adj_close" and frequency in (None, "D")):
        df = _read_returns_view(symbols, start, end, db_path)
        used_view = not df.empty
    else:
        df = None

    if not used_view:
        levels = _wide_levels(symbols, start, end, domain, field, db_path)
        if levels.empty:
            df = pd.DataFrame()
        else:
            df = _apply_transform(_resample(levels, frequency), transform)

    if not df.empty:
        df = _fill(df, fillna)
        if align:
            df = df.dropna(how="all")
        # keep requested column order where possible
        cols = [s for s in symbols if s in df.columns]
        if cols:
            df = df[cols]

    meta = _build_meta(df, symbols, domain, field, transform, frequency,
                       fillna, used_view, db_path)
    return df, meta


def _read_returns_view(symbols, start, end, db_path) -> pd.DataFrame:
    """Wide log-returns straight from the v_returns view."""
    con = reader._con(db_path)
    try:
        clauses = ["symbol IN (" + ",".join(["?"] * len(symbols)) + ")"]
        params: list = list(symbols)
        if start:
            clauses.append("date >= ?")
            params.append(start)
        if end:
            clauses.append("date <= ?")
            params.append(end)
        where = " AND ".join(clauses)
        raw = con.execute(
            f"SELECT date, symbol, log_return FROM v_returns WHERE {where} "
            f"AND log_return IS NOT NULL ORDER BY date", params).fetch_df()
    finally:
        con.close()
    if raw.empty:
        return pd.DataFrame()
    out = raw.pivot_table(index="date", columns="symbol", values="log_return",
                          aggfunc="last")
    out.index = pd.to_datetime(out.index)
    return out.sort_index()


def _build_meta(df: pd.DataFrame, symbols, domain, field, transform, frequency,
                fillna, used_view, db_path) -> dict:
    missing_pct = {}
    if not df.empty:
        missing_pct = {c: round(float(df[c].isna().mean() * 100), 3) for c in df.columns}
    return {
        "domain": domain,
        "symbols": symbols,
        "field": field,
        "transform": transform,
        "frequency": frequency,
        "fillna": fillna,
        "used_returns_view": used_view,
        "n_rows": int(len(df)),
        "n_cols": int(df.shape[1]) if not df.empty else 0,
        "columns": list(df.columns) if not df.empty else [],
        "missing": [s for s in symbols if df.empty or s not in df.columns],
        "date_start": df.index.min().date().isoformat() if not df.empty else None,
        "date_end": df.index.max().date().isoformat() if not df.empty else None,
        "missing_pct": missing_pct,
        "source": "market-data-hub",
        "quality": _quality(symbols, db_path) if domain in ("prices", "crypto") else {},
    }


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------
def extract_returns(symbols: Union[str, List[str]], start: Optional[str] = None,
                    end: Optional[str] = None, *, frequency: Optional[str] = "W",
                    field: str = "adj_close", fillna: str = "none",
                    db_path: Optional[str] = None) -> Tuple[pd.DataFrame, dict]:
    """Log-returns of prices, default weekly (W-FRI) — the shape LazyHMM expects.

    When frequency is weekly/monthly the levels are resampled with last() and the
    log-return is computed on the resampled series.
    """
    return extract_series(symbols, start=start, end=end, domain="prices",
                          field=field, transform="log_return",
                          frequency=frequency, fillna=fillna, db_path=db_path)


def extract_macro(series_ids: Union[str, List[str]], start: Optional[str] = None,
                  end: Optional[str] = None, *, transform: str = "level",
                  frequency: Optional[str] = None, fillna: str = "ffill",
                  db_path: Optional[str] = None) -> Tuple[pd.DataFrame, dict]:
    """FRED macro series as a wide matrix (ffill default since macro is sparse)."""
    return extract_series(series_ids, start=start, end=end, domain="macro",
                          transform=transform, frequency=frequency,
                          fillna=fillna, db_path=db_path)


def extract_panel(indicator: str, countries: Optional[List[str]] = None,
                  start: Optional[str] = None, end: Optional[str] = None,
                  asof: Optional[str] = None,
                  db_path: Optional[str] = None) -> Tuple[pd.DataFrame, dict]:
    """A single cross-country indicator pivoted date × country (wide)."""
    df = reader.read_macro_panel(indicator, countries=countries, start=start,
                                 end=end, wide=True, asof=asof, db_path=db_path)
    meta = {
        "domain": "macro_panel",
        "indicator": indicator,
        "countries": countries,
        "asof": asof,
        "n_rows": int(len(df)),
        "n_cols": int(df.shape[1]) if not df.empty else 0,
        "columns": list(df.columns) if not df.empty else [],
        "date_start": df.index.min().date().isoformat() if not df.empty else None,
        "date_end": df.index.max().date().isoformat() if not df.empty else None,
        "source": "market-data-hub",
    }
    return df, meta
