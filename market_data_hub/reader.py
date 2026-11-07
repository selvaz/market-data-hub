# -*- coding: utf-8 -*-
"""
reader.py — public read API for the other projects.

Opens the DB in read-only mode (multiple processes can read in parallel).
The returned DataFrames have a structure compatible with the parquet/CSV files
used so far by the projects (date index, columns = symbols) to minimize changes.

Examples:
    from market_data_hub.reader import read_prices, read_macro, read_crypto
    px = read_prices(["SPY", "^VIX"], start="2020-01-01")          # wide adj_close
    vix = read_prices(["^VIX9D","^VIX","^VIX3M"], field="adj_close")
    macro = read_macro(["DGS10", "CPIAUCSL"])
    btc = read_crypto("BTCUSDT", "1h", start="2024-01-01")
"""

from __future__ import annotations

from typing import List, Optional, Union

import pandas as pd

from market_data_hub.db.connection import get_conn
from market_data_hub.lazydatacore import InstrumentId, to_duckdb  # noqa: F401

# Columns of prices_daily that read_prices() may expose as the wide `field`.
# `field` is interpolated into the SQL (it cannot be a bind parameter), so it
# MUST be validated against this whitelist — reader.py is a public API for the
# other projects.
_ALLOWED_PRICE_FIELDS = {"open", "high", "low", "close", "adj_close", "volume"}


def _con(db_path: Optional[str]):
    return get_conn(db_path, read_only=True)


def _resolved_connection(db_path):
    from pathlib import Path

    from market_data_hub.db.connection import _connect_read_only_waiting, resolve_db_path

    path = resolve_db_path(db_path)
    if not Path(path).is_file():
        raise FileNotFoundError(path)
    return _connect_read_only_waiting(path)


def _resolved_cutoff(as_of):
    from datetime import date, datetime

    if as_of is None:
        return date.today()
    if isinstance(as_of, datetime):
        return as_of.date()
    if isinstance(as_of, date):
        return as_of
    return date.fromisoformat(as_of)


def _resolved_business_day_age(observation_date, cutoff):
    """Count weekdays strictly after observation_date through cutoff, inclusive."""
    from datetime import timedelta

    import numpy as np

    if cutoff < observation_date:
        raise ValueError('cutoff precedes observation date')
    one_day = timedelta(days=1)
    return int(np.busday_count((observation_date + one_day).isoformat(),
                               (cutoff + one_day).isoformat()))


