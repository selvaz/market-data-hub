# M2b consensus linking: production-backup evidence

PR #127 builds on #123 (stacked on #122). The branch is `feat/m2b-consensus-linking`; the lead will rebase it onto the integration branch. No merge, force push, deployment, production database write, archive write, or external contract-check edit was performed.

Verification on 2026-10-24 used `D:\Temp\mdh-m2b-pe_u5jua\macro_releases.sqlite`, created with the SQLite backup API from a stable private main/WAL snapshot. SQLite never opened production. Old consensus code and aliases at `353240a` were loaded privately, and old/new rules ran against the same initial database and read-only FF archive. The full local report is `D:\Temp\mdh-m2b-pe_u5jua\verification.json`; the committed [evidence JSON](m2b-consensus-verification.json) retains counts and skipped titles.

## Readings and skip reasons

66 readings were added across 9 families; 34 are pre-release eligible. The other 32 are retained as post-release observations and cannot be used as pre-release consensus. All other 24 certified families added zero because this archive has no eligible/matching supported print for them.

| Family | Readings added | Captured before release |
|---|---:|---:|
| `us_housing_starts` | 12 | 4 |
| `uk_cpi` | 12 | 6 |
| `uk_labour_market` | 6 | 2 |
| `us_jobless_claims` | 6 | 4 |
| `us_umich_sentiment` | 6 | 5 |
| `ea_ec_surveys` | 6 | 4 |
| `uk_total_pay` | 6 | 2 |
| `uk_retail_sales` | 6 | 5 |
| `pboc_lpr` | 6 | 2 |
| **Total** | **66** | **34** |

| Skip reason | Before | After |
|---|---:|---:|
| `capture_time_not_in_manifest` | 95 | 164 |
| `forecast_missing` | 9 | 9 |
| `release_event_not_found` | 42 | 24 |
| `unmapped` | 1,308 | 1,191 |

Both passes saw 1,454 events. Newly mapped titles expose another 69 observations without manifest evidence: `capture_time_not_in_manifest` rises from 95 to 164. The original 95 remain skipped, and no mtime is promoted to capture time. The 1,191 remaining `unmapped` occurrences are outside the exact certified measures: speeches, auctions, private PMIs, other countries/measures, Core PPI, existing-home sales, and 5Y LPR. Core PPI and existing-home sales are absent from this catalogue. ECB refinancing is not the certified deposit-rate scalar; 5Y LPR is not the certified 1Y scalar.

The 24 remaining `release_event_not_found` occurrences are six each of CNY Unemployment Rate, GBP Claimant Count Change, GBP Public Sector Net Borrowing, and JPY Trade Balance. They enter through pre-existing stored aliases and have no reviewed certified event; no event is invented for them.

The pass created 161 events, 200 schedule versions and 230 nonnumeric memberships. A second ingest processed zero snapshots and reused all 15 checkpoints. Integrity and foreign-key checks passed. Production main-file and raw-archive hashes stayed unchanged, official vintage counts stayed unchanged, and all 11 FF consensus entries exposed on cards retained null surprise and false surprise eligibility.

## Next week

| Family | Reviewed event ID | UTC cutoff | Real October 23 forecast |
|---|---|---|---|
| `us_gdp` | `BEA:us_gdp:advance:2026-10-29` | `2026-10-29T12:30:00.000000+00:00` | Absent |
| `us_pce` | `BEA:us_pce:regular:2026-10-29` | `2026-10-29T12:30:00.000000+00:00` | Absent |
| `us_durable_goods` | `Census:us_durable_goods:advance:2026-10-27` | `2026-10-27T12:30:00.000000+00:00` | Absent |
| `fomc` | `FederalReserve:fomc:final:2026-10-28` | `2026-10-28T18:00:00.000000+00:00` | Absent |

The raw directory contains 15 this-week snapshots dated October 9 through October 23. The manifest-backed October 23 snapshot has no GDP, PCE, durable-goods or FOMC forecast for October 26-30. Real-data linking for those forecasts is therefore **not verified**. They were not synthesized or fetched. Offline tests with an October 23 manifest capture prove all four forecast links, the issuer period/stage, and official actuals subsequently using the same event IDs.

