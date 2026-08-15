# Deep code audit — re-audit integrazione corrente

**Data:** 12 luglio 2026  
**Tipo:** code audit con test, smoke test sul DB locale e riproduzioni concorrenti.  
**Non è una certificazione di produzione o un audit legale.**

## Esito

L'ecosistema ha chiuso otto remediation sostanziali del precedente audit:
identity listing-keyed, registrazione single-name, atomicità di payload/ledger,
provenance SEC, rimozione dei direct finance tool, profili LLM bounded,
regimi hub-only con cap e generazione dei grafici.

Il risultato è ora un MVP interno molto più coerente:

~~~text
richiesta LLM
  -> DataHubTools / StatisticalAnalysisTools / RegimeTools
  -> market-data-hub (identity, job, DB, provenance)
  -> calcolo locale / depot regime
  -> summary, signal, statement o plot_key bounded
~~~

Restano un P0 di release coherence e un P1 di concorrenza dei job.
Non aggiungerei ulteriori capability prima di chiuderli.

## Scope preciso

| Repository / branch osservato | Revisione |
|---|---|
| market-data-hub/main | 57023ca |
| LazyTools/fix-ca03-resolvetools-bypass | 34d4185 |
| LazyTools/fix-ca04-lazybridge-stable | c973765 — branch separata, non ancora antenata dell'HEAD sopra |
| LazyFin/main | d677e5f |
| LazyStats/main | 07d3703 |
| LazyBridge/main | acbcc94 |
| LazyHMM | shim di compatibilità su LazyStats |

Il fatto che la branch CA-04 non sia integrata nella branch LazyTools con le
altre remediation è un finding, non un dettaglio di Git.

## Metodo e test

- lettura delle modifiche, schema, migrations, servizi, provider e surface
  effettivamente montata dagli agenti;
- ricerca dei direct provider, raw output, write path e dipendenze;
- smoke test reale contro market_data.duckdb;
- riproduzione multithread dell'ingestion;
- test completi aggiornati.

| Esecuzione | Esito |
|---|---|
| market-data-hub | **196 passed** |
| LazyTools | **380 passed, 9 skipped** |
| LazyFin | **215 passed** |
| LazyStats, test mirati core/io/grafici | **15 passed** |
| LazyStats suite completa | nessun failure osservato fino al 26%, poi timeout locale di 60 s; non la classifico come verde |
| Schema DB locale | versione **8**, nessun listing_id nullo, nessuna chiave (date, listing_id) duplicata |

Lo smoke test reale ha prodotto:

1. volatilità, correlazione e outlier su SPY/TLT dal DB (131 osservazioni
   weekly);
2. regime_load_from_datahub -> regime_fit -> regime_get_current su SPY
   (130 return weekly);
3. tre PNG regime salvati nel depot ed uno esportato (51,790 byte);
4. statement Apple CIK 0000320193, tre periodi annuali con revenue e net
   income;
5. Markdown, HTML e report file in sandbox.

## Stato delle remediation precedenti

| ID precedente | Stato attuale | Evidenza |
|---|---|---|
| CA-01 — collisione dual listing | **Chiuso** | prices_daily è keyed da (date, listing_id); migration v7 abortisce se la storia non è attribuibile univocamente. Test dual XNAS/XMIL. |
| CA-02 — serie raw nel profilo default | **Chiuso** | TOOL_FUNCTIONS e DataHubTools() escludono raw series/returns; restano solo con allow_raw_series=True. |
| CA-03 — direct EDGAR/price LLM tools | **Chiuso** | EdgarTools, MarketDataTools e ResolveTools sono rimossi dalla superficie LLM; restano solo client di trasporto non-agent. |
| CA-04 — release graph LazyBridge | **Non chiuso: P0** | la fix lazybridge >=1.0.1,<2.0 esiste in c973765, ma non è nell'HEAD 34d4185. |
| CA-05 — single name arbitrario | **Chiuso** | datahub_register_listing richiede exchange/currency, poi ensure_price_history esegue il job normale. |
| CA-06 — atomico e lock | **Parzialmente chiuso; nuovo P1 concorrente** | fetch è fuori lock e payload+ledger sono transazionali; manca però una claim/lease esclusiva del job. |
| CA-07 — refresh legacy | **Chiuso** | il tool è stato rimosso dalle superfici agentiche. |
| CA-08 — provenance filings | **Chiuso** | first_seen_run_id immutabile, last_seen_run_id aggiornato; migration v8 e test di re-ingestion. |
| CA-09 — bridge regimi | **Chiuso per la superficie agente** | RegimeTools è la bridge; LazyStats conserva dominio e depot. |
| CA-10 — test symlink Windows | **Chiuso** | skip condizionale senza privilegio; CI Linux continua a eseguire il test. |
| CA-11 — loader file locale | **Chiuso** | il connector espone solo regime_load_from_datahub, mai load_time_series. |
| CA-12 — output regimi non bounded | **Chiuso** | wrapper clampano sequenze e cambi, con hard_cap e truncated. |
| Grafici | **Chiuso per i regimi** | regime_generate_plots crea e salva PNG; regime_db_export_plot li esporta. |

