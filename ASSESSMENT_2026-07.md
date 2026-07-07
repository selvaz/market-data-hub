# Assessment di implementazione — market-data-hub (Luglio 2026)

Assessment condotto sul codice reale (commit `6d4b920`), con esecuzione della test suite in ambiente pulito. Ogni affermazione è supportata da riferimenti `file:riga` o da output reale.

---

## 1. Panoramica e stato di salute

market-data-hub è una pipeline di consolidamento dati di mercato: 5 sorgenti (Yahoo chart-v8 diretto via curl_cffi, FRED, Binance, panel cross-country WB/IMF/BIS, fattori Ken French) → un unico DuckDB con 12+ tabelle (schema versionato, `market_data_hub/db/schema.sql`), coverage engine per-serie, layer analitico "Dalio", vintages point-in-time per i backtest, API di lettura/estrazione per tool e LLM (`reader.py`, `extract.py`, `catalog.py`, `agent_tools.py`), contratto condiviso `lazydatacore`, e tabella `custom_series` come punto di estensione per app a valle (LazyFin).

Il progetto è in **buono stato**: ha già subito due audit interni (PR #6, #13, docs/DEEP_AUDIT_2026-07.md) i cui fix sono verificabili nel codice (lock scrittore, whitelist SQL in `reader.py:29`, prezzo live moltiplicativo `yahoo.py:57-69`, epoch UTC `yahoo_direct.py:52-63`). I 105 test passano tutti in 9 secondi. Restano però alcuni bug reali di correttezza (gestione HTTP non-200 su Yahoo, lock mancante sul path di scrittura `custom_series` usato da LazyFin, exit code mascherato di `run_daily`), incoerenze docs/codice e la duplicazione strutturale degli script top-level.

### Voti

| Area | Voto | Motivazione sintetica |
|---|---|---|
| Correttezza | **B** | Pipeline idempotente (INSERT OR REPLACE su PK), retry/backoff ovunque, timezone gestite esplicitamente; ma HTTP 429/5xx Yahoo degradati a "empty" (`yahoo_direct.py:99-118`) e scritture `custom_series` senza writer lock (`custom.py:106`). |
| Sicurezza | **A-** | Nessun segreto hardcoded (iniezione da env in `config_loader.py:26-40`), `yaml.safe_load` ovunque, SQL parametrizzato + whitelist campi, niente pickle/eval. Neo: il README istruisce a scrivere la chiave FRED nel file YAML tracciato (README.md:40-41). |
| Test | **B+** | 105 test, veloci, offline, di buona qualità (regression test mirati sugli audit fix). Non testati: orchestrazione `runner.py`, parser di rete WB/IMF/BIS/Binance/Yahoo, `agent_tools`, report HTML. |
| Docs | **B** | Sito mkdocs + 7 documenti in docs/, README ricco; ma soglie stalled errate nel README (A=400 vs 550 reale) e istruzione FRED-key contraddittoria. |
| Manutenibilità | **B-** | Package ben stratificato; però 8 script top-level con bootstrap `sys.path` duplicato, 6 retry-loop HTTP hand-rolled con 4 formule di backoff diverse, funzioni monstre (`run_dalio` ~230 righe, `make_dalio_report.py` 565 righe), binario morto in git. |

**Salute complessiva: B (buona, con debiti puntuali noti e circoscritti).**

---

## 2. Stato dell'implementazione

| Componente | Stato | Note |
|---|---|---|
| Download incrementale 5 sorgenti (`runner.py`) | **Completo** | Incrementale con tail-refresh (Yahoo 3g, FRED 95g, Binance 3 step di timeframe), batching per start-date, parallelismo configurabile. |
| Backfill (`run_backfill.py`) | **Completo** | CLI sottile su `runner.run(mode="backfill")`; eredita lock, coverage e layer analitico (fix B3 dell'audit precedente). Incoerenza: default sorgenti esclude `factors` (run_backfill.py:31) mentre il daily le include (runner.py:422). |
| Storage DuckDB + migrazioni | **Completo** | Schema idempotente, `schema_meta` versionato con ladder di migrazione corretta per DB pre-esistenti non stampati (`connection.py:66-151`), upsert transazionale (`upsert.py:105-120`). |
| Lock scrittore cross-processo | **Completo** | `lock.py` (filelock advisory accanto al DB), tenuto per l'intero run incluso il layer analitico (runner.py:436-447). **Ma non usato da `custom.py`** (vedi issue A2). |
| Vintages point-in-time | **Completo** | `record_vintage` append-on-change (`upsert.py:132-177`), lettura as-of in `reader.py` (`_asof_query`), retention in `retention.py`. |
| Coverage engine | **Completo** | Freq-aware (D/W/M/Q/A), business-day per il daily, full-rebuild con DELETE (report.py:124-129). |
| Layer Dalio + classificazione paesi | **Completo** | Z-score cross-country, fasi del ciclo del debito con soglie configurabili, regime four-box. Non testato su casi limite; scrittura DELETE+INSERT non transazionale (dalio.py:371-378). |
| Live intraday | **Completo** | Batch unico (no 429), mapping moltiplicativo live→adjusted, righe `is_live=TRUE` escluse da coverage e letture di default. |
| Report HTML/MD + email | **Completo** | `make_report.py`, `make_dalio_report.py`; SMTP con credenziali da env. |
| Scheduling Windows (`setup_scheduler.ps1`) | **Completo** | 3 task (EOD 22:00, weekend FRED, live orario 16-22 Lun-Ven). Path radice hardcoded `D:\market_data` (setup_scheduler.ps1:15). |
| Round-trip Excel↔YAML | **Completo** | `export_to_excel.py` / `import_from_excel.py` con merge non distruttivo e guardia anti-contaminazione FRED→Yahoo (import_from_excel.py:148-156, testata in test_guards.py). |
| API estrazione/discovery per LLM | **Completo** | `catalog.py`, `extract.py`, `agent_tools.py` (read-only di default, `tool_refresh_prices` opt-in con doppio lock), skill `skills/query-market-data-hub/SKILL.md` coerente col codice. |
| `custom_series` / integrazione LazyFin | **Completo** (con gap di concorrenza) | Contratto verificato su entrambi i lati: LazyFin `src/lazyfin/data/serieshub.py` chiama `market_data_hub.custom.store_series` con firma identica (`series_id, observations, series_name, unit, frequency, source, db_path`) e rilegge via `extract_series(domain="custom")`; LazyFin ha anche un integration test reale (`tests/test_data_serieshub_integration.py`). Manca il writer lock lato hub (issue A2). |
| CI/CD | **Parziale** | CI (lint ruff error-class, pip-audit advisory, validate_config, compileall, pytest su 3.11/3.12) + deploy docs Pages. Non testa `pip install -e .` (il packaging di pyproject.toml non è mai esercitato in CI). |
| Packaging | **Completo** | `pyproject.toml` coerente con `requirements.txt` (stesse 12 dipendenze), package-data per YAML e SQL. |

---

## 3. Issue trovate — per severità

### CRITICA

Nessuna issue critica: non ci sono perdite di dati, corruzioni dello storage, segreti committati o vulnerabilità sfruttabili da input esterno.

### ALTA

**A1 — HTTP 429/5xx da Yahoo esauriscono i retry e vengono registrati come "empty" (outage mascherato)**
`market_data_hub/sources/yahoo_direct.py:99-118` — `_fetch_one` gestisce solo `status_code == 200` (parse) e `404/400` (empty legittimo). Ogni altro status (429 rate-limit, 500, 503) non solleva eccezione e non viene registrato in `last_exc`: dopo i retry la funzione arriva a `if last_exc is not None` con `last_exc = None` e ritorna un frame vuoto. Il fix B5 dell'audit precedente ("un outage non deve loggarsi come empty") copre solo il ramo delle *eccezioni di rete*, non gli status non-200. Impatto: un rate-limit prolungato di Yahoo produce `status="empty"` in `download_log` per tutti i simboli, zero errori nel report, e la salvaguardia "tutti falliti ⇒ raise" di `yahoo_batch` (yahoo_direct.py:154-157) non scatta mai perché nessun simbolo risulta *failed*. La serie appare ferma e viene rilevata solo giorni dopo dal flag `stalled`.

**A2 — `custom.store_series`/`delete_series` scrivono sul DB senza il writer lock**
`market_data_hub/custom.py:106` e `custom.py:115` — entrambe aprono `get_conn(db_path)` in scrittura senza passare da `db_write_lock()` (`lock.py:34`). DuckDB ammette un solo writer per file: se LazyFin pubblica una NAV series (`lazyfin/data/serieshub.py:publish_series`) mentre il task schedulato EOD/live sta scrivendo, una delle due parti fallisce con IO error — esattamente la classe di bug che il lock era nato per eliminare (cfr. fix B3 in docs/DEEP_AUDIT_2026-07.md) e che `agent_tools.tool_refresh_prices` gestisce correttamente (agent_tools.py:195-208). Impatto: crash intermittenti dell'integrazione LazyFin↔hub negli orari di download; è il path di scrittura pubblicizzato ("sanctioned expansion point") del contratto tra le due repo.

**A3 — `run_daily.py --report` ritorna exit code 0 anche quando il download fallisce completamente**
`run_daily.py:50-57,117` — l'eccezione di `run()` viene catturata e, se `--report` è attivo (come nei task schedulati `MarketDataEOD`/`MarketDataWeekend`, setup_scheduler.ps1:29-30), l'esecuzione prosegue verso il report e termina con `return 0`. Anche il fallimento della *generazione report* è inghiottito (run_daily.py:110-113) e si ritorna comunque 0. Impatto: il Task Scheduler (campo "Last Run Result") e qualsiasi monitoraggio basato su exit code non vedranno mai un fallimento; l'unico segnale resta l'email, che però non parte se il crash avviene prima di `collect()`.

### MEDIA

**M1 — Fallimenti di rete per-simbolo Yahoo degradati a "empty" anche quando sollevano eccezione**
`market_data_hub/sources/yahoo_direct.py:146-157` — `yahoo_batch` raccoglie le eccezioni per-simbolo in `errors` ma le ri-solleva solo se *tutti* i simboli falliscono; per un fallimento parziale il simbolo riceve un frame vuoto e il runner logga `status="empty"` (runner.py:113-117), perdendo il messaggio d'errore. Impatto: un simbolo con problemi di rete persistenti è indistinguibile da un delisted nel `download_log`.

**M2 — README istruisce a salvare la FRED API key nel file YAML tracciato da git**
`README.md:39-41` ("open `settings.yaml` and set `fred_api_key: "YOUR_KEY"`") contraddice sia il commento del file (`settings.yaml:31-35`: "Do NOT commit a key here") sia il design del loader, che inietta i segreti solo da env (`config_loader.py:26-28`). Nota: il valore YAML *viene* comunque letto se valorizzato (`runner.py:136`), quindi l'istruzione del README funziona e induce a committare un segreto. Impatto: rischio concreto di leak della chiave al primo `git add`.

**M3 — Orientation 0 coercita a +1 nello z-score cross-country Dalio**
`market_data_hub/dalio.py:250` — `orient = _orient(g["orientation"].iloc[-1]) or 1`: l'helper `_orient` (dalio.py:66-68) mappa correttamente NaN→0, ma il successivo `or 1` trasforma **anche lo 0 legittimo** ("indicatore neutro", usato ad es. per `imports_gdp`, `fuel_exports_share` — cfr. tests/test_pipeline.py:22-30) in direzione +1. Impatto: indicatori dichiarati senza direzione contribuiscono ai `pillar_scores` e al composite come se "più alto = più sano", distorcendo lo score dei pilastri external/geopolitical.

**M4 — Scrittura del layer Dalio non transazionale**
`market_data_hub/dalio.py:371-378` — tre `DELETE FROM` seguiti da tre `executemany INSERT` in autocommit: un crash a metà (es. errore di arity su una riga) lascia `dalio_signals`/`pillar_scores`/`regime_state` vuote o parziali fino al run successivo. Il runner tratta l'errore come non bloccante (runner.py:506-507), quindi i report Dalio del giorno risultano vuoti in silenzio. Le altre scritture del progetto usano correttamente `BEGIN/COMMIT/ROLLBACK` (upsert.py:105-120, retention.py:88-97).

**M5 — Effetto collaterale a import-time: scrittura file + mutazione env SSL**
`market_data_hub/__init__.py:7-8` chiama `ensure_ssl()` all'import del package: scrive `ca_bundle.pem` accanto al package (`_ssl_bootstrap.py:24,40`, in site-packages se installato), imposta `SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE`/`CURL_CA_BUNDLE` per l'intero processo e inietta truststore. Impatto: qualunque consumatore (LazyFin, un server MCP) che importa il package si trova la configurazione TLS di processo alterata; su file-system read-only la scrittura fallisce silenziosamente (gestito, ma il bundle certifi-only su Linux sovrascrive eventuali CA di sistema configurate).

**M6 — Coverage `rebuild_coverage` carica l'intera `prices_daily` in memoria**
`market_data_hub/coverage/report.py:83-90` — `SELECT date, symbol, ... FROM prices_daily` senza filtri, poi groupby in pandas (idem `macro_series` e `crypto_ohlcv`, righe 96-114). Con l'universo attuale (~111 simboli × 15 anni ≈ 400k righe + crypto 1h dal 2018 ≈ 350k/simbolo) funziona, ma cresce linearmente col DB e viene rieseguito a ogni run, anche `--live-only` escluso solo di fatto (runner.py:478 gira per ogni run non live). Impatto: pressione memoria/tempo crescente sul task EOD.

### BASSA

**B1 — README: soglia stalled annuale errata** — `README.md:105` dichiara "A=400" ma il codice usa 550 (`coverage/stalled_detector.py:23`) e `LAG_TOLERANCE` usa 500 (`coverage/freq_detector.py:45`). Tre valori diversi per lo stesso concetto tra docs e due moduli.

**B2 — Binario morto in git** — `tickers_140_original.xlsx` (10.8 KB) è tracciato (`git ls-files`) ma non referenziato da nessun .py/.md del repo (grep senza risultati). `tickers_master.csv` invece è usato (export_to_excel.py:36). Nessun `.duckdb`/output in git (il `.gitignore` è corretto).

**B3 — Default sorgenti incoerente tra daily e backfill** — `run_backfill.py:31` esclude `factors` dal default; `runner.py:422` (`_DEFAULT_SOURCES`) le include. Un backfill "completo" non popola `factor_returns` a meno di flag esplicito.

**B4 — Path Windows hardcoded** — `setup_scheduler.ps1:15` (`$root = "D:\market_data"`), `settings.yaml:6` e `connection.py:29` (default `D:\market_data\market_data.duckdb`). Il fallback non-Windows esiste (`connection.py:24-31`) ma lo scheduler non è parametrizzabile senza editare il file.

**B5 — Race sulla creazione del DB da reader** — `connection.py:157-162`: due processi read-only concorrenti su DB inesistente tentano entrambi la creazione in write mode; il secondo può fallire per il single-writer di DuckDB. Finestra piccola, solo al primissimo avvio.

**B6 — `rows_updated` sovrastimato** — `upsert.py:73-83` conta come "updated" ogni riga già esistente per PK anche se il valore è identico; i numeri nel report email sono quindi gonfiati nei giorni di tail-refresh.

**B7 — Nessuna gestione dedicata del 429 Binance** — `binance.py:56-66`: retry generico su `raise_for_status()` senza lettura di `Retry-After`; Binance usa 429/418 con ban progressivi. Il backoff 1/4/16s in pratica basta per 6 simboli, ma è fragile se l'universo cresce.

**B8 — `z_window_n` ha cambiato semantica senza aggiornare lo schema** — `dalio.py:262` scrive il numero di *paesi* nello z cross-country, ma `schema.sql:113` documenta la colonna come "n observations used" (finestra temporale). Chi legge la tabella interpreta male il campo.

**B9 — Config in `lru_cache` mai invalidata** — `config_loader.py:24-63`: in un processo long-running (server MCP/agent che espone `agent_tools`) le modifiche ai YAML o alle env non vengono mai riviste. Per i CLI è irrilevante.

**B10 — Trigger live: ultima esecuzione alle 21:00, non 22:00** — `setup_scheduler.ps1:74-79`: RepetitionDuration di 6h da 16:00 fa cadere l'ultima ripetizione alle 21:00 (il boundary non fa fire in Task Scheduler), mentre commento e README dichiarano "16:00-22:00 ogni ora".

---

## 4. Punti di miglioramento

1. **Entry point unico del package** (già raccomandato in DEEP_AUDIT §4.1): gli 8 script top-level condividono il bootstrap `sys.path.insert(0, ...)` (run_daily.py:20, run_backfill.py:22, make_report.py:27, make_dalio_report.py:26, diagnose.py:17, export_to_excel.py:29, ...) e 5 di essi rileggono i YAML a mano invece di usare `config_loader` (es. export_to_excel.py:41-42, make_dalio_report.py:63). Un `python -m market_data_hub daily|backfill|report|diagnose` + `[project.scripts]` in pyproject.toml eliminerebbe ~150-200 LOC e renderebbe gli script veri entry point del package.
2. **Helper HTTP condiviso** (`sources/_http.py`): 6 retry-loop hand-rolled con 4 formule di backoff diverse — `fred.py:32-42` e `worldbank.py:29-41` sono identici verbatim (backoff 4^n), `imf.py:38-55` (403-aware), `bis.py:55-66` e `binance.py:56-66` (2^n), `factors.py:105-117`. Prerequisito: test dei parser di rete (oggi assenti).
3. **Helper di ingest nel runner**: la triade fetch→empty-check→upsert→log_run è copiata 5 volte (`runner.py:110-125, 155-175, 220-240, 270-287, 346-366`).
4. **Ridurre `run_dalio`** (~230 righe con 3 closure annidate, dalio.py:196-401) e spezzare `make_dalio_report.py` (565 righe, `render_html` monolitica) in template + collector.
5. **Deduplicare gli importer Excel**: `import_tickers` e `import_fred` (import_from_excel.py:131-247) differiscono solo per col_map/chiave/file — un `_import_catalog(df, key_col, col_map, yaml_path, root_key)` unico.
6. **Type hints**: buoni nel package (quasi tutte le firme annotate, `from __future__ import annotations` ovunque); assenti/parziali negli script top-level. Aggiungere un job mypy/pyright non-bloccante in CI.
7. **CI**: aggiungere uno step `pip install -e .` + `python -c "import market_data_hub"` da directory esterna, per esercitare il packaging (oggi i test girano via `conftest.py:11-13` sys.path e il pyproject non è mai validato).
8. **TODO/FIXME**: zero nel codice (grep pulito) — il debito è tracciato in docs/DEEP_AUDIT_2026-07.md §4, pratica da mantenere.
9. **`lazydatacore/series.py` e metà `timeutil`** consumati solo dai propri test (ammesso in DEEP_AUDIT §4.6): se al prossimo audit restano senza adozioni, potare gli export.

---

## 5. Piano di risoluzione dettagliato

### Fase 1 — Correttezza operativa (priorità massima, ~1 giornata)

**Step 1.1 — Trattare gli HTTP non-200 di Yahoo come errori (A1)** — Effort: **S**
- File: `market_data_hub/sources/yahoo_direct.py`, funzione `_fetch_one` (righe 99-118).
- Cosa fare: dopo il ramo `404/400`, registrare gli altri status come errore, es. `last_exc = RuntimeError(f"HTTP {r.status_code} for {symbol}")` prima di passare all'host successivo; per il 429 aggiungere uno sleep maggiorato (riusare il pattern di `imf.py:50-53`).
- Test: aggiungere in `tests/test_audit_fixes.py` un test che monkeypatcha `_session().get` per rispondere 429 su entrambi gli host e verifica che `_fetch_one` sollevi dopo i retry (e che 404 continui a restituire frame vuoto).
- Criterio di completamento: `pytest tests/test_audit_fixes.py -q` verde; un run con Yahoo in outage produce `status="error"` in `download_log`, non `empty`.

**Step 1.2 — Writer lock su `custom_series` (A2)** — Effort: **S**
- File: `market_data_hub/custom.py:106,115`.
- Cosa fare: in `store_series` e `delete_series`, avvolgere apertura connessione + upsert/DELETE in `with db_write_lock(db_path):` (import da `market_data_hub.lock`); su `DBLockTimeout` rilanciare con messaggio actionable ("another writer holds the DB; retry"). Non serve toccare LazyFin: il contratto (firma e semantica) resta identico.
- Test: in `tests/test_custom_series.py` aggiungere un test che acquisisce il lock con `db_write_lock()` in un contesto e verifica che `store_series(..., )` con timeout breve sollevi `DBLockTimeout` (parametrizzare il timeout o monkeypatchare `DEFAULT_TIMEOUT`).
- Criterio: test verde; `publish_series` di LazyFin concorrente a `run_daily` non può più causare IO error DuckDB.

**Step 1.3 — Exit code veritiero di `run_daily` (A3)** — Effort: **S**
- File: `run_daily.py:50-57,117`.
- Cosa fare: memorizzare l'esito (`download_failed = True` nell'`except`), generare comunque il report, ma chiudere con `return 1 if download_failed else 0`; idem propagare `1` se la generazione report fallisce quando è stata richiesta.
- Criterio: `python run_daily.py --report` con `run()` che solleva (monkeypatch in un piccolo test o verifica manuale con `--db` su path invalido... nota: path invalido viene creato, usare monkeypatch) ritorna exit code 1; il campo Last Run Result del Task Scheduler diventa affidabile.

**Step 1.4 — Log degli errori parziali Yahoo (M1)** — Effort: **S**
- File: `market_data_hub/sources/yahoo_direct.py:146-157` + `market_data_hub/runner.py:110-117`.
- Cosa fare: far ritornare a `yahoo_batch` anche la mappa `errors` (es. `return results, errors` o sentinella `None` nel dict per i simboli falliti); nel runner loggare `status="error", error_msg=str(errors[sym])` per quei simboli.
- Criterio: un simbolo con eccezione di rete persistente compare come `error` (col messaggio) in `download_log`, non come `empty`.

### Fase 2 — Igiene sicurezza e documentazione (~mezza giornata)

**Step 2.1 — Correggere l'istruzione FRED key nel README (M2)** — Effort: **S**
- File: `README.md:39-41`.
- Cosa fare: sostituire con l'istruzione env-only (`setx FRED_API_KEY "..."` / `export FRED_API_KEY=...`), allineata a `settings.yaml:31-35`. Opzionale hardening: in `config_loader.get_settings` emettere un warning se `fred_api_key` risulta valorizzata dal YAML.
- Criterio: nessuna istruzione nel repo suggerisce di scrivere segreti in file tracciati.

**Step 2.2 — Allineare le soglie stalled nel README (B1)** — Effort: **S**
- File: `README.md:105` → "A=550"; valutare se unificare `LAG_TOLERANCE["A"]=500` (freq_detector.py:45) con `STALLED_THRESHOLD_DAYS["A"]=550` o documentare la differenza (tolleranza score vs flag stalled).
- Criterio: grep di "400" e "550" coerente tra docs e codice.

**Step 2.3 — Rimuovere `tickers_140_original.xlsx` (B2)** — Effort: **S**
- Comandi: `git rm tickers_140_original.xlsx` (+ riga `tickers_140_original.xlsx` o `*.xlsx` in `.gitignore`, tenendo presente che `data_master.xlsx` è già ignorato come output).
- Criterio: `git ls-files | grep xlsx` vuoto.

### Fase 3 — Robustezza del layer analitico (~mezza giornata)

**Step 3.1 — Rispettare orientation 0 nello z cross-country (M3)** — Effort: **S**
- File: `market_data_hub/dalio.py:250`.
- Cosa fare: decidere la semantica: (a) escludere gli indicatori a orientation 0 da z/pillar (coerente con "nessuna direzione") oppure (b) mantenerli con z assoluto ma fuori dal composite. Implementare (a) è minimale: `orient = _orient(...)` e, se 0, salvare z=None/segnale NEUTRAL e saltare l'aggregazione pillar (il dropna a dalio.py:342 li esclude già se z è None).
- Test: estendere `tests/test_pipeline.py` verificando che un indicatore orientation=0 (es. `imports_gdp`) non alteri il segno del pillar score.
- Criterio: pillar external/geopolitical non contengono contributi da indicatori neutri.

**Step 3.2 — Transazione attorno alla scrittura Dalio (M4)** — Effort: **S**
- File: `market_data_hub/dalio.py:370-380`.
- Cosa fare: avvolgere DELETE+executemany in `con.execute("BEGIN TRANSACTION")` / `COMMIT` con `try/except → ROLLBACK`, stesso pattern di `upsert.py:105-120`.
- Criterio: un errore di INSERT lascia intatte le tabelle del run precedente (test: monkeypatch che fa fallire il terzo executemany).

**Step 3.3 — Correggere il commento `z_window_n` (B8)** — Effort: **S**
- File: `market_data_hub/db/schema.sql:113` → "n paesi nel cross-country z" (o rinominare la colonna in una futura migrazione v3).

### Fase 4 — Test mancanti (1-2 giornate)

**Step 4.1 — Test dei parser di rete** — Effort: **M**
- Nuovi file: `tests/test_sources_parsers.py`.
- Cosa fare: fixture JSON/CSV statiche per `yahoo_direct._parse` (chart JSON con null e adjclose mancante), `fred.fetch_fred` ramo CSV (monkeypatch `_http_get`), `worldbank.fetch_worldbank` (paginazione 2 pagine + pagina che fallisce), `imf.fetch_imf` (valori non numerici, anni fuori range), `bis._period_end` e parsing CSV, `binance.fetch_klines` (paginazione, `is_closed`). Nessuna rete: monkeypatch delle funzioni `_get_*`.
- Criterio: `pytest -q` verde; i moduli sources passano da 0% a copertura dei rami di parsing/errore. È il prerequisito dichiarato per il refactoring HTTP (Fase 5).

**Step 4.2 — Test dell'orchestrazione `runner`** — Effort: **M**
- Cosa fare: con `tmp_db` e monkeypatch delle funzioni `yh.yahoo_batch`/`fr.fetch_fred`/ecc., verificare: (i) status ok/empty/error in `download_log` per i tre esiti; (ii) `effective_start` incrementale dopo un primo run; (iii) `mode="backfill"` che forza `backfill_start`; (iv) lock occupato ⇒ skip pulito.
- Criterio: i 4 scenari coperti; regressioni sull'ingest intercettate prima del deploy schedulato.

**Step 4.3 — Test `agent_tools`** — Effort: **S**
- Cosa fare: su `tmp_db` seminato, verificare che ogni `tool_*` ritorni JSON parsabile, il cap `_MAX_ROWS`, e che `tool_refresh_prices` ripristini `runner.get_yahoo_tickers` anche in caso di eccezione.

### Fase 5 — Manutenibilità (opzionale, 2-3 giornate, dopo la Fase 4)

**Step 5.1 — `sources/_http.py` condiviso** — Effort: **M** — retry/backoff parametrico (formula, 403-aware, Retry-After per Binance/B7); migrare i 6 loop uno alla volta con i test della Fase 4 come rete di protezione. Criterio: una sola implementazione di retry nel package.

**Step 5.2 — Entry point unico `python -m market_data_hub`** — Effort: **L** — `market_data_hub/__main__.py` con subcommand daily/backfill/report/dalio-report/diagnose/export/import/validate; gli script top-level ridotti a shim deprecati (per non rompere `setup_scheduler.ps1`), poi aggiornare lo scheduler (`Args = "-m market_data_hub daily --report --send-email"`) e rimuovere gli shim in un secondo momento. Criterio: nessun `sys.path.insert` residuo, `[project.scripts]` in pyproject.
- In questo step risolvere anche B3 (allineare i default di backfill a `_DEFAULT_SOURCES`) e B4 (parametrizzare `$root` in setup_scheduler.ps1 con `param([string]$Root = "D:\market_data")`).

**Step 5.3 — Rifiniture** — Effort: **S/M** — B5 (creazione DB reader dentro `db_write_lock`), B6 (contare come updated solo le righe con valore diverso, via anti-join sul valore), B10 (RepetitionDuration 7h o documentare 16-21), M5 (rendere `ensure_ssl` opt-in via env `MDH_SSL_BOOTSTRAP=1` o chiamarlo solo negli entry point CLI — attenzione: è un cambio di comportamento per la macchina Windows target, coordinare col deploy), M6 (riscrivere `rebuild_coverage` con aggregazioni SQL in DuckDB invece del groupby pandas).

---

## 6. Esito dei test eseguiti

Ambiente: venv pulito in `/tmp/claude-0/.../scratchpad/venv-mdh` (Linux, Python 3.x di sistema).

```
pip install -e . -r requirements.txt pytest pytest-timeout   # OK, nessun errore
cd /home/user/market-data-hub
python -m pytest -x -q --timeout=120
```

Risultato reale:

```
........................................................................ [ 68%]
.................................                                        [100%]
105 passed in 9.03s
```

**105/105 test passati, 0 falliti, 0 skip, ~9 secondi.** La suite è interamente offline (nessuna chiamata di rete: fixture DuckDB temporanee via `MARKET_DATA_DB`, conftest.py:16-20) e copre: schema/migrazioni, upsert/vintage/retention, reader/extract/catalog, lazydatacore, pipeline end-to-end con dalio+classify su dati sintetici, guardie Excel/generatori, regression test degli audit precedenti. Moduli **senza** copertura: `runner.py` (orchestrazione), i fetcher di rete `sources/{yahoo_direct(_parse escluso indirettamente), fred, binance, worldbank, imf, bis}`, `agent_tools.py`, `make_report.py`, `make_dalio_report.py`, `diagnose.py`, `lock.py`, `_ssl_bootstrap.py`.