def _resolve_one(con, concept, spec, cutoff, tolerance):
    from itertools import combinations
    import math

    fresh = []
    reasons = []
    for source in spec['sources']:
        if source['role'] == 'history_only':
            continue
        sid = source['series_id']
        row = con.execute(
            'SELECT date, value FROM macro_series WHERE series_id = ? AND date <= ? '
            'AND value IS NOT NULL ORDER BY date DESC LIMIT 1', [sid, cutoff]
        ).fetchone()
        if row is None or not math.isfinite(row[1]):
            reasons.append(f'{sid}: missing')
            continue
        age = _resolved_business_day_age(row[0], cutoff)
        if age > source['max_age_business_days']:
            reasons.append(f'{sid}: stale ({age} business days)')
            continue
        fresh.append({'series_id': sid, 'value': row[1], 'observation_date': row[0],
                      'role': source['role'], 'age_business_days': age,
                      'convention': source['convention'],
                      'frequency': source.get('frequency', 'D')})
    result = {'concept': concept, 'value': None, 'observation_date': None,
              'series_id': None, 'role': None, 'status': 'unavailable',
              'age_business_days': None, 'other_fresh_candidates': [], 'alternatives': [],
              'reason': '; '.join(reasons) or 'no eligible fresh source'}
    if not fresh:
        return result
    daily = [item for item in fresh if item['frequency'] == 'D']
    pool = daily or fresh
    latest_date = max(item['observation_date'] for item in pool)
    same_date = [item for item in pool if item['observation_date'] == latest_date]
    chosen = same_date[0]
    disputed = False
    pair_tolerances = {frozenset(pair['series_ids']): pair['tolerance_bp']
                       for pair in spec.get('pair_tolerances', [])}

    def threshold(a, b):
        if a['frequency'] != b['frequency'] or a['convention'] != b['convention']:
            return None
        pair = frozenset((a['series_id'], b['series_id']))
        if pair in pair_tolerances:
            return pair_tolerances[pair]
        return tolerance

    # Fallbacks are sequential: the first fresh fallback is selectable only
    # when every higher-ranked source is unavailable, and never votes against
    # another source. Build a pairwise comparable set anchored at that source.
    eligible = []
    for candidate in same_date:
        if candidate['role'] == 'fallback':
            if not eligible:
                eligible.append(candidate)
            break
        eligible.append(candidate)
    voters = [eligible[0]]
    for candidate in eligible[1:]:
        if all(threshold(candidate, previous) is not None for previous in voters):
            voters.append(candidate)
    disagreement = any(abs(a['value'] - b['value']) * 100 > threshold(a, b) + 1e-9
                       for a, b in combinations(voters, 2))
    if disagreement:
        # Find all largest pairwise-agreeing groups; ambiguous groups cannot
        # decide which observation to use, even if both contain a majority.
        groups = []
        for size in range(len(voters), len(voters) // 2, -1):
            if size < 2 or len(voters) < 3:
                break
            groups = [group for group in combinations(range(len(voters)), size)
                      if all(abs(voters[a]['value'] - voters[b]['value']) * 100
                             <= threshold(voters[a], voters[b]) + 1e-9
                             for a, b in combinations(group, 2))]
            if groups:
                break
        if len(groups) == 1:
            chosen = voters[groups[0][0]]
            reasons.append('fresh sources disagree; strict majority agrees within tolerance')
        else:
            disputed = True
            reasons.append('fresh sources disagree without an unambiguous majority')
    result.update(chosen)
    higher = fresh[:next(i for i, item in enumerate(fresh) if item is chosen)]
    later_than_higher = any(item['frequency'] == chosen['frequency']
                            and item['observation_date'] < chosen['observation_date'] for item in higher)
    result['status'] = ('disputed' if disputed else 'primary' if chosen['role'] == 'primary'
                        else f"latest:{chosen['series_id']}" if later_than_higher
                        else f"fallback:{chosen['series_id']}")
    alternatives = [{**item, 'basis_bp': (item['value'] - chosen['value']) * 100
                     if item['observation_date'] == chosen['observation_date'] else None,
                     'voted': item in voters} for item in fresh if item is not chosen]
    result['other_fresh_candidates'] = alternatives
    result['alternatives'] = alternatives
    result['reason'] = '; '.join(reasons) or 'highest-priority source on latest fresh date'
    return result


def read_resolved_rates(concepts=None, as_of=None, db_path=None):
    """Resolve concepts using dated observations, without network or DB writes.

    The cutoff limits observation dates; it does not reconstruct vintage availability.
    Returns a list of dictionaries in requested order.
    """
    from market_data_hub.resolved_rates import load_resolved_rates

    config, table = load_resolved_rates()
    names = list(table) if concepts is None else [concepts] if isinstance(concepts, str) else list(concepts)
    for name in names:
        if name not in table:
            raise KeyError(f'unknown resolved rate concept: {name}')
    cutoff = _resolved_cutoff(as_of)
    con = _resolved_connection(db_path)
    try:
        return [_resolve_one(con, name, table[name], cutoff, config['tolerance_bp']) for name in names]
    finally:
        con.close()


def read_resolved_rate(concept, as_of=None, db_path=None):
    """Resolve one concept, returning value, provenance and fresh alternatives."""
    return read_resolved_rates([concept], as_of=as_of, db_path=db_path)[0]


def _resolved_source_for_date(sources, values):
    """Select the first configured daily source, or the first other source."""
    available = [source for source in sources if source['series_id'] in values]
    daily = [source for source in available if source.get('frequency', 'D') == 'D']
    return (daily or available)[0]


def read_resolved_history(concept, series_id=None, start=None, end=None, as_of=None,
                          db_path=None, *, single_source=None):
    """Compose by date, or select one source; counts are in DataFrame.attrs.

    ``series_id`` is the legacy alias for ``single_source``. Basis at a
    convention change is new minus old, from the most recent overlap on or
    before that date; values remain unadjusted.
    """
    import math

    from market_data_hub.resolved_rates import load_resolved_rates

    _, table = load_resolved_rates()
    if concept not in table:
        raise KeyError(f'unknown resolved rate concept: {concept}')
    sources = table[concept]['sources']
    if series_id is not None and single_source is not None and series_id != single_source:
        raise ValueError('series_id and single_source disagree')
    sid = single_source if single_source is not None else series_id
    by_id = {source['series_id']: source for source in sources}
    if sid is not None and sid not in by_id:
        raise ValueError(f'{sid} is not configured for {concept}')
    cutoff = _resolved_cutoff(as_of)
    if end is not None:
        cutoff = min(cutoff, _resolved_cutoff(end))
    con = _resolved_connection(db_path)
    try:
        ids = [sid] if sid is not None else list(by_id)
        placeholders = ','.join('?' for _ in ids)
        rows = con.execute(f'SELECT date, series_id, value FROM macro_series '
                           f'WHERE series_id IN ({placeholders}) AND date <= ? '
                           'AND value IS NOT NULL ORDER BY date', [*ids, cutoff]).fetchall()
    finally:
        con.close()
    dates = {}
    for day, series, value in rows:
        if math.isfinite(value):
            dates.setdefault(day, {})[series] = value
    start_date = _resolved_cutoff(start) if start is not None else None
    previous = None
    output = []
    for day, values in sorted(dates.items()):
        source = _resolved_source_for_date([by_id[sid]] if sid is not None else sources, values)
        current = source['series_id']
        mixed = previous is not None and previous['convention'] != source['convention']
        basis = None
        basis_date = None
        if mixed:
            old = previous['series_id']
            overlaps = [d for d, vals in dates.items() if d <= day and old in vals and current in vals]
            if overlaps:
                basis_date = max(overlaps)
                basis = (dates[basis_date][current] - dates[basis_date][old]) * 100
        if start_date is None or day >= start_date:
            output.append({'date': day, 'series_id': current, 'value': values[current],
                           'role': source['role'], 'mixed_source': mixed,
                           'basis_bp': basis, 'basis_date': basis_date})
        previous = source
    frame = pd.DataFrame(output, columns=['date', 'series_id', 'value', 'role',
                                          'mixed_source', 'basis_bp', 'basis_date'])
    frame.attrs['source_counts'] = frame['series_id'].value_counts().to_dict()
    return frame


def read_resolved_percentile(concept, as_of=None, window_years=10, db_path=None):
    """Descriptive monthly-history percentile with optional overlap correction.

    Observation-date cutoff only; no release or revision vintage is implied.
    The daily-history fallback always uses a single source.
    """
    import math

    from market_data_hub.resolved_rates import load_resolved_rates

    if isinstance(window_years, bool) or not isinstance(window_years, int) or window_years <= 0:
        raise ValueError('window_years must be a positive integer')
    config, table = load_resolved_rates()
    if concept not in table:
        raise KeyError(f'unknown resolved rate concept: {concept}')
    cutoff = _resolved_cutoff(as_of)
    start = (pd.Timestamp(cutoff) - pd.DateOffset(years=window_years)).date()
    sources = table[concept]['sources']
    monthly = next((s for s in sources if s['series_id'].startswith('IRLTLT01')
                    and s.get('frequency') == 'M'), None)
    daily = [s for s in sources if s.get('frequency', 'D') == 'D']
    con = _resolved_connection(db_path)
    try:
        current = _resolve_one(con, concept, table[concept], cutoff, config['tolerance_bp'])
        ids = [s['series_id'] for s in sources]
        placeholders = ','.join('?' for _ in ids)
        rows = con.execute(f'SELECT date, series_id, value FROM macro_series '
                           f'WHERE series_id IN ({placeholders}) AND date >= ? AND date <= ? '
                           'AND value IS NOT NULL ORDER BY date', [*ids, start, cutoff]).fetchall()
    finally:
        con.close()
    groups = {}
    dates = {}
    for day, sid, value in rows:
        if math.isfinite(value):
            groups.setdefault(sid, {}).setdefault(day.strftime('%Y-%m'), []).append(value)
            dates.setdefault(day, {})[sid] = value
    means = {sid: {month: sum(values) / len(values) for month, values in months.items()}
             for sid, months in groups.items()}
    result = {'concept': concept, 'status': 'unavailable', 'reason': None,
              'percentile': None, 'window_years': window_years, 'window_start': start,
              'window_end': cutoff, 'months_used': 0, 'first_month': None, 'last_month': None,
              'history_series_id': None, 'history_method': None, 'oecd_last_month': None,
              'current_value': current['value'], 'current_date': current['observation_date'],
              'current_series_id': current['series_id'], 'current_status': current['status'],
              'adjusted_value': None, 'basis_bp': None, 'basis_month': None,
              'basis_series': None, 'basis_series_id': None,
              'overlap_months': 0, 'basis_reason': None}
    history = {}
    if monthly is not None:
        result['history_method'] = 'OECD monthly'
        result['history_series_id'] = monthly['series_id']
        history = means.get(monthly['series_id'], {})
        result['oecd_last_month'] = max(history, default=None)
    else:
        result['history_method'] = 'single daily source monthly means'
        # Longest calendar span first; coverage and chain order break ties.
        candidates = [(s, means.get(s['series_id'], {})) for s in daily]
        candidates = [(s, values) for s, values in candidates if values]
        if candidates:
            def span(item):
                months = sorted(item[1])
                first, last = months[0].split('-'), months[-1].split('-')
                return ((int(last[0]) - int(first[0])) * 12 + int(last[1]) - int(first[1]), len(months))
            selected, history = max(candidates, key=span)
            result['history_series_id'] = selected['series_id']
    result['months_used'] = len(history)
    result['first_month'] = min(history, default=None)
    result['last_month'] = max(history, default=None)
    if current['value'] is None:
        result['reason'] = 'current resolved rate is unavailable'
        return result
    if not history:
        result['reason'] = 'no monthly history in the requested window'
        return result
    if monthly is None and len(history) < 36:
        result['reason'] = 'longest daily source has fewer than 36 observed months in the window'
        return result

    basis = None
    current_is_monthly = monthly is not None and current.get('series_id') == monthly['series_id']
    if current_is_monthly:
        # The fallback value is already on the monthly history's own basis.
        result['basis_reason'] = 'current value is the monthly series itself; no correction'
    elif monthly is not None:
        composed = {}
        counts = {}
        for day, values in sorted(dates.items()):
            available = [s for s in daily if s['series_id'] in values]
            if not available:
                continue
            source = _resolved_source_for_date(available, values)
            sid = source['series_id']
            month = day.strftime('%Y-%m')
            composed.setdefault(month, []).append(values[sid])
            month_counts = counts.setdefault(month, {})
            month_counts[sid] = month_counts.get(sid, 0) + 1
        overlap = sorted(set(history) & set(composed))
        if overlap:
            month = overlap[-1]
            basis = sum(composed[month]) / len(composed[month]) - history[month]
            result['basis_month'] = month
            result['basis_series'] = counts[month]
            if len(counts[month]) == 1:
                result['basis_series_id'] = next(iter(counts[month]))
            result['overlap_months'] = len(overlap)
        if basis is None:
            result['basis_reason'] = 'no common month with composed daily history'
    else:
        result['basis_reason'] = 'no OECD monthly series in this concept chain'
    adjusted = current['value'] - basis if basis is not None else current['value']
    result['adjusted_value'] = adjusted
    result['basis_bp'] = basis * 100 if basis is not None else None
    result['percentile'] = 100.0 * sum(value <= adjusted for value in history.values()) / len(history)
    result['status'] = 'ok' if basis is not None or current_is_monthly else 'basis_not_estimated'
    return result


def read_fred_series_metadata(db_path: Optional[str] = None) -> pd.DataFrame:
    """Latest captured FRED metadata per series; never refreshes or writes."""
    from pathlib import Path

    from market_data_hub.db.connection import _connect_read_only_waiting, resolve_db_path

    path = resolve_db_path(db_path)
    if not Path(path).is_file():
        raise FileNotFoundError(path)
    with _connect_read_only_waiting(path) as con:
        row = con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'fred_series_metadata'"
        ).fetchone()
        if row is None or not row[0]:
            return pd.DataFrame()
        return con.execute(
            "SELECT * REPLACE (CAST(retrieved_at AS VARCHAR) AS retrieved_at) "
            "FROM fred_series_metadata QUALIFY row_number() OVER "
            "(PARTITION BY series_id ORDER BY retrieved_at DESC) = 1 ORDER BY series_id"
        ).df()


