# Deep code audit dell'ecosistema Lazy — 11 luglio 2026

## Mandato e conclusione

Questo è un **code audit**, non un assessment generico. Ho seguito i flussi
reali dati/LLM, letto i percorsi critici, confrontato i contratti fra repository
ed eseguito una riproduzione isolata su DuckDB.

```text
provider esterno -> market-data-hub -> DB/provenance -> calcolo locale
                                                -> risultato bounded -> LLM
```

La direzione è buona, e il nuovo tool statistico segue già questo schema.
Ma non è ancora una proprietà applicata dell'ecosistema che tutti i dati
finanziari entrino dal Data Hub e che alla LLM arrivino solo risultati. Ci sono
quattro blocchi di rilascio P0.

Revisioni locali ispezionate: `market-data-hub e019f5a`, `LazyTools 31a81e1`,
`LazyFin 1a797e9`, `LazyStats 4161eee`, `LazyBridge acbcc94`.

## Metodo e controlli eseguiti

1. Tracciamento identity -> ingestion -> storage -> reader -> tool surface.
2. Ricerca statica di client finanziari diretti, tool provider e DB owner.
3. Lettura di schema, migrations, contratti e test di boundary.
4. Reproduzione dinamica della collisione di due listing con lo stesso ticker.
5. Verifica di packaging/revisioni effettivamente installate in Spyder.

| Controllo | Esito |
|---|---|
| test completi market-data-hub | 188 passati |
| test completi LazyFin | 215 passati |
| test completi LazyTools | 377 passati, 8 skipped; 1 failure solo in Windows non privilegiato sul test symlink, che passa con privilegio adeguato |
| `ruff check` sui package auditati | passato |
| `pip check` Spyder | passato, ma con LazyBridge 0.10.0, non il core 1.0.1 |

I test verdi sono significativi; non eliminano i finding, perché alcuni casi
non sono coperti o sono proprio il comportamento oggi testato.

## Aggiornamento dopo le modifiche successive all'audit

Le modifiche in `LazyTools 31a81e1` migliorano materialmente la Fase 6.

- È stato aggiunto `RegimeTools`: il wrapping `Tool.wrap` vive ora in LazyTools,
  non nel package statistico. Il connector separa read/write con
  `allow_write=True` e ha test di superficie e round-trip.
- `StatisticalAnalysisTools` non duplica più volatilità, correlazione e
  outlier: delega il calcolo a `lazystats.core.returns` e conserva nella bridge
  solo signature LLM, cap output e serializzazione.
- I test mirati a statistiche, regimi, DataHub e fin passano: **28 passed**.
- La suite LazyTools aggiornata ha esito **377 passed, 8 skipped, 1 failure
  ambientale** (creazione symlink senza diritto Windows), non una regressione
  dei connector nuovi.

Questo chiude la parte "raggiungibilità LLM" di CA-09 e riduce il rischio di
drift matematico. Non chiude però il confine dati: il nuovo connector espone
anche un loader da file locale e non espone il loader DataHub. I finding CA-11
e CA-12 sono aggiunti proprio per questo.

## Finding

### CA-01 — P0 — l'identità della listing viene persa nella serie prezzi

**Evidenza.** `prices_daily` è keyed da `(date, symbol)`
([schema.sql:21-33](../market_data_hub/db/schema.sql)); il modello identity
dichiara di lasciarla piatta e di unirla a `listings` tramite `symbol`
([schema.sql:259-268](../market_data_hub/db/schema.sql)). Dopo aver risolto una
`listing_id`, l'ingestion riscrive i record con il solo `symbol` prima
dell'upsert ([prices.py:228-293](../market_data_hub/services/prices.py)). La
summary poi legge `WHERE symbol = ?`, non `listing_id`
([prices.py:351-371](../market_data_hub/services/prices.py)).

