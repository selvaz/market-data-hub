# Implementation Plan — Piano v3.1 (Financial Data Hub)

**Base:** [FINANCIAL_DATA_HUB_PLAN_V3_1.md](FINANCIAL_DATA_HUB_PLAN_V3_1.md)
**Scopo di questo documento:** tradurre il piano architetturale in task eseguibili, ordinati, con file/tabelle concreti da toccare nello stato attuale del repo.

## 0. Stato attuale rilevante (baseline verificata)

- `market_data_hub/lazydatacore/` esiste già (`identity.py`, `resolver.py`, `result.py`, `series.py`, `timeutil.py`) ma vive **dentro** il hub, non come package indipendente → Step 1 del piano è parzialmente fatto, manca l'estrazione.
- `market_data_hub/db/schema.sql` è **flat**: `prices_daily`, `crypto_ohlcv`, `macro_series`, `macro_panel`, `download_log`, `coverage_report`, `macro_panel_coverage`, `factor_returns`, `custom_series`, `*_vintage`. Nessuna tabella `issuers` / `instruments` / `listings` / `identifier_aliases`, nessuna `ingestion_jobs` / `ingestion_runs`, nessuna tabella SEC. `ticker` è oggi la chiave universale — esattamente il problema che la sezione 2 del piano corregge.
- `market_data_hub/sources/` ha già `yahoo.py`, `yahoo_direct.py`, `fred.py`, `imf.py`, `bis.py`, `worldbank.py`, `binance.py`, `ecb.py`, `macro_panel.py`, `factors.py`. Nessun transport SEC/EDGAR.
- `market_data_hub/lock.py` fornisce già un file-lock di scrittura cross-process (`db_write_lock`) — riusabile per il writer lock richiesto da `ensure_*`.
- `market_data_hub/catalog.py`, `reader.py`, `extract.py`, `agent_tools.py` esistono e sono già la base dei tool read-only, ma non hanno concetto di job/run né di issuer/instrument/listing.
- Non esiste ancora `LazyStats`, né consolidamento LazyHMM/LazyRay in questo repo (fuori scope MVP, coerente con la sezione 1.6 del piano).
- Non è chiaro se `LazyTools` è nel workspace: non è presente in questa working directory. Gli step 1.3/1.4/5/6 che toccano `LazyTools` vanno confermati con l'owner di quel repo prima di iniziare.

## 1. Sequenza di implementazione

L'ordine segue il piano (Step 0→7) ma è riformulato in task atomici con criteri di completamento verificabili via test, non solo "review".

### Fase 0 — ADR e congelamento perimetro (0.5–1 giorno)
1. Copiare le decisioni sez. 1–5 del piano in un ADR (`docs/adr/0001-financial-data-hub-v3_1.md`).
2. Elencare esplicitamente nel README/AGENTS i tool vietati nel profilo `financial_research` standard: accesso diretto Stooq/EDGAR, loader da file locale, `datahub_get_series`/`datahub_get_returns` raw.
3. Fissare SLO iniziali (freshness prezzi, timeout job, retention artifact) come costanti in `market_data_hub/config/` — non hardcoded nei service.
4. **Nessuna modifica di codice funzionale in questa fase.**

**Uscita:** ADR mergiato, nessun test da aggiungere.

### Fase 1 — `lazydatacore` come package indipendente + catalogo capability
1. Spostare `market_data_hub/lazydatacore/*` in un package a sé stante (repo o cartella distribuibile separatamente, vedi decisione aperta §9.3 del piano — nel frattempo: sotto-package senza import di `duckdb`/`requests`/hub-internals, testato in isolamento).
2. Verificare che `identity.py`, `series.py`, `result.py`, `timeutil.py` non importino nulla da `market_data_hub.db`/`market_data_hub.sources` — se lo fanno, rompere la dipendenza.
3. `market_data_hub` diventa il primo consumer via import esplicito del package estratto (aggiornare gli import in `reader.py`/`extract.py` se cambia il path).
4. Il catalogo dichiarativo dei tool (owner/source/read-write/trust/budget/profilo) è responsabilità di `LazyTools`, non di questo repo — coordinarsi separatamente; qui limitarsi a garantire che `agent_tools.py` esponga solo funzioni pure, senza side effect nascosti, così che il wrapping a valle sia meccanico.

**Uscita/test:** `tests/test_lazydatacore.py` esteso con un test di import-boundary (fallisce se `lazydatacore` importa `duckdb` o `market_data_hub.db`).