def read_etf_index_map(symbols: Optional[Union[str, List[str]]] = None) -> pd.DataFrame:
    """Return current configured index metadata; this is not a historical view."""
    from market_data_hub.config_loader import get_etf_index_map

    rows = get_etf_index_map()
    if symbols is not None:
        wanted = {symbols} if isinstance(symbols, str) else set(symbols)
        rows = [row for row in rows if row["symbol"] in wanted]
    return pd.DataFrame(rows)


def _read_latest_equity(table, key, values, as_of, db_path):
    values = [values] if isinstance(values, str) else list(values)
    if not values:
        return pd.DataFrame()
    placeholders = ",".join("?" for _ in values)
    cutoff = pd.Timestamp(as_of)
    if pd.isna(cutoff):
        raise ValueError("as_of must be a valid UTC cutoff")
    if cutoff.tzinfo is not None:
        cutoff = cutoff.tz_convert("UTC").tz_localize(None)
    partition = "market, universe" if table == "market_breadth_snapshots" else key
    con = _con(db_path)
    try:
        return con.execute(
            f"SELECT * FROM {table} WHERE {key} IN ({placeholders}) "
            "AND fetched_at <= ? AND collection_date <= CAST(? AS DATE) "
            f"QUALIFY row_number() OVER (PARTITION BY {partition} ORDER BY collection_date DESC) = 1",
            [*values, cutoff.to_pydatetime(), cutoff.date()],
        ).fetch_df()
    finally:
        con.close()


