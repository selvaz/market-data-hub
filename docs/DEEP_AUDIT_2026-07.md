# Deep audit — market-data-hub + agent-facing tool surface (2026-07)

> Scope: the market-data-hub pipeline and every tool surface an LLM agent can
> reach it through — `agent_tools` / the `query-market-data-hub` skill (this
> repo), the LazyTools `datahub` connector, and the consumers (LazyHMM
> `load_from_datahub`, LazyFin `data/datahub.py` + `data/regime.py`).
> Method: four parallel audit passes (core pipeline, agent surface, LazyTools
> connector, consumers), every finding re-verified by hand before acting.
> Objective, as usual: **simplify where possible**.
>
> Everything in §1–§3 is applied in this branch. §4 lists what was found but
> deliberately *not* changed, with the reasoning, so nothing is lost.

---

## 1. Bugs found and fixed (this repo)

| # | Severity | Bug | Fix |
|---|----------|-----|-----|
| B1 | **High** | `extract_series` silently ignored `frequency`: `_resample` existed but was never called, so `frequency="W"/"M"/"Q"` returned native-frequency rows while `meta["frequency"]` claimed otherwise. Invalid frequencies were silently accepted. This was live agent surface (`tool_get_series`). | Frequency is validated up front; levels are resampled (`last()` per bucket) **before** the transform, so W/M/Q log-returns compound correctly. `extract_returns`' separate hand-rolled resample path collapsed into a single `extract_series` call (it is now a pure convenience wrapper). Regression tests added. |
| B2 | **High** (crypto path) | `extract_series(domain="crypto")` queried `timeframe='adj_close'`: the code read `field or "1d"` but `field` defaults to `"adj_close"` (never falsy), so the intended `"1d"` fallback was unreachable and the call returned an empty frame with no error. | The price-field default now maps to the `1d` timeframe for crypto; `meta["field"]` reports the effective timeframe. Regression test added. |
| B3 | **Med-High** | `run_backfill.py` wrote to DuckDB **without the writer lock** and its parallel orchestration never rebuilt `macro_panel_coverage` nor ran the dalio/classify layer — a multi-hour backfill overlapping a scheduled EOD/live task meant a single-writer IO error for one of the two. | `runner.run` gained `mode="backfill"` (per-source `backfill_start` dates — note a plain `run(start_override=…)` would *not* have been equivalent, the backfill dates are per source); `run_backfill.py` is now a thin CLI over it and inherits the lock, both coverage rebuilds and the analytical layer. |
| B4 | **Med** | World Bank pagination failures were swallowed (`except: break`): a WB outage mid-run produced a silently-truncated frame logged as `ok`, and the IMF fallback was never tried. | Page-fetch failures now propagate; `run_macro_panel` already logs them as `status="error"` per indicator and the fallback logic engages. |
| B5 | **Med** | A persistent Yahoo outage was indistinguishable from a delisted symbol: `_fetch_one` returned an empty frame after exhausting retries, so a full outage logged as ~111 × `status="empty"` with zero errors. | `_fetch_one` raises the last network error after retries (HTTP 404/400 still return empty = legitimately delisted); `yahoo_batch` re-raises when **every** symbol failed with an exception, which the runner logs as an error batch. |
| B6 | **Low-Med** | `rebuild_coverage` only upserted: a symbol removed from the universe kept its last coverage row forever, permanently inflating the stalled-series alert. | `DELETE FROM coverage_report` before the rebuild — same full-rebuild policy `macro_panel_coverage` already used. |
| B7 | **Low** | `int(g["orientation"].iloc[-1] or 0)` in dalio: a NULL orientation arrives as `NaN`, which is *truthy*, so `int(nan)` raised `ValueError` and emptied all three dalio tables for the run. | NaN-safe `_orient()` helper at both call sites. |
| B8 | **Low** | FRED public-CSV parsing assigned exactly 2 column names; a >2-column `fredgraph.csv` response would raise. | Keep the first two columns before renaming. |

## 2. Simplifications applied (this repo)

- **Dead yfinance path deleted** (~150 LOC + 1 dependency). `sources/yahoo.py`
  had delegated to the curl_cffi chart API (`yahoo_direct.py`) for a while; the
  parallel yfinance implementation (`_extract_symbol`, `_download_chunk`,
  `get_session`, `get_last_price_live`, `_FIELDS`, version sniffing) had **zero
  callers**. Removed together with the `yfinance` requirement, the two
  yfinance warning filters in `__init__.py`, and `reinstall_yfinance.ps1`.
