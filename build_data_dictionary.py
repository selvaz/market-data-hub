# -*- coding: utf-8 -*-
"""
build_data_dictionary.py — genera un dizionario dati Excel con TUTTE le serie
del sistema (Yahoo, FRED, Macro_Panel): codice, nome, significato economico,
provider, categoria, frequenza, unita', area/paese, ultima data, priorita'.

Output: data_dictionary.xlsx  (sheet 'Dizionario' + 'Riepilogo')
"""
from __future__ import annotations
import sys
from pathlib import Path
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent))
BASE = Path(__file__).parent
CFG = BASE / "market_data_hub" / "config"


def _y(name):
    return yaml.safe_load(open(CFG / name, encoding="utf-8"))


# ---- frequenza/ultima data reali dal DB (se accessibile) -------------------
def db_freshness():
    out = {}
    try:
        import duckdb
        con = duckdb.connect(str(BASE / "market_data.duckdb"), read_only=True)
        for r in con.execute("SELECT symbol, source, freq_detected, last_date "
                             "FROM coverage_report").fetchall():
            out[r[0]] = (r[2], str(r[1]) if r[1] is not None else "")
            out[(r[1], r[0])] = (r[2], str(r[3]) if r[3] is not None else "")
        con.close()
    except Exception as e:
        print(f"(DB non accessibile per freq/last_date: {str(e)[:50]})")
    return out


FREQ_IT = {"D": "Giornaliera", "W": "Settimanale", "M": "Mensile",
           "Q": "Trimestrale", "A": "Annuale", "UNKNOWN": "-"}

# ---- significato economico: FRED (per symbol) ------------------------------
FRED_MEANING = {
 "DCOILBRENTEU": "Prezzo spot petrolio Brent: benchmark energetico globale, driver di inflazione e ragioni di scambio.",
 "DCOILWTICO": "Prezzo spot WTI (Cushing): benchmark petrolifero nordamericano.",
 "VIXCLS": "Indice VIX: volatilita' implicita a 30gg dell'S&P 500, 'indice della paura' e termometro del risk sentiment.",
 "DGS10": "Rendimento Treasury 10Y: tasso privo di rischio di riferimento, ancora dei tassi globali.",
 "DGS2": "Treasury 2Y: molto sensibile alle attese di politica monetaria Fed.",
 "DGS30": "Treasury 30Y: aspettative di crescita e inflazione di lungo periodo.",
 "DGS3MO": "Treasury 3M: prossimo al tasso di policy, costo del denaro a breve.",
 "T10Y2Y": "Spread 10Y-2Y: pendenza della curva; se negativo, classico segnale anticipatore di recessione.",
 "EFFR": "Effective Federal Funds Rate: tasso di policy operativo della Fed.",
 "ECBDFR": "Tasso sui depositi BCE: floor del corridoio dei tassi in area euro.",
 "ECBMRRFR": "Tasso principale di rifinanziamento BCE (refi): tasso di policy centrale.",
 "ECBMLFR": "Tasso di rifinanziamento marginale BCE: cap del corridoio dei tassi.",
 "T10YIE": "Breakeven inflation 10Y: aspettative d'inflazione di mercato a 10 anni.",
 "T5YIE": "Breakeven inflation 5Y: aspettative d'inflazione di mercato a 5 anni.",
 "CPIAUCSL": "CPI USA headline: inflazione al consumo, riferimento per la Fed.",
 "CPILFESL": "Core CPI USA (ex food & energy): inflazione di fondo, piu' persistente.",
 "PCEPI": "PCE USA: deflatore dei consumi, misura d'inflazione preferita dalla Fed.",
 "PCEPILFE": "Core PCE USA: misura d'inflazione chiave per le decisioni Fed.",
 "CP0000EZ19M086NEST": "HICP area euro: inflazione armonizzata, target della BCE.",
 "GDP": "PIL USA nominale (prezzi correnti).",
 "GDPC1": "PIL reale USA (chained 2017$): crescita economica al netto dell'inflazione.",
 "INDPRO": "Produzione industriale USA: ciclo manifatturiero ed energetico.",
 "CLVMEURSCAB1GQEA19": "PIL reale area euro (chained 2010 EUR).",
 "EUNNGDP": "PIL nominale area euro.",
 "UNRATE": "Tasso di disoccupazione USA: salute del mercato del lavoro, mandato Fed.",
 "PAYEMS": "Occupati non agricoli USA (Nonfarm Payrolls): indicatore-chiave del lavoro.",
 "LRHUTTTTEZM156S": "Disoccupazione armonizzata area euro.",
 "M2SL": "Massa monetaria M2 USA: liquidita' nel sistema, segnale inflazionistico di medio termine.",
 "WALCL": "Attivi totali della Fed: dimensione del bilancio (QE/QT), liquidita' di base.",
 "BAMLC0A0CM": "OAS corporate Investment Grade USA: premio al rischio di credito IG.",
 "BAMLH0A0HYM2": "OAS High Yield USA: rischio di credito speculativo, proxy del risk appetite.",
 "AAA": "Rendimento corporate Aaa (Moody's): costo del debito di altissima qualita'.",
 "BAA": "Rendimento corporate Baa (Moody's): costo del debito di qualita' medio-bassa.",
 "NFCI": "Chicago Fed National Financial Conditions Index: condizioni finanziarie aggregate USA.",
 "STLFSI4": "St. Louis Fed Financial Stress Index: stress sistemico dei mercati.",
 "HOUST": "Avvii di cantieri residenziali USA: settore immobiliare, indicatore ciclico anticipatore.",
 "RSAFS": "Vendite al dettaglio USA: consumi e domanda interna.",
 "DTWEXBGS": "Trade-Weighted US Dollar Index (Broad): forza del dollaro ponderata per il commercio.",
}

