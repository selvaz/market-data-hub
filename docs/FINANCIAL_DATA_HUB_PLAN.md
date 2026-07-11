# Piano di razionalizzazione: financial data hub e tool LLM

**Stato:** proposta da revisionare prima dell'implementazione  
**Owner proposto:** `market-data-hub`  
**Ambito:** dati finanziari, serie storiche, filing e bilanci. Non include email, web generico o tool di coding.

## 1. Decisione proposta

`market-data-hub` diventa l'unico sistema autorizzato a scaricare, aggiornare, normalizzare e servire dati finanziari. Tutti gli altri pacchetti leggono dal hub attraverso API pubbliche read-only.

> Nessun consumer (`LazyTools`, `LazyFin`, `LazyHMM`, `LazyRay` o agente LLM) chiama un provider finanziario esterno. Solo `market-data-hub` puo' farlo. Tutte le letture passano dal reader/extract del hub e dal suo storage versionato.

Questo non significa che un LLM debba ricevere il database o una serie grezza. Il flusso corretto e':

```text
provider esterno -> ingestion del hub -> DuckDB/artifact catalog -> calcolo locale -> risultato compatto -> LLM
```

Le tabelle e i documenti completi restano nel processo del tool. L'agente riceve metadati, metriche, estratti delimitati, identificativi di job e report.

## 2. Assessment dello stato attuale

| Area | Stato attuale | Valutazione | Conseguenza proposta |
|---|---|---|---|
| Prezzi e macro | Il hub ha gia' `reader.py`, `extract.py` e `agent_tools.py`. | Buona base. | Consolidare qui ogni nuovo download di serie storiche. |
| Tool LLM hub | `datahub_*` espone discovery, serie e returns; le serie sono cappate a 500 righe. | Il cap protegge il contesto, ma la API mescola discovery e dati raw. | Profilo LLM standard: discovery + risultati analitici, non matrici raw. |
| Statistiche | `LazyTools.statistical_analysis` legge dal hub e restituisce risultati compatti. | Pattern corretto. | Generalizzarlo a tutti i calcoli quantitativi. |
| Prezzi esterni | `LazyTools.connectors.marketdata` puo' leggere Stooq direttamente. | Bypass del hub. | Non includerlo nei toolset finanziari; deprecarlo per questo uso. |
| EDGAR/bilanci | LazyTools ha un client EDGAR diretto; LazyFin normalizza facts. | Due ingressi esterni, due formati, nessuna storia centralizzata. | Spostare ingestion SEC/EDGAR nel hub. |
| LazyHMM | Ha sia `load_time_series(file_path=...)` sia `load_from_datahub(...)`. | Il secondo e' coerente, il primo e' un bypass potenziale. | Per agenti finanziari consentire solo il loader hub. |
| LazyFin | Puo' leggere prezzi dal hub ma mantiene adapter generici. | Il dominio e' corretto; la sorgente non e' forzata. | Collegarlo a reader/API hub per prezzi e fundamentals. |
| LazyRay | Legge il hub via API e conserva risultati in un DuckDB separato. | Separazione sana: dati vs risultati analitici. | Mantenere il DB risultati, aggiungendo provenance al run. |
| Provenance | `lazydatacore` definisce identita' ed envelope, ma l'adozione e' incompleta. | Incompleto ma recuperabile. | Rendere obbligatori instrument, source, as-of e tool version alle frontiere. |

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

### 3.1 Responsabilita' per repository

| Repository | Responsabilita' target | Non deve fare |
|---|---|---|
| `market-data-hub` | download, writer lock, schema, history, revisioni, coverage, reader/extract e tool semantics | dipendere da LazyTools/LazyBridge o delegare fetch ai consumer |
| `lazydatacore` | identita', tempo, envelope risultati e schemi condivisi | accesso a DuckDB, HTTP o logica di business |
| `LazyTools` | adattatori LLM, limiti di output e safety | download finanziari o storage finanziario proprio |
| `LazyFin` | ledger, facts interpretation, score, rischio, ottimizzazione | fetch SEC/Yahoo/FRED o query SQL alle tabelle hub |
| `LazyHMM` | fit/regime su matrici preparate dal hub, depot risultati | leggere file arbitrari nel profilo finanziario standard |
| `LazyRay` | score/regimi paese derivati, storage dei soli output analitici | duplicare input macro dal hub |
| `LazyBridge` | runtime agentico, sessione, guardrail, tool contract | conoscere fonti o modelli finanziari |