- **`sources/base.py` deleted** (30 LOC): `SourceResult` was never imported
  anywhere — an abstraction proposed and never adopted.
- **One `DataHubTools`, not two.** This repo shipped a LazyBridge
  `ToolProvider` producing the same 11 `datahub_*` tool names as the LazyTools
  `datahub` connector — two competing providers, drifting descriptions, and a
  stale docstring still calling the LazyTools move "planned" (it had shipped).
  Grep across all seven repos: **nothing imported the MDH provider**. Deleted
  it (plus the `agent` extra); `agent_tools` keeps the framework-free `tool_*`
  functions as the single source of tool semantics, and the LazyTools
  connector is the one LazyBridge binding (it gained the opt-in
  `datahub_refresh_prices` write tool so no capability was lost — see §3).
- **`lazydatacore` pruned to what is actually shared** (−250 LOC + 290 test
  LOC). Two whole modules had no consumer anywhere (verified across all seven
  repos, tests and docs included):
  - `registry.py` (symbol ⇄ `InstrumentId`, "closing the gap that forced every
    tool to wrap bare symbols ad-hoc") — the one consumer with that need,
    LazyHMM, wraps bare symbols inline in `contract._as_instrument` and never
    adopted it.
  - `quant.py` ("the single float implementation… pinned by
    numeric-equivalence tests") — **the claim was false**: `extract.py` kept
    its pandas ops, LazyFin kept its Decimal kernel, and no equivalence test
    imported it. It was a third copy of the math, not the unification.
  Both are one `git revert` away if the convergence plan restarts —
  `docs/ECOSYSTEM_RATIONALIZATION.md` phase table updated accordingly (wire
  the consumers *first* next time).
- **Dead analytics/config surface removed**: dalio `_zscore` + `_trailing_avg`
  (superseded by the cross-country z-score), the three `settings.yaml` dalio
  thresholds no code ever read (`credit_gap_near_zero`,
  `debt_income_gap_high/low`), `clean_price_frame` + the computed-but-never-
  persisted `adj_ratio_anomaly` flag in `coverage/quality_checks.py`, the
  parsed-but-never-read `--full` flag in `run_daily.py`.
- **Duplication folded**: the default source list in `runner.run` was written
  twice (the `macro_done` gate could silently diverge) → one `_DEFAULT_SOURCES`
  constant; `extract_returns`' duplicate resample block → gone with B1.
- **Docs/SKILL made truthful**: SKILL.md no longer implies `allow_refresh` is
  reachable through a connector that didn't expose it, and no longer points at
  two competing providers; README/EXTRACTION/ARCHITECTURE/LAZYDATACORE updated
  for everything above.

Net for this repo: **~700 LOC of code+tests deleted, one dependency dropped,
8 bugs fixed**, with the test suite growing by 5 regression tests
(97 tests, all green).

## 3. Changes in the related repos

- **LazyTools** — the connector is now the single LazyBridge binding for the
  hub: added the opt-in `datahub_refresh_prices` write tool
  (`DataHubTools(allow_refresh=True)`, read-only by default, mirroring the
  gating convention of the other connectors); added a **signature-parity
  contract test** between the `DataHubBackend` Protocol and
  `market_data_hub.agent_tools.tool_*` plus an end-to-end test through the
  real backend (both skip when the hub isn't installed) — the Protocol is
  hand-mirrored in 4 places and previously nothing would catch drift; removed
  the `lazytoolkit[datahub]` extra (below).
- **LazyTools + LazyHMM** — both declared `datahub = ["market-data-hub"]` as a
  **bare PyPI name for a private, git-only package**: the extra could never
  resolve, and worse, anyone squatting `market-data-hub` on PyPI would get
  installed instead (dependency confusion). Extras removed; every install hint
  (ImportError messages, docs, READMEs) now points at
  `pip install 'market-data-hub @ git+https://github.com/selvaz/market-data-hub.git'`
  — the pattern LazyFin already used correctly.
- **LazyHMM** — removed the dead first import path in
  `datasources/datahub.py` (`from market_data_hub import extract_returns`
  can never succeed: the package intentionally re-exports only modules), so
  one live import path remains.

## 4. Found, deliberately not changed (recommendations)

Ranked by value; all medium-risk refactors that deserve their own PR and, in
some cases, a test first.

1. **Single CLI entrypoint** (`python -m market_data_hub daily|backfill|…`).
   The ten root scripts share a `sys.path` bootstrap (8 copies), re-implement
   YAML loading despite `config_loader` (5 copies), and `run_daily.py` inlines
   `make_report`'s main. Collapsing the *mains* (not the leaf logic) removes
   ~150–200 LOC and 8 root files — but changes the scheduler command lines
   (`setup_scheduler.ps1`) and user habits, so it needs coordination with the
   Windows deployment.
2. **One shared HTTP retry helper** (`sources/_http.py`). Six hand-rolled
   retry loops with four different backoff formulas (fred/worldbank are
   verbatim-identical); IMF's WAF 403 handling becomes a parameter. Touches
   every source's failure semantics → wants the (currently missing) parser
   tests for the network fetchers first.
3. **Runner ingest helper.** The fetch → empty-check → upsert → `log_run`
   triad is copy-pasted five times (~80 LOC); extract `_ingest(...)` once
   `runner.run` orchestration has direct test coverage (today only the
   post-ingest pipeline is tested).
4. **LazyFin `regime.py` refits the HMM on every call** and squats the global
   `result_key="lazyfin_macro_regime"` in LazyHMM's store. LazyHMM ships an
   explicit warm-reuse mechanism (`regime_params_list` / `apply_regime_params`
   — "REUSE BEFORE REFITTING" in its own docstring) that LazyFin ignores; the
   fix belongs in LazyFin and needs behavioural-parity checks.
5. **Two hub→HMM ingestion idioms.** LazyFin hand-rolls extract+dropna+inline
   matrix while `lazyhmm.load_from_datahub` exists for exactly this (with a
   deliberate NaN policy). Converging on the loader needs a `yoy`/multi-period
   transform in `extract_series` first (its `pct_change` is single-period —
   the reason LazyFin computes YoY itself).
6. **`lazydatacore/series.py` + half of `timeutil`** (`PriceBar`,
   `OHLCV_COLUMNS`, `Frequency`, `ReturnKind`, `validate_*`, `to_iso`,
   `parse_iso`) are still consumed only by their own tests. Kept for now:
   unlike registry/quant they are pure declarative schema with doc value. If
   still unadopted at the next audit, trim the exports.
7. **`reader.read_instrument` / resolver adoption**: the documented "L0
   adapter" has zero external adopters (LazyHMM/LazyFin import identity types
   but query via `extract_*`). Either promote it into the consumers or accept
   it as documentation. **Do not delete identity/result** — those are the
   genuinely shared parts (LazyFin `identity.py`, LazyHMM `contract.py`).
8. **Excel round-trip subsystem** (~860 LOC across export/import/dictionary):
   `import_tickers` / `import_fred` / `import_macro_panel` are one generic
   merge loop written three times. A `merge_sheet(...)` helper saves ~110 LOC;
   extend `tests/test_guards.py` before refactoring.
9. **Smaller items**: move `ensure_ssl()` from package-import side effect to
   the CLI entrypoints (pairs with #1; today it writes `ca_bundle.pem` into
   site-packages on import); `catalog.list_crypto_symbols`/`list_factor_sets`
   reach into the private `reader._con` — give reader two small functions;
   `diagnose.py` hard-codes the `:1h` suffix for crypto history; per-symbol
   `duration_sec` in a failed Yahoo batch logs the full batch duration for
   every symbol; the empty migration ladder in `db/connection.py` is ~75 LOC
   of insurance for zero migrations (tested and cheap — fine to keep until a
   real v2 migration exists).
10. **LazyFin/lazydatacore envelope triplication** (`SourceRef`/`Provenance`/
    `Money` defined in both, "keep in sync" by comment; plus LazyFin's
    identity fallbacks): the honest fix is extracting `lazydatacore` into its
    own installable package so LazyFin can depend on it without dragging
    duckdb/pandas. Bigger packaging decision — out of scope here.

## 5. Tool-surface map after this audit (for orientation)

```
agent (LazyBridge)
  └── lazytools.connectors.datahub.DataHubTools     ← the ONE ToolProvider
        11 read tools datahub_* (+ opt-in datahub_refresh_prices)
        └── DataHubBackend Protocol  (fake for tests; parity-tested vs tool_*)
              └── market_data_hub.agent_tools.tool_*   ← single source of truth
                    ├── catalog.*  (discovery: config + coverage join)
                    └── extract.*  (analysis-ready matrices over reader.*)
LazyHMM  load_from_datahub ──► extract.extract_returns   (depot ingestion)
LazyFin  data/datahub.py  ───► extract.extract_series    (prices, monitor)
LazyFin  data/regime.py   ───► extract.extract_series + lazyhmm.fit_regimes
```