### Fase 2 — Vertical slice: prezzi single-name con identità corretta
Questa è la fase più grande e rischiosa: introduce il nuovo modello dati sopra lo schema esistente senza rompere `prices_daily`.

1. **Schema** (`market_data_hub/db/schema.sql`): aggiungere
   - `issuers(issuer_id PK, cik UNIQUE NULL, name, sic, fiscal_year_end, ...)`
   - `instruments(instrument_id PK, issuer_id FK NULL, kind, ...)`
   - `listings(listing_id PK, instrument_id FK, symbol, mic/exchange, currency, provider_symbol, active_from, active_to)`
   - `identifier_aliases(namespace, value, target_type, target_id, valid_from, valid_to)` — namespace ∈ {ticker_historic, isin, figi, cik}
   - `ingestion_runs(run_id PK, kind, input_json, provider, status, attempts, error, started_at, finished_at, payload_hash)`
   - `ingestion_jobs(job_id PK, request_hash UNIQUE, status, run_id FK, requester, created_at, updated_at)`
   - Bump `schema_meta` version; scrivere una migration idempotente (il repo non ha ancora un migration runner formale — verificare `db/connection.py`/`db/upsert.py` per il pattern attuale di `CREATE TABLE IF NOT EXISTS` e seguirlo).
2. **Non toccare `prices_daily` in questa fase** — resta la tabella fisica dei prezzi; `listings`/`instruments` sono un layer di identità sopra, referenziato da una nuova colonna `listing_id` (nullable in transizione) su `prices_daily`, popolata da un backfill one-shot.
3. **Servizio** `market_data_hub/services/prices.py` (nuovo modulo):
   - `resolve_instrument(query, exchange=None, currency=None) -> list[candidate]` — usa `identifier_aliases` + `listings`; se ambiguo, ritorna candidati invece di indovinare (requisito esplicito §2 del piano).
   - `ensure_price_history(listing_id_or_query, start, end) -> job_id` — crea/riusa `ingestion_jobs` via `request_hash` (idempotenza), acquisisce `db_write_lock` da `lock.py`, invoca il source esistente (`sources/yahoo.py` ecc.), scrive `ingestion_runs`, aggiorna `prices_daily`.
   - `get_price_summary(listing_id, ...) -> dict` — legge solo da DB, nessuna rete.
4. **Binding LLM**: nuove `tool_*` in `agent_tools.py` (`tool_resolve_instrument`, `tool_ensure_price_history` con `allow_write` esplicito, `tool_get_price_summary`, `tool_get_job_status`) che chiamano `services.prices`, non i `sources/*` direttamente.
5. Backfill one-shot: script `scripts/backfill_listings_from_tickers.py` che popola `issuers/instruments/listings` dai ticker già in config (`tickers_master.csv`, config YAML) e riempie `prices_daily.listing_id`.

**Uscita/test:**
- test nuovo `tests/test_services_prices.py`: una listing non presente viene risolta → ingerita sotto lock → letta dal DB → richiesta di nuovo senza duplicare righe in `prices_daily` né creare un secondo `ingestion_run` per lo stesso `request_hash`.
- test di ambiguità: query che matcha più listing ritorna candidati, non sceglie a caso.
- nessuna riga OHLCV raw nell'output di `tool_get_price_summary` (solo metriche aggregate).

### Fase 3 — SEC metadata e facts
1. Nuovo modulo `market_data_hub/sources/sec.py`: transport protetto verso EDGAR (User-Agent obbligatorio con contatto, rate limit ≤10 req/s per policy SEC, host allowlist `www.sec.gov`/`data.sec.gov`, cap dimensione risposta).
2. Schema: `sec_entities`, `sec_filings`, `sec_company_facts` (append-only), `sec_statement_lines`, `sec_coverage` come da §4.2 del piano.
3. Entity resolver: CIK ↔ `issuer_id` ↔ ticker/listing storici, via `identifier_aliases` (namespace `cik`).
4. `services/financials.py`: `ensure_filings_and_facts(issuer_or_cik) -> job_id`, ingest filing metadata + `companyfacts` JSON prima di qualunque testo/HTML completo.
5. Mapping XBRL → line key minimo e **versionato**: revenue, net_income, assets, liabilities, equity, operating_cash_flow. File `market_data_hub/config/xbrl_mapping_v1.yaml` (o simile), con test golden su 2-3 issuer noti (es. AAPL, MSFT) per validare unità/periodo/accession.