**Riproduzione eseguita.** Ho registrato `ACME/XNAS/USD` e `ACME/XMIL/EUR`, con
provider symbol e prezzi diversi, poi ho ingested entrambe le serie:

```text
xnas summary last_adj_close: 101.0
xmil summary last_adj_close: 101.0
stored rows: [(2024-01-02, 'ACME', 100.0), (2024-01-03, 'ACME', 101.0)]
```

La seconda serie sovrascrive la prima; entrambe le summary leggono quella
italiana. L'ambiguità viene gestita prima della scrittura, ma il dato collide
dopo la scelta.

**Impatto.** Bloccante per dual listing, ADR, share class, venue e provider
symbol differenti. Può contaminare return, volatility, correlation e segnali.

**Gap test.** Il test corrente rifiuta il ticker ambiguo prima dell'ingestion
([test_services_prices.py:87-103](../tests/test_services_prices.py)); non
ingestisce due listing già disambiguate e non verifica l'isolamento.

**Correzione.** Nuova tabella `listing_prices_daily` keyed almeno da
`(date, listing_id)`; reader e summary keyed da listing; view legacy solo per
mapping univoci; migration/backfill con report collisioni; test XNAS/XMIL.

### CA-02 — P0 — il provider DataHub predefinito passa ancora serie grezze alla LLM

**Evidenza.** `tool_get_series` e `tool_get_returns` serializzano record di
serie/returns ([agent_tools.py:124-150](../market_data_hub/agent_tools.py)).
Il cap di 500 righe esiste solo per non saturare il contesto
([agent_tools.py:32-46](../market_data_hub/agent_tools.py)). `DataHubTools()`
li espone nel profilo default
([tools.py:51-141](../../LazyTools/src/lazytools/connectors/datahub/tools.py));
il test ne richiede esplicitamente la presenza
([test_datahub.py:12-53](../../LazyTools/tests/test_datahub.py)).

**Perché 500 righe.** Il cap non è una scelta casuale: limita il context
flooding. È però la soluzione sbagliata per il requisito attuale: 500 righe
sono ancora dati finanziari grezzi e, essendo `head(500)`, sono una porzione
troncata inadatta a un'analisi storica completa. Non va alzato il cap: va tolto
il raw extraction dal profilo agentico finance.

**Contro-esempio positivo.** `StatisticalAnalysisTools` legge internamente
`extract.extract_returns`, non il JSON troncato
([backend.py:40-113](../../LazyTools/src/lazytools/statistical_analysis/backend.py)),
e permette in output solo metadata allow-listati e risultati bounded
([tools.py:281-307](../../LazyTools/src/lazytools/statistical_analysis/tools.py)).

**Correzione.** Separare `DataHubDiscoveryTools`, `DataHubResearchResultsTools`
e `DataHubRawExtractionTools`. Il financial-agent default monta solo i primi
due; il raw è notebook/service-only o dietro capability non disponibile alla
LLM. Il test del profilo deve fallire se espone i due raw tool.

### CA-03 — P0 — restano tool LLM finanziari che bypassano il Data Hub

**Evidenza.** `ResolveTools` è ancora export pubblico di `connectors.fin`
([fin/__init__.py:26-49](../../LazyTools/src/lazytools/connectors/fin/__init__.py))
e chiama direttamente `self._client.company_facts(cik)`
([fin/tools.py:211-262](../../LazyTools/src/lazytools/connectors/fin/tools.py)).
Non è deprecato. `EdgarTools` espone ancora resolve, filings, filing text e raw
company facts
([edgar/tools.py:27-115](../../LazyTools/src/lazytools/connectors/edgar/tools.py)).
`MarketDataTools` espone price quote/history dirette
([marketdata/tools.py:22-82](../../LazyTools/src/lazytools/connectors/marketdata/tools.py)).
I warning di deprecazione informano, ma non impediscono il mount.

**Impatto.** Un agent può saltare DB, coverage, run ledger e provenance
centralizzata; può anche reintrodurre payload raw nel contesto.