## 4. Principi non negoziabili

1. **Un solo writer per i dati finanziari.** Il writer e' sempre un job del hub sotto lock; i consumer non aprono il DB in scrittura.
2. **Letture da API, non SQL privato.** I consumer chiamano `reader`/`extract` o un backend del hub; non usano tabelle fisiche o provider HTTP.
3. **Il dato raw non entra nel prompt.** Ogni tool LLM ha un budget di output e restituisce un risultato compatto.
4. **Identita' canoniche ai confini.** Input/output usano `ticker:`, `cik:`, `macro:` ecc.; un simbolo bare e' solo comodita' di input, normalizzata subito.
5. **As-of e provenance obbligatori.** Un valore finanziario senza fonte, timestamp e run/filing id non e' riusabile.
6. **Scritture separate dalle letture.** I `get_*` non fanno rete e non mutano. I `ensure_*`/ `request_*` sono capability esplicite, approvate e auditabili.
7. **Storia, non solo ultimo valore.** Prezzi, filing e facts devono poter essere interrogati per data di osservazione e, quando applicabile, data di conoscenza.

## 5. Capacita' nuova: serie storiche single-name

### 5.1 Obiettivo

Un workflow deve poter chiedere una serie non presente nel catalogo curato, ad esempio `ticker:NVDA`, `ticker:ENEL.MI` o un ADR, senza introdurre un client esterno in LazyTools o LazyFin.

Il hub controlla la coverage, scarica solo quando necessario, scrive sotto lock e serve poi la serie dalla propria base.

### 5.2 Registro strumenti proposto

Introdurre un registro di strumenti/alias. Il nome della tabella e' da decidere; qui e' chiamato `instrument_catalog`.

| Campo | Esempio | Nota |
|---|---|---|
| `instrument_id` | `ticker:NVDA` | Identita' lazydatacore canonica |
| `symbol` | `NVDA` | Chiave provider/warehouse |
| `asset_type` | equity, etf, adr, index, fx | `auto` all'ingestion, correggibile |
| `exchange`, `currency`, `country` | NASDAQ, USD, US | Metadata quando disponibili |
| `source_symbol` | NVDA | Mapping provider esplicito |
| `active`, `first_seen_at`, `last_checked_at` | ... | Lifecycle e audit |
| `metadata_json` | ... | Campi non normalizzati, non critici |

Il catalogo curato esistente continua a esistere. Il registro ad hoc non deve trasformare ogni ticker richiesto in un ETF o in un membro permanente dell'universo editoriale.

### 5.3 Tool proposti

Questi tool vanno in un provider separato, ad esempio `DataHubWriteTools`, e non nel profilo read-only normale.

| Tool | Azione | Output verso LLM | Protezioni |
|---|---|---|---|
| `datahub_resolve_instrument` | Risolve ticker/CIK/nome e dice se e' presente. | candidati compatti + coverage | Read-only |
| `datahub_ensure_price_history` | Richiede/avvia ingestion di una serie price single-name. | job id, instrument id, periodo, stato, coverage | writer lock, grant one-shot, rate limit, idempotenza |
| `datahub_get_job_status` | Legge stato e errori sintetici del job. | stato, conteggi, coverage | Read-only |
| `datahub_get_price_summary` | Legge DB e calcola ultimo valore, range, rendimento e coverage. | metriche, non barre OHLCV | budget output fisso |

### 5.4 Contratto proposto per il write tool

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

### 5.5 Casi da gestire

