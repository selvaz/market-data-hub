# Piano di razionalizzazione: financial data hub, domini e bridge LLM — v3

**Stato:** proposta aggiornata, pronta per review esterna prima dell'implementazione.
**Owner proposto:** `market-data-hub`
**Ambito:** dati finanziari, serie storiche, filing e bilanci, funzioni statistiche e loro esposizione come tool LLM. Non include email, web generico o tool di coding.

**Storia del documento:**

- **v1** — bozza iniziale.
- **v2** — ogni affermazione sullo stato attuale verificata contro il codice reale (commit in sezione 3); corretti due errori fattuali; `lazydatacore` promosso a prerequisito.
- **v3 (questa)** — recepite tre decisioni dell'owner che ridisegnano l'assetto dei repository:
  1. **LazyTools è sempre e soltanto il bridge tra LLM e tool specifici.** I repository di dominio diventano librerie Python pure, senza dipendenza dal runtime agentico; tutto il codice di wrapping/tool LLM vive in LazyTools ("opzione A", implicazioni in sezione 5).
  2. **Nasce `LazyStats`**, repository unico per tutte le funzioni statistiche, che **assorbe LazyHMM e LazyRay** (entrambi deprecati a fine migrazione). `LazyFin` resta il repository unico per tutto il dominio finance.
  3. **Anche l'utente umano usa i repository, ma i dati passano sempre da market-data-hub.** Un umano in Python/notebook importa direttamente LazyFin/LazyStats; il vincolo non è l'interfaccia ma la sorgente dati: solo reader/extract del hub, mai provider esterni. Gli agenti possono inoltre far *scrivere* il hub on-demand (ingestion di serie non presenti) tramite write tool gated — non è pensabile pre-scaricare e mantenere tutte le serie — ma questo resta un secondo step rispetto al read-only.

Le decisioni ancora aperte per il reviewer sono in sezione 12.

---

## 1. Decisione proposta

Tre pilastri, tutti vincolanti:

### 1.1 Un solo owner dei dati

`market-data-hub` diventa l'unico sistema autorizzato a scaricare, aggiornare, normalizzare e servire dati finanziari. Tutti gli altri pacchetti — e tutti gli utenti, umani o agenti — leggono dal hub attraverso API pubbliche read-only.

> Nessun consumer (`LazyTools`, `LazyFin`, `LazyStats` o agente LLM) chiama un provider finanziario esterno. Solo `market-data-hub` può farlo. Tutte le letture passano dal reader/extract del hub e dal suo storage versionato.

### 1.2 Un solo bridge LLM

`LazyTools` è l'unico punto in cui una funzione diventa un tool LLM. I repository di dominio (`LazyFin`, `LazyStats`) espongono esclusivamente funzioni Python native, senza alcun import di `lazybridge`; LazyTools le wrappa (`Tool.wrap` di LazyBridge è nato per questo), applica limiti di output, gating e trust, e le compone in bundle/profili. Questo confine è verificabile in CI con un test banale: *nessun import `lazybridge` fuori da LazyTools*.

### 1.3 Il dato raw non entra nel prompt

Questo non significa che un LLM debba ricevere il database o una serie grezza. Il flusso corretto è:

```text
provider esterno -> ingestion del hub -> DuckDB/artifact catalog -> calcolo locale (LazyFin/LazyStats) -> risultato compatto -> LLM
```

Le tabelle e i documenti completi restano nel processo del tool. L'agente riceve metadati, metriche, estratti delimitati, identificativi di job e report.

## 2. Assetto target dei repository

```text
                    Provider esterni
              Yahoo / FRED / SEC / altri
                         |
               solo ingestion del hub
                         v
                 market-data-hub
        writer lock + DuckDB + catalog + runs
                         |
              reader/extract read-only
              /          |          \
             v           v           v
         LazyFin     LazyStats    (umano in
       dominio       statistica    notebook)
       finance       + modelli
       (lib pura)    (lib pura)
              \          |
               v         v
              LazyTools  <- unico bridge: wrapping, profili, limiti, safety
                   |
               LazyBridge <- runtime agentico (invariato)
                   |
                  LLM
```

### 2.1 Responsabilità per repository

| Repository | Responsabilità target | Non deve fare | Stato |
|---|---|---|---|
| `market-data-hub` | download, writer lock, schema, history, revisioni, coverage, reader/extract; unica frontiera verso provider esterni | dipendere da LazyTools/LazyBridge o delegare fetch ai consumer | esistente, da estendere (Step 2-4) |
| `lazydatacore` | identità (`ticker:`, `cik:`, `macro:`...), tempo/as-of, envelope `AnalysisResult`, schemi condivisi | accesso a DuckDB, HTTP o logica di business | da estrarre dal hub (Step 1, prerequisito) |
| `LazyFin` | **tutto il dominio finance**: ledger, facts interpretation, scoring, rischio, ottimizzazione; libreria Python pura | fetch SEC/Yahoo/FRED, query SQL alle tabelle hub, **import lazybridge** (i provider attuali migrano in LazyTools, Step 5) | esistente, da purificare |
| `LazyStats` | **tutte le funzioni statistiche**: statistiche generiche + modelli applicati (regimi HMM, score/pillar stile Dalio); depot risultati proprio; libreria Python pura | fetch esterno, lettura file arbitrari nel profilo standard, import lazybridge | **nuovo** (Step 6); assorbe LazyHMM e LazyRay |
| `LazyTools` | **unico bridge LLM**: wrapping delle funzioni di hub/LazyFin/LazyStats, catalogo tool, bundle/profili, limiti output, gating scritture, trust/safety | download finanziari, storage finanziario proprio, logica di dominio | esistente, da estendere (Step 1, 4, 5, 6) |
| `LazyBridge` | runtime agentico: sessione, event log, engine, tool contract (`Tool.wrap`) | conoscere fonti o modelli finanziari | esistente, **invariato** |
| `LazyHMM` | — | — | **deprecato** a fine Step 6 (assorbito in LazyStats) |
| `LazyRay` | — | — | **deprecato** a fine Step 6 (assorbito in LazyStats) |

### 2.2 Struttura interna proposta per LazyStats

L'assorbimento di LazyHMM e LazyRay dentro un repo "statistiche" mescola due nature diverse; per non farne un cassetto misto, il package nasce con tre strati espliciti:

```text
lazystats/
  core/          # statistiche generiche: descrittive, distribuzioni, test,
                 # regressioni, correlazioni, rolling metrics, drawdown...
  models/        # modelli applicati con stato/fit:
    hmm/         #   fit/regime detection (da LazyHMM: fit_regimes,
                 #   get_current_regime, transizioni, diagnostica)
    cycles/      #   score/pillar/regimi paese stile Dalio (da LazyRay:
                 #   dalio_signals, pillar_scores, regime_state, v2 engine)
  io/            # UNICI punti di ingresso/uscita dati:
    datahub.py   #   loader dal hub (evoluzione di lazyhmm.datasources.datahub:
                 #   load_from_datahub -> matrici per core/models)
    depot.py     #   depot risultati DuckDB proprio (evoluzione di lazyray/db:
                 #   connection, schema, scritture con provenance hub)
    local.py     #   loader da file SOLO per notebook/ricerca locale;
                 #   mai esposto come tool LLM (successore di
                 #   lazyhmm.load_time_series con lo stesso perimetro)
```

Regole interne:

- `core/` e `models/` sono funzioni pure sui dati che ricevono: non sanno da dove arrivano le matrici (pattern già in uso in LazyHMM, dove `fit_regimes` legge il payload `{"Y","columns","index"}` scritto da qualunque loader via `_swrite`).
- `io/datahub.py` è l'unico loader ammesso nei profili LLM e nei workflow di produzione; `io/local.py` esiste solo per l'umano in notebook.
- `io/depot.py` scrive ogni risultato con provenance completa: `run_id`/vintage dei dati hub usati, versione del modello, `computed_at` (colma la lacuna verificata in LazyRay, sezione 3.3.5).
- Il depot è un DuckDB separato dal DB del hub (pattern LazyRay, già corretto): il hub contiene *dati*, il depot contiene *risultati analitici*.

### 2.3 Il percorso dell'utente umano

"User standard" = umano in Python/notebook. Non passa da LazyTools (che serve gli agenti): importa direttamente le librerie pure.

| Bisogno | Come lo fa | Cosa NON può fare |
|---|---|---|
| leggere prezzi/macro/facts | `market_data_hub.reader` / `extract` (come fa già LazyRay oggi) | connessione SQL diretta al DuckDB del hub; chiamare Yahoo/FRED/SEC |
| calcoli finance | `import lazyfin` — ledger, scoring, risk, optimizer su dati letti dal hub | — |
| calcoli statistici | `import lazystats` — core/models su matrici da `lazystats.io.datahub` | — |
| serie mancante nel hub | job di ingestion del hub (CLI/API `ensure_*`), poi rilettura dal reader | scaricarsela da sé e usarla nei workflow di produzione |
| esplorazione con file locali | `lazystats.io.local` in notebook | portare quel percorso in un workflow o in un tool LLM |

L'agente LLM fa le stesse cose passando dai tool di LazyTools, con in più i limiti di output, il gating delle scritture e l'audit. La differenza tra umano e agente è il *canale* e le protezioni, non i privilegi sui dati: per entrambi la sorgente è solo il hub.

### 2.4 Scritture on-demand (secondo step, ma parte del disegno)

Non è pensabile pre-scaricare e mantenere serie storiche per ogni strumento possibile. Quindi il disegno prevede che un agente (o un umano) possa *far ingerire* al hub una serie mancante: `datahub_ensure_price_history`, `datahub_ensure_financials` (sezioni 7-8). I punti fermi:

- chi richiede non scarica mai direttamente: chiede al hub, il hub scarica sotto writer lock, tutti rileggono dal reader;
- i write tool stanno in un profilo separato (`financial_data_ops`), gated con grant one-shot e auditati;
- l'ordine di implementazione è: prima il read-only e i profili (Step 0-1), poi il write path (Step 2+). Il vertical slice prezzi (Step 2) è il banco di prova del meccanismo.

Su LazyBridge non serve costruire nulla per tutto questo: `Tool.wrap` copre l'esposizione di funzioni native, `Session`/`EventLog` copre l'audit. Il pezzo mancante è sopra il runtime: il catalogo/profili in LazyTools (Step 1) e il rate limiting (unico componente genuinamente nuovo insieme al catalogo).

## 3. Stato attuale verificato

Verifica effettuata sul codice a questi commit:

| Repository | Commit verificato |
|---|---|
| `market-data-hub` | `b25f41c7` |
| `LazyTools` | `3edb561a` |
| `LazyFin` | `ff6af8f` |
| `LazyHMM` | `d8e46028` |
| `LazyRay` | `a4b8cb5c` |

### 3.1 Cosa esiste già ed è solido

