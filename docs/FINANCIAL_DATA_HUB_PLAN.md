# Piano di razionalizzazione: financial data hub e tool LLM — v2

**Stato:** proposta aggiornata, pronta per review esterna prima dell'implementazione.
**Owner proposto:** `market-data-hub`
**Ambito:** dati finanziari, serie storiche, filing e bilanci. Non include email, web generico o tool di coding.

**Novità della v2:** ogni affermazione sullo stato attuale è stata verificata contro il codice reale dei repository (commit indicati in sezione 2). Rispetto alla v1: corretti due errori fattuali (il tool `statistical_analysis` non esiste; `lazydatacore` non è un pacchetto condiviso ma vive solo dentro il hub), l'estrazione di `lazydatacore` è stata promossa da "decisione aperta" a prerequisito dello Step 1, e lo Step 5 è stato riscritto con i task di migrazione reali che la v1 dava per già fatti. Le decisioni aperte per il reviewer sono in sezione 10.

---

## 1. Decisione proposta

`market-data-hub` diventa l'unico sistema autorizzato a scaricare, aggiornare, normalizzare e servire dati finanziari. Tutti gli altri pacchetti leggono dal hub attraverso API pubbliche read-only.

> Nessun consumer (`LazyTools`, `LazyFin`, `LazyHMM`, `LazyRay` o agente LLM) chiama un provider finanziario esterno. Solo `market-data-hub` può farlo. Tutte le letture passano dal reader/extract del hub e dal suo storage versionato.

Questo non significa che un LLM debba ricevere il database o una serie grezza. Il flusso corretto è:

```text
provider esterno -> ingestion del hub -> DuckDB/artifact catalog -> calcolo locale -> risultato compatto -> LLM
```

Le tabelle e i documenti completi restano nel processo del tool. L'agente riceve metadati, metriche, estratti delimitati, identificativi di job e report.

## 2. Stato attuale verificato

Verifica effettuata sul codice a questi commit:

| Repository | Commit verificato |
|---|---|
| `market-data-hub` | `b25f41c7` |
| `LazyTools` | `3edb561a` |
| `LazyFin` | `ff6af8f` |
| `LazyHMM` | `d8e46028` |
| `LazyRay` | `a4b8cb5c` |

### 2.1 Cosa esiste già ed è solido

