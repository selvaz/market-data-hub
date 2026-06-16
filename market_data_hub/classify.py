# -*- coding: utf-8 -*-
"""
classify.py — classificazione dei paesi su due livelli:
  - STATICA   : geografia, sviluppo (DM/EM/Frontier), income, regime di cambio,
                programma IMF, flag G7/EU/ASEAN (da config/countries.yaml)
  - DATA-DRIVEN: posizione energetica (oil exporter/importer), dipendenza da
                risorse naturali, turismo, rimesse — derivata da macro_panel.

Scrive la tabella country_classification (statica + data-driven, una riga per
paese), ricalcolata a ogni run.

Formula oil/energia (corretta rispetto a FRONTIER, che usava un solo
denominatore): le importazioni di carburante si scalano sulle IMPORTAZIONI/PIL,
le esportazioni sulle ESPORTAZIONI/PIL.
    net_fuel_gdp = fuel_exp%·(export/PIL)/100  −  fuel_imp%·(import/PIL)/100
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from market_data_hub.config_loader import get_countries
from market_data_hub.db.connection import get_conn

# indicatori macro_panel usati per la classificazione data-driven
_NEED = ["fuel_exports_share", "fuel_imports_share", "exports_gdp", "imports_gdp",
         "natural_resource_rents_gdp", "tourism_exports_share", "remittances_gdp",
         "metals_exports_share"]


def _bucket(v, edges, labels, unknown="unknown"):
    """Assegna v a un bucket dati gli edge crescenti e le label (len+1)."""
    if v is None or pd.isna(v):
        return unknown
    for e, lab in zip(edges, labels):
        if v < e:
            return lab
    return labels[-1]


def energy_position(fe, fi, ex, im):
    """Posizione energetica. Primario: net fuel %PIL (formula corretta). Se
    mancano export/import %PIL (es. Nigeria in WDI), fallback sulle QUOTE:
    se il carburante domina l'export il paese e' esportatore comunque.
    Ritorna (label, net_fuel_gdp | None)."""
    if None not in (fe, fi, ex, im):
        net = fe * ex / 100 - fi * im / 100
        return _bucket(net, [-10, -3, 3, 10],
                      ["strong_importer", "importer", "neutral", "exporter",
                       "strong_exporter"]), round(net, 2)
    # --- fallback su quote dell'export (manca scaling su PIL) ---
    if fe is not None:
        if fe > 50:
            return "strong_exporter", None      # export dominato dal carburante
        if fe > 20:
            return "exporter", None
        if fi is not None and fi > 15 and fe < 10:
            return "importer", None             # importa carburante, non esporta
        return "neutral", None
    return "unknown", None


def resource_dependence(rents):
    return _bucket(rents, [5, 20], ["diversified", "resource_significant", "resource_driven"])


def tourism_dependence(t):
    return _bucket(t, [5, 15, 30], ["low", "moderate", "significant", "dominant"])


def remittance_dependence(r):
    return _bucket(r, [3, 10], ["low", "moderate", "high"])


def classify_countries(db_path: Optional[str] = None,
                       ref_year: Optional[int] = None) -> dict:
    con = get_conn(db_path)
    now = datetime.now(timezone.utc)
    ry = ref_year or now.year

    # ultimo valore <= anno corrente per ogni (paese, indicatore) necessario
    df = con.execute(
        "SELECT country_iso3, indicator_id, date, value FROM macro_panel "
        "WHERE indicator_id IN (" + ",".join("?" * len(_NEED)) + ") "
        "AND value IS NOT NULL AND year(date) <= ?",
        _NEED + [ry]).fetch_df()
    df = df.sort_values("date").drop_duplicates(
        ["country_iso3", "indicator_id"], keep="last")
    latest = {}
    for _, r in df.iterrows():
        latest.setdefault(r["country_iso3"], {})[r["indicator_id"]] = r["value"]

    countries = get_countries()
    rows = []
    for c in countries:
        iso = c["iso3"]
        d = latest.get(iso, {})

        def g(k):
            v = d.get(k)
            return None if v is None or pd.isna(v) else float(v)

        fe, fi = g("fuel_exports_share"), g("fuel_imports_share")
        ex, im = g("exports_gdp"), g("imports_gdp")
        energy, net_fuel = energy_position(fe, fi, ex, im)
        rents = g("natural_resource_rents_gdp")
        # sanita': un "esportatore" senza rendite da risorse naturali (~0) NON e'
        # un produttore ma un hub di ri-esportazione/bunkeraggio (es. Cipro) ->
        # declassa a neutro. Gli esportatori veri hanno rendite > ~2% PIL.
        if energy in ("exporter", "strong_exporter") and rents is not None and rents < 2.0:
            energy = "neutral"
        tour = g("tourism_exports_share")
        remit = g("remittances_gdp")

        rows.append((
            iso, c.get("name", iso),
            # --- statica ---
            c.get("region_group", ""), c.get("region_geo", ""),
            c.get("income", ""), c.get("development", ""),
            c.get("fx_regime", ""), bool(c.get("imf_program", False)),
            bool(c.get("g7", False)), bool(c.get("eu", False)),
            bool(c.get("euro", False)),
            # --- data-driven ---
            energy,
            net_fuel,
            resource_dependence(rents),
            tourism_dependence(tour),
            remittance_dependence(remit),
            now,
        ))

    con.execute("DROP TABLE IF EXISTS country_classification")
    con.execute("""CREATE TABLE country_classification (
        country_iso3 VARCHAR PRIMARY KEY, name VARCHAR,
        region_group VARCHAR, region_geo VARCHAR, income VARCHAR,
        development VARCHAR, fx_regime VARCHAR, imf_program BOOLEAN,
        g7 BOOLEAN, eu BOOLEAN, euro BOOLEAN,
        energy_position VARCHAR, net_fuel_gdp DOUBLE,
        resource_dependence VARCHAR, tourism_dependence VARCHAR,
        remittance_dependence VARCHAR, computed_at TIMESTAMP)""")
    con.executemany(
        "INSERT INTO country_classification VALUES (" + ",".join("?" * 17) + ")", rows)
    con.commit()

    summ = pd.DataFrame(rows, columns=[
        "iso", "name", "rg", "geo", "inc", "dev", "fx", "imf", "g7", "eu", "euro",
        "energy", "netfuel", "res", "tour", "remit", "t"])
    out = {
        "countries": len(rows),
        "development": summ["dev"].value_counts().to_dict(),
        "energy": summ["energy"].value_counts().to_dict(),
        "resource": summ["res"].value_counts().to_dict(),
    }
    con.close()
    return out
