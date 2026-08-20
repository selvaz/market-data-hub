# -*- coding: utf-8 -*-
"""TradingView's stock scanner: the global earnings schedule.

A JSON endpoint, no key and no browser, which is why it is fetched with plain
requests rather than through the crawler.

It carries exactly two dates per company -- the last release and the next
expected one -- in two different fields, and no history at all. Hence the two
modes: ``next`` looks forward and is what the weekly watchlist runs on,
``last`` looks back and is what turns an expectation into an outcome. Anything
older than roughly one reporting cycle exists only if we stored it.
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

URL = "https://scanner.tradingview.com/global/scan"

# The forward field carries next quarter's forecast; the backward one carries
# the figures for the quarter that just reported, alongside its own forecast.
_CAMPI = {
    "next": ("earnings_release_next_date",
             "earnings_per_share_forecast_next_fq", "revenue_forecast_next_fq",
             None, None),
    "last": ("earnings_release_date",
             "earnings_per_share_forecast_fq", "revenue_forecast_fq",
             "earnings_per_share_fq", "revenue_fq"),
}
_PAGINA = 500
_MEZZANOTTE = time(0, 0)


def scarica(da: datetime, a: datetime, *, mode: str = "next",
            min_market_cap: float = 0, timeout: int = 30) -> pd.DataFrame:
    """Every scheduled or published release in the window, one row per company.

    ``is_primary`` keeps a company to its primary listing: without it a single
    Walmart arrives fifteen times, once per venue that cross-lists it.
    """
    if mode not in _CAMPI:
        raise ValueError(f"unknown mode {mode!r}; expected 'next' or 'last'")
    campo_data, campo_eps_att, campo_rev_att, campo_eps, campo_rev = _CAMPI[mode]

    colonne = ["name", "description", "market_cap_basic", campo_data, "country",
               "exchange", "sector", "industry", "currency",
               campo_eps_att, campo_rev_att]
    if campo_eps:
        colonne += [campo_eps, campo_rev]

    filtri = [
        # in_range includes BOTH bounds, while `a` is exclusive everywhere else
        # here. The difference is not cosmetic: day-precision releases land on
        # midnight, and a window ending on a Monday midnight collected 65
        # Chinese companies into that week AND the next one.
        {"left": campo_data, "operation": "in_range",
         "right": [_unix(da), _unix(a) - 1]},
        {"left": "is_primary", "operation": "equal", "right": True},
    ]
    if min_market_cap:
        filtri.append({"left": "market_cap_basic", "operation": "egreater",
                       "right": min_market_cap})

    righe, inizio = [], 0
    while True:
        risposta = requests.post(
            URL, timeout=timeout,
            json={"filter": filtri, "columns": colonne,
                  "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
                  "range": [inizio, inizio + _PAGINA]},
        )
        risposta.raise_for_status()
        corpo = risposta.json()
        blocco = corpo.get("data") or []
        righe.extend(blocco)
        inizio += _PAGINA
        if len(blocco) < _PAGINA or inizio >= corpo.get("totalCount", 0):
            break

    if not righe:
        return pd.DataFrame(columns=["tv_ticker"] + colonne)

    tabella = pd.DataFrame([dict(zip(colonne, r["d"])) for r in righe])
    tabella.insert(0, "tv_ticker", [r["s"] for r in righe])
    tabella = tabella.rename(columns={
        "name": "symbol", "description": "company_name",
        "market_cap_basic": "market_cap", campo_data: "release_ts",
        campo_eps_att: "eps_estimate", campo_rev_att: "revenue_estimate",
    })
    if campo_eps:
        tabella = tabella.rename(columns={campo_eps: "eps_actual",
                                          campo_rev: "revenue_actual"})
    tabella["status"] = "occurred" if mode == "last" else "estimated"
    return tabella


def leggi(percorso, *, vintage_date=None) -> list:
    """Read a collected CSV back into observations.

    ``vintage_date`` records when the hub saw this version; it defaults to the
    day the file was written, not to today, so re-ingesting an older CSV does
    not restamp it as a fresh reading.
    """
    from market_data_hub.earnings_calendar.ingest import EarningsObservation

    percorso = Path(percorso)
    if vintage_date is None:
        vintage_date = datetime.fromtimestamp(percorso.stat().st_mtime,
                                              tz=timezone.utc).date()
    tabella = pd.read_csv(percorso)
    osservazioni = []
    for _, r in tabella.iterrows():
        istante = _istante(r.get("release_ts"))
        if istante is None:
            continue
        osservazioni.append(EarningsObservation(
            symbol=str(r["symbol"]), exchange=str(r["exchange"]),
            source="tradingview", status=str(r["status"]),
            release_ts_utc=istante,
            # Midnight UTC is what the scanner reports when it knows the day
            # but not the hour; recording it as a minute-precise time would
            # place an Asian release on the previous local day.
            release_precision="day" if istante.time() == _MEZZANOTTE else "minute",
            tv_ticker=_testo(r.get("tv_ticker")),
            company_name=_testo(r.get("company_name")),
            country=_testo(r.get("country")),
            sector=_testo(r.get("sector")), industry=_testo(r.get("industry")),
            market_cap=_numero(r.get("market_cap")),
            eps_estimate=_numero(r.get("eps_estimate")),
            eps_actual=_numero(r.get("eps_actual")),
            revenue_estimate=_numero(r.get("revenue_estimate")),
            revenue_actual=_numero(r.get("revenue_actual")),
            currency=_testo(r.get("currency")),
            vintage_date=vintage_date,
        ))
    return osservazioni


def _unix(istante: datetime) -> int:
    """Seconds since the epoch, reading a naive datetime as UTC.

    datetime.timestamp() would read it in the host's timezone instead, which
    shifts the whole window by the machine's offset and quietly drops the
    releases sitting at either end of it.
    """
    if istante.tzinfo is None:
        istante = istante.replace(tzinfo=timezone.utc)
    return int(istante.timestamp())


def _istante(valore) -> Optional[datetime]:
    """Unix seconds -> naive UTC. Midnight means the source knew only the day."""
    numero = _numero(valore)
    if numero is None:
        return None
    return datetime.fromtimestamp(numero, tz=timezone.utc).replace(tzinfo=None)


def _numero(valore) -> Optional[float]:
    if valore is None or (isinstance(valore, float) and pd.isna(valore)):
        return None
    try:
        return float(valore)
    except (TypeError, ValueError):
        return None


def _testo(valore) -> Optional[str]:
    if valore is None or (isinstance(valore, float) and pd.isna(valore)):
        return None
    testo = str(valore).strip()
    return testo or None
