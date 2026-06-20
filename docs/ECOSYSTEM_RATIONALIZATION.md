# Razionalizzazione dati dell'ecosistema — verifica di architettura e piano

> Stato: **proposta approvata nelle decisioni di fondazione** (vedi §5). Documento di
> riferimento per lo standard condiviso `lazydatacore` e per lo strato di analisi/grafici
> comune ai tool esterni al core dati.
> Repo coinvolti: `market-data-hub`, `LazyBridge`, `LazyFin`, `LazyHMM`, `LazyPulse`,
> `LazyCrawler`, `LazyTools`, `lazybridgewebsite`.

## 1. Verdetto sintetico

L'impressione di un "mostro di dati ingovernabile" è **per metà corretta**, e la distinzione
cambia il piano:

- **NON c'è sprawl di database.** La proliferazione di storage è limitata e ogni scelta è
  difendibile (vedi §3). LazyFin in particolare **non ha database sparsi**: ha un solo
  ledger JSON dentro lo Store di LazyBridge, tutto il resto è calcolato a runtime.
- **C'è invece un vero problema: manca un contratto dati comune.** Ogni tool esterno al core
  ridefinisce a modo suo *cos'è un simbolo, cos'è un timestamp, cos'è una serie di rendimenti,
  come si fa un grafico*. Questo è il punto su cui l'ecosistema "si impicca", ed è risolvibile
  **senza toccare i database**.

Il mostro non è lo storage: è **l'assenza di uno strato di contratti** tra il core dati e i
tool che lo analizzano.

## 2. Mappa dell'architettura attuale

| Repo | Ruolo | Storage | Modelli | Numerico | Grafici |
|------|-------|---------|---------|----------|---------|
| **market-data-hub** | Source of truth serie storiche/macro | **DuckDB** (1 file) + SQL raw | nessun tipo → **pandas DataFrame** snake_case | `float64` | nessuno |
| **LazyBridge** | Runtime agenti + stato operativo | **SQLite** (EventLog + Store) | Pydantic (`Envelope`) | n/a | web viz (trace agenti) |
| **LazyFin** | Portfolio / risk / scoring | ledger JSON in Store LazyBridge (SQLite opz.) | **15 modelli Pydantic** canonici | **Decimal** (8dp) | nessuno → delega a `Memo` |
| **LazyHMM** | Regimi HMM su serie | **SQLite proprio** (`db.py`: time_series, results, **plot PNG come BLOB**) | dataclass (`RegimeRun`, `FitResult`) | numpy / `float` | **matplotlib + PlotTheme** (bloomberg/light/minimal) |
| **LazyPulse** | Orchestrazione agenti | Store LazyBridge (SQLite) | Pydantic | n/a | nessuno |
| **LazyCrawler** | Raccolta web | **SQLite proprio** (pages, artifacts, FTS5) | Pydantic | n/a | nessuno |
| **LazyTools** | Libreria condivisa di connettori/tool | **nessuno** (stateless) | `Memo`/`Section`/`TableBlock` | n/a | solo tabelle HTML/MD |
| **lazybridgewebsite** | Docs | nessuno | nessuno | n/a | nessuno |

### Flusso dati di alto livello

```
Sorgenti (Yahoo/FRED/Binance/WorldBank/IMF/Factors)
   │
   ▼
market-data-hub  ──DuckDB──►  reader.py / agent_tools.py  ──pandas──►  tool esterni
   (source of truth serie/macro)

LazyCrawler ──SQLite──► pagine/artifacts ──► LazyTools(WebTools) ──► agenti
LazyBridge  ──SQLite──► Store/EventLog (stato operativo + audit) ──► LazyFin/LazyPulse
```

## 3. I database reali (sfatare il mito dello sprawl)

Conteggio effettivo: **1 DuckDB** (il core) + **SQLite** usato con due ruoli distinti e
legittimi:

1. **DuckDB** in market-data-hub — serie storiche/macro analitiche colonnari. Scelta corretta
   (OLAP), single-file, schema versionato.
2. **SQLite via LazyBridge Store/EventLog** — stato operativo e audit trail degli agenti.
   Riusato da LazyFin (ledger) e LazyPulse (task lifecycle).
3. **SQLite "proprio"** in LazyHMM (`db.py`: time_series, model_results, plot PNG come BLOB) e
   LazyCrawler (pages, artifacts, FTS5) — cache/risultati locali.

Questo è **polyglot persistence ragionevole**, non sprawl. *Su questo non si interviene.*

