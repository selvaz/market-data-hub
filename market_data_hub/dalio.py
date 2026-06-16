# -*- coding: utf-8 -*-
"""
dalio.py — layer analitico "Ray Dalio" sopra macro_panel.

Implementa la Parte A della spec Dalio v3:
  - signals z-score (x direction) per (paese, indicatore), finestra 10y
  - calcoli derivati: debt-to-income gap, nominal growth vs nominal rate,
    productivity trend, growth/inflation delta (sorprese vs trend)
  - debt cycle phase classifier (7 fasi, soglie configurabili)
  - four-box growth/inflation regime (Q1-Q4)
  - pillar_scores + composite pesato + 3 etichette categoriche

Scrive: dalio_signals, pillar_scores, regime_state.
Robusto: indicatori mancanti -> NaN / fase INDETERMINATE, mai crash.

Uso:
    from market_data_hub.dalio import run_dalio
    run_dalio()                       # calcola e scrive nel DB
    run_dalio(ref_year=2026)          # data di riferimento specifica
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Optional

import numpy as np
import pandas as pd

from market_data_hub.config_loader import get_settings
from market_data_hub.db.connection import get_conn

# mappa indicatori-chiave Dalio -> id nel macro_panel (con fallback)
IND = {
    "credit_gap": "bis_credit_gap",
    "dsr": "bis_dsr_private",
    "policy_rate": "bis_policy_rate",
    "pub_debt": "public_debt_gdp",
    "fiscal": ["fiscal_balance_gdp"],
    "growth": ["gdp_growth_weo", "real_gdp_growth"],
    "inflation": ["inflation_avg_weo", "inflation_cpi"],
    "productivity": ["labor_productivity_level", "gdp_per_capita_growth"],
}


def _first_avail(series_by_ind: Dict[str, pd.DataFrame], ids):
    """Ritorna la prima serie disponibile (non vuota) tra una lista di id."""
    if isinstance(ids, str):
        ids = [ids]
    for i in ids:
        s = series_by_ind.get(i)
        if s is not None and not s.empty:
            return s
    return None


def _latest(s: Optional[pd.DataFrame]):
    if s is None or s.empty:
        return np.nan, None
    r = s.sort_values("date").iloc[-1]
    return float(r["value"]), pd.Timestamp(r["date"])


def _prev(s: Optional[pd.DataFrame]):
    """Valore precedente (penultima osservazione annuale)."""
    if s is None or len(s) < 2:
        return np.nan
    return float(s.sort_values("date").iloc[-2]["value"])


def _trailing_avg(s: Optional[pd.DataFrame], n: int):
    if s is None or s.empty:
        return np.nan
    v = s.sort_values("date")["value"].dropna()
    if len(v) < 2:
        return np.nan
    return float(v.tail(n).mean())


def _pct_in_range(s: Optional[pd.DataFrame]):
    """Posizione del valore corrente nel range storico [0,1]. Per il DSR:
    ~1 = al picco storico (stress, lettura Dalio). None se storia insufficiente."""
    if s is None or s.empty:
        return float("nan")
    v = s.sort_values("date")["value"].dropna()
    if len(v) < 8:
        return float("nan")
    lo, hi = v.min(), v.max()
    if hi <= lo:
        return float("nan")
    return float((v.iloc[-1] - lo) / (hi - lo))


def _slope(s: Optional[pd.DataFrame], y_lo: int, y_hi: int):
    """Slope OLS (pp/anno) del valore vs anno nella finestra [y_lo, y_hi].
    Usata per la TRAIETTORIA del debito/PIL (include i forecast WEO)."""
    if s is None or s.empty:
        return float("nan")
    d = s.copy()
    d["y"] = pd.to_datetime(d["date"]).dt.year
    d = d[(d["y"] >= y_lo) & (d["y"] <= y_hi)].dropna(subset=["value"])
    if len(d) < 3:
        return float("nan")
    import numpy as _np
    x = d["y"].values - d["y"].values.min()
    return float(_np.polyfit(x, d["value"].values, 1)[0])


def _zscore(s: Optional[pd.DataFrame], orient: int, win: int, min_obs: int):
    """z = (x - mean)/std su finestra win, x orientation. None se < min_obs."""
    if s is None or s.empty:
        return np.nan, 0
    v = s.sort_values("date")["value"].dropna().tail(win)
    if len(v) < min_obs:
        return np.nan, len(v)
    sd = v.std(ddof=0)
    if sd == 0 or np.isnan(sd):
        return 0.0, len(v)
    z = (v.iloc[-1] - v.mean()) / sd
    return float(z) * (orient if orient else 1), len(v)


def classify_regime(growth_delta, infl_delta):
    """Four-box Bridgewater: Q1 g+/i-, Q2 g+/i+, Q3 g-/i+, Q4 g-/i-."""
    if pd.isna(growth_delta) or pd.isna(infl_delta):
        return None
    g_up = growth_delta >= 0
    i_up = infl_delta >= 0
    if g_up and not i_up:
        return "Q1"   # crescita su, inflazione giu -> risk assets
    if g_up and i_up:
        return "Q2"   # reflation -> commodity, EM, linkers
    if not g_up and i_up:
        return "Q3"   # stagflazione -> oro, difensivo
    return "Q4"        # disinflazione/recessione -> bond, cash


def classify_cycle_phase(x: dict, th: dict) -> str:
    """Albero a soglie del ciclo del debito (Dalio, spec 2.1 estesa).

    Estende la logica della spec con le fasi "normali" che altrimenti cadrebbero
    in INDETERMINATE: espansione sana, late-leveraging (verso la bolla),
    contrazione lieve, ugly deleveraging. INDETERMINATE resta solo quando i dati
    di base (crescita E credit_gap) mancano entrambi.
    """
    cg = x.get("credit_gap")
    g = x.get("growth")
    gn = x.get("nom_growth")
    rn = x.get("nom_rate")
    dsr = x.get("dsr")
    debt_falling = x.get("debt_falling")
    debt_lvl = x.get("debt_level")
    fisc = x.get("fiscal_balance")
    dtrend = x.get("debt_trend")          # traiettoria debito/PIL (pp/anno)

    def has(v):
        return v is not None and not pd.isna(v)

    # il debito sta SALENDO se la traiettoria pluriennale e' positiva
    debt_rising = has(dtrend) and dtrend > th["debt_trend_moderate"]
    debt_rising_fast = has(dtrend) and dtrend > th["debt_trend_high"]

    # serve almeno crescita O credit_gap, altrimenti non si puo' dire nulla
    if not has(g) and not has(cg):
        return "INDETERMINATE"

    # 1) contrazione / depressione (crescita negativa). DEPRESSION se il debt
    #    service ratio e' al PICCO della sua storia (Dalio), non a un livello
    #    assoluto: 20% e' normale per CH/NL, alto per IT. Fallback assoluto se
    #    manca storia.
    dsr_pct = x.get("dsr_pct")
    dsr_stressed = (dsr_pct > th["dsr_peak_pct"]) if has(dsr_pct) \
        else (has(dsr) and dsr > th["dsr_high"])
    if has(g) and g < 0:
        return "DEPRESSION" if dsr_stressed else "CONTRACTION"

    # 2) deleveraging (debito/PIL in CALO sostenuto): beautiful vs ugly
    #    Usa la traiettoria pluriennale, non la variazione di 1 anno.
    #    Ha precedenza -> Giappone/Grecia (debito alto ma in discesa) qui.
    if (debt_falling or (has(dtrend) and dtrend < -th["debt_trend_moderate"])) \
            and has(gn) and has(rn):
        return "BEAUTIFUL_DELEVERAGING" if gn > rn else "UGLY_DELEVERAGING"

    # 3) ciclo del debito LUNGO/sovrano (tesi-chiave di Dalio): un debito alto
    #    (>100% PIL) e non in calo NON e' "espansione sana". Ma si distingue la
    #    gradazione:
    #      LATE_LONG_CYCLE   = debito molto alto (>130%) OPPURE in deterioramento
    #                          (in salita o con deficit ampio) -> verso la resa dei conti
    #      HIGH_DEBT_STABLE  = debito alto ma stabilizzato/plateau, non in peggioramento
    if has(debt_lvl) and not debt_falling and debt_lvl > th["debt_high_level"]:
        deteriorating = debt_rising or (has(fisc) and fisc < th["deficit_large"])
        if debt_lvl > th["debt_crisis_level"] or deteriorating:
            return "LATE_LONG_CYCLE"
        return "HIGH_DEBT_STABLE"

    # 4) bolla / leveraging up (credito privato breve + traiettoria debito)
    if has(cg) and cg > th["credit_gap_bubble"]:
        return "BUBBLE"
    if (has(cg) and cg > th["credit_gap_late"]) or debt_rising_fast:
        return "LATE_LEVERAGING"

    # 4) politica monetaria esausta: tassi ~0 e crescita debole
    if has(rn) and rn < th["rate_near_zero"] and has(g) and g < th["weak_growth"]:
        return "PUSHING_ON_STRING"

    # 5) espansione sana (il caso "normale": crescita positiva, niente eccessi)
    if has(g) and g > 0:
        return "EARLY_EXPANSION"

    return "INDETERMINATE"


def _deleveraging_quality(g, gn, rn, debt_falling):
    """Qualita' del deleveraging — ha senso SOLO se il debito sta scendendo.
    Se il debito non cala, NON c'e' deleveraging -> NA (no contraddizioni col
    tipo 'EARLY_EXPANSION + UGLY'). Beautiful: crescita nom. > tasso nom.;
    Ugly: crescita nom. < tasso nom. (riduzione dolorosa/deflattiva)."""
    if not debt_falling:
        return "NA"
    if pd.isna(gn) or pd.isna(rn):
        return "NA"
    return "BEAUTIFUL" if gn > rn else "UGLY"


def run_dalio(db_path: Optional[str] = None, ref_year: Optional[int] = None) -> dict:
    cfg = get_settings().get("dalio", {})
    win_y = cfg.get("z_window_years", 10)
    min_obs = cfg.get("z_min_obs", 5)
    z_pos, z_neg = cfg.get("z_pos", 1.0), cfg.get("z_neg", -1.0)
    weights = cfg.get("pillar_weights", {})
    th = {k: cfg.get(k) for k in (
        "credit_gap_bubble", "credit_gap_near_zero", "debt_income_gap_high",
        "debt_income_gap_low", "dsr_high", "rate_near_zero",
        "credit_gap_late", "weak_growth",
        "debt_high_level", "debt_crisis_level", "deficit_large",
        "debt_trend_high", "debt_trend_moderate", "dsr_peak_pct")}
    tw_back = cfg.get("debt_trend_window_back", 3)
    tw_fwd = cfg.get("debt_trend_window_fwd", 5)

    con = get_conn(db_path)
    panel = con.execute(
        "SELECT date, country_iso3, indicator_id, value, pillar, orientation, "
        "frequency FROM macro_panel WHERE value IS NOT NULL").fetch_df()
    if panel.empty:
        con.close()
        raise RuntimeError("macro_panel vuoto: lanciare prima il download.")
    panel["date"] = pd.to_datetime(panel["date"])
    now = datetime.now(timezone.utc)

    sig_rows, pil_rows, reg_rows = [], [], []

    # ref_year = anno CORRENTE (non il max=ultimo forecast!). Le metriche di
    # stato del paese vanno calcolate sul presente; i forecast (> ref_year)
    # restano disponibili per i grafici ma NON sono il "valore corrente".
    ry = ref_year or now.year
    ref_date = pd.Timestamp(ry, 12, 31)

    # --- z-score CROSS-COUNTRY (forza relativa vs gli altri paesi) ---
    # Per ogni indicatore: ultimo valore <= ref per ogni paese, poi z fra paesi
    # (x-mean)/std x direction, clippato a [-3,3]. E' questo che rende il
    # composite un punteggio di FORZA paese (Svizzera alta, EM stressati bassi),
    # non un "vs propria storia". Lo z temporale resta per le letture cicliche
    # (es. dsr_pct, gia' separato).
    pref = panel[panel["date"] <= ref_date]
    xz = {}          # indicator -> {country: z_cross}
    ind_meta = {}    # indicator -> (pillar, orientation)
    for ind, g in pref.groupby("indicator_id"):
        last = g.sort_values("date").groupby("country_iso3").tail(1)
        vals = dict(zip(last["country_iso3"], last["value"]))
        orient = int(g["orientation"].iloc[-1] or 0) or 1
        ind_meta[ind] = (g["pillar"].iloc[-1], orient)
        arr = np.array([v for v in vals.values()], dtype=float)
        mean, std = np.nanmean(arr), np.nanstd(arr)
        if not std or np.isnan(std):
            xz[ind] = {c: 0.0 for c in vals}
        else:
            xz[ind] = {c: float(np.clip((v - mean) / std * orient, -3, 3))
                       for c, v in vals.items()}

    for country, cdf_full in panel.groupby("country_iso3"):
        cdf = cdf_full[cdf_full["date"] <= ref_date]
        if cdf.empty:
            continue
        # serie debito/PIL COMPLETA (incl. forecast) per la traiettoria
        debt_full = cdf_full[cdf_full["indicator_id"] == IND["pub_debt"]][["date", "value"]]
        debt_trend = _slope(debt_full, ry - tw_back, ry + tw_fwd)
        # finestra z-score per frequenza (10y): A->10, Q->40, M->120
        by_ind = {i: g[["date", "value"]] for i, g in cdf.groupby("indicator_id")}
        meta = {i: (g["pillar"].iloc[-1], int(g["orientation"].iloc[-1] or 0),
                    g["frequency"].iloc[-1])
                for i, g in cdf.groupby("indicator_id")}

        # ---- signals: z-score CROSS-COUNTRY per ogni indicatore ----
        for ind, s in by_ind.items():
            pillar, orient, freq = meta[ind]
            z = xz.get(ind, {}).get(country)
            val, _ = _latest(s)
            sgl = ("POS" if (z is not None and z >= z_pos) else
                   "NEG" if (z is not None and z <= z_neg) else "NEUTRAL")
            sig_rows.append((country, ref_date.date(), ind, pillar, val,
                             z, len(xz.get(ind, {})), sgl, now))

        # ---- calcoli derivati Dalio ----
        s_cg = _first_avail(by_ind, IND["credit_gap"])
        s_dsr = _first_avail(by_ind, IND["dsr"])
        s_rate = _first_avail(by_ind, IND["policy_rate"])
        s_debt = _first_avail(by_ind, IND["pub_debt"])
        s_fisc = _first_avail(by_ind, IND["fiscal"])
        s_g = _first_avail(by_ind, IND["growth"])
        s_i = _first_avail(by_ind, IND["inflation"])

        credit_gap, _ = _latest(s_cg)
        dsr, _ = _latest(s_dsr)
        dsr_pct = _pct_in_range(s_dsr)   # posizione DSR vs sua storia (Dalio)
        nom_rate, _ = _latest(s_rate)
        debt, _ = _latest(s_debt)
        debt_prev = _prev(s_debt)
        fiscal_balance, _ = _latest(s_fisc)
        growth, _ = _latest(s_g)
        infl, _ = _latest(s_i)

        debt_income_gap = (debt - debt_prev) if not (pd.isna(debt) or pd.isna(debt_prev)) else np.nan
        # "debito in calo" = TRAIETTORIA pluriennale in discesa (non 1 anno)
        debt_falling = (not pd.isna(debt_trend)) and debt_trend < 0
        nom_growth = (growth + infl) if not (pd.isna(growth) or pd.isna(infl)) else np.nan
        # four-box: crescita/inflazione correnti vs il PROPRIO POTENZIALE/trend
        # (output gap), non momentum YoY. Il potenziale = media WEO di medio
        # termine [ry+2, ry+5] (stima IMF dell'equilibrio, immune al COVID).
        # Cosi' un paese ad alta crescita SOPRA il suo potenziale e' "crescita su"
        # (es. Vietnam), uno a bassa crescita sotto il suo trend e' "giu" (Italia).
        def _potential(ind_ids):
            for iid in ([ind_ids] if isinstance(ind_ids, str) else ind_ids):
                fs = cdf_full[cdf_full["indicator_id"] == iid]
                if fs.empty:
                    continue
                yy = pd.to_datetime(fs["date"]).dt.year
                med = fs[(yy >= ry + 2) & (yy <= ry + 5)]["value"].dropna()
                if len(med) >= 2:
                    return float(med.mean())
            return np.nan
        # crescita: vs POTENZIALE (output gap -> forte/debole)
        pot_g = _potential(IND["growth"])
        growth_delta = (growth - pot_g) if not (pd.isna(growth) or pd.isna(pot_g)) else np.nan
        # inflazione: vs media dei 3 anni PRECEDENTI (direzione -> reflazione/
        # disinflazione). Il potenziale WEO assume disinflazione e renderebbe
        # quasi tutti "in salita"; la direzione recente e' piu' informativa.
        def _prior3(s):
            if s is None or s.empty:
                return np.nan
            yy = pd.to_datetime(s["date"]).dt.year
            v = s[(yy >= ry - 3) & (yy <= ry - 1)]["value"].dropna()
            return float(v.mean()) if len(v) >= 2 else np.nan
        infl_prior = _prior3(s_i)
        infl_delta = (infl - infl_prior) if not (pd.isna(infl) or pd.isna(infl_prior)) else np.nan

        phase = classify_cycle_phase({
            "credit_gap": credit_gap, "debt_income_gap": debt_income_gap,
            "debt_level": debt, "fiscal_balance": fiscal_balance,
            "debt_trend": debt_trend,
            "growth": growth, "nom_growth": nom_growth, "nom_rate": nom_rate,
            "dsr": dsr, "dsr_pct": dsr_pct, "debt_falling": debt_falling}, th)
        quadrant = classify_regime(growth_delta, infl_delta)
        delev = _deleveraging_quality(growth, nom_growth, nom_rate, debt_falling)

        reg_rows.append((country, ref_date.date(), growth_delta, infl_delta,
                         quadrant, phase, nom_growth, nom_rate, delev,
                         credit_gap, dsr, debt_income_gap,
                         None if pd.isna(debt_trend) else round(debt_trend, 2), now))

        # ---- pillar_scores + composite (z CROSS-COUNTRY) ----
        zdf = pd.DataFrame([(meta[i][0], xz.get(i, {}).get(country))
                            for i in by_ind], columns=["pillar", "z"]).dropna()
        comp_num = comp_den = 0.0
        for pillar, pdf in zdf.groupby("pillar"):
            sc = pdf["z"].mean()
            pil_rows.append((country, ref_date.date(), pillar, sc, len(pdf),
                             None, None, None, now))
            w = weights.get(pillar, 0)
            if w and not pd.isna(sc):
                comp_num += w * sc
                comp_den += w
        composite = comp_num / comp_den if comp_den else np.nan
        # short cycle: proxy semplice da growth_delta
        short = ("late/contraction" if (not pd.isna(growth_delta) and growth_delta < 0)
                 else "mid/late upswing" if not pd.isna(growth_delta) else None)
        pil_rows.append((country, ref_date.date(), "COMPOSITE",
                         None if pd.isna(composite) else composite, len(zdf),
                         phase, short, quadrant, now))

    # ---- scrittura idempotente (regime_state ricreata: aggiunta col debt_trend) ----
    con.execute("DROP TABLE IF EXISTS regime_state")
    con.execute("""CREATE TABLE regime_state (
        country_iso3 VARCHAR NOT NULL, ref_date DATE NOT NULL,
        growth_delta DOUBLE, infl_delta DOUBLE, quadrant VARCHAR,
        debt_cycle_phase VARCHAR, nom_growth DOUBLE, nom_rate DOUBLE,
        deleveraging_quality VARCHAR, credit_gap DOUBLE, dsr DOUBLE,
        debt_income_gap DOUBLE, debt_trend DOUBLE, computed_at TIMESTAMP,
        PRIMARY KEY (country_iso3, ref_date))""")
    con.execute("DELETE FROM dalio_signals")
    con.execute("DELETE FROM pillar_scores")
    con.executemany(
        "INSERT OR REPLACE INTO dalio_signals VALUES (?,?,?,?,?,?,?,?,?)", sig_rows)
    con.executemany(
        "INSERT OR REPLACE INTO pillar_scores VALUES (?,?,?,?,?,?,?,?,?)", pil_rows)
    con.executemany(
        "INSERT OR REPLACE INTO regime_state VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", reg_rows)
    con.commit()

    n_countries = panel["country_iso3"].nunique()
    phases = pd.DataFrame(reg_rows, columns=[
        "c", "d", "gd", "id_", "q", "phase", "ng", "nr", "dq", "cg", "dsr", "dig", "dtr", "t"])

    # --- freschezza forecast WEO: l'orizzonte deve superare l'anno corrente ---
    weo = panel[panel["indicator_id"].isin(["gdp_growth_weo", "public_debt_gdp"])]
    weo_horizon = int(pd.to_datetime(weo["date"]).dt.year.max()) if not weo.empty else None
    cur_year = datetime.now(timezone.utc).year
    forecast_stale = weo_horizon is None or weo_horizon < cur_year + 1

    summary = {
        "countries": n_countries,
        "signals": len(sig_rows),
        "phases": phases["phase"].value_counts().to_dict(),
        "regimes": phases["q"].value_counts(dropna=True).to_dict(),
        "weo_horizon": weo_horizon,
        "forecast_stale": forecast_stale,
    }
    con.close()
    return summary