| Fatto verificato | Dove | Conseguenza per il piano |
|---|---|---|
| Layer read-only completo: `read_prices`, `read_macro`, `read_custom`, `read_crypto`, `read_macro_panel(_ext)`, `read_factors`, `read_instrument`, più `get_coverage`/`get_latest`/`get_stalled` | `market_data_hub/reader.py` | Base pronta per i nuovi reader SEC e single-name |
| Layer analysis-ready: `extract_series`, `extract_returns`, `extract_macro`, `extract_panel` con trasformazioni (level/log_return/pct_change/diff) e resampling D/W/M/Q | `market_data_hub/extract.py` | Da estendere, non da creare |
| Shim LLM con discovery (`tool_list_*`, `tool_describe`, `tool_search`) ed estrazione (`tool_get_series`, `tool_get_returns`, `tool_get_coverage`) | `market_data_hub/agent_tools.py` | Il profilo LLM target parte da qui |
| Cap serie a 500 righe con flag `truncated` e `meta.n_rows` | `agent_tools.py`, `_MAX_ROWS = 500` | Il pattern di output budget esiste già; va generalizzato |
| Writer lock cross-process via `filelock` con `DBLockTimeout`, usato dal runner schedulato e da `tool_refresh_prices` | `market_data_hub/lock.py` | Il principio "un solo writer" ha già le fondamenta |
| Audit trail: `download_log` (una riga per simbolo per `run_id`), `coverage_report`, `macro_panel_coverage` | `market_data_hub/db/schema.sql` | Base per `ingestion_runs`; forse basta estenderla |
| Tabelle point-in-time `macro_series_vintage` / `macro_panel_vintage` | `db/schema.sql` | Il pattern vintage/as-of esiste per i macro; da replicare per i facts SEC |
| Bridge LazyTools -> hub: `DataHubBackend` (Protocol) + `MarketDataHubBackend`, tool `datahub_*` (list/describe/search/get_series/get_returns/get_coverage + `datahub_refresh_prices` gated), con test di parità firme (`test_backend_protocol_matches_mdh_tool_signatures`) e round-trip live | `LazyTools/src/lazytools/connectors/datahub/` | Il pattern mirror-con-parity-test è il modello per tutti i nuovi tool |
| LazyRay legge il hub solo via API pubblica (`reader.read_macro_panel_ext`), mai SQL diretto, e tiene i risultati in un DuckDB proprio (`lazyray/db/`) | `lazyray/dalio.py`, `lazyray/dalio_v2/runner.py`, `lazyray/db/connection.py` | Consumer già conforme al target; manca solo la provenance (vedi 2.3) |
| LazyFin ha `ResolveTools`, `ScoringTools`, `RiskTools`, `PortfolioTools`, `OptimizerTools`, `PortfolioLedger` e nessun codice HTTP/fetch proprio (EDGAR e prezzi arrivano da client iniettati via Protocol) | `lazyfin/resolve/`, `lazyfin/scoring/`, `lazyfin/kernel/` | Il dominio è già pulito; va solo forzata la sorgente |
| Grant one-shot esistenti: `ConfirmationGate` (grant legati a target+scope, consumati all'uso, nessuna approvazione sticky) e `Allowlist` | `LazyTools/src/lazytools/safety/gates.py`, `safety/allowlist.py` | Il meccanismo di approvazione per i write tool si compone da qui, non si costruisce da zero |
| Audit runtime esistente: `Session`/`EventLog` SQLite con `run_id` su ogni evento, tipi `TOOL_CALL`/`TOOL_RESULT`/`TOOL_ERROR`/`TOOL_TIMEOUT`, redazione segreti, exporter pluggabili | `lazybridge/session.py` | L'audit richiesto in sezione 8.2 esiste già a livello runtime |
| Filtri capability per-connector (allow/deny fnmatch, deny-by-default) | `lazybridge` MCP (`SECURITY.md`) | Base per i profili, ma non è un sistema di bundle (vedi 2.3) |

### 2.2 Bypass confermati (il problema che il piano risolve)

| Bypass | Dove | Azione |
|---|---|---|
| `StooqAdapter`/`MarketDataClient`/`MarketDataTools` (`prices_get`, `prices_history`) chiamano stooq.com direttamente, nessuna dipendenza dal hub | `LazyTools/src/lazytools/connectors/marketdata/` | Escludere dai toolset finanziari; deprecare per questo uso |
| `EdgarClient`/`EdgarTools` (`edgar_resolve_company`, `edgar_list_filings`, `edgar_get_filing`, `edgar_company_facts`) parlano direttamente con sec.gov/data.sec.gov | `LazyTools/src/lazytools/connectors/edgar/` | Portare l'ingestion SEC nel hub (Step 3); il client LazyTools diventa adapter deprecato |
| `load_time_series(file_path=...)` è l'unico loader esposto come LLM tool in LazyHMM; carica CSV/Excel/Parquet da path arbitrario | `lazyhmm/tools.py:638` (sezione "§10 LLM TOOL API") | Nel profilo finanziario standard va sostituito dal loader hub (vedi 2.3) |
| `tool_refresh_prices` costruisce i ticker come `{"symbol": s, "asset_class": "EQUITY", ...}` hardcoded: nessuna identità, classificazione o lifecycle per-strumento | `market_data_hub/agent_tools.py` | Predecessore funzionale di `datahub_ensure_price_history`; da evolvere (Step 2) |

### 2.3 Correzioni rispetto alla v1 (affermazioni non verificate o sovrastimate)

Queste correzioni cambiano stime e ordine del piano; il reviewer deve tenerne conto.

1. **`LazyTools.statistical_analysis` non esiste.** La v1 lo citava come "pattern corretto da generalizzare". Nessun tool con quel nome (o simile) esiste in LazyTools. Il pattern "leggi dal hub, calcola nel tool, restituisci risultato compatto" resta l'obiettivo, ma non ha oggi un'implementazione di riferimento in LazyTools: la più vicina è `tool_get_coverage`/`extract_*` nel hub stesso.
2. **`lazydatacore` non è un pacchetto condiviso.** Vive esclusivamente dentro `market-data-hub` (`market_data_hub/lazydatacore/`, con `identity.py` che definisce `InstrumentId`). Nessun altro repository lo importa: adozione esterna zero, non "incompleta". I namespace `cik:`/`isin:` sono riconosciuti sintatticamente ma `reader.py` conferma che sollevano `NotResolvableError`. Conseguenza: l'estrazione in pacchetto installabile è un **prerequisito** (Step 1), non una decisione rinviabile.
3. **`load_from_datahub` in LazyHMM esiste ma non è un LLM tool.** È definito in `lazyhmm/datasources/datahub.py:59`, scrive lo stesso payload `{"Y","columns","index"}` via `_swrite` ed è quindi già compatibile con `fit_regimes`. Ma né la docstring §10 né `docs/api-tools.md` lo elencano tra i tool wrappabili: oggi solo `load_time_series` (file) è esposto agli agenti. "Consentire solo il loader hub" è lavoro di implementazione, non un toggle.
4. **L'integrazione hub in LazyFin è opt-in, non default.** `DataHubPriceSource` (`lazyfin/data/datahub.py`) si attiva solo con l'extra `[datacore]`; il workflow accetta qualunque oggetto con `prices_get` e la docstring indica esplicitamente `MarketDataClient` di LazyTools come alternativa. Rendere il hub "default esplicito" richiede modifiche a codice e dipendenze.
5. **La provenance di LazyRay verso il hub è più debole del dichiarato.** Gli output hanno `computed_at` e un `model_version` che è lo SHA del *proprio* codice; nessun campo cattura il `run_id` o il vintage dei dati hub usati. Il principio 5 (as-of/provenance) non è soddisfatto end-to-end da nessun consumer oggi.
6. **Nessun codice SEC nel hub.** `market_data_hub/sources/` contiene yahoo, binance, fred, worldbank, imf, imf_sdmx, bis, ecb, factors. Zero riferimenti a SEC/EDGAR/CIK. Lo Step 3 è un porting completo del client da LazyTools più tutto il modello dati: è lo step a maggior rischio di stima.
7. **Non esiste un sistema di tool profile/bundle.** Né in LazyTools né in LazyBridge c'è un registro dichiarativo (owner, read/write, trust, limiti output, profilo). Esistono i mattoni (`ConfirmationGate`, `Allowlist`, `EventLog` con `run_id`, filtri allow/deny per-connector) ma la composizione in bundle nominati va costruita. Manca del tutto il rate limiting.

## 3. Confini architetturali target

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
     |          |             |
LazyTools     LazyFin     LazyHMM/LazyRay
tool LLM      risk/score  analisi e risultati
```

### 3.1 Responsabilità per repository

| Repository | Responsabilità target | Non deve fare |
|---|---|---|
| `market-data-hub` | download, writer lock, schema, history, revisioni, coverage, reader/extract e tool semantics | dipendere da LazyTools/LazyBridge o delegare fetch ai consumer |
| `lazydatacore` (da estrarre, oggi dentro il hub) | identità, tempo, envelope risultati e schemi condivisi | accesso a DuckDB, HTTP o logica di business |
| `LazyTools` | adattatori LLM, limiti di output e safety | download finanziari o storage finanziario proprio |
| `LazyFin` | ledger, facts interpretation, score, rischio, ottimizzazione | fetch SEC/Yahoo/FRED o query SQL alle tabelle hub |
| `LazyHMM` | fit/regime su matrici preparate dal hub, depot risultati | leggere file arbitrari nel profilo finanziario standard |
| `LazyRay` | score/regimi paese derivati, storage dei soli output analitici | duplicare input macro dal hub |
| `LazyBridge` | runtime agentico, sessione, guardrail, tool contract | conoscere fonti o modelli finanziari |

## 4. Principi non negoziabili

1. **Un solo writer per i dati finanziari.** Il writer è sempre un job del hub sotto lock (`market_data_hub/lock.py` già esistente); i consumer non aprono il DB in scrittura.
2. **Letture da API, non SQL privato.** I consumer chiamano `reader`/`extract` o un backend del hub; non usano tabelle fisiche o provider HTTP. (LazyRay è già conforme; è il modello.)
3. **Il dato raw non entra nel prompt.** Ogni tool LLM ha un budget di output e restituisce un risultato compatto. (Il cap `_MAX_ROWS = 500` è il precedente; il target per i nuovi tool è "metriche, non matrici".)
4. **Identità canoniche ai confini.** Input/output usano `ticker:`, `cik:`, `macro:` ecc.; un simbolo bare è solo comodità di input, normalizzata subito via `lazydatacore.identity.InstrumentId`.
5. **As-of e provenance obbligatori.** Un valore finanziario senza fonte, timestamp e run/filing id non è riusabile. Include la propagazione del `run_id` hub negli output dei consumer (oggi assente ovunque, vedi 2.3.5).
6. **Scritture separate dalle letture.** I `get_*` non fanno rete e non mutano. I `ensure_*`/`request_*` sono capability esplicite, approvate e auditabili.
7. **Storia, non solo ultimo valore.** Prezzi, filing e facts devono poter essere interrogati per data di osservazione e, quando applicabile, data di conoscenza (pattern già presente nelle tabelle `*_vintage`).

## 5. Prerequisito trasversale: estrazione di `lazydatacore`

Promosso da decisione aperta (v1, domanda 10) a prerequisito, perché gli Step 4 e 5 richiedono che i consumer importino identità ed envelope condivisi, e oggi nessun consumer può farlo.

1. Estrarre `market_data_hub/lazydatacore/` in pacchetto installabile separato (repo o subdirectory pubblicata, da decidere — domanda 10.10).
2. Contenuto minimo v0: `InstrumentId` e namespace (`ticker:`, `cik:`, `macro:`, `isin:`, `fx:`, `index:`), tipi tempo/as-of, envelope `AnalysisResult` (source, as_of, tool_version, provenance/run_id).
3. Il hub diventa il primo consumer del pacchetto estratto (dipendenza invertita, senza cambi di comportamento).
4. Nessun consumer è obbligato ad adottarlo in questo step; l'obbligo scatta alle frontiere toccate dagli Step 4-5.

**Acceptance:** `pip install lazydatacore` (o equivalente) funziona in un ambiente pulito; il hub passa i test usando il pacchetto esterno; nessun cambiamento funzionale.

## 6. Capacità nuova: serie storiche single-name

### 6.1 Obiettivo

Un workflow deve poter chiedere una serie non presente nel catalogo curato, ad esempio `ticker:NVDA`, `ticker:ENEL.MI` o un ADR, senza introdurre un client esterno in LazyTools o LazyFin.

Il hub controlla la coverage, scarica solo quando necessario, scrive sotto lock e serve poi la serie dalla propria base.

### 6.2 Registro strumenti proposto

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

### 6.3 Tool proposti

Questi tool vanno in un provider separato, ad esempio `DataHubWriteTools`, e non nel profilo read-only normale.

| Tool | Azione | Output verso LLM | Protezioni |
|---|---|---|---|
| `datahub_resolve_instrument` | Risolve ticker/CIK/nome e dice se è presente. | candidati compatti + coverage | Read-only |
| `datahub_ensure_price_history` | Richiede/avvia ingestion di una serie price single-name. | job id, instrument id, periodo, stato, coverage | writer lock, grant one-shot, rate limit, idempotenza |
| `datahub_get_job_status` | Legge stato e errori sintetici del job. | stato, conteggi, coverage | Read-only |
| `datahub_get_price_summary` | Legge DB e calcola ultimo valore, range, rendimento e coverage. | metriche, non barre OHLCV | budget output fisso |

`tool_refresh_prices` esistente è il predecessore funzionale ma va evoluto: oggi hardcoda `asset_class: "EQUITY"` per ogni simbolo e non esprime identità, classificazione, approvazione né job lifecycle.

### 6.4 Contratto proposto per il write tool

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

Non restituisce le 4.150 righe. Una successiva analisi usa statistiche, HMM o summary server-side.

### 6.5 Casi da gestire

- ticker ambiguo, cambiato, delistato o non supportato;
- share class, ADR e suffissi di borsa;
- currency/exchange non noti;
- storia parziale o buchi;
- split/dividendi e scelta esplicita tra `close` e `adj_close`;
- dati recenti non finalizzati;
- retry e idempotenza quando un job viene rilanciato.

## 7. Capacità nuova: filing e bilanci SEC/EDGAR

**Nota di stima:** nel hub non esiste alcun codice SEC (verificato: `market_data_hub/sources/` non ha alcuna sorgente SEC; i namespace `cik:` sollevano `NotResolvableError`). Il client esistente è in `LazyTools/src/lazytools/connectors/edgar/` (throttling fair-access, User-Agent obbligatorio, host pinning già implementati): la logica di trasporto si porta, il modello dati si costruisce da zero. Questo è lo step a maggior rischio del piano.

### 7.1 Obiettivo MVP

Copertura affidabile per società USA, partendo da `ticker:` o `cik:`, per rendere leggibili dal DB:

- identificazione società/CIK;
- filing 10-K, 10-Q e 8-K rilevanti;
- XBRL company facts;
- stato patrimoniale, conto economico e cash flow standardizzati;
- filing date, report date, accession, unit, periodo e fonte;
- revisioni/restatement senza sovrascrivere la storia.

Il primo MVP è SEC/US GAAP. IFRS, bilanci PDF e transcript sono fasi successive.

### 7.2 Storage proposto

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

### 7.3 Semantica obbligatoria

Ogni osservazione deve preservare:

- `cik`, `accession_no`, `form`, `filed_at`, `report_date`;
- concept XBRL originale e line key standardizzata;
- unit e scala;
- periodo `instant` o `duration`, data inizio/fine, fiscal year/period;
- fonte, hash payload, data ingestion;
- versione/revisione.

Non è ammesso un generico "latest revenue" senza periodo e filing. Le viste convenience possono offrire il valore più recente, ma devono esporre accession, report date e filed date.

### 7.4 Tool proposti

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

### 7.5 Cosa non esporre alla LLM standard

- companyfacts JSON completo;
- HTML/PDF completo di filing;
- query SQL o nomi di tabelle fisiche;
- download SEC diretto;
- limiti unbounded per record, periodi o caratteri.

## 8. Profilo tool LLM target

### 8.1 financial_research (default, read-only)

| Famiglia | Tool consentiti | Regola |
|---|---|---|
| Discovery | list/search/describe/coverage del hub | Output paginati e filtrati |
| Prezzi | summary e analisi statistiche | Nessuna matrice raw |
| Bilanci | coverage, facts filtrati, statement, summary, filing metadata/extract | Limiti server-side e provenance |
| Portfolio | LazyFin exposure, concentration, drift, risk, optimizer | Deterministici; nessun trade |
| Output | render memo/HTML | Nessuna scrittura file di default |

### 8.2 financial_data_ops (scrittura, separato)

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

## 9. Piano step-by-step

### Step 0 — Decision record e baseline

1. Approvare i principi della sezione 4.
2. Congelare MVP: prezzi single-name supportati dal provider corrente; filing SEC/US GAAP.
3. Registrare i tool vietati al profilo standard: `prices_get`, `prices_history` (MarketDataTools), `edgar_*` diretti (EdgarTools), `load_time_series` da file (LazyHMM §10), `datahub_get_series` e `datahub_get_returns` raw.
4. Definire SLO: freshness EOD, ritardo filing, output massimo, retention raw e policy retry.

**Acceptance:** ADR approvato, inventario tool/profili pubblicato, nessuna modifica funzionale.
**Rischio:** scope creep. **Mitigazione:** MVP US/SEC e un provider prezzi.

### Step 1 — lazydatacore, tool catalog e confini capability

1. **Estrarre `lazydatacore` in pacchetto installabile** (sezione 5). Prerequisito per tutto ciò che segue: oggi nessun consumer può importare identità o envelope.
2. Aggiungere un catalogo dichiarativo in LazyTools: owner, data source, read/write, trust, limite output, profilo e lifecycle. (Da zero: non esiste; i mattoni sono `ConfirmationGate`, `Allowlist`, filtri MCP allow/deny.)
3. Costruire bundle `financial_research` e `financial_data_ops`, componendo `ConfirmationGate` + `EventLog` invece di reimplementarli.
4. Spostare i write tools in provider separati.
5. Aggiungere test contro collisioni di nome e tool non ammessi in un profilo.

**Acceptance:** `lazydatacore` installabile e usato dal hub; un agente standard non può ricevere fetch esterno o scrittura.
**Rischio:** compatibilità agenti esistenti. **Mitigazione:** alias deprecati con warning per una release.

### Step 2 — Single-name price ingestion nel hub

1. Disegnare e migrare `instrument_catalog`; decidere se `ingestion_runs` è una tabella nuova o un'estensione di `download_log` esistente.
2. Estrarre da `tool_refresh_prices` un job generico per instrument canonico (eliminando l'hardcode `asset_class: "EQUITY"`).
3. Implementare controllo coverage e download incrementale (riusando `get_coverage`/`coverage_report`).
4. Implementare `datahub_ensure_price_history` e `datahub_get_job_status` in `agent_tools.py`, poi il mirror in `LazyTools/connectors/datahub/` estendendo il test di parità firme esistente.
5. Implementare `datahub_get_price_summary` come alternativa LLM-safe alle barre raw.

**Acceptance:** da DB vuoto, un `ticker:<single-name>` viene normalizzato, ingestito, coperto e riletto dal DB; una seconda richiesta è idempotente; il tool non restituisce barre raw.

**Test minimi:** ticker noto/sconosciuto/ambiguo, periodo coperto, gap interno, provider failure, lock concorrente (riusare `DBLockTimeout`), retry, split/adj-close.

### Step 3 — Ingestion SEC e modello facts/filing

1. Portare nel hub il client SEC da `LazyTools/connectors/edgar/` (throttle, User-Agent, host pinning e size cap sono già implementati lì; il porting è trasporto + integrazione con lock e run tracking del hub).
2. Creare schema SEC (tabelle 7.2), migration, writer sotto `db_write_lock()` e coverage/run.
3. Implementare resolver `ticker <-> cik` con alias storici; rimuovere il `NotResolvableError` per il namespace `cik:` in `reader.py`.
4. Ingerire prima filing metadata e company facts; filing text dopo aver definito retention/chunking.
5. Materializzare le tre viste statement standardizzate e documentare mapping concept XBRL -> line key.
6. Gestire restatement senza sovrascrivere filing/facts precedenti (pattern `*_vintage` già in produzione per i macro).

**Acceptance:** `ticker:MSFT` e `cik:0000789019` risolvono allo stesso entity; 10-K/10-Q persistiti; revenue, assets e operating cash flow hanno unit, periodo, accession e filed date verificabili.

**Rischio principale:** concetti XBRL non uniformi, unit, annual vs quarterly, amendment e filing duplicati. È lo step a maggior incertezza di stima (parte da zero nel hub).
**Mitigazione:** raw facts immutabili, mapping versionato e test golden su issuer con casi anomali.

### Step 4 — Reader/extract e tool LLM financials

1. Aggiungere reader pubblici per entity, filing, facts e statement (stesso stile di `reader.py` esistente).
2. Aggiungere extract con identità canoniche, periodi e filtri espliciti.
3. Implementare i `datahub_get_*` della sezione 7.4 con limiti server-side (generalizzando il pattern `_MAX_ROWS`/`truncated`).
4. Rendere ogni output un `AnalysisResult` del pacchetto `lazydatacore` estratto, con source/as-of/tool version/run id.
5. Marcare gli estratti filing con `content_is_untrusted=true`.

**Acceptance:** un agente confronta ricavi, margini e leva su periodi specifici senza ricevere XBRL o testo completi.

### Step 5 — Migrazione consumer

Riscritto rispetto alla v1: questi task erano dati per quasi fatti, ma la verifica mostra che sono tutti lavoro reale.

1. **LazyTools**: EdgarTools diventa adapter deprecato o thin adapter del backend hub; nessun HTTP nel profilo finanziario. MarketDataTools escluso dai bundle finanziari con warning di deprecazione.
2. **LazyFin**: aggiungere la dipendenza hub come default dei workflow finanziari (oggi è extra opzionale `[datacore]`): `DataHubPriceSource` default esplicito, `ResolveTools`/`ScoringTools` leggono facts normalizzati dal hub invece che da `EdgarClientLike` iniettato. Il Protocol resta per i test.
3. **LazyHMM**: **wrappare `load_from_datahub` come LLM tool** (oggi non lo è: solo `load_time_series` è nella sezione §10) e rimuovere `load_time_series` dal profilo finanziario standard; resta disponibile per notebook/analisi locale. Aggiornare `docs/api-tools.md`.
4. **LazyRay**: **aggiungere la propagazione della provenance hub** — gli output (`dalio_signals`, `pillar_scores`, `regime_state`, ...) devono registrare il `run_id`/vintage dei dati hub usati, non solo `computed_at` e lo SHA del proprio codice.
5. **Tutti i consumer**: adottare `lazydatacore` estratto alle frontiere (identità in input, `AnalysisResult` in output).

**Acceptance:** test architetturale non trova client Yahoo/SEC/FRED nei consumer finanziari; ogni lettura runtime passa dal hub; un output LazyRay/LazyHMM è riconducibile al run di ingestion hub che ha prodotto i suoi input.

### Step 6 — Enforcement, osservabilità e deprecazione

1. Test di import/boundary: fuori dal hub sono vietati moduli HTTP finanziari e accesso a tabelle DuckDB private (LazyBridge ha già boundary test dello stesso tipo verso LazyTools: riusare il pattern).
2. Test di tool profile: bundle standard senza write/raw/bypass.
3. Test di rete: `datahub_get_*` non fa HTTP; solo `ensure_*` può farlo e produce run record.
4. Dashboard coverage: freshness, buchi, errori, ultimo filing, data ingestion (base: `coverage_report` e `v_stalled` esistenti).
5. Warning e data di rimozione per MarketDataTools e EdgarTools dal profilo finanziario.

**Acceptance:** CI blocca un bypass; audit session collega tool call -> run id -> fonte -> as-of.

## 10. Decisioni per la review

1. Raw filing text in DuckDB, artifact store o entrambi?
2. Primo writer tool sincrono per ticker/CIK o sempre coda persistita?
3. Provider prezzi e fallback ammessi per single-name?
4. Nuova `instrument_catalog` o estensione del catalogo YAML esistente (che oggi è config statica, non DB)?
5. Mapping US GAAP minimo per statement MVP?
6. Restatement/as-of: accession, vintage (pattern `*_vintage` esistente) o entrambi?
7. Limiti standard per output LLM: record, caratteri e byte? (Oggi solo `_MAX_ROWS = 500` sulle serie.)
8. `datahub_get_series` e `datahub_get_returns`: tool opt-in, Python-only o sostituiti da summary/analysis?
9. Meccanismo di approvazione per `financial_data_ops`: basta comporre `ConfirmationGate` + `EventLog`, o serve un livello di grant persistente/asincrono per job lunghi?
10. Dove vive `lazydatacore` estratto: repo dedicato o pubblicazione dalla subdirectory del hub? (L'estrazione in sé non è più in discussione: è prerequisito dello Step 1.)
11. `ingestion_runs`: tabella nuova o evoluzione di `download_log`?
12. La provenance hub nei consumer (Step 5.4): `run_id` per riga di output, per run analitico, o snapshot id per sessione?

## 11. Criteri go/no-go

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

### No-go

- un consumer può ancora scaricare dati finanziari direttamente;
- un tool read-only fa refresh implicito o scrive;
- l'agente riceve documenti, facts o serie completi;
- un valore financial non è riconducibile a fonte e periodo;
- writer non serializzato o non idempotente;
- un output analitico consumer non è riconducibile al run hub dei suoi input.

## 12. Ordine raccomandato

1. Step 0 e Step 1: decisioni, estrazione `lazydatacore`, profili.
2. Step 2: single-name prices, come vertical slice completo.
3. Step 3: SEC metadata e company facts, senza filing text.
4. Step 4: reader/tool facts e statement.
5. Step 5: migrazione LazyTools/LazyFin/LazyHMM/LazyRay.
6. Step 6: enforcement e deprecazioni.
7. Filing text/chunking, IFRS e provider aggiuntivi soltanto dopo il MVP.

Questo ordine riduce il rischio: il vertical slice price dimostra writer, coverage, approval, tool output e reader DB prima della complessità contabile dei filing. L'estrazione di `lazydatacore` sta all'inizio perché gli Step 4-5 la richiedono e ritardarla forzerebbe rework alle frontiere.

## 13. Riferimenti verificati

Componenti citati nel piano, con posizione confermata nel codice:

- `market_data_hub/reader.py`, `extract.py`, `agent_tools.py` — layer read-only, extract e tool LLM correnti (cap `_MAX_ROWS = 500`).
- `market_data_hub/lock.py` — `db_write_lock()` via filelock, `DBLockTimeout`.
- `market_data_hub/db/schema.sql` — `prices_daily`, `macro_series(_vintage)`, `macro_panel(_vintage)`, `download_log`, `coverage_report`, viste `v_returns`/`v_stalled`.
- `market_data_hub/lazydatacore/identity.py` — `InstrumentId`; da estrarre (Step 1). Namespace `cik:` oggi non risolvibile.
- `LazyTools/src/lazytools/connectors/datahub/` — bridge, tool `datahub_*`, test parità firme.
- `LazyTools/src/lazytools/connectors/edgar/` — client SEC da portare nel hub (Step 3).
- `LazyTools/src/lazytools/connectors/marketdata/` — bypass Stooq da deprecare nel profilo finanziario.
- `LazyTools/src/lazytools/safety/gates.py` — `ConfirmationGate`, grant one-shot da riusare (8.2).
- `lazybridge/session.py` — `Session`/`EventLog` con `run_id`, audit da riusare (8.2).
- `lazyfin/data/datahub.py` — `DataHubPriceSource`, oggi opt-in via extra `[datacore]`.
- `lazyhmm/tools.py` (§10) e `lazyhmm/datasources/datahub.py` — `load_time_series` (LLM tool, file) vs `load_from_datahub` (non ancora LLM tool).
- `lazyray/dalio_v2/`, `lazyray/db/` — consumer API-only con DB risultati separato; provenance hub da aggiungere.
- `docs/EXTRACTION.md`, `docs/ARCHITECTURE.md`, `docs/LAZYDATACORE.md`, `docs/DEEP_AUDIT_2026-07.md` — documentazione interna del hub.