## 4. Le quattro incoerenze reali (con evidenze)

**4.1 Identità del titolo divergente.** Lo stesso strumento ha tre nomi nel sistema:
- market-data-hub: `symbol = "AAPL"` / `"BTCUSDT"` (stringa piatta)
- LazyFin: `security_id = "ticker:AAPL"`, `company_id = "cik:0000320193"` (namespaced)
- LazyHMM: nome colonna generico del DataFrame

Inoltre **market-data-hub è già namespaced di fatto, ma in modo implicito**: l'identità è
spalmata su quattro colonne diverse — `prices_daily.symbol`, `crypto_ohlcv.symbol` (in realtà
una coppia Binance), `macro_series.series_id`, `factor_returns.factor`. "Che cosa sei" lo
deduci da *quale tabella leggi*.

**4.2 Politica numerica incoerente e non documentata.** market-data-hub e LazyHMM lavorano in
`float64`; LazyFin impone `Decimal` ovunque. È corretto in principio (float per analisi,
Decimal per denaro) **ma il confine non è scritto da nessuna parte**.

**4.3 "Returns" implementati due volte con semantiche diverse.**
`market-data-hub/extract.py` (log-return su pandas) e `LazyFin/kernel/returns.py` (Decimal,
ln/exp con precisione 50). Due verità per la stessa quantità.

**4.4 Grafici completamente ad-hoc.** L'unico sistema maturo di charting è il `PlotTheme` di
LazyHMM (temi bloomberg/light/minimal su matplotlib), ma è **prigioniero di LazyHMM**. LazyFin
non sa fare grafici (delega a `Memo`, che fa solo tabelle). Ogni nuovo tool riparte da zero.

## 5. Decisioni di fondazione (approvate)

1. **Identità → namespaced.** Tipo canonico `InstrumentId` (`equity:AAPL`, `crypto:BTCUSDT`,
   `macro:FEDFUNDS`, `factor:MOM`, `cik:0000320193`). Unifica le quattro identità interne del
   core *e* combacia con LazyFin. **Il DuckDB non si tocca**: un resolver in `lazydatacore`
   mappa `InstrumentId ⇄ (tabella, chiave piatta)`.
2. **Numerico → `float64` in analisi/grafici, `Decimal` nel ledger LazyFin.** Confine e
   conversioni esplicite definiti in `lazydatacore`.
3. **Host → `lazydatacore` come sottopacchetto self-contained dentro `market-data-hub`.**
   Contratti accanto alla source of truth, grafo dipendenze ad albero, LazyBridge resta il
   core di runtime agnostico al dominio.

### Nota sui due "core"

L'ecosistema ha **due nozioni distinte di core** che vanno tenute separate:
- **Core di runtime / piattaforma = LazyBridge** — framework per agenti multi-provider,
  *deliberatamente agnostico al dominio*. Non deve conoscere `PriceBar` o `Currency`.
- **Core di dominio dati = market-data-hub** — source of truth delle serie che i contratti
  descrivono. È qui che vivono i contratti dati.

## 6. Architettura target a livelli

```
L0  CONTRATTI        lazydatacore  (in market-data-hub)  — pydantic puro, zero deps pesanti
                     ├─ Identificatori (InstrumentId, Currency)
                     ├─ Tempo (UTC tz-aware, helper ISO-8601)
                     ├─ Schemi serie (PriceBar, ReturnSeries, TS wide/long)
                     ├─ Envelope risultati (AnalysisResult + Provenance)
                     └─ Resolver InstrumentId ⇄ (tabella, chiave) per il DuckDB

L1  DATA CORE        market-data-hub (DuckDB)            → output conforme a L0
L1b STATO/RUNTIME    LazyBridge Store/EventLog (SQLite)  → invariato, resta agnostico

L2  ANALISI COMUNE   lazyquant (in LazyTools, extra)     → returns, vol, drawdown, resample
L2b GRAFICI COMUNI   lazyviz   (in LazyTools, extra)     → PlotTheme di LazyHMM promosso a lib
                                                           + chart-spec dichiarativo

L3  TOOL ESTERNI     LazyFin · LazyHMM · LazyPulse · LazyCrawler
                     consumano L0/L2/L2b, non reinventano nulla
```

Grafo delle dipendenze (DAG, `lazydatacore` come foglia in basso):

