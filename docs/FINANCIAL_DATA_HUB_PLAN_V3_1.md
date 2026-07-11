# Piano v3.1 — Financial Data Hub, domini e bridge LLM

**Stato:** proposta aggiornata per review architetturale e implementazione incrementale.  
**Owner dei dati:** `market-data-hub`.  
**Ambito:** prezzi e serie storiche single-name, dati SEC/EDGAR e bilanci, analytics finance/statistica e relativa esposizione agli agenti.

## 1. Decisioni approvate

1. `market-data-hub` è l'unico componente autorizzato a scaricare, normalizzare, versionare e servire dati finanziari. I consumer non chiamano provider finanziari esterni.
2. `LazyTools` è l'unico bridge LLM: catalogo, wrapping, limiti di output, profili, trust e gating delle scritture vivono qui.
3. `LazyFin` e `LazyStats` sono librerie Python pure. Devono essere utilizzabili da notebook e servizi senza importare `lazybridge`.
4. I dati completi restano nel processo del tool. L'agente riceve solo risultati, metadati, estratti strettamente limitati e identificativi di job/provenance.
5. I tool di lettura non fanno rete e non mutano lo stato. Le ingestion sono capability `ensure_*` esplicite, separate e auditabili.
6. Consolidare LazyHMM e LazyRay in `LazyStats` è un obiettivo di fine percorso, non un prerequisito per prezzi o bilanci.

## 2. Correzione fondamentale: issuer, instrument e listing sono distinti

Un CIK identifica un **emittente**; un ticker identifica una **quotazione**. Non sono la stessa entità: un emittente può avere più classi azionarie, ADR, listing e ticker storici.

```text
issuer (CIK, società legale)
  └── instrument (titolo economico)
        └── listing (ticker, borsa, valuta, provider symbol)

SEC facts / filing / statement  -> issuer
prezzi / corporate actions      -> listing o instrument
```

Il hub deve quindi introdurre questi concetti, senza usare `ticker:` come identità universale:

| Entità | Chiave proposta | Responsabilità |
|---|---|---|
| `issuers` | `issuer_id`, CIK nullable/unico | società, alias legali, fiscal year end, SIC |
| `instruments` | `instrument_id` | security economica, tipo, issuer di riferimento |
| `listings` | `listing_id` | simbolo, MIC/exchange, valuta, provider/source symbol, lifecycle |
| `identifier_aliases` | namespace + valore + validità temporale | ticker storico, ISIN, FIGI, CIK e mapping |
| `instrument_catalog` | `instrument_id`/`listing_id` | metadati operativi e coverage di strumenti richiesti on-demand |

Input umano come `NVDA` resta ammesso come comodità, ma `datahub_resolve_instrument` deve chiedere o restituire candidati se exchange, valuta o classe non sono determinabili. I tool restituiscono sia `listing_id`/`instrument_id` sia l'eventuale `issuer_id`.

## 3. Architettura target

```text
Provider esterni (Yahoo/FRED/SEC/altri)
                 |
                 | solo job di ingestion del hub
                 v
market-data-hub
  service API + writer lock + DuckDB + artifact catalog + run ledger
                 |
                 | reader/extract read-only + snapshot/provenance
        +--------+---------+
        |                  |
     LazyFin            LazyStats
  dominio puro       core + modelli puri
        |                  |
        +--------+---------+
                 v
             LazyTools
 catalogo LLM, wrapper, profili, safety, budget output
                 |
            LazyBridge / LLM
```

### 3.1 Confine API corretto

La semantica pubblica deve vivere nel hub, non in `agent_tools.py`. Ad esempio:

- `services.prices.ensure_history(...)`
- `services.financials.ensure_filings_and_facts(...)`
- `jobs.get_status(...)`
- `reader.get_statement(...)`
- `extract.return_matrix(...)`

CLI, notebook, worker e adapter LLM invocano le stesse funzioni. `agent_tools.py` e `LazyTools/connectors/datahub` sono adattatori sottili, con contratti e budget specifici LLM.