| Fatto verificato | Dove | Conseguenza per il piano |
|---|---|---|
| Layer read-only completo: `read_prices`, `read_macro`, `read_custom`, `read_crypto`, `read_macro_panel(_ext)`, `read_factors`, `read_instrument`, più `get_coverage`/`get_latest`/`get_stalled` | `market_data_hub/reader.py` | Base pronta per i nuovi reader SEC e single-name; è anche l'API dell'utente umano |
| Layer analysis-ready: `extract_series`, `extract_returns`, `extract_macro`, `extract_panel` con trasformazioni (level/log_return/pct_change/diff) e resampling D/W/M/Q | `market_data_hub/extract.py` | Da estendere, non da creare |
| Shim LLM con discovery (`tool_list_*`, `tool_describe`, `tool_search`) ed estrazione (`tool_get_series`, `tool_get_returns`, `tool_get_coverage`) | `market_data_hub/agent_tools.py` | Semantica dei tool; il binding LLM resta in LazyTools |
| Cap serie a 500 righe con flag `truncated` e `meta.n_rows` | `agent_tools.py`, `_MAX_ROWS = 500` | Il pattern di output budget esiste già; va generalizzato |
| Writer lock cross-process via `filelock` con `DBLockTimeout`, usato dal runner schedulato e da `tool_refresh_prices` | `market_data_hub/lock.py` | Il principio "un solo writer" ha già le fondamenta |
| Audit trail: `download_log` (una riga per simbolo per `run_id`), `coverage_report`, `macro_panel_coverage` | `market_data_hub/db/schema.sql` | Base per `ingestion_runs`; forse basta estenderla |
| Tabelle point-in-time `macro_series_vintage` / `macro_panel_vintage` | `db/schema.sql` | Il pattern vintage/as-of esiste per i macro; da replicare per i facts SEC |
| Bridge LazyTools -> hub: `DataHubBackend` (Protocol) + `MarketDataHubBackend`, tool `datahub_*` (list/describe/search/get_series/get_returns/get_coverage + `datahub_refresh_prices` gated), con test di parità firme (`test_backend_protocol_matches_mdh_tool_signatures`) e round-trip live | `LazyTools/src/lazytools/connectors/datahub/` | **Il pattern di riferimento per tutto il bridge**: Protocol + parity test + import lazy; si replica per LazyFin e LazyStats (Step 5-6) |
| Funzioni LazyHMM già scritte come Python nativo pensato per `Tool.wrap` (sezione "§10 LLM TOOL API"), senza dipendenza dal runtime | `lazyhmm/tools.py` | Lo stile "libreria pura + wrap alla frontiera" esiste già in casa; LazyStats nasce così |
| LazyRay legge il hub solo via API pubblica (`reader.read_macro_panel_ext`), mai SQL diretto, e tiene i risultati in un DuckDB proprio (`lazyray/db/`) | `lazyray/dalio.py`, `lazyray/dalio_v2/runner.py`, `lazyray/db/connection.py` | Consumer già conforme al target; il suo pattern DB-risultati diventa `lazystats.io.depot` |
| LazyFin ha `ResolveTools`, `ScoringTools`, `RiskTools`, `PortfolioTools`, `OptimizerTools`, `PortfolioLedger` e nessun codice HTTP/fetch proprio (EDGAR e prezzi arrivano da client iniettati via Protocol) | `lazyfin/resolve/`, `lazyfin/scoring/`, `lazyfin/kernel/` | Il dominio è già pulito; i provider (wrapper sottili sul kernel) migrano in LazyTools (Step 5) |
| Grant one-shot esistenti: `ConfirmationGate` (grant legati a target+scope, consumati all'uso, nessuna approvazione sticky) e `Allowlist` | `LazyTools/src/lazytools/safety/gates.py`, `safety/allowlist.py` | Il meccanismo di approvazione per i write tool si compone da qui, non si costruisce da zero |
| Audit runtime esistente: `Session`/`EventLog` SQLite con `run_id` su ogni evento, tipi `TOOL_CALL`/`TOOL_RESULT`/`TOOL_ERROR`/`TOOL_TIMEOUT`, redazione segreti, exporter pluggabili | `lazybridge/session.py` | L'audit richiesto in sezione 9.2 esiste già a livello runtime |
| Filtri capability per-connector (allow/deny fnmatch, deny-by-default) e boundary test lazybridge↛lazytools | `lazybridge` (MCP, `SECURITY.md`) | Base per i profili; il pattern di boundary test si riusa per "no lazybridge fuori da LazyTools" |

### 3.2 Bypass confermati (il problema che il piano risolve)

| Bypass | Dove | Azione |
|---|---|---|
| `StooqAdapter`/`MarketDataClient`/`MarketDataTools` (`prices_get`, `prices_history`) chiamano stooq.com direttamente, nessuna dipendenza dal hub | `LazyTools/src/lazytools/connectors/marketdata/` | Escludere dai toolset finanziari; deprecare per questo uso |
| `EdgarClient`/`EdgarTools` (`edgar_resolve_company`, `edgar_list_filings`, `edgar_get_filing`, `edgar_company_facts`) parlano direttamente con sec.gov/data.sec.gov | `LazyTools/src/lazytools/connectors/edgar/` | Portare l'ingestion SEC nel hub (Step 3); il client LazyTools diventa adapter deprecato |
| `load_time_series(file_path=...)` è l'unico loader esposto come LLM tool in LazyHMM; carica CSV/Excel/Parquet da path arbitrario | `lazyhmm/tools.py:638` (sezione "§10 LLM TOOL API") | Il successore in LazyStats (`io/local.py`) non sarà mai wrappato come tool LLM; il loader hub sì |
| `tool_refresh_prices` costruisce i ticker come `{"symbol": s, "asset_class": "EQUITY", ...}` hardcoded: nessuna identità, classificazione o lifecycle per-strumento | `market_data_hub/agent_tools.py` | Predecessore funzionale di `datahub_ensure_price_history`; da evolvere (Step 2) |

### 3.3 Correzioni rispetto alla v1 (affermazioni non verificate o sovrastimate)

Queste correzioni cambiano stime e ordine del piano; il reviewer deve tenerne conto.

1. **`LazyTools.statistical_analysis` non esiste.** La v1 lo citava come "pattern corretto da generalizzare". Nessun tool con quel nome esiste in LazyTools. Nella v3 il pattern "leggi dal hub, calcola nel tool, restituisci risultato compatto" ha una casa precisa: le funzioni di `lazystats` wrappate da LazyTools (Step 6).
2. **`lazydatacore` non è un pacchetto condiviso.** Vive esclusivamente dentro `market-data-hub` (`market_data_hub/lazydatacore/`, con `identity.py` che definisce `InstrumentId`). Nessun altro repository lo importa: adozione esterna zero. I namespace `cik:`/`isin:` sono riconosciuti sintatticamente ma `reader.py` conferma che sollevano `NotResolvableError`. Conseguenza: l'estrazione in pacchetto installabile è un **prerequisito** (Step 1).
3. **`load_from_datahub` in LazyHMM esiste ma non è un LLM tool.** È definito in `lazyhmm/datasources/datahub.py:59`, scrive lo stesso payload `{"Y","columns","index"}` via `_swrite` ed è quindi già compatibile con `fit_regimes`. Ma solo `load_time_series` (file) è esposto agli agenti oggi. Nella v3 questo si risolve in LazyStats: `io/datahub.py` è il loader wrappato, `io/local.py` non lo è mai.
4. **L'integrazione hub in LazyFin è opt-in, non default.** `DataHubPriceSource` (`lazyfin/data/datahub.py`) si attiva solo con l'extra `[datacore]`; il workflow accetta qualunque oggetto con `prices_get` e la docstring indica esplicitamente `MarketDataClient` di LazyTools come alternativa. Rendere il hub default richiede modifiche a codice e dipendenze (Step 5).
5. **La provenance di LazyRay verso il hub è più debole del dichiarato.** Gli output hanno `computed_at` e un `model_version` che è lo SHA del *proprio* codice; nessun campo cattura il `run_id` o il vintage dei dati hub usati. Il principio 6 (as-of/provenance) non è soddisfatto end-to-end da nessun consumer oggi. Si risolve nel depot di LazyStats (sezione 2.2).
6. **Nessun codice SEC nel hub.** `market_data_hub/sources/` contiene yahoo, binance, fred, worldbank, imf, imf_sdmx, bis, ecb, factors. Zero riferimenti a SEC/EDGAR/CIK. Lo Step 3 è un porting completo del client da LazyTools più tutto il modello dati: è lo step a maggior rischio di stima.
7. **Non esiste un sistema di tool profile/bundle.** Né in LazyTools né in LazyBridge c'è un registro dichiarativo (owner, read/write, trust, limiti output, profilo). Esistono i mattoni (`ConfirmationGate`, `Allowlist`, `EventLog` con `run_id`, filtri allow/deny per-connector) ma la composizione in bundle nominati va costruita. Manca del tutto il rate limiting.

## 4. Principi non negoziabili

1. **Un solo writer per i dati finanziari.** Il writer è sempre un job del hub sotto lock (`market_data_hub/lock.py` già esistente); i consumer non aprono il DB in scrittura.
2. **Letture da API, non SQL privato.** Ogni lettura — umana o agente — passa da `reader`/`extract` o da un backend del hub; nessuno usa tabelle fisiche o provider HTTP. (LazyRay è già conforme; è il modello.)
3. **LazyTools è l'unico bridge LLM.** Nessun repository di dominio importa `lazybridge`; l'unica strada da una funzione Python a un tool LLM passa dal catalogo, dai limiti e dai profili di LazyTools. Verificato in CI (Step 7).
4. **Le librerie di dominio sono pure.** `LazyFin` e `LazyStats` espongono funzioni Python native usabili in notebook; niente framework agentico, niente HTTP finanziario, niente lettura file arbitrari nei percorsi di produzione.
5. **Il dato raw non entra nel prompt.** Ogni tool LLM ha un budget di output e restituisce un risultato compatto. (Il cap `_MAX_ROWS = 500` è il precedente; il target per i nuovi tool è "metriche, non matrici".)
6. **Identità canoniche ai confini.** Input/output usano `ticker:`, `cik:`, `macro:` ecc. via `lazydatacore.identity.InstrumentId`; un simbolo bare è solo comodità di input, normalizzata subito.
7. **As-of e provenance obbligatori.** Un valore finanziario senza fonte, timestamp e run/filing id non è riusabile. Include la propagazione del `run_id` hub negli output analitici (depot LazyStats, output LazyFin).
8. **Scritture separate dalle letture.** I `get_*` non fanno rete e non mutano. I `ensure_*`/`request_*` sono capability esplicite, approvate e auditabili, in un profilo separato.
9. **Storia, non solo ultimo valore.** Prezzi, filing e facts devono poter essere interrogati per data di osservazione e, quando applicabile, data di conoscenza (pattern già presente nelle tabelle `*_vintage`).

## 5. Decisione architetturale: librerie pure + bridge unico (opzione A)

Registrata come decisione dell'owner; le implicazioni accettate sono elencate perché il reviewer le validi.

**Cosa comporta:**

- `LazyFin` perde le classi provider (`ResolveTools`, `ScoringTools`, `RiskTools`, `PortfolioTools`, `OptimizerTools`): sono wrapper sottili attorno al kernel, quindi migra lo strato di wrapping in LazyTools, non la logica. In LazyFin restano funzioni/classi di dominio (`PortfolioLedger`, scoring engine, risk kernel, optimizer, normalizzazione facts).
- `LazyStats` nasce direttamente pura (lo stile esiste già: le funzioni §10 di LazyHMM sono Python nativo pensato per `Tool.wrap`).
- LazyTools acquisisce dipendenze verso i domini, gestite come **extras opzionali con import lazy** (pattern già in uso per `market-data-hub`): `lazytools[fin]`, `lazytools[stats]`, `lazytools[datahub]`.
- Il rischio di drift firma tra wrapper e funzione è mitigato dal pattern già in produzione nel connector datahub: Protocol che specchia le firme + test di parità (`inspect.signature`) + smoke test round-trip. Si replica identico per `connectors/fin/` e `connectors/stats/`.

**Costi accettati:**

- esporre un nuovo tool richiede toccare due repo (funzione nel dominio, wrap nel bridge);
- ogni cambio di firma nel dominio richiede una release coordinata di LazyTools (il parity test in CI lo intercetta, non lo evita);
- migrazione una tantum dei 5 provider LazyFin, con shim deprecati per una release.

**Alternativa scartata (opzione B, provider nei domini + bundle in LazyTools):** meno rework immediato, ma ogni dominio si porta la dipendenza dal framework, la policy di safety si sparpaglia su quattro repo e il confine diventa una proprietà runtime difficile da testare, contro il test di import banale dell'opzione A.

**Layout proposto in LazyTools** (nomi da confermare, domanda 12.13):

```text
lazytools/src/lazytools/connectors/
  datahub/    # esistente: backend Protocol + tool datahub_* (read + write gated)
  fin/        # nuovo (Step 5): wrap di lazyfin — resolve, scoring, risk,
              # portfolio, optimizer; parity test vs lazyfin
  stats/      # nuovo (Step 6): wrap di lazystats — statistiche, regimi HMM,
              # cycles; parity test vs lazystats
  edgar/      # deprecato a fine Step 5 (sostituito dai tool datahub financials)
  marketdata/ # escluso dai profili finanziari, deprecato per uso finance
```

## 6. Prerequisito trasversale: estrazione di `lazydatacore`

Promosso da decisione aperta (v1) a prerequisito, perché gli Step 4-6 richiedono che i consumer importino identità ed envelope condivisi, e oggi nessun consumer può farlo.

1. Estrarre `market_data_hub/lazydatacore/` in pacchetto installabile separato (repo o subdirectory pubblicata, da decidere — domanda 12.10).
2. Contenuto minimo v0: `InstrumentId` e namespace (`ticker:`, `cik:`, `macro:`, `isin:`, `fx:`, `index:`), tipi tempo/as-of, envelope `AnalysisResult` (source, as_of, tool_version, provenance/run_id).
3. Il hub diventa il primo consumer del pacchetto estratto (dipendenza invertita, senza cambi di comportamento).
4. Nessun consumer è obbligato ad adottarlo in questo step; l'obbligo scatta alle frontiere toccate dagli Step 4-6.

**Acceptance:** `pip install lazydatacore` (o equivalente) funziona in un ambiente pulito; il hub passa i test usando il pacchetto esterno; nessun cambiamento funzionale.

## 7. Capacità nuova: serie storiche single-name

### 7.1 Obiettivo

Un workflow deve poter chiedere una serie non presente nel catalogo curato, ad esempio `ticker:NVDA`, `ticker:ENEL.MI` o un ADR, senza introdurre un client esterno in LazyTools o LazyFin.

Il hub controlla la coverage, scarica solo quando necessario, scrive sotto lock e serve poi la serie dalla propria base.

### 7.2 Registro strumenti proposto

Introdurre un registro di strumenti/alias in DuckDB. Oggi non esiste nulla di simile: `catalog.py` è un layer di discovery su YAML statico (`config/tickers.yaml`), non un registro. Nome tabella da decidere; qui è chiamato `instrument_catalog`.

| Campo | Esempio | Nota |
|---|---|---|
| `instrument_id` | `ticker:NVDA` | Identità lazydatacore canonica |
| `symbol` | `NVDA` | Chiave provider/warehouse |
| `asset_type` | equity, etf, adr, index, fx | `auto` all'ingestion, correggibile |
| `exchange`, `currency`, `country` | NASDAQ, USD, US | Metadata quando disponibili |
| `source_symbol` | NVDA | Mapping provider esplicito |
| `active`, `first_seen_at`, `last_checked_at` | ... | Lifecycle e audit |
| `metadata_json` | ... | Campi non normalizzati, non critici |

Il catalogo curato YAML continua a esistere. Il registro ad hoc non deve trasformare ogni ticker richiesto in un membro permanente dell'universo editoriale.

### 7.3 Tool proposti

La *semantica* di questi tool vive nel hub (`agent_tools.py` o modulo dedicato); il *binding LLM* vive in LazyTools (`connectors/datahub/`), con parity test. I write tool vanno in un provider separato (`DataHubWriteTools`), non nel profilo read-only.

| Tool | Azione | Output verso LLM | Protezioni |
|---|---|---|---|
| `datahub_resolve_instrument` | Risolve ticker/CIK/nome e dice se è presente. | candidati compatti + coverage | Read-only |
| `datahub_ensure_price_history` | Richiede/avvia ingestion di una serie price single-name. | job id, instrument id, periodo, stato, coverage | writer lock, grant one-shot, rate limit, idempotenza |
| `datahub_get_job_status` | Legge stato e errori sintetici del job. | stato, conteggi, coverage | Read-only |
| `datahub_get_price_summary` | Legge DB e calcola ultimo valore, range, rendimento e coverage. | metriche, non barre OHLCV | budget output fisso |

`tool_refresh_prices` esistente è il predecessore funzionale ma va evoluto: oggi hardcoda `asset_class: "EQUITY"` per ogni simbolo e non esprime identità, classificazione, approvazione né job lifecycle.

### 7.4 Contratto proposto per il write tool

Input:

```json
{
  "instrument": "ticker:NVDA",
  "start": "2010-01-01",
  "end": "",
  "provider": "auto",
  "asset_type": "auto"
}
```

Output:

```json
{
  "job_id": "price_refresh_...",
  "instrument": "ticker:NVDA",
  "status": "completed",
  "requested_range": ["2010-01-01", "2026-07-11"],
  "stored_range": ["2010-01-04", "2026-07-10"],
  "observations_written": 4150,
  "coverage": {"score": 99.6, "stalled": false},
  "source": "market-data-hub"
}
```

Non restituisce le 4.150 righe. Una successiva analisi usa `lazystats` (via tool LazyTools) o summary server-side.

### 7.5 Casi da gestire

- ticker ambiguo, cambiato, delistato o non supportato;
- share class, ADR e suffissi di borsa;
- currency/exchange non noti;
- storia parziale o buchi;
- split/dividendi e scelta esplicita tra `close` e `adj_close`;
- dati recenti non finalizzati;
- retry e idempotenza quando un job viene rilanciato.

## 8. Capacità nuova: filing e bilanci SEC/EDGAR

**Nota di stima:** nel hub non esiste alcun codice SEC (verificato: `market_data_hub/sources/` non ha alcuna sorgente SEC; i namespace `cik:` sollevano `NotResolvableError`). Il client esistente è in `LazyTools/src/lazytools/connectors/edgar/` (throttling fair-access, User-Agent obbligatorio, host pinning già implementati): la logica di trasporto si porta, il modello dati si costruisce da zero. Questo è lo step a maggior rischio del piano.

### 8.1 Obiettivo MVP

Copertura affidabile per società USA, partendo da `ticker:` o `cik:`, per rendere leggibili dal DB:

- identificazione società/CIK;
- filing 10-K, 10-Q e 8-K rilevanti;
- XBRL company facts;
- stato patrimoniale, conto economico e cash flow standardizzati;
- filing date, report date, accession, unit, periodo e fonte;
- revisioni/restatement senza sovrascrivere la storia.

Il primo MVP è SEC/US GAAP. IFRS, bilanci PDF e transcript sono fasi successive.

### 8.2 Storage proposto

| Entità | Chiave | Contenuto minimo | Motivo |
|---|---|---|---|
| `sec_entities` | CIK | nome, ticker/alias, SIC, fiscal year end | Risoluzione stabile |
| `sec_filings` | CIK + accession | form, filing date, report date, URL, hash, run id | Provenance del documento |
| `sec_filing_text` | accession | testo estratto/versione, hash, dimensione | Ricerca ed estratti controllati |
| `sec_company_facts` | CIK + concept + unit + period + accession | valore, frame, fiscal period/year, filed date | Facts XBRL preservati |
| `sec_statement_lines` | CIK + statement + line key + period + accession | valore standardizzato, unit, concept sorgente | Bilanci confrontabili |
| `sec_coverage` | CIK/filing family | ultimo filing, lag, forme presenti, status | Qualità e freshness |
| `ingestion_runs` | run id | source, parametri, esito, timestamp, errore | Audit e retry; valutare se estendere `download_log` esistente invece di creare tabella nuova |

Per il versionamento as-of, replicare il pattern già in produzione nelle tabelle `macro_series_vintage`/`macro_panel_vintage`.

Il testo completo può stare nel DuckDB se il volume è sostenibile. Se si userà un artifact store per HTML/PDF originali, il DB deve comunque conservare hash, URI, estratto testuale, metadata e retention. Il consumer legge sempre dall'API hub, mai da SEC.

### 8.3 Semantica obbligatoria

Ogni osservazione deve preservare:

- `cik`, `accession_no`, `form`, `filed_at`, `report_date`;
- concept XBRL originale e line key standardizzata;
- unit e scala;
- periodo `instant` o `duration`, data inizio/fine, fiscal year/period;
- fonte, hash payload, data ingestion;
- versione/revisione.

Non è ammesso un generico "latest revenue" senza periodo e filing. Le viste convenience possono offrire il valore più recente, ma devono esporre accession, report date e filed date.

### 8.4 Tool proposti

Come in 7.3: semantica nel hub, binding LLM in LazyTools con parity test.

| Tool | Tipo | Output LLM | Nota |
|---|---|---|---|
| `datahub_ensure_financials` | Scrittura controllata | job id, CIK, forms, stato e coverage | Scarica/normalizza solo nel hub |
| `datahub_get_financial_coverage` | Lettura | forme presenti, periodi, lag, ultimi filing | Primo controllo |
| `datahub_get_financial_facts` | Lettura | facts filtrati per concept, periodo e limite | Mai XBRL raw completo |
| `datahub_get_statement` | Lettura | righe standardizzate per statement e periodi limitati | Income/balance/cash-flow |
| `datahub_list_filings` | Lettura | metadata filing paginati | Nessun testo integrale |
| `datahub_get_filing_extract` | Lettura | estratti/chunk delimitati dal DB | `content_is_untrusted=true` |
| `datahub_get_financial_summary` | Lettura/calcolo | crescita, margini, leva e provenance | Calcolo nel hub/tool |

Input esempio:

```json
{
  "instrument": "ticker:MSFT",
  "forms": ["10-K", "10-Q"],
  "start_filed_at": "2018-01-01",
  "include_filing_text": false
}
```

### 8.5 Cosa non esporre alla LLM standard

- companyfacts JSON completo;
- HTML/PDF completo di filing;
- query SQL o nomi di tabelle fisiche;
- download SEC diretto;
- limiti unbounded per record, periodi o caratteri.

## 9. Profili tool LLM target

### 9.1 financial_research (default, read-only)

| Famiglia | Tool consentiti | Origine funzioni | Regola |
|---|---|---|---|
| Discovery | list/search/describe/coverage del hub | hub via `connectors/datahub` | Output paginati e filtrati |
| Prezzi | summary e coverage | hub via `connectors/datahub` | Nessuna matrice raw |
| Statistiche | descrittive, correlazioni, rolling, drawdown, regimi HMM, cycles | `lazystats` via `connectors/stats` | Input solo dal loader hub; risultato compatto |
| Bilanci | coverage, facts filtrati, statement, summary, filing metadata/extract | hub via `connectors/datahub` | Limiti server-side e provenance |
| Portfolio | exposure, concentration, drift, risk, optimizer | `lazyfin` via `connectors/fin` | Deterministici; nessun trade |
| Output | render memo/HTML | LazyTools report | Nessuna scrittura file di default |

### 9.2 financial_data_ops (scrittura, separato)

Include soltanto `datahub_ensure_price_history`, `datahub_ensure_financials` e `datahub_get_job_status`.

Requisiti obbligatori e componenti da riusare (verificati esistenti):

- `allow_write=True` alla costruzione del provider (pattern già usato da `datahub_refresh_prices` in LazyTools);
- grant di approvazione one-shot, legato a job/instrument: **riusare `ConfirmationGate`** (`lazytools/safety/gates.py`) — grant target+scope-bound consumati all'uso, già in produzione per Gmail/Telegram;
- writer lock del hub: **riusare `db_write_lock()`** (`market_data_hub/lock.py`);
- audit event con autorizzazione, input normalizzato e run id: **riusare `Session`/`EventLog`** di LazyBridge (SQLite, `run_id` su ogni evento, redazione segreti) collegandolo al `run_id` di ingestion del hub;
- nessun risultato raw, solo stato e coverage;
- rate limit per provider/CIK/ticker: **da costruire ex novo** (non esiste nulla nell'ecosistema);
- retry deterministico e idempotenza.

Da costruire ex novo anche la composizione in bundle nominati: oggi esistono solo filtri allow/deny per-connector in LazyBridge, non profili riusabili cross-provider.

## 10. Piano step-by-step

### Step 0 — Decision record e baseline

1. Approvare i principi della sezione 4 e registrare in ADR le tre decisioni della v3: opzione A (sezione 5), nascita di LazyStats con assorbimento di LazyHMM/LazyRay, percorso utente umano (sezione 2.3).
2. Congelare MVP: prezzi single-name supportati dal provider corrente; filing SEC/US GAAP.
3. Registrare i tool vietati al profilo standard: `prices_get`, `prices_history` (MarketDataTools), `edgar_*` diretti (EdgarTools), qualunque loader da file locale, `datahub_get_series` e `datahub_get_returns` raw.
4. Definire SLO: freshness EOD, ritardo filing, output massimo, retention raw e policy retry.

**Acceptance:** ADR approvato, inventario tool/profili pubblicato, nessuna modifica funzionale.
**Rischio:** scope creep. **Mitigazione:** MVP US/SEC e un provider prezzi.

### Step 1 — lazydatacore, tool catalog e confini capability

1. **Estrarre `lazydatacore` in pacchetto installabile** (sezione 6). Prerequisito per tutto ciò che segue.
2. Aggiungere un catalogo dichiarativo in LazyTools: per ogni tool — owner, data source, read/write, trust, limite output, profilo di appartenenza e lifecycle (attivo/deprecato/rimosso). Da zero: non esiste; i mattoni sono `ConfirmationGate`, `Allowlist`, filtri MCP allow/deny.
3. Costruire i bundle `financial_research` e `financial_data_ops`, componendo `ConfirmationGate` + `EventLog` invece di reimplementarli.
4. Spostare i write tools esistenti (`datahub_refresh_prices`) in provider separati.
5. Aggiungere test contro collisioni di nome e tool non ammessi in un profilo.
6. Predisporre gli extras `lazytools[fin]` / `lazytools[stats]` (vuoti per ora) e il template del parity test riusabile.

**Acceptance:** `lazydatacore` installabile e usato dal hub; un agente standard non può ricevere fetch esterno o scrittura; il catalogo elenca ogni tool esposto con owner e profilo.
**Rischio:** compatibilità agenti esistenti. **Mitigazione:** alias deprecati con warning per una release.

### Step 2 — Single-name price ingestion nel hub

1. Disegnare e migrare `instrument_catalog`; decidere se `ingestion_runs` è una tabella nuova o un'estensione di `download_log` esistente.
2. Estrarre da `tool_refresh_prices` un job generico per instrument canonico (eliminando l'hardcode `asset_class: "EQUITY"`).
3. Implementare controllo coverage e download incrementale (riusando `get_coverage`/`coverage_report`).
4. Implementare `datahub_ensure_price_history` e `datahub_get_job_status` nel hub, poi il binding in `LazyTools/connectors/datahub/` estendendo il test di parità firme esistente.
5. Implementare `datahub_get_price_summary` come alternativa LLM-safe alle barre raw.

**Acceptance:** da DB vuoto, un `ticker:<single-name>` viene normalizzato, ingestito, coperto e riletto dal DB; una seconda richiesta è idempotente; il tool non restituisce barre raw; lo stesso percorso funziona per l'umano via CLI/API del hub.

**Test minimi:** ticker noto/sconosciuto/ambiguo, periodo coperto, gap interno, provider failure, lock concorrente (riusare `DBLockTimeout`), retry, split/adj-close.

### Step 3 — Ingestion SEC e modello facts/filing

1. Portare nel hub il client SEC da `LazyTools/connectors/edgar/` (throttle, User-Agent, host pinning e size cap sono già implementati lì; il porting è trasporto + integrazione con lock e run tracking del hub).
2. Creare schema SEC (tabelle 8.2), migration, writer sotto `db_write_lock()` e coverage/run.
3. Implementare resolver `ticker <-> cik` con alias storici; rimuovere il `NotResolvableError` per il namespace `cik:` in `reader.py`.
4. Ingerire prima filing metadata e company facts; filing text dopo aver definito retention/chunking.
5. Materializzare le tre viste statement standardizzate e documentare mapping concept XBRL -> line key.
6. Gestire restatement senza sovrascrivere filing/facts precedenti (pattern `*_vintage` già in produzione per i macro).

**Acceptance:** `ticker:MSFT` e `cik:0000789019` risolvono allo stesso entity; 10-K/10-Q persistiti; revenue, assets e operating cash flow hanno unit, periodo, accession e filed date verificabili.

**Rischio principale:** concetti XBRL non uniformi, unit, annual vs quarterly, amendment e filing duplicati. È lo step a maggior incertezza di stima (parte da zero nel hub).
**Mitigazione:** raw facts immutabili, mapping versionato e test golden su issuer con casi anomali.

### Step 4 — Reader/extract e tool LLM financials

1. Aggiungere reader pubblici per entity, filing, facts e statement (stesso stile di `reader.py` esistente) — questa è anche l'API dell'utente umano.
2. Aggiungere extract con identità canoniche, periodi e filtri espliciti.
3. Implementare i `datahub_get_*` della sezione 8.4 (semantica nel hub, binding in LazyTools) con limiti server-side, generalizzando il pattern `_MAX_ROWS`/`truncated`.
4. Rendere ogni output un `AnalysisResult` del pacchetto `lazydatacore` estratto, con source/as-of/tool version/run id.
5. Marcare gli estratti filing con `content_is_untrusted=true`.

**Acceptance:** un agente confronta ricavi, margini e leva su periodi specifici senza ricevere XBRL o testo completi; un umano ottiene gli stessi dati via reader in notebook.

### Step 5 — LazyFin libreria pura e bridge fin in LazyTools

1. Creare `lazytools/connectors/fin/`: Protocol che specchia le funzioni pubbliche del kernel LazyFin + provider tool (resolve, scoring, risk, portfolio, optimizer) + parity test firma per firma + smoke test round-trip (stesso pattern del connector datahub).
2. Migrare la logica di wrapping da `ResolveTools`/`ScoringTools`/`RiskTools`/`PortfolioTools`/`OptimizerTools` (LazyFin) ai provider di `connectors/fin/`; in LazyFin restano solo le funzioni di dominio.
3. Lasciare in LazyFin shim deprecati con warning per una release, poi rimuovere `lazybridge` dalle dipendenze di LazyFin.
4. Rendere il hub la sorgente dati default dei workflow finanziari: `DataHubPriceSource` default esplicito (oggi extra opzionale `[datacore]`), facts normalizzati letti dai nuovi reader hub invece che da `EdgarClientLike` iniettato. Il Protocol resta per i test.
5. `EdgarTools` (LazyTools) diventa adapter deprecato del backend hub: nessun HTTP nel profilo finanziario. `MarketDataTools` escluso dai bundle finanziari con warning di deprecazione.
6. Adottare `lazydatacore` alle frontiere di LazyFin: identità in input, `AnalysisResult` in output.

**Acceptance:** `import lazyfin` non tira dentro `lazybridge`; tutti i tool finance arrivano da `lazytools.connectors.fin`; il parity test passa; i workflow LazyFin leggono prezzi e facts solo dal hub; gli shim emettono deprecation warning.

**Rischio:** rottura di agenti/config esistenti che istanziano i provider da LazyFin. **Mitigazione:** shim con firma identica per una release; changelog con mappa vecchio->nuovo.

### Step 6 — Nascita di LazyStats (assorbe LazyHMM e LazyRay)

1. Creare il repo `LazyStats` con la struttura della sezione 2.2 (`core/`, `models/hmm/`, `models/cycles/`, `io/`), pura (nessun lazybridge), con `lazydatacore` alle frontiere.
2. **Migrare LazyHMM**: `fit_regimes`, `get_current_regime` e le funzioni §10 in `models/hmm/`; `load_from_datahub` diventa `io/datahub.py` (loader unico di produzione); `load_time_series` diventa `io/local.py`, documentato come solo-notebook e mai wrappato. Il payload `{"Y","columns","index"}`/`_swrite` resta il contratto interno tra loader e modelli.
3. **Migrare LazyRay**: engine dalio/dalio_v2 in `models/cycles/`; `lazyray/db/` (connection + schema `dalio_signals`, `pillar_scores`, `regime_state`, ...) diventa `io/depot.py`, esteso con provenance hub obbligatoria (`run_id`/vintage dei dati di input, oltre a `computed_at` e versione modello).
4. Popolare `core/` con le statistiche generiche (descrittive, test, correlazioni, rolling, drawdown): prima le funzioni che servono ai profili LLM, il resto per accrescimento.
5. Creare `lazytools/connectors/stats/`: Protocol + provider + parity test, nello stesso stile di `fin/` e `datahub/`. Solo `io/datahub.py` è raggiungibile dai tool; `io/local.py` non è nel catalogo.
6. Deprecare LazyHMM e LazyRay: README di redirect, pin dell'ultima release, niente sviluppo nuovo; rimozione a valle di una release di convivenza.

**Acceptance:** i risultati HMM e cycles prodotti da LazyStats coincidono con quelli di LazyHMM/LazyRay su dataset golden; ogni riga del depot ha provenance hub verificabile; un agente col profilo `financial_research` può calcolare regimi e statistiche solo su dati provenienti dal hub; `import lazystats` non tira dentro `lazybridge`.

**Rischio:** doppia migrazione (due repo in uno) con drift durante la convivenza. **Mitigazione:** migrare per primo LazyHMM (più piccolo e già in stile puro), congelare LazyRay durante il porting, test golden di equivalenza numerica prima dello switch.

### Step 7 — Enforcement, osservabilità e deprecazione

1. Test di import/boundary in CI, per ogni repo:
   - `lazyfin`, `lazystats`, `market_data_hub`: vietato importare `lazybridge`;
   - `lazyfin`, `lazystats`, `lazytools`: vietati moduli HTTP finanziari (requests/httpx verso provider finanziari) e accesso a tabelle DuckDB private del hub;
   - `lazystats` profili produzione: vietato `io/local.py` nei percorsi wrappati.
   (Il pattern esiste già: LazyBridge ha boundary test verso LazyTools.)
2. Test di tool profile: bundle standard senza write/raw/bypass; ogni tool nel catalogo ha owner e profilo.
3. Test di rete: `datahub_get_*` e tutti i tool stats/fin non fanno HTTP; solo `ensure_*` può farlo e produce run record.
4. Dashboard coverage: freshness, buchi, errori, ultimo filing, data ingestion (base: `coverage_report` e `v_stalled` esistenti).
5. Deprecazioni con data di rimozione: `MarketDataTools` ed `EdgarTools` dal profilo finanziario; shim provider in LazyFin; repository LazyHMM e LazyRay.

**Acceptance:** CI blocca un bypass (import, HTTP o profilo); audit session collega tool call -> run id -> fonte -> as-of; le deprecazioni hanno date pubblicate.

## 11. Ordine raccomandato e razionale

1. **Step 0-1** — decisioni, `lazydatacore`, catalogo e profili: sblocca tutto il resto e mette subito il confine read-only/write.
2. **Step 2** — single-name prices come vertical slice completo: dimostra writer, coverage, approval, tool output e reader DB su un caso semplice.
3. **Step 3** — SEC metadata e company facts, senza filing text: lo step più incerto va affrontato quando il meccanismo (job, lock, coverage, run) è già rodato dallo Step 2.
4. **Step 4** — reader/tool facts e statement: completa il valore lato lettura.
5. **Step 5** — LazyFin pura + bridge fin: la migrazione dei provider avviene dopo che catalogo e profili (Step 1) esistono, così i nuovi provider nascono direttamente dentro i bundle.
6. **Step 6** — LazyStats: per ultimo tra i lavori grossi perché dipende dal loader hub (Step 2) e dal pattern bridge (Step 5); dentro lo step, prima LazyHMM poi LazyRay.
7. **Step 7** — enforcement e deprecazioni: chiude il cerchio quando non ci sono più percorsi legittimi da vietare.
8. Filing text/chunking, IFRS e provider aggiuntivi soltanto dopo il MVP.

Stato transitorio accettato: tra Step 1 e Step 5, i provider dentro LazyFin e i tool attuali di LazyHMM continuano a funzionare come oggi (raggiungibili solo via bundle LazyTools dal momento in cui i profili esistono); l'opzione A è il target di fine Step 6, non un big bang.

## 12. Decisioni per la review

1. Raw filing text in DuckDB, artifact store o entrambi?
2. Primo writer tool sincrono per ticker/CIK o sempre coda persistita?
3. Provider prezzi e fallback ammessi per single-name?
4. Nuova `instrument_catalog` o estensione del catalogo YAML esistente (che oggi è config statica, non DB)?
5. Mapping US GAAP minimo per statement MVP?
6. Restatement/as-of: accession, vintage (pattern `*_vintage` esistente) o entrambi?
7. Limiti standard per output LLM: record, caratteri e byte? (Oggi solo `_MAX_ROWS = 500` sulle serie.)
8. `datahub_get_series` e `datahub_get_returns`: tool opt-in, Python-only o sostituiti da summary/analysis?
9. Meccanismo di approvazione per `financial_data_ops`: basta comporre `ConfirmationGate` + `EventLog`, o serve un livello di grant persistente/asincrono per job lunghi?
10. Dove vive `lazydatacore` estratto: repo dedicato o pubblicazione dalla subdirectory del hub? (L'estrazione in sé è decisa: prerequisito dello Step 1.)
11. `ingestion_runs`: tabella nuova o evoluzione di `download_log`?
12. Provenance hub nei consumer: `run_id` per riga di output, per run analitico, o snapshot id per sessione?
13. Nome e layout definitivi: `LazyStats` come nome repo; `connectors/fin/` e `connectors/stats/` in LazyTools; conferma o alternative?
14. Depot risultati LazyStats: schema unificato cross-modello (una tabella results generica + payload) o tabelle per-modello come oggi in LazyRay?
15. Politica di convivenza: quante release di shim deprecati per i provider LazyFin e per LazyHMM/LazyRay prima della rimozione?
16. Gestione versioni cross-repo: LazyTools pinna range di versioni di `lazyfin`/`lazystats`/`market-data-hub` negli extras, o si adotta un lockfile/BOM d'ecosistema?

## 13. Criteri go/no-go

### Go MVP prezzi

- write tool gated e idempotente;
- single-name letto dal DB dopo ingestion;
- coverage e run id disponibili;
- nessuna barra/raw matrix nel risultato LLM;
- test lock e provider failure verdi.

### Go MVP bilanci

- entity/ticker/CIK risolti in modo tracciabile;
- 10-K/10-Q e company facts storicizzati;
- almeno tre statement line standardizzate validate su issuer diversi;
- ogni valore ha unit, periodo, filing accession e filed date;
- reader/tool non fa HTTP e rispetta limiti output;
- test restatement e duplicate filing verdi.

### Go consolidamento repo (Step 5-6)

- `import lazyfin` e `import lazystats` senza `lazybridge`;
- parity test fin/stats/datahub verdi in CI;
- risultati HMM/cycles equivalenti ai golden di LazyHMM/LazyRay;
- ogni riga del depot LazyStats riconducibile a run hub;
- shim deprecati funzionanti e con warning.

### No-go

- un consumer può ancora scaricare dati finanziari direttamente;
- un tool read-only fa refresh implicito o scrive;
- l'agente riceve documenti, facts o serie completi;
- un valore financial non è riconducibile a fonte e periodo;
- writer non serializzato o non idempotente;
- un output analitico non è riconducibile al run hub dei suoi input;
- un tool LLM è raggiungibile senza passare dal catalogo/profili di LazyTools;
- un repository di dominio importa `lazybridge` (a regime, fine Step 6).

## 14. Riferimenti verificati

Componenti citati nel piano, con posizione confermata nel codice:

- `market_data_hub/reader.py`, `extract.py`, `agent_tools.py` — layer read-only, extract e semantica tool corrente (cap `_MAX_ROWS = 500`).
- `market_data_hub/lock.py` — `db_write_lock()` via filelock, `DBLockTimeout`.
- `market_data_hub/db/schema.sql` — `prices_daily`, `macro_series(_vintage)`, `macro_panel(_vintage)`, `download_log`, `coverage_report`, viste `v_returns`/`v_stalled`.
- `market_data_hub/lazydatacore/identity.py` — `InstrumentId`; da estrarre (Step 1). Namespace `cik:` oggi non risolvibile.
- `LazyTools/src/lazytools/connectors/datahub/` — bridge, tool `datahub_*`, test parità firme: il pattern di riferimento per `connectors/fin/` e `connectors/stats/`.
- `LazyTools/src/lazytools/connectors/edgar/` — client SEC da portare nel hub (Step 3), poi deprecato.
- `LazyTools/src/lazytools/connectors/marketdata/` — bypass Stooq da deprecare nel profilo finanziario.
- `LazyTools/src/lazytools/safety/gates.py` — `ConfirmationGate`, grant one-shot da riusare (9.2).
- `lazybridge/session.py` — `Session`/`EventLog` con `run_id`, audit da riusare (9.2); boundary test esistenti come pattern per lo Step 7.
- `lazyfin/data/datahub.py` — `DataHubPriceSource`, oggi opt-in via extra `[datacore]`; default esplicito allo Step 5.
- `lazyfin/resolve/`, `scoring/`, `kernel/` — provider da migrare in `lazytools/connectors/fin/` (Step 5); il dominio resta.
- `lazyhmm/tools.py` (§10) e `lazyhmm/datasources/datahub.py` — funzioni già in stile "libreria pura"; confluiscono in `lazystats/models/hmm/` e `lazystats/io/` (Step 6).
- `lazyray/dalio_v2/`, `lazyray/db/` — engine e depot risultati; confluiscono in `lazystats/models/cycles/` e `lazystats/io/depot.py` (Step 6).
- `docs/EXTRACTION.md`, `docs/ARCHITECTURE.md`, `docs/LAZYDATACORE.md`, `docs/DEEP_AUDIT_2026-07.md` — documentazione interna del hub.