- ticker ambiguo, cambiato, delistato o non supportato;
- share class, ADR e suffissi di borsa;
- currency/exchange non noti;
- storia parziale o buchi;
- split/dividendi e scelta esplicita tra `close` e `adj_close`;
- dati recenti non finalizzati;
- retry e idempotenza quando un job viene rilanciato.

## 6. Capacita' nuova: filing e bilanci SEC/EDGAR

### 6.1 Obiettivo MVP

Copertura affidabile per societa' USA, partendo da `ticker:` o `cik:`, per rendere leggibili dal DB:

- identificazione societa'/CIK;
- filing 10-K, 10-Q e 8-K rilevanti;
- XBRL company facts;
- stato patrimoniale, conto economico e cash flow standardizzati;
- filing date, report date, accession, unit, periodo e fonte;
- revisioni/restatement senza sovrascrivere la storia.

Il primo MVP e' SEC/US GAAP. IFRS, bilanci PDF e transcript sono fasi successive.

### 6.2 Storage proposto

| Entita' | Chiave | Contenuto minimo | Motivo |
|---|---|---|---|
| `sec_entities` | CIK | nome, ticker/alias, SIC, fiscal year end | Risoluzione stabile |
| `sec_filings` | CIK + accession | form, filing date, report date, URL, hash, run id | Provenance del documento |
| `sec_filing_text` | accession | testo estratto/versione, hash, dimensione | Ricerca ed estratti controllati |
| `sec_company_facts` | CIK + concept + unit + period + accession | valore, frame, fiscal period/year, filed date | Facts XBRL preservati |
| `sec_statement_lines` | CIK + statement + line key + period + accession | valore standardizzato, unit, concept sorgente | Bilanci confrontabili |
| `sec_coverage` | CIK/filing family | ultimo filing, lag, forme presenti, status | Qualita' e freshness |
| `ingestion_runs` | run id | source, parametri, esito, timestamp, errore | Audit e retry |

Il testo completo puo' stare nel DuckDB se il volume e' sostenibile. Se si usera' un artifact store per HTML/PDF originali, il DB deve comunque conservare hash, URI, estratto testuale, metadata e retention. Il consumer legge sempre dall'API hub, mai da SEC.

### 6.3 Semantica obbligatoria

Ogni osservazione deve preservare:

- `cik`, `accession_no`, `form`, `filed_at`, `report_date`;
- concept XBRL originale e line key standardizzata;
- unit e scala;
- periodo `instant` o `duration`, data inizio/fine, fiscal year/period;
- fonte, hash payload, data ingestion;
- versione/revisione.

Non e' ammesso un generico "latest revenue" senza periodo e filing. Le viste convenience possono offrire il valore piu' recente, ma devono esporre accession, report date e filed date.

### 6.4 Tool proposti

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

### 6.5 Cosa non esporre alla LLM standard

- companyfacts JSON completo;
- HTML/PDF completo di filing;
- query SQL o nomi di tabelle fisiche;
- download SEC diretto;
- limiti unbounded per record, periodi o caratteri.

## 7. Profilo tool LLM target

### 7.1 financial_research (default, read-only)

| Famiglia | Tool consentiti | Regola |
|---|---|---|
| Discovery | list/search/describe/coverage del hub | Output paginati e filtrati |
| Prezzi | summary e analisi statistiche | Nessuna matrice raw |
| Bilanci | coverage, facts filtrati, statement, summary, filing metadata/extract | Limiti server-side e provenance |
| Portfolio | LazyFin exposure, concentration, drift, risk, optimizer | Deterministici; nessun trade |
| Output | render memo/HTML | Nessuna scrittura file di default |

### 7.2 financial_data_ops (scrittura, separato)

Include soltanto `datahub_ensure_price_history`, `datahub_ensure_financials` e `datahub_get_job_status`.

Requisiti obbligatori:

- `allow_write=True` alla costruzione del provider;
- grant di approvazione one-shot, legato a job/instrument;
- writer lock del hub;
- audit event con autorizzazione, input normalizzato e run id;
- nessun risultato raw, solo stato e coverage;
- rate limit per provider/CIK/ticker;
- retry deterministico e idempotenza.

`datahub_refresh_prices` esistente e' il predecessore funzionale, ma va evoluto: oggi costruisce tickers come `EQUITY` e non esprime bene identita', classificazione, approvazione e job lifecycle di un single-name.

## 8. Piano step-by-step

### Step 0 - Decision record e baseline

1. Approvare i principi della sezione 4.
2. Congelare MVP: prezzi single-name supportati dal provider corrente; filing SEC/US GAAP.
3. Registrare i tool vietati al profilo standard: `prices_get`, `prices_history`, download EDGAR diretto, `load_time_series` da file, `datahub_get_series` e `datahub_get_returns` raw.
4. Definire SLO: freshness EOD, ritardo filing, output massimo, retention raw e policy retry.

**Acceptance:** ADR approvato, inventario tool/profili pubblicato, nessuna modifica funzionale.  
**Rischio:** scope creep.  
**Mitigazione:** MVP US/SEC e un provider prezzi.

### Step 1 - Tool catalog e confini capability

1. Aggiungere un catalogo dichiarativo in LazyTools: owner, data source, read/write, trust, limite output, profilo e lifecycle.
2. Costruire bundle `financial_research` e `financial_data_ops`.
3. Spostare i write tools in provider separati.
4. Aggiungere test contro collisioni di nome e tool non ammessi in un profilo.

**Acceptance:** un agente standard non puo' ricevere fetch esterno o scrittura.  
**Rischio:** compatibilita' agenti esistenti.  
**Mitigazione:** alias deprecati con warning per una release.

### Step 2 - Single-name price ingestion nel hub

1. Disegnare/migrare `instrument_catalog` e `ingestion_runs`, se non esistono equivalenti.
2. Estrarre dall'attuale refresh un job generico per instrument canonico.
3. Implementare controllo coverage e download incrementale.
4. Implementare `datahub_ensure_price_history` e `datahub_get_job_status` in `agent_tools`, poi il mirror LazyTools con test di firma.
5. Implementare `datahub_get_price_summary` come alternativa LLM-safe alle barre raw.

**Acceptance:** da DB vuoto, un `ticker:<single-name>` viene normalizzato, ingestito, coperto e riletto dal DB; una seconda richiesta e' idempotente; il tool non restituisce barre raw.

**Test minimi:** ticker noto/sconosciuto/ambiguo, periodo coperto, gap interno, provider failure, lock concorrente, retry, split/adj-close.

### Step 3 - Ingestion SEC e modello facts/filing

1. Portare nel hub il client SEC con user-agent, throttle, size cap e redirect/host validation.
2. Creare schema SEC, migration, writer e tabelle coverage/run.
3. Implementare resolver `ticker <-> cik` con alias storici.
4. Ingerire prima filing metadata e company facts; aggiungere filing text dopo aver definito retention/chunking.
5. Materializzare le tre viste statement standardizzate e documentare mapping concept XBRL -> line key.
6. Gestire restatement senza sovrascrivere filing/facts precedenti.

**Acceptance:** `ticker:MSFT` e `cik:0000789019` risolvono allo stesso entity; 10-K/10-Q sono persistiti; revenue, assets e operating cash flow hanno unit, periodo, accession e filed date verificabili.

**Rischio principale:** concetti XBRL non uniformi, unit, annual vs quarterly, amendment e filing duplicati.  
**Mitigazione:** raw facts immutabili, mapping versionato e test golden su issuer con casi anomali.

### Step 4 - Reader/extract e tool LLM financials

1. Aggiungere reader pubblici per entity, filing, facts e statement.
2. Aggiungere extract con identita' canoniche, periodi e filtri espliciti.
3. Implementare i `datahub_get_*` della sezione 6.4 con limiti server-side.
4. Rendere ogni output un `AnalysisResult` o envelope equivalente con source/as-of/tool version.
5. Marcare gli estratti filing con `content_is_untrusted=true`.