**Correzione.** Rimuovere `ResolveTools` dagli export; sostituirlo con
`datahub_ensure_financials` + `datahub_get_*`. Isolare EDGAR/market-data
diretti in `legacy_direct`, non importato dalle agent factory, con data di
rimozione. Aggiungere un test AST/integration cross-repo che vieti client
finanziari diretti nei bundle finance.

### CA-04 — P0 — il grafo installabile resta su LazyBridge 0.10, non 1.0.1

**Evidenza.** Il core locale dichiara `version = "1.0.1"`
([LazyBridge/pyproject.toml:6-25](../../LazyBridge/pyproject.toml)). LazyTools
dichiara invece `lazybridge>=0.7.9,<0.11`
([LazyTools/pyproject.toml:25](../../LazyTools/pyproject.toml)) ed esclude 1.x.
LazyFin fissa l'extra bridge al commit 0.10.0 (`09604bd...`) e fissa anche
Data Hub a `6d4b920...`, antecedente alla revisione auditata
([LazyFin/pyproject.toml:21-40](../../LazyFin/pyproject.toml)).

L'ambiente reale conferma:

```text
lazybridge = 0.10.0
lazytoolkit = 0.3.1
lazyfin = 0.3.0
market-data-hub = 0.1.0
pip check = No broken requirements found
```

`pip check` è verde perché i pin vecchi sono consistenti, non perché i
consumer siano stati verificati sul core 1.0.1.

**Correzione.** Decidere se il contratto è LazyBridge 1.x. Se sì: aggiornare
range e pin, creare un release manifest centralizzato e una CI
install-from-zero che esegua smoke end-to-end alle revisioni correnti.

### CA-05 — P1 — l'ingestion on-demand non amplia il DB per un single name arbitrario

**Evidenza.** Prima dell'ingestion, `_config_candidates` cerca solo ticker
esatti in `get_yahoo_tickers()`
([prices.py:80-99](../market_data_hub/services/prices.py)).
`_resolve_single` rifiuta quelli non presenti in listing/alias/config
([prices.py:155-162](../market_data_hub/services/prices.py)); è il comportamento
testato per `NOPE_XYZ`
([test_services_prices.py:106-109](../tests/test_services_prices.py)).

**Impatto.** `ensure_price_history` estende oggi un universo statico già noto,
non implementa ancora “nuovo single name richiesto quando serve”.

**Correzione.** Workflow `register_listing`/`ensure_listing` con resolver
venue-aware, exchange/currency/provider symbol obbligatori quando non risolto,
stato `pending_review` sugli ambigui e job/provenance identici all'ingestion.

### CA-06 — P1 — ingestion non atomica e writer lock tenuto durante la rete

**Evidenza.** Il lock dei prezzi include fetch esterno, upsert e ledger
([prices.py:236-323](../market_data_hub/services/prices.py)); il fetch è alla
riga 285. Il lock SEC include submissions e facts esterni
([financials.py:179-289](../market_data_hub/services/financials.py)). Non c'è
transazione: nei prezzi l'upsert precede il `completed`
([prices.py:293-307](../market_data_hub/services/prices.py)); per SEC le
filings sono scritte prima del fetch facts
([financials.py:215-263](../market_data_hub/services/financials.py)).

**Impatto.** Un provider lento blocca gli altri writer; un errore dopo una
scrittura può lasciare payload materializzato ma job/run `error`.

**Correzione.** Fetch/validate/normalize fuori lock; poi transazione DuckDB
sotto lock per payload + run/job/coverage. Aggiungere fault injection dopo
upsert, filings e facts, verificando commit atomico o recovery state esplicito.

### CA-07 — P1 — `tool_refresh_prices` aggira identity e job ledger