Pre-release readings exist on October 18-22 (11, 11, 6, 4, 2 respectively); October 23 has zero. There is no October 24 snapshot. The unmodified external `consensus_five_days` check, pointed at the backup using `MACRO_RELEASES_DB`, exits 1 for missing pre-release days October 23/24 and for the obsolete log requirement. A successful future five-day production streak cannot be claimed from this archive.

## Exact external contract-check change for the lead

In `C:\ProgramData\lazytools\contract_checks\macro_checks.py:94`, replace:

```python
CONSENSUS_JOBS = ("macro_releases_daily", "econ_calendar")
```

with:

```python
CONSENSUS_JOBS = ("macro_releases_daily",)
```

In `consensus_five_days.logs()` (lines 650-670), read the consensus result inside the JSON emitted by `macro_releases_daily`, under `C:\ProgramData\InvestmentCommittee\marketdata\logs\macro_releases_daily\*.log`. Do not require recent logs for disabled `econ_calendar` and do not use overall official-family status as the consensus outcome. The corresponding persisted result is `collect_run_consensus.result_json` for the same `run_id`.

The inspected October 24 log mixes an ordinary-text header with UTF-16LE/NUL-padded process output. Normalize the current log before locating JSON or matching HTTP 429:

```python
text = path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")
start = text.find("\n{")
if start < 0:
    raise CheckFailed(f"no daily result JSON in {path}")
run, _ = json.JSONDecoder().raw_decode(text[start + 1:])
step = run.get("consensus")
if not isinstance(step, dict) or step.get("status") not in SUCCESS_STATUSES:
    raise CheckFailed(f"missing/failed consensus step in {path}")
# Apply _HTTP_429 to json.dumps(step), retaining the existing no-log failure.
```

Keep the five-consecutive-pre-release-days SQL and provider-label/no-averaging checks unchanged. Fixing log selection does not fabricate the missing October 23/24 readings. This external file was read and executed, never edited.

## Calendar and verification boundaries

Calendar membership uses the existing `unavailable` reading representation with null value, null publication time, an unverified issuer series, and explicit reviewed calendar provenance. It cannot create an actual vintage or a numeric resolution. Event identity matches `adapters.persist`: issuer, family, stage, edition. Existing issuer schedule versions take precedence; no schedule is overwritten. BOJ retains a date-only issuer-midnight lower bound, and known release instants use issuer time zones and DST. Daily actuals cannot publish earlier on a scheduled release day.

Only existing reviewed calendar entries are seeded. This branch has no reviewed forward UK monthly GDP edition table, so no UK GDP date was extrapolated. Missing entries remain fail-closed. Official future publications and a future five-day production streak are not observable yet; event reuse is tested offline. No issuer HTTP, new dependency, or production job was needed for this verification.

Validation: all requested `tests/test_macro_*.py` and `tests/test_indicator_cards.py` tests passed: **1,078 passed, 1 skipped**. The skipped legacy integration test requires `.tmp/prod-copy.sqlite`; it is not supplied in this worktree. M2b's separate production-backup verification above ran successfully. The 109 targeted consensus/calendar tests passed, as did the four daily-runner/review regressions covering independent calendar membership. `ruff check .` and `git diff --check` passed. Tests used local fixtures and made no network requests.

Reproduce:

```powershell
& C:\ProgramData\spyder-6\python.exe -B scripts/verify_macro_m2b.py `
  --source C:\ProgramData\InvestmentCommittee\live\db\macro_releases.sqlite `
  --destination-root D:\Temp `
  --consensus-raw-dir C:\ProgramData\InvestmentCommittee\econ_calendar\raw