## Finding aperti

### RA-01 — P0 — la branch di integrazione LazyTools dichiara ancora LazyBridge < 0.11

**Evidenza riproducibile**

La branch LazyTools auditata come corrente (34d4185) mantiene:

~~~toml
dependencies = ["lazybridge>=0.7.9,<0.11"]
~~~

in [LazyTools/pyproject.toml:25](../../LazyTools/pyproject.toml).

La correzione corretta esiste nel commit c973765 su
fix-ca04-lazybridge-stable:

~~~toml
dependencies = ["lazybridge>=1.0.1,<2.0"]
~~~

ma git merge-base --is-ancestor c973765 HEAD è falso: non è nella branch che
contiene i nuovi connector e i test surface.

Il problema è mascherato in questo desktop environment: i metadati installati
di lazytoolkit dichiarano già >=1.0.1, mentre il source checkout corrente
dichiara ancora <0.11. Perciò pip check può essere verde senza verificare il
manifest da cui verrà pubblicata la branch corrente.

Inoltre il workflow
[ecosystem-install.yml](../../LazyTools/.github/workflows/ecosystem-install.yml)
installa prima il checkout corrente con pip install -e . e poi richiede
LazyBridge 1.x nello smoke assert. Con il manifest corrente, una esecuzione
pulita risolve un grafo incompatibile oppure fallisce l'assert.

**Impatto**

È un blocco di release: non esiste ancora una singola revisione LazyTools
installabile che contenga simultaneamente remediation funzionali e contratto
LazyBridge 1.x. La CI install-from-zero non è prova finché questa divergenza
non è risolta.

**Correzione**

Integrare/cherry-pickare c973765 nella branch che porta 34d4185, aggiornare il
pin di LazyFin alla revisione risultante e far passare ecosystem-install da un
ambiente pulito. Solo allora CA-04 può essere marcato chiuso.

### RA-02 — P1 — due ensure identici concorrenti scaricano due volte

**Evidenza nel codice**

La nuova struttura a tre fasi è corretta sul piano dell'atomicità:

1. lock breve: crea/porta il job a running;
2. nessun lock: provider fetch;
3. lock breve + transazione: upsert e stato finale.

Vedi [prices.py:294-424](../market_data_hub/services/prices.py) e
[financials.py:149-320](../market_data_hub/services/financials.py).

Ma quando un secondo caller entra nella fase 1 e trova il job già running,
lo riporta a running, crea un nuovo run_id e procede al proprio fetch. Non
esiste una lease o owner token sul job.

Riproduzione eseguita su DB temporaneo inizializzato:

~~~text
fetch_calls = 2
job_ids     = ['job_7b7abc69b809', 'job_7b7abc69b809']
run_ids     = ['run_cf2e9b5f58a4', 'run_1295ae11a223']
~~~

La transazione impedisce corruzione dei dati, ma non evita doppio download,
doppia pressione sul provider e ambiguità su quale run sia quello finale.

C'è un secondo aspetto sul cold start: due resolve_instrument read_only
simultanei su un DB inesistente cercano entrambi di creare schema/tabelle in
get_conn, causando un conflitto DuckDB di catalogo. È emerso nella prima
riproduzione concorrente.

**Correzione**

- Nella prima fase, acquisire atomicamente ownership: status=running con
  lease_id, lease_expires_at e compare-and-set. Se il job è running e la lease
  è valida, restituire lo stesso job_id con stato running, senza fetch.
- Gestire lease scadute come retry esplicito, incrementando attempts.
- Serializzare la creazione di un DB/schema nuovo con il lock writer, oppure
  richiedere un bootstrap writer esplicito prima di aprire reader concorrenti.