**Evidenza.** Il tool costruisce ticker fittizi tutti `EQUITY`, monkeypatcha
globalmente `runner.get_yahoo_tickers` e invoca il batch runner
([agent_tools.py:275-329](../market_data_hub/agent_tools.py)). Rimane in
`WRITE_TOOL_FUNCTIONS` accanto all'ensure canonico
([agent_tools.py:382-383](../market_data_hub/agent_tools.py)).

**Impatto.** Nessun `ingestion_job`, nessuna listing identity,
`provider_symbol` perso, asset class inventata: due percorsi semantici diversi
per la stessa operazione.

**Correzione.** Deprecarlo a livello API, rimuoverlo da `DataHubTools`,
conservarlo soltanto come CLI amministrativa temporanea e migrare i caller.

### CA-08 — P1 — metadata SEC non ha provenance append-only

**Evidenza.** I company facts usano anti-join append-only
([financials.py:247-262](../market_data_hub/services/financials.py)); le filing
usano `INSERT OR REPLACE`, sovrascrivendo `run_id` e `updated_at` per
`(cik, accession)`
([financials.py:215-236](../market_data_hub/services/financials.py)).

**Correzione.** Tenere una current projection e aggiungere
`sec_filing_observations` append-only, oppure almeno
`first_seen_run_id`/`last_seen_run_id` con semantica esplicita.

### CA-09 — P2 — LazyStats conserva un layer LLM-oriented nel dominio

**Evidenza.** La boundary LazyStats è buona: vieta client HTTP/duckdb/tool
provider ([test_boundary.py:1-107](../../LazyStats/tests/test_boundary.py)).
Ma `lazystats.regimes.tools` dichiara e gestisce una “LLM Tool API”, store e
persistence ([regimes/tools.py:1-150](../../LazyStats/src/lazystats/regimes/tools.py)).
Il nuovo loader DataHub è invece corretto: legge internamente, deposita la
matrice e restituisce solo summary
([regimes/datasources/datahub.py:57-160](../../LazyStats/src/lazystats/regimes/datasources/datahub.py)).
Il vecchio download Yahoo viene bloccato, non eseguito
([regimes/db.py:927-935](../../LazyStats/src/lazystats/regimes/db.py)).

**Aggiornamento.** Il nuovo `lazytools.connectors.regimes.RegimeTools` ha
spostato il wrapping vero (`Tool.wrap`) in LazyTools e quindi risolve la parte
più importante del finding: le funzioni sono finalmente montabili da un agente
attraverso la frontiera corretta. Resta ownership drift nel package di dominio
(`regimes.tools` conserva API e documentazione LLM-oriented); è ora P2, non un
blocco. Mantenere in LazyStats service/contratti e spostare gradualmente
descrizioni, policy e binding Store nella bridge.

### CA-10 — P2 — test symlink non portabile su Windows standard

La suite LazyTools passa con il diritto symlink adeguato; in un Windows standard
il test `test_refuses_symlink_escape` non può creare il symlink. È un problema
di test environment, non una vulnerabilità dimostrata. Fare skip condizionale
in locale e renderlo obbligatorio nella CI Windows configurata.

### CA-11 — P1 — il nuovo connector RegimeTools non forza il caricamento dal Data Hub

**Evidenza.** Con `allow_write=True`, il connector espone
`regime_load_time_series`, che chiama il loader da file locale
([regimes/tools.py:89-107](../../LazyTools/src/lazytools/connectors/regimes/tools.py)).
Quel loader accetta un `file_path` e legge CSV/Excel con pandas
([LazyStats regimes/tools.py:635-694](../../LazyStats/src/lazystats/regimes/tools.py)).
Il connector **non** espone `lazystats.regimes.load_from_datahub`, benché il
loader corretto DataHub sia public e restituisca solo un summary
([LazyStats regimes/__init__.py:56-93](../../LazyStats/src/lazystats/regimes/__init__.py)).