```

## Reviewed alias table

54 exact title/currency aliases cover all 33 certified families. Every alias has a title-specific justification in [ff_aliases_v1.json](../market_data_hub/macro_releases/ff_aliases_v1.json). Stages remain distinct; an explicit title qualifier never changes the measure or transform.

| Currency | FF title | Indicator | Stage |
|---|---|---|---|
| USD | CPI m/m | `us_cpi_headline_mm` | regular |
| USD | CPI y/y | `us_cpi_headline_yy` | regular |
| USD | Core CPI m/m | `us_cpi_core_mm` | regular |
| USD | Core CPI y/y | `us_cpi_core_yy` | regular |
| GBP | GDP m/m | `uk_gdp_mm` | regular |
| EUR | Final CPI y/y | `ea21_hicp_yy` | final |
| USD | Non-Farm Employment Change | `us_payrolls_change` | regular |
| USD | Advance GDP q/q | `us_gdp_qq_saar` | advance |
| USD | Prelim GDP q/q | `us_gdp_qq_saar` | second |
| USD | Final GDP q/q | `us_gdp_qq_saar` | third |
| USD | PPI m/m | `us_ppi_mm` | regular |
| USD | PPI y/y | `us_ppi_yy` | regular |
| USD | JOLTS Job Openings | `us_jolts_openings` | regular |
| USD | PCE Price Index m/m | `us_pce_price_index_mm` | regular |
| USD | PCE Price Index y/y | `us_pce_price_index_yy` | regular |
| USD | Core PCE Price Index m/m | `us_core_pce_mm` | regular |
| USD | Core PCE Price Index y/y | `us_core_pce_yy` | regular |
| USD | Retail Sales m/m | `us_retail_sales_mm` | advance |
| USD | Core Retail Sales m/m | `us_retail_sales_ex_auto_mm` | advance |
| USD | Housing Starts | `us_housing_starts` | regular |
| USD | Building Permits | `us_building_permits` | regular |
| USD | Durable Goods Orders m/m | `us_durable_goods_orders_mm` | advance |
| USD | Core Durable Goods Orders m/m | `us_durable_goods_ex_transport_mm` | advance |
| USD | Industrial Production m/m | `us_industrial_production_mm` | regular |
| USD | Capacity Utilization Rate | `us_capacity_utilization` | regular |
| USD | Trade Balance | `us_trade_balance` | regular |
| GBP | CPI y/y | `uk_cpi_yy` | regular |
| GBP | Core CPI y/y | `uk_core_cpi_yy` | regular |
| GBP | Unemployment Rate | `uk_unemployment_rate` | regular |
| GBP | Average Earnings Index 3m/y | `uk_average_weekly_earnings_yy` | final |
| EUR | Prelim Flash GDP q/q | `ea21_gdp_qq` | preliminary_flash |
| EUR | Flash GDP q/q | `ea21_gdp_qq` | flash |
| EUR | Revised GDP q/q | `ea21_gdp_qq` | regular |
| USD | Unemployment Claims | `us_initial_jobless_claims` | advance |
| USD | Prelim UoM Consumer Sentiment | `us_umich_consumer_sentiment` | preliminary |
| USD | Revised UoM Consumer Sentiment | `us_umich_consumer_sentiment` | final |
| EUR | Consumer Confidence | `ea_consumer_confidence` | unknown |
| EUR | Economic Sentiment | `ea_economic_sentiment_indicator` | final |
| EUR | Core CPI Flash Estimate y/y | `ea21_core_hicp_yy` | flash |
| EUR | Final Core CPI y/y | `ea21_core_hicp_yy` | final |
| EUR | CPI Flash Estimate y/y | `ea21_hicp_yy` | flash |
| EUR | Final CPI m/m | `ea21_hicp_mm` | final |
| USD | New Home Sales | `us_new_home_sales` | final |
| GBP | Retail Sales m/m | `uk_retail_sales_mm` | final |
| EUR | Trade Balance | `ea_trade_balance` | final |
| EUR | Industrial Production m/m | `ea_industrial_production_mm` | final |
| EUR | Retail Sales m/m | `ea_retail_sales_mm` | final |
| EUR | Unemployment Rate | `ea_unemployment_rate` | final |
| USD | Federal Funds Rate | `fed_funds_rate_decision` | final |
| EUR | Deposit Facility Rate | `ecb_deposit_facility_rate_decision` | final |
| GBP | Official Bank Rate | `boe_bank_rate_decision` | final |
| JPY | BOJ Policy Rate | `boj_policy_rate_decision` | final |
| CNY | 1-y Loan Prime Rate | `pboc_lpr_decision` | final |
| CAD | Overnight Rate | `boc_overnight_rate_decision` | final |