def read_etf_valuations(symbols, *, as_of: str, db_path: Optional[str] = None):
    """Latest first-capture valuation per symbol known by the UTC cutoff."""
    return _read_latest_equity(
        "etf_valuation_snapshots", "symbol", symbols, as_of, db_path
    )


def read_market_breadth(markets, *, as_of: str, db_path: Optional[str] = None):
    """Latest breadth snapshot per market known by the UTC cutoff."""
    return _read_latest_equity(
        "market_breadth_snapshots", "market", markets, as_of, db_path
    )


def read_etf_carry(
    symbols: Union[str, List[str]], *, as_of: str, db_path: Optional[str] = None
) -> pd.DataFrame:
    """Latest observation per symbol and field known by the UTC cutoff."""
    symbols = [symbols] if isinstance(symbols, str) else list(symbols)
    if not symbols:
        return pd.DataFrame(
            columns=[
                "date",
                "symbol",
                "field",
                "value",
                "source",
                "retrieved_at",
                "raw_field",
                "source_as_of",
                "proxy_symbol",
                "index_currency",
                "hedge_currency",
            ]
        )
    placeholders = ",".join("?" for _ in symbols)
    con = _con(db_path)
    try:
        return con.execute(
            f"SELECT * FROM etf_carry_snapshots WHERE symbol IN ({placeholders}) "
            "AND retrieved_at <= ? AND date <= CAST(? AS DATE) "
            "AND NOT (field = 'duration' AND source = 'yahoo') "
            "QUALIFY row_number() OVER (PARTITION BY symbol, field ORDER BY date DESC, retrieved_at DESC) = 1 "
            "ORDER BY symbol, field",
            [*symbols, as_of, as_of],
        ).fetch_df()
    finally:
        con.close()


def _asof_query(
    table: str,
    part_keys: List[str],
    filters: dict,
    asof: str,
    start: Optional[str],
    end: Optional[str],
):
    """Build the (sql, params) for a point-in-time read of a *_vintage table:
    for each partition (part_keys) keep the row with the greatest
    vintage_date <= asof, then apply the date window. ``filters`` maps a column
    to a list of allowed values."""
    sel = ", ".join(part_keys + ["value"])
    clauses, params = ["vintage_date <= ?"], [asof]
    for col, vals in filters.items():
        clauses.append(f"{col} IN (" + ",".join(["?"] * len(vals)) + ")")
        params += list(vals)
    inner = (
        f"SELECT {sel}, row_number() OVER (PARTITION BY {', '.join(part_keys)} "
        f"ORDER BY vintage_date DESC) AS rn FROM {table} "
        f"WHERE {' AND '.join(clauses)}"
    )
    outer = ["rn = 1"]
    if start:
        outer.append("date >= ?")
        params.append(start)
    if end:
        outer.append("date <= ?")
        params.append(end)
    return (
        f"SELECT {sel} FROM ({inner}) t WHERE {' AND '.join(outer)} "
        f"ORDER BY {', '.join(part_keys)}"
    ), params