### 3.2 Regola DB e sua applicazione

"Si legge dal hub" è un contratto architetturale; con un file DuckDB locale non è da solo un confine di sicurezza. Per l'enforcement forte, il DB e gli artifact devono essere accessibili soltanto al processo/servizio hub, mentre i consumer ricevono API reader con credenziali read-only. Nel MVP locale, CI e API pubbliche impongono il contratto; l'accesso SQL privato resta vietato per convenzione e test di boundary.

## 4. Modello di dati e provenance

### 4.1 Prezzi e job

| Tabella/concetto | Minimo richiesto |
|---|---|
| `ingestion_runs` | `run_id`, tipo, input normalizzato, provider, stato, tentativi, errori, timestamp, hash/versione payload |
| `ingestion_jobs` | `job_id`, richiesta idempotente, stato, `run_id`, requester, grant e retry policy |
| coverage | intervallo richiesto/disponibile, buchi, freshness, campo prezzo, corporate-action policy |
| prezzi | listing/instrument, osservazione, valore, currency, source, `run_id` |

Ogni `ensure_*` crea o riusa sempre un job persistente. Il tool può attendere brevemente e rispondere `completed`; altrimenti restituisce `queued` o `running`. Non esistono operazioni lunghe senza `job_id`.

### 4.2 SEC e bilanci

| Entità | Chiave/attributi essenziali |
|---|---|
| `sec_entities` | `issuer_id`, CIK, nome, ticker/alias storici, SIC, fiscal year end |
| `sec_filings` | CIK + accession, form, filed date, report date, URL, hash, `run_id` |
| `sec_company_facts` | CIK, taxonomy, concept, unit, instant o start/end, fiscal year/period, form, filed date, accession, frame |
| `sec_statement_lines` | issuer, statement, line key, periodo strutturato, unit, accession, concept sorgente, mapping version |
| `sec_coverage` | issuer/filing family, forme, ultimo filing, lag, qualità |

I facts raw sono append-only. Una vista convenience può selezionare il valore più recente, ma deve riportare sempre periodo, unità, accession e filed date. Il mapping XBRL → line key è versionato e testato; non sovrascrive lo storico.

### 4.3 Documenti e artifact

DuckDB conserva metadati, hash, URI di artifact, estratti e chunk indicizzati. HTML/PDF originali vanno in artifact storage immutabile; non nel warehouse come blob primario. Gli extract esposti a LLM sono piccoli, delimitati e trattati come contenuto non fidato.

### 4.4 Snapshot analitico

Un'analisi multi-serie non può avere un solo `run_id`. Ogni risultato di LazyFin/LazyStats deve riferire un `snapshot_manifest_id` contenente:

- identità di issuer/instrument/listing e filtri temporali;
- transform, frequenza e campo prezzo;
- lista dei `run_id`/vintage e hash dei dati effettivamente usati;
- versione di calcolo/modello e timestamp.

Il manifest è la provenance dell'intera analisi; le righe di origine mantengono il proprio `run_id`.

## 5. Contratti tool

### 5.1 Read-only: `financial_research`

| Famiglia | Tool | Output massimo |
|---|---|---|
| Discovery | `datahub_resolve_instrument`, coverage e search | candidati/metadati paginati |
| Prezzi | `datahub_get_price_summary` | metriche, intervallo, freshness, snapshot |
| Statistica | volatilità, correlazione, outlier, rolling/drawdown | metriche, top eventi, provenance |
| Financials | coverage, facts filtrati, statement, summary, filing metadata | periodi e righe limitati |
| Filing | `datahub_get_filing_extract` | solo chunk pertinenti e non fidati |
| Portfolio | funzioni LazyFin deterministicamente pure | nessun trade, nessun fetch |

Le matrici raw (`datahub_get_series`, `datahub_get_returns`) non sono nel profilo LLM standard. Restano disponibili nelle API Python reader/extract e, se necessario, in un profilo tecnico esplicito e fortemente limitato.

