# ADR 0001 — Financial Data Hub v3.1: domini, identità e bridge LLM

**Stato:** accettato (2026-07-11)
**Contesto completo:** [FINANCIAL_DATA_HUB_PLAN_V3_1.md](../FINANCIAL_DATA_HUB_PLAN_V3_1.md) e [IMPLEMENTATION_PLAN_V3_1.md](../IMPLEMENTATION_PLAN_V3_1.md).

## Decisioni

1. **Ownership dei dati.** `market-data-hub` è l'unico componente autorizzato a scaricare, normalizzare, versionare e servire dati finanziari. I consumer (LazyFin, LazyStats, LazyTools, notebook) non chiamano provider finanziari esterni.
2. **Bridge LLM unico.** `LazyTools` è l'unico bridge LLM: catalogo, wrapping, limiti di output, profili, trust e gating delle scritture vivono lì. In questo repo `agent_tools.py` è un adapter sottile sopra `services/`, `reader`, `extract`.
3. **Librerie pure.** `LazyFin` e `LazyStats` restano librerie Python pure, utilizzabili senza importare `lazybridge`.
4. **Dati nel processo, non nel prompt.** L'agente riceve solo risultati, metadati, estratti limitati e identificativi di job/provenance. Mai serie raw complete o testo filing intero.
5. **Read vs write.** I tool di lettura non fanno rete e non mutano stato. Le ingestion sono capability `ensure_*` esplicite, separate, auditabili, con job persistente (`job_id`) e run record (`run_id`).
6. **Identità corretta.** Issuer (CIK), instrument e listing (ticker/exchange/valuta) sono entità distinte. `ticker` non è un'identità universale: la risoluzione ambigua ritorna candidati, non indovina.
7. **Provenance.** Ogni analisi multi-serie riferisce uno snapshot manifest (identità, filtri, run_id/hash usati, versione calcolo). I facts SEC sono append-only.
8. **LazyHMM/LazyRay → LazyStats** è un obiettivo di fine percorso, non un prerequisito per prezzi o bilanci.

## Tool vietati nel profilo LLM `financial_research` standard

- Fetch diretto verso provider (Stooq, EDGAR, Yahoo, FRED) da parte di consumer o tool read-only.
- Loader da file locale arbitrario.
- Matrici raw: `datahub_get_series`, `datahub_get_returns` (disponibili solo nelle API Python reader/extract o in un profilo tecnico esplicito e limitato).
- Testo filing completo o HTML/XBRL integrale nel contesto.
- Tool read-only che aggiornano implicitamente il DB.

## SLO iniziali (rivedibili)

| Parametro | Valore iniziale |
|---|---|
| Freshness prezzi daily | ≤ 2 giorni di borsa |
| Timeout attesa sincrona `ensure_*` | 30 s, poi `queued`/`running` con `job_id` |
| Retry job | 3 tentativi, backoff esponenziale |
| Budget output LLM | discovery 50 candidati; facts/statement 100 righe × 12 periodi; outlier 100 default / 250 hard cap; filing extract 3 chunk × 4.000 char |
| Retention artifact | da decidere con il primo artifact store (ADR futuro) |

## Conseguenze

- Lo schema DuckDB acquisisce `issuers`/`instruments`/`listings`/`identifier_aliases` e `ingestion_jobs`/`ingestion_runs` (migrazione v5); `prices_daily` guadagna `listing_id` nullable in transizione.
- La semantica pubblica vive in `market_data_hub/services/` (prima nuova superficie: `services.prices`); CLI, notebook e adapter LLM invocano le stesse funzioni.
- Nel MVP locale l'enforcement è contrattuale (CI + boundary test), non di sicurezza: l'accesso SQL diretto dai consumer resta vietato per convenzione e test.