def _read_asof(
    con,
    table: str,
    id_col: str,
    ids: List[str],
    start: Optional[str],
    end: Optional[str],
    wide: bool,
    asof: str,
) -> pd.DataFrame:
    """Point-in-time macro_series read (one id column, pivoted by it when wide)."""
    q, params = _asof_query(table, ["date", id_col], {id_col: ids}, asof, start, end)
    df = con.execute(q, params).fetch_df()
    if not wide:
        return df
    if df.empty:
        return pd.DataFrame()
    out = df.pivot_table(index="date", columns=id_col, values="value", aggfunc="last")
    out.index = pd.to_datetime(out.index)
    return out.sort_index()


def read_prices(
    symbols: Union[str, List[str]],
    start: Optional[str] = None,
    end: Optional[str] = None,
    field: str = "adj_close",
    wide: bool = True,
    include_live: bool = False,
    db_path: Optional[str] = None,
) -> pd.DataFrame:
    """
    Daily prices. wide=True -> date index, symbol columns (field `field`).
    wide=False -> long format with all OHLCV columns.
    """
    if isinstance(symbols, str):
        symbols = [symbols]
    if field not in _ALLOWED_PRICE_FIELDS:
        raise ValueError(
            f"Invalid field {field!r}; allowed: {sorted(_ALLOWED_PRICE_FIELDS)}"
        )
    con = _con(db_path)
    try:
        clauses = ["symbol IN (" + ",".join(["?"] * len(symbols)) + ")"]
        params: list = list(symbols)
        if not include_live:
            clauses.append("is_live = FALSE")
        if start:
            clauses.append("date >= ?")
            params.append(start)
        if end:
            clauses.append("date <= ?")
            params.append(end)
        where = " AND ".join(clauses)
        if wide:
            df = con.execute(
                f"SELECT date, symbol, {field} AS v FROM prices_daily "
                f"WHERE {where} ORDER BY date",
                params,
            ).fetch_df()
            if df.empty:
                return pd.DataFrame()
            out = df.pivot_table(
                index="date", columns="symbol", values="v", aggfunc="last"
            )
            out.index = pd.to_datetime(out.index)
            return out.sort_index()
        else:
            return con.execute(
                f"SELECT * FROM prices_daily WHERE {where} ORDER BY symbol, date",
                params,
            ).fetch_df()
    finally:
        con.close()


def read_macro(
    series_ids: Union[str, List[str]],
    start: Optional[str] = None,
    end: Optional[str] = None,
    wide: bool = True,
    asof: Optional[str] = None,
    db_path: Optional[str] = None,
) -> pd.DataFrame:
    """Macro series. wide=True -> date index, series_id columns.

    asof=<YYYY-MM-DD> -> point-in-time read from macro_series_vintage: the value
    as it was known on that date (greatest vintage_date <= asof), avoiding
    revision look-ahead in backtests. Without asof, the latest values are used.
    """
    if isinstance(series_ids, str):
        series_ids = [series_ids]
    con = _con(db_path)
    try:
        if asof:
            return _read_asof(
                con,
                "macro_series_vintage",
                "series_id",
                series_ids,
                start,
                end,
                wide,
                asof,
            )
        clauses = ["series_id IN (" + ",".join(["?"] * len(series_ids)) + ")"]
        params: list = list(series_ids)
        if start:
            clauses.append("date >= ?")
            params.append(start)
        if end:
            clauses.append("date <= ?")
            params.append(end)
        where = " AND ".join(clauses)
        if wide:
            df = con.execute(
                f"SELECT date, series_id, value FROM macro_series "
                f"WHERE {where} ORDER BY date",
                params,
            ).fetch_df()
            if df.empty:
                return pd.DataFrame()
            out = df.pivot_table(
                index="date", columns="series_id", values="value", aggfunc="last"
            )
            out.index = pd.to_datetime(out.index)
            return out.sort_index()
        return con.execute(
            f"SELECT * FROM macro_series WHERE {where} ORDER BY series_id, date", params
        ).fetch_df()
    finally:
        con.close()


def read_treasury_cash_balance(
    start: Optional[str] = None,
    end: Optional[str] = None,
    account_type: Optional[str] = None,
    *,
    db_path: Optional[str] = None,
) -> pd.DataFrame:
    """Treasury cash-balance rows, optionally filtered by account type."""
    con = _con(db_path)
    try:
        clauses: list = []
        params: list = []
        if start:
            clauses.append("record_date >= ?")
            params.append(start)
        if end:
            clauses.append("record_date <= ?")
            params.append(end)
        if account_type:
            clauses.append("account_type = ?")
            params.append(account_type)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return con.execute(
            f"SELECT * FROM treasury_cash_balance{where} "
            "ORDER BY record_date, account_type",
            params,
        ).fetch_df()
    finally:
        con.close()


def read_treasury_debt(
    start: Optional[str] = None,
    end: Optional[str] = None,
    *,
    db_path: Optional[str] = None,
) -> pd.DataFrame:
    """Daily U.S. public-debt totals."""
    con = _con(db_path)
    try:
        clauses: list = []
        params: list = []
        if start:
            clauses.append("record_date >= ?")
            params.append(start)
        if end:
            clauses.append("record_date <= ?")
            params.append(end)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return con.execute(
            f"SELECT * FROM treasury_debt_outstanding{where} ORDER BY record_date",
            params,
        ).fetch_df()
    finally:
        con.close()