**Impatto.** Un agent configurato con il write profile può alimentare i regimi
con dati fuori dal hub e con path scelto dal modello. Questo contraddice sia il
single source of truth, sia la regola di non scambiare dataset grezzi fra LLM e
tool. Il gate di costruzione è utile, ma non è una sandbox sui path né un
vincolo di provenance.

**Correzione.** Sostituire `regime_load_time_series` nel provider finanziario
con `regime_load_from_datahub`, che accetti soltanto simboli, periodo,
frequenza e `data_key`; lasciare il loader file in un provider notebook locale
esplicito, sandboxato su una root, non montabile nei finance profile. Aggiungere
un test che verifichi: il provider finance contiene `regime_load_from_datahub`
e non contiene `regime_load_time_series`.

### CA-12 — P2 — alcuni read tool dei regimi restano potenzialmente non bounded

`RegimeTools()` espone di default `regime_db_get_state_sequence`
([regimes/tools.py:66-88](../../LazyTools/src/lazytools/connectors/regimes/tools.py)).
Il sottostante consente `last_n=0`, che restituisce tutta la sequenza T×S di
posteriori e stati ([db.py:1113-1163](../../LazyStats/src/lazystats/regimes/db.py)).
Anche `get_regime_changes` restituisce tutti i cambiamenti se `last_n=0`
([tools.py:1469-1533](../../LazyStats/src/lazystats/regimes/tools.py)).

Non sono prezzi raw, ma possono diventare payload LLM grandi quanto l'intera
storia modellata. Imporre hard cap nella bridge, mantenere `last_n` positivo
per default e riportare sempre `total`/`truncated`.

## Piano di remediation

### Gate 1 — prima di usare single-name/listing internazionali

1. CA-01: storage listing-keyed, migration, backfill e test collisione.
2. CA-04: una matrice versioni supportate e CI install-from-zero.
3. CA-05: non dichiarare single-name generalizzato finché il registration
   workflow non esiste.

### Gate 2 — prima di dichiarare Data Hub unico proprietario

1. CA-02: profili tool realmente distinti, default senza raw extraction.
2. CA-03: direct provider vietati nei finance bundle.
3. CA-07: refresh legacy fuori dalla superficie agentica.
4. CA-11: RegimeTools finance carica solo dal Data Hub, mai da file locale.
5. Test cross-repo sui nomi tool montati, non solo test dei package isolati.

### Gate 3 — robustezza operativa

1. CA-06: fetch/stage/transaction e fault-injection.
2. CA-08: filing observations/provenance.
3. CA-09: completare LazyHMM -> LazyStats -> LazyTools senza rompere golden
   numeric test.
4. CA-12: cap espliciti sui risultati dei regimi.
5. CA-10: stabilizzare il test symlink Windows.

## Cosa è solido oggi

- I tool volatilità/correlazione/outlier usano tutto il dataset nel processo e
  non restituiscono la matrice; la loro direzione è corretta.
- I read path del Data Hub sono testati senza network; summary, facts e
  statement sono bounded e DB-backed.
- I nuovi write path hanno job, run ledger, provider reason e lock; la lacuna
  è atomicità/concorrenza, non l'assenza di struttura.
- LazyFin sta separando kernel e bridge; il problema è il residuo direct fetch
  e il packaging, non il domain model puro.
- LazyStats è sulla direzione corretta: DataHub loader e blocco Yahoo sono già
  presenti. Il nuovo RegimeTools in LazyTools è un passo concreto; va ancora
  collegato al loader DataHub ed escluso il caricamento file dai profili finance.

## Decisione pratica

Non aggiungerei nuove capability finanziarie prima di Gate 1 e Gate 2. Solo
dopo potrai affermare in modo tecnicamente difendibile:

> I dati finanziari entrano nel Data Hub; gli agenti usano capability
> controllate e ricevono risultati bounded, non serie storiche complete.

Oggi questa è una direzione architetturale corretta, ma non ancora una
proprietà imposta dal codice.