**Acceptance:** un agente confronta ricavi, margini e leva su periodi specifici senza ricevere XBRL o testo completi.

### Step 5 - Migrazione consumer

1. LazyTools EDGAR diventa adapter deprecato o thin adapter del backend hub; non fa HTTP nel profilo finanziario.
2. LazyFin ResolveTools/ScoringTools leggono facts normalizzati dal hub.
3. DataHubPriceSource diventa default esplicito dei workflow finanziari.
4. LazyHMM espone un provider che usa `load_from_datahub`; file loader resta soltanto notebook/local analysis.
5. LazyRay continua a usare reader pubblici e allega run id/provenance hub ai propri output.

**Acceptance:** test architetturale non trova client Yahoo/SEC/FRED nei consumer finanziari; ogni lettura runtime passa dal hub.

### Step 6 - Enforcement, osservabilita' e deprecazione

1. Test di import/boundary: fuori dal hub sono vietati moduli HTTP finanziari e accesso a tabelle DuckDB private.
2. Test di tool profile: bundle standard senza write/raw/bypass.
3. Test di rete: `datahub_get_*` non fa HTTP; solo `ensure_*` puo' farlo e produce run record.
4. Dashboard coverage: freshness, buchi, errori, ultimo filing, data ingestion.
5. Warning e data rimozione per MarketDataTools e EDGAR diretto dal profilo finanziario.

**Acceptance:** CI blocca un bypass; audit session collega tool call -> run id -> fonte -> as-of.

## 9. Decisioni per la review dell'altra LLM

1. Raw filing text in DuckDB, artifact store o entrambi?
2. Primo writer tool sincrono per ticker/CIK o sempre coda persistita?
3. Provider prezzi e fallback ammessi per single-name?
4. Nuova `instrument_catalog` o estensione del catalogo esistente?
5. Mapping US GAAP minimo per statement MVP?
6. Restatement/as-of: accession, vintage o entrambi?
7. Limiti standard per output LLM: record, caratteri e byte?
8. `datahub_get_series` e `datahub_get_returns`: tool opt-in, Python-only o sostituiti da summary/analysis?
9. Meccanismo di approvazione per `financial_data_ops`?
10. Estrarre `lazydatacore` in pacchetto installabile prima di renderlo obbligatorio?

## 10. Criteri go/no-go

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

- un consumer puo' ancora scaricare dati finanziari direttamente;
- un tool read-only fa refresh implicito o scrive;
- l'agente riceve documenti, facts o serie completi;
- un valore financial non e' riconducibile a fonte e periodo;
- writer non serializzato o non idempotente.

## 11. Ordine raccomandato

1. Step 0 e Step 1: decisioni e profili.
2. Step 2: single-name prices, come vertical slice completo.
3. Step 3: SEC metadata e company facts, senza filing text.
4. Step 4: reader/tool facts e statement.
5. Step 5: migrazione LazyTools/LazyFin/LazyHMM.
6. Step 6: enforcement e deprecazioni.
7. Filing text/chunking, IFRS e provider aggiuntivi soltanto dopo il MVP.

Questo ordine riduce il rischio: il vertical slice price dimostra writer, coverage, approval, tool output e reader DB prima della complessita' contabile dei filing.

## 12. Riferimenti interni

- `docs/EXTRACTION.md` - API discovery/extract e contratti agent.
- `docs/ARCHITECTURE.md` - writer lock, schema e reader pubblici.
- `docs/LAZYDATACORE.md` - identita' e AnalysisResult.
- `docs/DEEP_AUDIT_2026-07.md` - audit precedente; verificare le parti superate contro il codice corrente.
- `market_data_hub/agent_tools.py` - semantica tool corrente.
- `LazyTools/src/lazytools/connectors/datahub/` - bridge LazyBridge e test di parita' firme.