def read_treasury_auctions(
    start: Optional[str] = None,
    end: Optional[str] = None,
    security_type: Optional[str] = None,
    *,
    db_path: Optional[str] = None,
) -> pd.DataFrame:
    """Treasury auction results, optionally filtered by security type."""
    con = _con(db_path)
    try:
        clauses: list = []
        params: list = []
        if start:
            clauses.append("record_date >= ?")
            params.append(start)
        if end:
            clauses.append("record_date <= ?")
            params.append(end)
        if security_type:
            clauses.append("security_type = ?")
            params.append(security_type)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return con.execute(
            f"SELECT * FROM treasury_auctions{where} ORDER BY auction_date, cusip",
            params,
        ).fetch_df()
    finally:
        con.close()


def _read_cftc(
    table: str,
    start: Optional[str],
    end: Optional[str],
    contract_market_name: Optional[str],
    db_path: Optional[str],
) -> pd.DataFrame:
    con = _con(db_path)
    try:
        clauses: list = []
        params: list = []
        if start:
            clauses.append("report_date >= ?")
            params.append(start)
        if end:
            clauses.append("report_date <= ?")
            params.append(end)
        if contract_market_name:
            clauses.append("contract_market_name = ?")
            params.append(contract_market_name)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return con.execute(
            f"SELECT * FROM {table}{where} ORDER BY contract_market_name, report_date",
            params,
        ).fetch_df()
    finally:
        con.close()


def read_cftc_tff(
    start=None, end=None, contract_market_name=None, *, db_path=None
) -> pd.DataFrame:
    """TFF Futures Only COT positioning in long form."""
    return _read_cftc("cftc_tff_positioning", start, end, contract_market_name, db_path)


def read_cftc_legacy(
    start=None, end=None, contract_market_name=None, *, db_path=None
) -> pd.DataFrame:
    """Legacy Futures Only COT positioning in long form."""
    return _read_cftc(
        "cftc_legacy_positioning", start, end, contract_market_name, db_path
    )


def read_alfred_vintage(
    series_id: str,
    date: Optional[str] = None,
    as_of: Optional[str] = None,
    *,
    db_path=None,
) -> pd.DataFrame:
    """Stored ALFRED observations for one series, optionally by date/vintage."""
    con = _con(db_path)
    try:
        clauses = ["series_id = ?"]
        params = [series_id]
        if date is not None:
            clauses.append("date = ?")
            params.append(date)
        if as_of is not None:
            clauses.append("as_of = ?")
            params.append(as_of)
        where = " AND ".join(clauses)
        return con.execute(
            f"SELECT * FROM alfred_vintage_observations WHERE {where} "
            "ORDER BY date, as_of",
            params,
        ).fetch_df()
    finally:
        con.close()


def alfred_as_known_at(series_id: str, as_of: str, *, db_path=None) -> pd.DataFrame:
    """ALFRED values available on a calendar date, including vintage provenance.

    Select the latest stored revision per observation before checking its
    validity end or missing value: a withdrawal must not resurrect an old value.
    Dates are day precision, not release instants. Legacy snapshot rows without
    realtime_end remain valid until superseded. History must be backfilled first.
    """
    con = _con(db_path)
    try:
        # Older, read-only databases may predate the additive v26 migration.
        columns = {
            r[1]
            for r in con.execute(
                "PRAGMA table_info('alfred_vintage_observations')"
            ).fetchall()
        }
        validity = "realtime_end" if "realtime_end" in columns else "NULL::DATE"
        return (
            con.execute(
                "SELECT * EXCLUDE (rn) FROM ("
                f"SELECT *, {validity} AS valid_until, row_number() OVER "
                "(PARTITION BY date ORDER BY as_of DESC) AS rn "
                "FROM alfred_vintage_observations WHERE series_id = ? AND as_of <= ?) "
                "WHERE rn = 1 AND value IS NOT NULL "
                "AND (valid_until IS NULL OR valid_until >= ?) ORDER BY date",
                [series_id, as_of, as_of],
            )
            .fetch_df()
            .drop(columns="valid_until")
        )
    finally:
        con.close()


def read_custom(
    series_ids: Union[str, List[str]],
    start: Optional[str] = None,
    end: Optional[str] = None,
    wide: bool = True,
    db_path: Optional[str] = None,
) -> pd.DataFrame:
    """App-published series (``custom_series``, written via
    :func:`market_data_hub.custom.store_series`).

    wide=True -> date index, series_id columns — same shape as read_macro so
    the extract layer treats both domains uniformly.
    """
    if isinstance(series_ids, str):
        series_ids = [series_ids]
    con = _con(db_path)
    try:
        clauses = ["series_id IN (" + ",".join(["?"] * len(series_ids)) + ")"]
        params: list = list(series_ids)
        if start:
            clauses.append("date >= ?")
            params.append(start)
        if end:
            clauses.append("date <= ?")
            params.append(end)
        where = " AND ".join(clauses)
        if wide:
            df = con.execute(
                f"SELECT date, series_id, value FROM custom_series "
                f"WHERE {where} ORDER BY date",
                params,
            ).fetch_df()
            if df.empty:
                return pd.DataFrame()
            out = df.pivot_table(
                index="date", columns="series_id", values="value", aggfunc="last"
            )
            out.index = pd.to_datetime(out.index)
            return out.sort_index()
        return con.execute(
            f"SELECT * FROM custom_series WHERE {where} ORDER BY series_id, date",
            params,
        ).fetch_df()
    finally:
        con.close()