- Aggiungere test multithread per prezzi e SEC: un solo fetch/run per request
  hash; il secondo caller osserva il job già attivo.

### RA-03 — P2 — generazione grafici headless emette warning e non chiude le figure

Il nuovo flusso grafici è funzionale e il test end-to-end passa. Durante
generate_regime_plots, però, i tre renderer chiamano plt.show() anche con
backend Agg:

- [LazyStats regimes/tools.py:395-398](../../LazyStats/src/lazystats/regimes/tools.py)
- [LazyStats regimes/tools.py:431-435](../../LazyStats/src/lazystats/regimes/tools.py)
- [LazyStats regimes/tools.py:467-471](../../LazyStats/src/lazystats/regimes/tools.py)

Lo smoke test reale ha prodotto tre warning FigureCanvasAgg is non-interactive.
In un worker persistente, figure non chiuse possono accumulare memoria.

**Correzione**

Nel percorso save_to_db/agent, salvare e fare plt.close(fig) senza plt.show().
Lasciare show() solo in un percorso notebook interattivo. Il tool può restituire
i plot_keys come ora.

## Flussi agente verificati

### 1. Statistiche e richiesta dati

StatisticalAnalysisTools legge l'intera storia dal Data Hub nel processo e
restituisce report/signal bounded. I tool disponibili sono:

- statistical_return_volatility;
- statistical_return_correlation;
- statistical_return_outliers.

DataHubTools(allow_refresh=True) espone:

- datahub_register_listing per un single name nuovo con identity esplicita;
- datahub_ensure_price_history;
- datahub_ensure_financials.

Il modello non riceve di default raw matrix; raw series è una capability
tecnica opt-in, non nel finance profile.

### 2. Regimi e grafici

Il percorso agente completo ora è:

~~~text
regime_load_from_datahub(symbols, data_key)
  -> regime_fit(data_key, result_key)
  -> regime_get_current / regime_get_summary / regime_get_changes
  -> regime_generate_plots(result_key)
  -> regime_db_export_plot(plot_key, output_path)
~~~

Il test reale ha prodotto 3 plot keys (serie con regimi, barcode states,
barcode high-vol) e ha esportato un PNG. Per funzionare in un processo agente
serve configurare un depot con init_regime_db all'avvio; oggi non è un tool
agente, ed è correttamente un setup dell'applicazione.

### 3. Bilanci

I tool standard DB-backed sono:

- datahub_get_financials_coverage;
- datahub_get_financial_facts (max 100);
- datahub_get_statement (annual, max 12 periodi, provenance per valore).

L'ingestion SEC è opt-in e passa dal job hub. Nessun tool LLM diretto EDGAR
resta nel profilo finance.

### 4. Reportistica

ReportTools rende una struttura Memo in Markdown o HTML; ReportFiles scrive
solo dentro una base directory sandboxata. I grafici regime sono PNG nel
depot/sandbox e possono essere allegati o referenziati dalla reportistica.
Non c'è ancora un chart tool generico per statistiche non-HMM: è una capability
di prodotto futura, non un blocco dei regimi.

## Giudizio aggiornato

| Area | Giudizio |
|---|---:|
| Ownership dati e identity | 8.5/10 |
| Confini LLM / raw data | 9/10 |
| Regimi e report visuale | 8/10 |
| Auditability/provenance | 8.5/10 |
| Concorrenza ingestion | 6.5/10 |
| Release/installabilità | 5/10 finché RA-01 non è integrato |
| Qualità test | 8.5/10 |

L'ecosistema è passato da “architettura promettente con bypass reali” a
“piattaforma interna coerente con due problemi operativi precisi”. La
priorità corretta non è aggiungere altro: **integrare il manifest 1.x e
rendere i job single-flight**. Dopo questi due punti, il nucleo dati/agenti è
difendibile anche in un contesto professionale.

## Ordine di lavoro consigliato

1. Integrare c973765 nella branch LazyTools corrente e far passare la CI
   install-from-zero su un ambiente pulito.
2. Implementare lease/claim per ingestion_jobs, includendo il bootstrap
   concorrente del DB, e aggiungere test multithread.
3. Correggere il lifecycle matplotlib headless (close, non show).
4. Solo dopo, valutare chart generici per volatilità/correlazione e composizione
   automatica di memo + allegati.