**Uscita/test:** `tests/test_sources_sec.py` + `tests/test_services_financials.py`: issuer risolto in modo tracciabile; facts con unità, periodo, accession e filed date verificabili; nessuna sovrascrittura di righe storiche (append-only verificato).

### Fase 4 — Reader/extract finanziari e statement
1. `reader.get_statement(issuer_id, statement, periods)`, `reader.get_facts(...)` — read-only, no HTTP (da testare esplicitamente con transport bloccato, vedi Fase 7).
2. Materializzazione statement standardizzate da `sec_statement_lines` via mapping versionato.
3. `snapshot_manifest` per ogni risposta analitica multi-serie (§4.4): tabella o struct `snapshot_manifests(manifest_id, issuer/instrument/listing ids, filters, transform, run_ids[], hash, calc_version, created_at)`.
4. Filing extract (`tool_get_filing_extract`) solo dopo: artifact store per HTML/PDF originali (fuori DB, §4.3), retention policy, e wrapping esplicito `content_is_untrusted=true` che tratta il testo come citazione, non istruzione.

**Uscita/test:** confronto ricavi/margini/leva tra periodi via tool, senza che XBRL/HTML completo appaia mai nel contesto LLM (verificabile controllando i byte/char massimi restituiti dai tool, §5.3).

### Fase 5 — Purificazione LazyFin (repo esterno, coordinamento richiesto)
Fuori da questo repo. Azione qui: garantire che `services/prices.py` e `reader.get_statement` espongano un'interfaccia stabile che `DataHubPriceSource` in LazyFin possa consumare come reader read-only. Non bloccante per Fasi 2–4.

### Fase 6 — LazyStats (repo esterno, fuori scope MVP)
Nessuna azione in questo repo oltre a mantenere compatibili le firme dei tool statistici già esistenti (`statistical_return_volatility/correlation/outliers`, già in LazyTools per §6 del piano — non rimuovere, non duplicare).

### Fase 7 — Enforcement e osservabilità
1. Test di import-boundary: nessun modulo in `market_data_hub/sources/` importato da `reader.py`/`extract.py`/`catalog.py` (i "get" non fanno rete).
2. Test runtime con transport monkeypatchato a "blocked": tutte le funzioni `tool_get_*`/`reader.*`/`extract.*` devono passare; solo `ensure_*` può toccare la rete.
3. Test su profili tool: nessuna collisione di nomi tra `financial_research` e `financial_data_ops`, paginazione sempre presente, `allow_write` obbligatorio sui write, idempotenza dei job già coperta in Fase 2/3.
4. Dashboard/report di coverage (estensione di `market_data_hub/coverage/`): buchi, freshness, filing lag, errori job, fallback provider.

**Uscita:** CI blocca un bypass (es. un test che tenta `tool_get_price_summary` con rete disabilitata e verifica che non lanci eccezioni di rete); audit trail tool call → job_id → run_id → snapshot_manifest_id → source.

## 2. Rischi e dipendenze da chiarire prima di iniziare la Fase 2

- **Migrazione `prices_daily`**: serve decidere se `listing_id` diventa obbligatorio subito o resta nullable per una release di convivenza (coerente con §5, "compatibilità per una release" usato altrove nel piano). Raccomandazione: nullable in Fase 2, NOT NULL solo dopo il backfill verificato.
- **`LazyTools`/`LazyFin`/`LazyStats` non sono in questo workspace**: le Fasi 1.3-1.4, 5 e 6 richiedono coordinamento cross-repo. Se non hai accesso qui, questi step vanno tracciati come issue collegate, non implementati alla cieca.
- **Provider primario/fallback per classe/listing** (§9.2 del piano) non è ancora deciso — necessario prima di scrivere `ensure_price_history` in modo definitivo, altrimenti si rifà la selezione provider due volte.
- **Artifact store** (§9.1) — necessario solo da Fase 4 in poi (filing extract); non blocca Fasi 2-3.

## 3. Prossimo passo concreto

Iniziare dalla Fase 0 (ADR, 0.5 giorno) e poi Fase 2 (vertical slice prezzi), perché è la parte con il valore più immediato e il rischio di schema più alto da validare presto. Le Fasi 3-4 (SEC) possono partire in parallelo una volta che lo schema `issuers/instruments/listings` di Fase 2 è stabile, perché SEC dipende da `issuer_id`.