def read_crypto(
    symbols: Union[str, List[str]],
    timeframe: str = "1h",
    start: Optional[str] = None,
    end: Optional[str] = None,
    db_path: Optional[str] = None,
) -> pd.DataFrame:
    """Crypto OHLCV in long format for one or more symbols at a given timeframe."""
    if isinstance(symbols, str):
        symbols = [symbols]
    con = _con(db_path)
    try:
        clauses = [
            "symbol IN (" + ",".join(["?"] * len(symbols)) + ")",
            "timeframe = ?",
        ]
        params: list = list(symbols) + [timeframe]
        if start:
            clauses.append("ts >= ?")
            params.append(start)
        if end:
            clauses.append("ts <= ?")
            params.append(end)
        where = " AND ".join(clauses)
        return con.execute(
            f"SELECT * FROM crypto_ohlcv WHERE {where} ORDER BY symbol, ts", params
        ).fetch_df()
    finally:
        con.close()


def read_macro_panel(
    indicators: Union[str, List[str]],
    countries: Optional[Union[str, List[str]]] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    wide: bool = False,
    asof: Optional[str] = None,
    db_path: Optional[str] = None,
) -> pd.DataFrame:
    """
    Cross-country macro panel (World Bank / IMF / BIS).
    wide=False -> long format (date, country_iso3, indicator_id, value, ...).
    wide=True  -> date×country pivot for a SINGLE indicator.
    asof=<YYYY-MM-DD> -> point-in-time read from macro_panel_vintage (value as
    known on that date), avoiding revision look-ahead in backtests.
    """
    if isinstance(indicators, str):
        indicators = [indicators]
    if isinstance(countries, str):
        countries = [countries]
    con = _con(db_path)
    try:
        if asof:
            filters = {"indicator_id": indicators}
            if countries:
                filters["country_iso3"] = countries
            q, params = _asof_query(
                "macro_panel_vintage",
                ["date", "country_iso3", "indicator_id"],
                filters,
                asof,
                start,
                end,
            )
            df = con.execute(q, params).fetch_df()
            if wide and not df.empty:
                if len(indicators) != 1:
                    raise ValueError("wide=True requires a single indicator")
                out = df.pivot_table(
                    index="date", columns="country_iso3", values="value", aggfunc="last"
                )
                out.index = pd.to_datetime(out.index)
                return out.sort_index()
            return df
        clauses = ["indicator_id IN (" + ",".join(["?"] * len(indicators)) + ")"]
        qparams: list = list(indicators)
        if countries:
            if isinstance(countries, str):
                countries = [countries]
            clauses.append("country_iso3 IN (" + ",".join(["?"] * len(countries)) + ")")
            qparams += list(countries)
        if start:
            clauses.append("date >= ?")
            qparams.append(start)
        if end:
            clauses.append("date <= ?")
            qparams.append(end)
        where = " AND ".join(clauses)
        df = con.execute(
            f"SELECT * FROM macro_panel WHERE {where} ORDER BY indicator_id, "
            f"country_iso3, date",
            qparams,
        ).fetch_df()
        if wide and not df.empty:
            if len(indicators) != 1:
                raise ValueError("wide=True requires a single indicator")
            out = df.pivot_table(
                index="date", columns="country_iso3", values="value", aggfunc="last"
            )
            out.index = pd.to_datetime(out.index)
            return out.sort_index()
        return df
    finally:
        con.close()


def read_macro_panel_ext(
    indicators: Optional[Union[str, List[str]]] = None,
    countries: Optional[Union[str, List[str]]] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    db_path: Optional[str] = None,
) -> pd.DataFrame:
    """
    Long-format read of v_macro_panel_ext: macro_panel PLUS single-country
    FRED series remapped into panel shape (currently the 10Y government bond
    yield, indicator_id='bond_yield_10y'). Columns: date, country_iso3,
    indicator_id, value, indicator_name, pillar, orientation, source,
    provider_dataset, provider_code, unit, frequency, updated_at.

    indicators=None -> every indicator in the view (matches the query the
    Dalio analytical layer used before it moved to the LazyRay package).
    """
    if isinstance(indicators, str):
        indicators = [indicators]
    if isinstance(countries, str):
        countries = [countries]
    con = _con(db_path)
    try:
        clauses = ["value IS NOT NULL"]
        qparams: list = []
        if indicators:
            clauses.append(
                "indicator_id IN (" + ",".join(["?"] * len(indicators)) + ")"
            )
            qparams += list(indicators)
        if countries:
            clauses.append("country_iso3 IN (" + ",".join(["?"] * len(countries)) + ")")
            qparams += list(countries)
        if start:
            clauses.append("date >= ?")
            qparams.append(start)
        if end:
            clauses.append("date <= ?")
            qparams.append(end)
        where = " AND ".join(clauses)
        return con.execute(
            f"SELECT * FROM v_macro_panel_ext WHERE {where} ORDER BY "
            f"indicator_id, country_iso3, date",
            qparams,
        ).fetch_df()
    finally:
        con.close()


