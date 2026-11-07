"""Pure daily euro sovereign spreads with explicit dated reference selection.

Consumers pass macro_series observations and the sovereign_yields catalog before
presenting results to an LLM/dashboard. This function opens no database and writes
nothing. Historical values are retained even when stalled; consumers must check
the returned status before presenting a current stress indicator.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from market_data_hub.coverage.stalled_detector import is_stalled

REFERENCE_ID = "CB_EA_AAA_10Y"
BUND_ID = "DEU_BUND10_FA"
ITALY_CURRENT_ID = "ITA_BTP10_MOT"
# Membership and adoption dates are explicit: earlier local-currency observations
# must not enter a EUR spread if a country's history crosses its adoption date.
# https://www.ecb.europa.eu/euro/intro/html/index.en.html
EURO_SINCE = dict.fromkeys("AUT BEL DEU ESP FIN FRA IRL ITA LUX NLD PRT".split(), "1999-01-01")
EURO_SINCE.update(GRC="2001-01-01", SVN="2007-01-01", CYP="2008-01-01", MLT="2008-01-01",
                  SVK="2009-01-01", EST="2011-01-01", LVA="2014-01-01", LTU="2015-01-01",
                  HRV="2023-01-01", BGR="2026-01-01")
OBS_COLUMNS = ["date", "series_id", "value", "series_name", "unit", "frequency",
               "source", "country", "country_series_id", "reference_id", "country_isin", "reference_isin"]
STATUS_COLUMNS = ["series_id", "country", "reference_id", "last_country_date",
                  "last_reference_date", "freshness_date", "last_spread_date", "stalled", "reason", "country_series_id"]


@dataclass(frozen=True)
class SpreadResult:
    observations: pd.DataFrame
    status: pd.DataFrame


def derive_sovereign_spreads(observations: pd.DataFrame, catalog: list[dict], *,
                            as_of: date) -> SpreadResult:
    """100 * (country percent yield - labelled reference), on common dates.

    Returns each configured euro-country 10y series' history and an explicit status
    row, including missing legs. Neither missing dates nor future quotes are filled.
    Freshness uses the older leg and also the latest common date: recent but disjoint
    histories cannot produce a fresh spread. Each leg's holiday calendar and lag
    threshold are respected. Identical duplicates are collapsed; unresolved,
    conflicting revisions raise ValueError (a caller should read upserted rows).
    On each Italy observation date, MOT takes precedence over BMK0200 and an
    available same-date Finanzagentur Bund takes precedence over ECB AAA. Neither
    historical choice depends on as_of freshness. Status describes only the
    current country row/reference; historical rows retain their identities even
    during a later outage. LEFT JOIN sovereign_bond_metadata to include ISINs.
    """
    reference = next((e for e in catalog if e["symbol"] == REFERENCE_ID), None)
    if reference is None:
        raise ValueError(f"catalog requires {REFERENCE_ID}")
    countries = [e for e in catalog if e.get("enabled", True)
                 and e.get("country") in EURO_SINCE
                 and pd.Timestamp(as_of) >= pd.Timestamp(EURO_SINCE[e["country"]])
                 and re.fullmatch(r"CB_[A-Z]{2}_10Y", e["symbol"])]
    bond_entries = [e for e in catalog if e.get("enabled", True)
                    and e["symbol"] in {BUND_ID, ITALY_CURRENT_ID}]
    if pd.Timestamp(as_of) >= pd.Timestamp(EURO_SINCE["ITA"]) and not any(e["country"] == "ITA" for e in countries):
        countries.extend(e for e in bond_entries if e["symbol"] == ITALY_CURRENT_ID)
    selected = {e["symbol"]: e for e in [reference, *countries, *bond_entries]}
    required = {"date", "series_id", "value", "unit", "frequency", "country"}
    if not required <= set(observations.columns):
        raise ValueError("observations require daily macro_series fields")
    data = observations[observations.series_id.isin(selected)].copy()
    data["date"] = pd.to_datetime(data.date, errors="raise")
    if data.date.isna().any() or (data.date != data.date.dt.normalize()).any() or data.date.dt.tz is not None:
        raise ValueError("observation dates must be plain daily dates")
    data = data[data.date <= pd.Timestamp(as_of)]
    if (data.unit != "%").any() or (data.frequency != "D").any():
        raise ValueError("spread legs must be daily percentage yields")
    data["value"] = pd.to_numeric(data.value, errors="raise")
    if not np.isfinite(data.value).all():
        raise ValueError("spread legs must be finite yields")
    if (data.country != data.series_id.map({s: e["country"] for s, e in selected.items()})).any():
        raise ValueError("spread leg country disagrees with catalog")
    if (data.groupby(["series_id", "date"]).value.nunique() > 1).any():
        raise ValueError("conflicting revisions: read upserted macro_series rows")
    data = data.drop_duplicates(["series_id", "date"]).sort_values("date")
    if "isin" not in data:
        data["isin"] = None

    def stalled(when, meta):
        return is_stalled(when, "D", as_of=as_of,
                          max_lag_days=meta.get("max_lag_days", 3),
                          lag_calendar=meta.get("lag_calendar", "business"),
                          holidays=meta.get("holidays", ()))

    frames, statuses = [], []
    for meta in countries:
        symbol = meta["symbol"]
        country_code = "IT_10Y" if meta["country"] == "ITA" else symbol[3:]
        country = data[data.series_id == symbol][["date", "value", "isin", "series_id"]]
        if meta["country"] == "ITA" and ITALY_CURRENT_ID in selected:
            mot = data[data.series_id == ITALY_CURRENT_ID][["date", "value", "isin", "series_id"]]
            country = pd.concat([country, mot]).drop_duplicates("date", keep="last")
        country = country[country.date >= pd.Timestamp(EURO_SINCE[meta["country"]])].sort_values("date")
        country = country.rename(columns={"series_id": "country_series_id"})
        last_country = country.date.max()
        if not country.empty:
            # Metadata/holiday calendar belongs to the latest actual country leg.
            meta = selected[country.country_series_id.iloc[-1]]
            symbol = meta["symbol"]
        base = data[data.series_id == REFERENCE_ID][["date", "value", "isin"]]
        aligned = country.merge(base, on="date", how="left", suffixes=("_country", "_reference"), validate="one_to_one")
        aligned["reference_id"] = REFERENCE_ID
        ref = reference
        if meta["country"] == "ITA" and BUND_ID in selected:
            bund = data[data.series_id == BUND_ID][["date", "value", "isin"]].rename(
                columns={"value": "value_bund", "isin": "isin_bund"})
            aligned = aligned.merge(bund, on="date", how="left", validate="one_to_one")
            has_bund = aligned.value_bund.notna()
            aligned.loc[has_bund, "reference_id"] = BUND_ID
            aligned.loc[has_bund, "value_reference"] = aligned.loc[has_bund, "value_bund"]
            aligned.loc[has_bund, "isin_reference"] = aligned.loc[has_bund, "isin_bund"]
            # The current status uses the latest country's dated reference.
            # A stale same-date Bund remains labelled Bund and is marked stale;
            # an outage on a newer date selects ECB for that newer date only.
            if (bund.date == last_country).any():
                ref = selected[BUND_ID]
        aligned = aligned[aligned.value_reference.notna()].copy()
        reference_id = ref["symbol"]
        base = data[data.series_id == reference_id][["date", "value", "isin"]]
        last_base = base.date.max()
        suffix = "BUND_FA" if reference_id == BUND_ID else "ECB_AAA"
        spread_id = f"SPREAD_{country_code}_VS_{suffix}"
        current_history = aligned[aligned.reference_id == reference_id]
        last_common = current_history.date.max()
        missing = pd.isna(last_country) or pd.isna(last_base)
        freshness = pd.NaT if missing else min(last_country, last_base)
        stale = (missing or stalled(last_country, meta) or stalled(last_base, ref)
                 or stalled(last_common, meta) or stalled(last_common, ref))
        reason = "missing_leg" if missing else ("no_common_date" if current_history.empty else ("stale" if stale else "fresh"))
        statuses.append(dict(series_id=spread_id, country=meta["country"], reference_id=reference_id,
                             last_country_date=last_country, last_reference_date=last_base,
                             freshness_date=freshness, last_spread_date=last_common,
                             stalled=bool(stale), reason=reason, country_series_id=symbol))
        if aligned.empty:
            continue
        aligned["value"] = 100.0 * (aligned.value_country - aligned.value_reference)
        aligned["country_isin"] = aligned.isin_country
        aligned["reference_isin"] = aligned.isin_reference
        labels = {BUND_ID: "Finanzagentur nominal Bund 10y reference yield",
                  REFERENCE_ID: "ECB AAA 10y spot (Bund stand-in)"}
        suffixes = {BUND_ID: "BUND_FA", REFERENCE_ID: "ECB_AAA"}
        aligned["series_id"] = f"SPREAD_{country_code}_VS_" + aligned.reference_id.map(suffixes)
        aligned["series_name"] = f"{meta['country']} 10y yield minus " + aligned.reference_id.map(labels)
        for key, value in dict(country=meta["country"], unit="bp", frequency="D",
                               source="derived:sovereign_yields").items():
            aligned[key] = value
        frames.append(aligned[OBS_COLUMNS])
    history = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=OBS_COLUMNS)
    return SpreadResult(history, pd.DataFrame(statuses, columns=STATUS_COLUMNS))