# ---- significato economico: Macro_Panel (per id) ---------------------------
MP_MEANING = {
 "real_gdp_growth": "Crescita reale del PIL: ritmo di espansione dell'economia, base per la sostenibilita' del debito.",
 "gdp_current_usd": "Dimensione assoluta dell'economia in USD: peso e capacita' di assorbimento del paese.",
 "gdp_per_capita_growth": "Crescita del PIL pro-capite: miglioramento del tenore di vita medio.",
 "unemployment_rate": "Disoccupazione (ILO): sottoutilizzo del lavoro, tensioni sociali e domanda interna.",
 "investment_gdp": "Formazione di capitale fisso %PIL: investimenti, motore della crescita futura.",
 "gross_savings_gdp": "Risparmio lordo %PIL: capacita' di autofinanziare gli investimenti.",
 "labor_productivity_level": "PIL per occupato (PPP): produttivita' del lavoro, competitivita' strutturale.",
 "high_tech_exports_share": "Quota export hi-tech: sofisticazione e valore aggiunto della base produttiva.",
 "rnd_expenditure_gdp": "Spesa in R&S %PIL: capacita' innovativa e crescita potenziale.",
 "population_growth": "Crescita della popolazione: dinamica demografica, domanda potenziale.",
 "dependency_ratio": "Tasso di dipendenza: pressione demografica su welfare e finanze pubbliche.",
 "gdp_growth_weo": "Crescita PIL reale (WEO, con proiezioni): outlook prospettico del FMI.",
 "gdp_usd_weo": "PIL nominale USD (WEO, con proiezioni).",
 "gdp_per_capita_usd": "PIL pro-capite USD: livello di reddito assoluto.",
 "gdp_ppp": "PIL a parita' di potere d'acquisto: dimensione economica reale comparabile.",
 "gdp_per_capita_ppp": "PIL pro-capite PPP: tenore di vita comparabile tra paesi.",
 "gdp_ppp_world_share": "Quota sul PIL mondiale (PPP): peso geopolitico ed economico globale.",
 "population": "Popolazione (milioni): dimensione del mercato e della forza lavoro.",
 "current_account_usd": "Saldo delle partite correnti in USD: posizione esterna in valore assoluto.",
 "unemployment_weo": "Disoccupazione (WEO, con proiezioni): outlook del mercato del lavoro.",
 "inflation_cpi": "Inflazione CPI: erosione del potere d'acquisto, ancora delle aspettative.",
 "real_interest_rate": "Tasso d'interesse reale: costo reale del capitale, restrittivita' monetaria.",
 "lending_interest_rate": "Tasso sui prestiti: costo del credito per imprese e famiglie.",
 "broad_money_gdp": "M ampia %PIL: profondita' finanziaria e liquidita' dell'economia.",
 "inflation_avg_weo": "Inflazione media CPI (WEO, con proiezioni): regime inflazionistico atteso.",
 "inflation_eop_weo": "Inflazione fine periodo (WEO): pressione dei prezzi a fine anno.",
 "current_account_gdp": "Saldo partite correnti %PIL: fabbisogno di finanziamento estero; deficit ampi = vulnerabilita'.",
 "fx_reserves_usd": "Riserve valutarie (incl. oro): cuscinetto contro shock esterni e difesa del cambio.",
 "fx_reserves_months_imports": "Riserve in mesi di import: copertura esterna; <3 mesi = soglia di allerta FMI.",
 "official_fx_rate": "Tasso di cambio ufficiale (LCU/USD): regime e livello del cambio.",
 "exports_gdp": "Esportazioni %PIL: apertura e capacita' di generare valuta forte.",
 "imports_gdp": "Importazioni %PIL: dipendenza dall'estero, fabbisogno valutario.",
 "fdi_inflows_gdp": "IDE netti in entrata %PIL: fiducia estera e finanziamento stabile non-debito.",
 "ppp_conversion_rate": "Tasso di conversione PPP implicito: divergenza prezzi interni vs USA.",
 "remittances_gdp": "Rimesse %PIL: flussi valutari stabili dei lavoratori emigrati, sostegno ai conti esteri.",
 "private_credit_gdp": "Credito al settore privato %PIL: ciclo del credito; aumenti rapidi = rischio bolla.",
 "public_debt_gdp": "Debito pubblico lordo %PIL: solvibilita' sovrana; >90% = soglia di stress.",
 "fiscal_balance_gdp": "Saldo di bilancio pubblico %PIL: deficit = fabbisogno di finanziamento.",
 "primary_balance_gdp": "Saldo primario %PIL (ex interessi): sforzo fiscale per stabilizzare il debito.",
 "government_revenue_gdp": "Entrate pubbliche %PIL: capacita' di prelievo fiscale dello Stato.",
 "government_expenditure_gdp": "Spesa pubblica %PIL: dimensione e rigidita' del bilancio statale.",
 "total_external_debt_usd": "Debito estero totale (USD): esposizione complessiva verso creditori esteri.",
 "external_debt_gni": "Debito estero %RNL: peso del debito verso l'estero sul reddito nazionale.",
 "ppg_external_debt_usd": "Debito estero PPG (USD): quota verso creditori ufficiali (multilaterali/bilaterali).",
 "short_term_debt_usd": "Debito estero a breve (USD): obbligazioni in scadenza entro l'anno.",
 "short_term_debt_reserves": "Debito a breve %riserve: rischio di rollover; >100% = vulnerabilita' a sudden stop.",
 "debt_service_exports": "Servizio del debito %export: capacita' di ripagare il debito con valuta forte.",
 "interest_revenue": "Interessi %entrate: onere degli interessi sul bilancio; affordability del debito.",
 "tourism_receipts_usd": "Incassi turistici (USD): fonte di valuta forte, rilevante per economie turistiche.",
 "tourism_exports_share": "Turismo %export: dipendenza strutturale dal turismo.",
 "food_imports_share": "Importazioni alimentari %import merci: vulnerabilita' ai prezzi alimentari.",
 "fuel_imports_share": "Importazioni di carburante %import: esposizione al rincaro energetico (importatori).",
 "npl_ratio": "NPL %prestiti lordi: qualita' degli attivi bancari, rischio sistemico.",
 "bank_capital_ratio": "Capitale bancario/attivi: solidita' patrimoniale del sistema bancario.",
 "wgi_voice_accountability": "Voice & accountability (WGI): liberta' civili e responsabilita' politica.",
 "wgi_political_stability": "Stabilita' politica (WGI): rischio di instabilita'/violenza.",
 "wgi_government_effectiveness": "Efficacia del governo (WGI): qualita' dell'amministrazione pubblica.",
 "wgi_regulatory_quality": "Qualita' regolatoria (WGI): clima normativo per il settore privato.",
 "wgi_rule_of_law": "Stato di diritto (WGI): certezza del diritto e tutela dei contratti.",
 "wgi_control_corruption": "Controllo della corruzione (WGI): integrita' istituzionale.",
 "trade_openness": "Apertura commerciale (export+import %PIL): integrazione nel commercio globale.",
 "natural_resource_rents_gdp": "Rendite da risorse naturali %PIL: dipendenza dalle materie prime.",
 "military_expenditure_gdp": "Spesa militare %PIL: priorita' di bilancio, rischio geopolitico.",
 "food_exports_share": "Esportazioni alimentari %export merci: esposizione ai prezzi agricoli (esportatori).",
 "fuel_exports_share": "Esportazioni di carburante %export merci: dipendenza dalle entrate da idrocarburi.",
 "metals_exports_share": "Esportazioni di metalli/minerali %export merci: esposizione al ciclo dei metalli.",
 "bis_dsr_private": "Debt service ratio privato (BIS): quota di reddito assorbita dal servizio del debito; picco = top/depressione del ciclo (Dalio).",
 "bis_credit_gap": "Credit-to-GDP gap privato (BIS, HP filter): scostamento del credito dal trend; >+10pp = bolla/leveraging (soglia archetipale Dalio).",
 "bis_policy_rate": "Tasso di policy della banca centrale (BIS): tasso nominale; ~0 = 'pushing on a string'; serve a nom_growth vs nom_rate.",
}