def read_factors(
    factors: Optional[Union[str, List[str]]] = None,
    factor_set: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    wide: bool = True,
    db_path: Optional[str] = None,
) -> pd.DataFrame:
    """Fama-French / momentum factor returns (decimal).

    wide=True -> date index, factor columns (filter to one factor_set to avoid
    collapsing same-named factors across datasets). wide=False -> long format.
    """
    con = _con(db_path)
    try:
        clauses: list = []
        params: list = []
        if factor_set:
            clauses.append("factor_set = ?")
            params.append(factor_set)
        if factors:
            if isinstance(factors, str):
                factors = [factors]
            clauses.append("factor IN (" + ",".join(["?"] * len(factors)) + ")")
            params += list(factors)
        if start:
            clauses.append("date >= ?")
            params.append(start)
        if end:
            clauses.append("date <= ?")
            params.append(end)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        if wide:
            df = con.execute(
                f"SELECT date, factor, value FROM factor_returns{where} ORDER BY date",
                params,
            ).fetch_df()
            if df.empty:
                return pd.DataFrame()
            out = df.pivot_table(
                index="date", columns="factor", values="value", aggfunc="last"
            )
            out.index = pd.to_datetime(out.index)
            return out.sort_index()
        return con.execute(
            f"SELECT * FROM factor_returns{where} ORDER BY factor_set, factor, date",
            params,
        ).fetch_df()
    finally:
        con.close()


def get_coverage(
    symbols: Optional[List[str]] = None, db_path: Optional[str] = None
) -> pd.DataFrame:
    """coverage_report table (optionally filtered on a list of symbols)."""
    con = _con(db_path)
    try:
        if symbols:
            ph = ",".join(["?"] * len(symbols))
            return con.execute(
                f"SELECT * FROM coverage_report WHERE symbol IN ({ph}) "
                f"ORDER BY coverage_score",
                list(symbols),
            ).fetch_df()
        return con.execute(
            "SELECT * FROM coverage_report ORDER BY coverage_score"
        ).fetch_df()
    finally:
        con.close()


def get_macro_panel_coverage(db_path: Optional[str] = None) -> pd.DataFrame:
    """Cross-country availability per macro_panel indicator (ordered worst first)."""
    con = _con(db_path)
    try:
        return con.execute(
            "SELECT * FROM macro_panel_coverage ORDER BY coverage_pct, last_date"
        ).fetch_df()
    finally:
        con.close()


def get_stalled(db_path: Optional[str] = None) -> pd.DataFrame:
    """Only the stalled symbols."""
    con = _con(db_path)
    try:
        return con.execute("SELECT * FROM v_stalled").fetch_df()
    finally:
        con.close()


def get_latest(symbol: str, db_path: Optional[str] = None) -> dict:
    """Latest data point + coverage metrics for a symbol."""
    con = _con(db_path)
    try:
        px = con.execute(
            "SELECT date, close, adj_close, volume FROM prices_daily "
            "WHERE symbol = ? ORDER BY date DESC LIMIT 1",
            [symbol],
        ).fetch_df()
        cov = con.execute(
            "SELECT lag_days, coverage_score, stalled, freq_detected, status "
            "FROM coverage_report WHERE symbol = ? LIMIT 1",
            [symbol],
        ).fetch_df()
        out: dict = {"symbol": symbol}
        if not px.empty:
            out.update(px.iloc[0].to_dict())
        if not cov.empty:
            out.update(cov.iloc[0].to_dict())
        return out
    finally:
        con.close()


def read_instrument(
    instrument: "str | InstrumentId",
    start: Optional[str] = None,
    end: Optional[str] = None,
    wide: bool = False,
    asof: Optional[str] = None,
    db_path: Optional[str] = None,
) -> pd.DataFrame:
    """Read a single instrument addressed by its canonical ``InstrumentId``.

    The thin lazydatacore (L0) adapter: downstream tools pass a namespaced
    identifier (or its string) and the resolver routes it to the matching
    ``read_*`` function — the DuckDB layout is never touched. Examples::

        read_instrument("ticker:AAPL")          # 'price:AAPL' is an accepted alias
        read_instrument("crypto:BTCUSDT@4h", start="2024-01-01")
        read_instrument("macro:FEDFUNDS")
        read_instrument("macro_panel:USA/real_gdp_growth")
        read_instrument("factor:FF5_daily/Mkt-RF")

    ``asof`` applies only to the macro datasets (point-in-time vintage reads);
    it is ignored for prices/crypto/factors. Reference identities (``cik:``,
    ``isin:``) raise :class:`~market_data_hub.lazydatacore.NotResolvableError`.
    """
    ref = to_duckdb(instrument)
    f = ref.filters
    if ref.dataset == "prices":
        return read_prices(
            f["symbol"], start=start, end=end, wide=wide, db_path=db_path
        )
    if ref.dataset == "crypto":
        return read_crypto(
            f["symbol"], timeframe=f["timeframe"], start=start, end=end, db_path=db_path
        )
    if ref.dataset == "macro":
        return read_macro(
            f["series_id"], start=start, end=end, wide=wide, asof=asof, db_path=db_path
        )
    if ref.dataset == "macro_panel":
        return read_macro_panel(
            f["indicator_id"],
            countries=[f["country_iso3"]],
            start=start,
            end=end,
            wide=wide,
            asof=asof,
            db_path=db_path,
        )
    if ref.dataset == "factors":
        return read_factors(
            f["factor"],
            factor_set=f["factor_set"],
            start=start,
            end=end,
            wide=wide,
            db_path=db_path,
        )
    raise ValueError(f"unsupported dataset {ref.dataset!r}")  # defensive