### 5.2 Write: `financial_data_ops`

Include esclusivamente:

- `datahub_ensure_price_history`
- `datahub_ensure_financials`
- `datahub_get_job_status`

Ogni write richiede `allow_write=True`, grant one-shot target/scope-bound, rate limit, writer lock, job idempotente e audit che colleghi sessione LLM, grant, `job_id` e `run_id`. Il fallback provider non è mai silenzioso: provider scelto e ragione sono nel run record.

### 5.3 Budget LLM

Ogni tool ha limite in record, caratteri e byte. Indicazione iniziale:

- discovery: 50 candidati;
- facts/statement: 100 righe e 12 periodi;
- outlier: 100 risultati di default, hard cap 250, con conteggio totale e top per severità;
- filing extract: massimo 3 chunk, 4.000 caratteri ciascuno;
- nessuna risposta senza paginazione o limite server-side.

Una flag `content_is_untrusted=true` è necessaria ma non sufficiente: l'adapter deve incapsulare il contenuto come citazione/dato e impedire che istruzioni presenti nel filing diventino istruzioni per l'agente.

## 6. Stato già realizzato da preservare

`LazyTools` contiene ora un'implementazione transitoria, già testata anche con agente DeepSeek, di:

- `statistical_return_volatility`;
- `statistical_return_correlation`;
- `statistical_return_outliers`.

I tool caricano internamente dal `market-data-hub` tutta la storia necessaria, calcolano localmente e restituiscono `AnalysisResult` senza inviare serie raw al prompt. Questa implementazione non va rimossa: nella fase LazyStats diventa il comportamento di riferimento e il wrapper di LazyTools resta compatibile.

Il limite storico di 500 righe in `agent_tools.py` è quindi corretto per dati raw verso LLM, ma non deve limitare i calcoli server-side.

## 7. Piano di implementazione

### Step 0 — ADR, perimetro e compatibilità

1. Approvare le decisioni delle sezioni 1-5.
2. Congelare MVP: pricing single-name con un provider primario; SEC/US GAAP per 10-K, 10-Q e company facts.
3. Pubblicare catalogo di tool vietati nel profilo finanziario standard: Stooq/EDGAR diretti, loader da file locale e raw series tools.
4. Definire SLO di freshness, timeout job, retention artifact e retry.

**Acceptance:** ADR e inventario tool; nessuna modifica funzionale.

### Step 1 — `lazydatacore` e catalogo capability

1. Estrarre il minimo `lazydatacore` in package leggero e versionato: identità, tempo/as-of, `AnalysisResult`, manifest provenance. Non deve dipendere da DuckDB o HTTP.
2. Il hub diventa il primo consumer del package; poi LazyTools/LazyFin/LazyStats lo adottano alle frontiere.
3. Creare in LazyTools catalogo dichiarativo: owner, source, read/write, trust, budget, lifecycle e profilo.
4. Creare bundle `financial_research` e `financial_data_ops`; spostare gli attuali write tool nel secondo.

**Acceptance:** import pulito di `lazydatacore`; il profilo standard non contiene write o fetch esterno.

### Step 2 — Vertical slice single-name prices

1. Migrare issuer/instrument/listing/alias e `ingestion_jobs`/`ingestion_runs`.
2. Estrarre dal refresh corrente un servizio idempotente `ensure_price_history` senza hardcode `EQUITY`.
3. Implementare risoluzione non ambigua, coverage, download incrementale e `get_price_summary`.
4. Esporre binding LazyTools con parity test verso i servizi hub.

**Acceptance:** una listing non presente viene risolta, ingerita sotto lock, letta dal DB e richiesta di nuovo senza duplicazione.

### Step 3 — SEC metadata e facts