PROVIDER = {"IMF": "IMF WEO", "WB": "World Bank", "fred": "FRED (St. Louis Fed)",
            "yahoo": "Yahoo Finance"}
ASSET_IT = {"EQUITY": "Azionario", "FIXED_INCOME": "Obbligazionario",
            "COMMODITIES": "Materie prime", "FX": "Valute",
            "ALTERNATIVES": "Alternativi/Volatilita'", "REAL_ESTATE": "Immobiliare",
            "MACRO": "Macro", "CRYPTO": "Cripto"}


def _s(v):
    """Stringa pulita: NaN/None -> ''."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip()


def yahoo_meaning(e, layers):
    ac = e.get("asset_class", "")
    geo = _s(layers.get("Layer3_Geographic")) or _s(e.get("area"))
    sub = _s(layers.get("Layer2_SubAssetClass"))
    base = {
     "EQUITY": f"Esposizione azionaria ({geo}{'/'+sub if sub else ''}); fattore di rischio equity.",
     "FIXED_INCOME": f"Esposizione obbligazionaria {sub or geo}; rischio tasso e/o credito.",
     "COMMODITIES": f"Esposizione a materie prime ({sub or geo}); ragioni di scambio e inflazione.",
     "FX": f"Coppia/indice valutario; rischio di cambio ({geo}).",
     "ALTERNATIVES": f"Strumento alternativo/volatilita' ({sub or geo}); copertura e risk sentiment.",
     "REAL_ESTATE": f"Esposizione immobiliare/REIT ({geo}); sensibile ai tassi.",
    }
    return base.get(ac, e.get("name", ""))


def main():
    fresh = db_freshness()
    rows = []

    # --- Tickers (Yahoo) con tassonomia Layer da data_master.xlsx se presente
    layers_map = {}
    dm = BASE / "data_master.xlsx"
    if dm.exists():
        try:
            tk = pd.read_excel(dm, "Tickers")
            for _, r in tk.iterrows():
                layers_map[str(r["Ticker"])] = r.to_dict()
        except Exception:
            pass

    for e in _y("tickers.yaml").get("yahoo", []):
        sym = e["symbol"]
        if sym in {x["symbol"] for x in _y("macro_series.yaml").get("fred", [])}:
            continue  # i codici FRED restano nel downloader FRED
        f, ld = fresh.get(("yahoo", sym), fresh.get(sym, ("D", "")))
        rows.append({
            "Sistema": "Yahoo", "Codice": sym, "Nome": e.get("name", ""),
            "Significato_Economico": yahoo_meaning(e, layers_map.get(sym, {})),
            "Provider": "Yahoo Finance", "Categoria": ASSET_IT.get(e.get("asset_class"), e.get("asset_class", "")),
            "Frequenza": FREQ_IT.get(f, f or "Giornaliera"), "Unita": "prezzo",
            "Area_Paese": e.get("area", ""), "Ultima_Data": ld, "Priorita": e.get("priority", ""),
        })

    # --- FRED
    for e in _y("macro_series.yaml").get("fred", []):
        sym = e["symbol"]
        f, ld = fresh.get(("fred", sym), fresh.get(sym, ("", "")))
        rows.append({
            "Sistema": "FRED", "Codice": sym, "Nome": e.get("name", ""),
            "Significato_Economico": FRED_MEANING.get(sym, e.get("name", "")),
            "Provider": "FRED (St. Louis Fed)", "Categoria": ASSET_IT.get(e.get("asset_class"), e.get("asset_class", "")),
            "Frequenza": FREQ_IT.get(f, f or "-"), "Unita": "indice/valore",
            "Area_Paese": e.get("area", ""), "Ultima_Data": ld, "Priorita": e.get("priority", ""),
        })

    # --- Macro_Panel
    for e in _y("macro_panel.yaml").get("indicators", []):
        iid = e["id"]
        f, ld = fresh.get(("macro_panel", iid), ("A", ""))
        rows.append({
            "Sistema": "Macro_Panel", "Codice": e.get("code", ""), "Nome": e.get("name", ""),
            "Significato_Economico": MP_MEANING.get(iid, e.get("name", "")),
            "Provider": PROVIDER.get(e.get("source"), e.get("source", "")) +
                        (f" / {e.get('dataset')}" if e.get("dataset") else ""),
            "Categoria": f"Pillar: {e.get('pillar','')}",
            "Frequenza": FREQ_IT.get(e.get("freq", "A"), "Annuale"),
            "Unita": e.get("unit", ""), "Area_Paese": "64 paesi (cross-country)",
            "Ultima_Data": ld, "Priorita": e.get("priority", ""),
        })

    df = pd.DataFrame(rows, columns=["Sistema", "Codice", "Nome", "Significato_Economico",
        "Provider", "Categoria", "Frequenza", "Unita", "Area_Paese", "Ultima_Data", "Priorita"])

    # riepilogo
    summ = (df.groupby("Sistema").agg(Serie=("Codice", "count")).reset_index())
    summ.loc[len(summ)] = ["TOTALE", len(df)]

    out = BASE / "data_dictionary.xlsx"
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="Dizionario", index=False)
        summ.to_excel(w, sheet_name="Riepilogo", index=False)
        # larghezza colonne leggibile
        ws = w.sheets["Dizionario"]
        widths = {"A": 12, "B": 22, "C": 42, "D": 70, "E": 20, "F": 22,
                  "G": 13, "H": 14, "I": 22, "J": 12, "K": 9}
        for col, wd in widths.items():
            ws.column_dimensions[col].width = wd

    print(f"OK -> {out}")
    print(summ.to_string(index=False))
    miss = df[df["Significato_Economico"] == df["Nome"]]
    if len(miss):
        print(f"\nSenza significato dedicato (usano il nome): {len(miss)}")
        print("  ", miss["Codice"].tolist()[:20])


if __name__ == "__main__":
    main()