```
        lazydatacore  (pydantic puro)
          ▲   ▲   ▲   ▲
          │   │   │   └────────────── LazyHMM
          │   │   └────────────────── LazyFin
          │   └──── market-data-hub          LazyBridge (runtime, resta agnostico)
          └──────── lazyquant/lazyviz (LazyTools)
```

## 7. Lo standard concreto: `lazydatacore`

Pacchetto **solo Pydantic, senza pandas/numpy/matplotlib**, così *tutti* possono dipenderne
(anche market-data-hub, oggi dependency-light).

### 7.1 Identità
- `InstrumentId`: stringa namespaced `"<dominio>:<chiave>"`; domini iniziali
  `equity | crypto | fx | macro | factor | cik | isin`.
- `resolve(instrument_id) -> (tabella_duckdb, chiave_piatta)` e inverso, unico punto di
  traduzione. market-data-hub e LazyFin smettono di tradurre ad-hoc.

### 7.2 Tempo
- Tutti i timestamp **UTC tz-aware**, ISO-8601. Helper di parsing/normalizzazione condivisi
  (già coerente di fatto: market-data-hub, LazyCrawler, LazyFin usano UTC).

### 7.3 Schemi serie
- `PriceBar` (OHLCV: open/high/low/close/adj_close/volume) — contratto minimo che una serie
  di prezzo deve rispettare.
- `ReturnSeries` — contratto per i rendimenti (tipo: simple/log; frequenza; decimale, non %).
- Convenzione **wide vs long** congelata (market-data-hub già offre entrambe via `reader.py`).

### 7.4 Envelope risultati
- `AnalysisResult` + `Provenance(source, as_of, tool_version)` — promozione a standard del
  pattern già presente in LazyFin. Ogni output di analisi (regimi, score, risk) viaggia in
  questo envelope, JSON-serializzabile.

### 7.5 Politica numerica (regola scritta)
- `float64` per serie, analisi e grafici (L2/L2b).
- `Decimal` **solo** nel ledger monetario/portfolio di LazyFin (L3).
- Conversione esplicita ai bordi, helper in `lazydatacore`.

## 8. Analisi e grafici comuni

- **`lazyquant`** (modulo in LazyTools, extra `lazytools[quant]`): assorbe e unifica
  `extract.py` (market-data-hub) e `returns.py` (LazyFin). Una sola implementazione di
  log-return, pct-change, volatilità annualizzata, drawdown, resample. Test di equivalenza
  numerica prima dello switch.
- **`lazyviz`** (modulo in LazyTools, extra `lazytools[viz]`): estrazione e generalizzazione
  del `PlotTheme` di LazyHMM (temi + `plot_series_with_regimes`, barcode, small-multiples) +
  **chart-spec dichiarativo** così LazyFin chiede un grafico senza scrivere matplotlib.
  Si estende `Memo` di LazyTools per accettare `ChartBlock`, non solo `TableBlock`.

## 9. Piano per fasi (incrementale, nessun big-bang)

| Fase | Contenuto | Rischio |
|------|-----------|---------|
| **0** | Decisioni di fondazione (§5) | — *fatto* |
| **1** | `lazydatacore` in market-data-hub: identità, tempo, schemi serie, envelope, resolver. Solo schemi, nessuna logica | basso |
| **2** | Conformità del core: `reader.py`/`agent_tools.py` etichettano l'output con i tipi L0 (adapter sottili). DuckDB invariato | basso |
| **3** | `lazyquant`: unifica le primitive di rendimento/rischio. market-data-hub e LazyFin ri-esportano da qui | medio |
| **4** | `lazyviz`: estrazione PlotTheme; LazyHMM migra a usarlo; LazyFin produce grafici via `Memo` esteso | medio |
| **5** | Registry di mapping simbolo ↔ security_id in `lazydatacore` per chiudere `AAPL` ↔ `ticker:AAPL` | basso |

Ogni fase è indipendente e committabile da sola. Fermandosi alla Fase 3 sono già eliminati i
due problemi peggiori (identità + returns duplicati).

## 10. Principi guida

1. **I database restano dove sono.** Si standardizzano i *contratti*, non lo storage.
2. **`lazydatacore` è una foglia senza deps pesanti** — chiunque può dipenderne, niente cicli.
3. **LazyBridge resta agnostico al dominio.** Nessuno schema finanziario nel runtime.
4. **Una sola implementazione per ogni primitiva** (identità, returns, tema grafico).
5. **`float` per analizzare, `Decimal` per il denaro** — confine esplicito e documentato.