1. Portare nel hub il transport SEC protetto (User-Agent, throttle, host validation, cap dimensione).
2. Implementare entity resolver CIK/ticker/listing e schema append-only per filing/facts/coverage.
3. Ingerire filing metadata e company facts prima del testo completo.
4. Implementare mapping minimo versionato: revenue, net income, assets, liabilities, equity, operating cash flow.

**Acceptance:** issuer risolto in modo tracciabile; facts con unità, periodo, accession e filed date verificabili.

### Step 4 — Reader, statement e tool finanziari

1. Aggiungere reader/extract pubblici per entity, filing, facts e statement.
2. Materializzare statement line standardizzate da mapping versionato.
3. Esporre solo risposte bounded con snapshot manifest e provenance.
4. Aggiungere filing extract solo dopo retention, artifact store e protezioni untrusted.

**Acceptance:** confronto di ricavi, margini e leva tra periodi senza XBRL/HTML completi nel contesto LLM.

### Step 5 — Purificazione LazyFin e bridge unico

1. Spostare in `LazyTools/connectors/fin` i provider/wrapper agentici; LazyFin conserva kernel e API Python.
2. Rendere DataHubPriceSource e reader financials del hub la strada default nei workflow produzione.
3. Deprecare `MarketDataTools` ed `EdgarTools` diretti dai bundle finanziari; compatibilità per una release.

**Acceptance:** `import lazyfin` non importa lazybridge; nessun workflow finance scarica direttamente.

### Step 6 — LazyStats senza big bang

1. Creare `LazyStats` puro con `core/`, `models/`, `io/datahub.py`, `io/depot.py` e `io/local.py` solo notebook.
2. Migrare prima le statistiche già presenti, mantenendo le firme LazyTools e golden test.
3. Migrare LazyHMM; congelare LazyRay e migrarlo solo dopo equivalenza dei risultati e del depot.
4. Deprecare i repository soltanto dopo una release di convivenza e test golden completi.

**Acceptance:** stessa output numerico sui dataset golden, provenance completa nel depot, nessun loader file nel profilo LLM.

### Step 7 — Enforcement e osservabilità

1. Test di import e dependency boundary: niente `lazybridge` in LazyFin/LazyStats; niente client finanziari esterni fuori dal hub.
2. Test runtime con transport bloccato: `get_*`, fin e stats non fanno HTTP; solo `ensure_*` può farlo.
3. Test dei profili, collisioni nomi, paging, cap output, grant, lock, retry e idempotenza.
4. Dashboard di coverage, buchi, freshness, filing lag, job error e provider fallback.

**Acceptance:** CI blocca un bypass e l'audit collega tool call → job → run → snapshot → fonte.

## 8. Go / no-go

### Go MVP prezzi

- listing risolto senza ambiguità o con richiesta esplicita di scelta;
- job gated e idempotente, con lock e provider registrato;
- lettura successiva esclusivamente dal hub;
- nessuna barra OHLCV raw nell'output LLM;
- coverage, `run_id` e snapshot disponibili.

### Go MVP financials

- issuer separato da instrument/listing;
- 10-K/10-Q e company facts storicizzati;
- mapping minimo validato su più issuer;
- accession, filed date, periodo e unità presenti su ogni valore;
- documenti raw fuori dal prompt e reader senza HTTP.

### No-go

- consumer che fa fetch finanziario diretto;
- tool read-only che aggiorna implicitamente;
- ticker trattato come equivalente universale di CIK/emittente;
- fallback provider non tracciato;
- risultato analitico senza manifest di input;
- testo filing completo o matrice raw nel profilo LLM standard.

## 9. Decisioni ancora aperte, ma non bloccanti

1. Primo artifact store concreto e sua retention policy.
2. Provider primario e fallback espliciti per ogni classe/listing.
3. Formato e hosting del package `lazydatacore` (repo separato o distribuzione dedicata).
4. Durata di convivenza e calendario deprecazioni di LazyHMM/LazyRay e dei wrapper LazyFin.

Tutte le altre decisioni essenziali sono fissate in questo documento per evitare che il MVP diventi un redesign continuo.
