# M9f housing release families

Branch `feat/m9f-housing` was cut from `origin/integrate/m2b` at `e301c52`,
originally stacked on [#128](https://github.com/selvaz/market-data-hub/pull/128).
That integration is now in main and has been merged into this branch.
[PR #130](https://github.com/selvaz/market-data-hub/pull/130) targets `main` and
remains draft while work is delivered one family at a time. Do not merge it.

## Japan MLIT housing starts verification checkpoint

1589 passed, 1 skipped; 464 focused housing/cards tests passed. Card/doc freshness, Ruff and `mkdocs build --strict` passed. Fresh schema-v9 backup verification: `D:\Temp\mdh-m9f-juvtf_rl\verification.json`. Dry run and collection returned `ok`, then `no_change` on repeat; 1 latest issuer-certified read-API measures. Integrity `ok`, production main unchanged. Production was read only as stable main/WAL bytes; all verification writes stayed under `D:/Temp`.

## Japan MLIT housing starts

Source assessment: MLIT provides free, machine-readable [dated Japanese HTML releases](https://www.mlit.go.jp/report/press/joho04_hh_001395.html) and a usable release instant from its [official construction-statistics calendar](https://www.mlit.go.jp/statistics/details/t-other-2_tk_000214.html). Family `jp_housing_starts` collects the national new residential starts **annual percent change, NSA**: **August +6.1%**, released 30 September, and **July +8.2%**, released 31 August. The residential paragraph is selected explicitly. Its separate SAAR monthly change (August -2.7%), owner-occupied/rental subdivisions and nonresidential starts are not substituted. Reiwa dates and fullwidth characters are normalized; no e-Stat API credential is needed.

Five regular editions are reviewed through year-end, all **14:00 Asia/Tokyo / 05:00 UTC**: 31 August, 30 September, 30 October, 30 November and 25 December. December data publish in January 2027 and are not seeded within the horizon. Future press links come from exact report titles in MLIT's edition-month press index; resource numbers are not guessed. Date, clock, period, residential-scope or future-calendar changes fail closed with `needs_review`, with shared newly due publication grace and year-end expiry.

The new `jp_housing_starts` v2.3 proposal covers `cat:jp_housing_starts_yy`: timing UNKNOWN, real annual-versus-SAAR and volatility caveats, issuer URL rationale and pending owner approval in timing metadata. No FF title mapping is inferred.

## CMHC housing starts verification checkpoint

1575 passed, 1 skipped; 49 focused CMHC/HTTP tests passed. Card/doc freshness, Ruff and `mkdocs build --strict` passed. Fresh schema-v9 backup verification: `D:\Temp\mdh-m9f-iy7jctvk\verification.json`. Dry run and collection returned `ok`, then `no_change` on repeat; 1 latest issuer-certified read-API measures. Integrity `ok`, production main unchanged. Production was read only as stable main/WAL bytes; all verification writes stayed under `D:/Temp`.

## Canada CMHC housing starts

CMHC supplies stable, free [dated public releases](https://www.cmhc-schl.gc.ca/media-newsroom/news-releases/2026/housing-starts-construction-data-september-2026). Family `ca_housing_starts` collects the **total monthly seasonally adjusted annual rate for all areas in Canada**, in units. September is **248,896 SAAR**, released 16 October; August's original 16 September print is **229,046**. September later revises August to 242,223; that later value does not overwrite the original release vintage. The six-month trend (September 250,411), urban actual starts, completions and year-to-date counts are separate measures and are not substituted.

The [official 2026 release calendar](https://www.cmhc-schl.gc.ca/professionals/housing-markets-data-and-research/news-releases-reports-calendar) explicitly enumerates 16 September, 16 October, 17 November and 15 December. The dated releases state **08:15 AM ET** for the next housing-starts publication; the parser cross-checks its reference month, date and clock against reviewed editions. The shared scheduler uses `America/Toronto`, with regular-stage events only. No holiday dates are forecast at run time. Date, scope, clock, future-calendar or headline changes fail closed with `needs_review`; the shared six-hour newly due 404 grace and 31 December review horizon apply.

CMHC public pages require the browser User-Agent already used for BoE; redirects remain disabled. Document identity normalizes only the named Sitecore render ticks, form verification token and `SearchResourcesProperties_` render identifier only for documents whose source URL has the exact CMHC HTTPS host. Original bytes and raw SHA are retained. Tests vary those transport fields and prove that headline/date changes remain distinct.

The new `ca_housing_starts` v2.3 draft has timing UNKNOWN, volatility/revision caveats in `flag`, the source URL in its rationale and pending owner approval in timing metadata. The approved US starts concept is not reused for Canadian geography. No FF title is inferred.

## Destatis residential permits verification checkpoint

1524 passed, 1 skipped; 12 focused Destatis tests passed. Card/doc freshness, Ruff and `mkdocs build --strict` passed. Fresh schema-v9 verification: `D:\Temp\mdh-m9f-9tpkidiy\verification.json`. All 1 collected families returned `ok` for dry run and collection, then `no_change`; all 2 latest read-API measures were issuer-certified with correct source classes. Integrity `ok`, production main unchanged. Production was read only as stable main/WAL bytes; every verification write stayed under `D:/Temp`.

## Germany residential building permits

Destatis supplies a stable, free [dated German press release](https://www.destatis.de/DE/Presse/Pressemitteilungen/2026/10/PD26_370_3111.html), so Germany is collected. Family `de_building_permits` covers total **dwellings permitted in new and existing buildings**, monthly NSA count and issuer-published y/y change. August is **20,600 dwellings, +6.8% y/y**; July is **22,500, +1.9%**. These are neither building counts, new-build-only subtotals nor cumulative January-to-date totals. Counts are rounded to 100; annual rates are never recomputed from those rounded counts. Late administrative reports and revisions remain caveats.

The [filtered annual issuer calendar](https://www.destatis.de/SiteGlobals/Forms/Suche/Termine/EN/Terminsuche_Formular.html?nn=1426424&resourceId=263318&input_=263386&pageLocale=en&templateQueryString=Building+permits) explicitly states **08:00 CET/CEST** and confirms the remaining 18 November and 18 December editions. Its ICS midnight defaults are not release clocks. Four regular editions (18 September, 16 October, 18 November, 18 December) are reviewed through year-end. Future article URLs are discovered from the issuer's filtered press index; press-release numbers are not guessed. Clock/calendar, national scope, period, date or title changes fail closed with `needs_review`; newly due publication delays have the shared six-hour grace. No GENESIS credentials are needed.

One new v2.3 draft, `de_building_permits`, covers the count and annual-change ids. It keeps timing UNKNOWN, real rounding/revision caveats, issuer URL rationale and pending owner approval in timing metadata. No FF title mapping is inferred.

## BoE mortgage approvals verification checkpoint

1508 passed, 1 skipped; 15 focused BoE tests passed. Card/doc freshness, Ruff and `mkdocs build --strict` passed. Fresh schema-v9 verification: `D:\Temp\mdh-m9f-wjulpyc4\verification.json`. All 1 collected families returned `ok` for dry run and collection, then `no_change`; all 1 latest read-API measures were issuer-certified with correct source classes. Integrity `ok`, production main unchanged. Production was read only as stable main/WAL bytes; every verification write stayed under `D:/Temp`.

## BoE mortgage approvals step

Family `uk_mortgage_approvals` collects the **seasonally adjusted net number
of mortgage approvals for house purchase to individuals**, from the dated
[Money and Credit release](https://www.bankofengland.co.uk/statistics/money-and-credit/2026/august-2026).
The August 2026 headline is **54,900**, released **29 September at 09:30 BST**
(08:30 UTC). The July golden is **56,100**, released 1 September at 09:30 BST.
The headline and the detailed house-purchase paragraph must agree. These counts
are rounded by the issuer to the nearest 100, net of cancellations, and exclude
remortgaging. Lending amounts, remortgaging, all-purpose approvals and later
revised/unrounded `LPMVTVX` history do not replace the dated headline.

The [Bankstats calendar](https://www.bankofengland.co.uk/statistics/tables)
confirms 29 October (September data) and 30 November (October data). November
data release on 5 January 2027: **no December edition is invented**. The
[BoE statistical code](https://www.bankofengland.co.uk/-/media/boe/files/statistics/statistical-code-of-practice.pdf)
and the saved current upcoming-events page corroborate the **09:30 London**
clock. Four reviewed regular editions are seeded, with DST handled by the
shared release-instant scheduler. A changed date, purpose, adjustment, headline
or future calendar enters `needs_review`; the shared six-hour newly due 404
publication grace and year-end review horizon apply.

A new strict `uk_mortgage_approvals` v2.3 draft covers the catalogue id of the
same name, timing `UNKNOWN`, real rounding/coverage caveats in `flag`, source
URL in the rationale and pending owner approval in timing metadata. No exact
FF title was verified, so no consensus alias is inferred.

## UK HPI verification checkpoint

1491 passed, 1 skipped; 16 focused UK HPI tests passed. Card/doc freshness, Ruff and `mkdocs build --strict` passed. Fresh schema-v9 verification: `D:\Temp\mdh-m9f-z2g3bdyn\verification.json`. All 1 collected families returned `ok` for dry run and collection, then `no_change`; all 1 latest read-API measures were issuer-certified with correct source classes. Integrity `ok`, production main unchanged. Production was read only as stable main/WAL bytes; every verification write stayed under `D:/Temp`.

## UK HPI step

Family `uk_hpi` certifies the HM Land Registry / ONS **UK-wide annual HPI
change**, all residential properties purchased for market value, including cash
and mortgage transactions, NSA. It collects the national headline from the
[dated HM Land Registry summary](https://www.gov.uk/government/statistics/uk-house-price-index-for-august-2026/uk-house-price-index-summary-august-2026),
cross-checking the economic statement, title and reference period. Regional,
monthly, rent and buyer/funding subtotals are not substituted. The latest card
is **August 2026 +0.9% y/y**, first published **21 October 2026 at 09:30:08 BST**
(08:30:08 UTC). The other saved golden is July **+1.4%**, first published
16 September at 09:30:03 BST. Later August-publication revisions to July (1.2%)
do not replace July's original release vintage.

The [issuer calendar](https://www.gov.uk/government/publications/about-the-uk-house-price-index/about-the-uk-house-price-index)
is reviewed through 31 December: 16 September, 21 October, 18 November and
16 December, all scheduled at **09:30 Europe/London**. Only the regular stage
publishes. The first-publication metadata supplies the observed seconds;
`updated-at` and `public-updated-at` never supply a release clock. Both the
scheduled instant and observed clock gate actuals. Changed metadata, mismatched
headlines, missing due reports or calendar expiry fail closed with `needs_review`;
newly due 404s receive the shared six-hour publication grace.

The new `uk_hpi` row in `draft_rows_v2.3.psv` proposes `cat:uk_hpi_yy`, timing
`UNKNOWN`, with registration lag/provisional revision caveats in `flag`, the
issuer URL in its rationale and pending owner approval in draft timing metadata.
No FF alias has been added without exact-title evidence.

## ECB mortgage lending and borrowing cost (24 October 2026)

The ECB step adds **two monthly official families** because BSI loan growth and
MIR borrowing cost have different publication dates. Each has only a `regular`
stage and its own scheduled-event membership. The reviewed calendar ends on
31 December 2026. Its **10:00 Frankfurt** clock uses `Europe/Berlin`: 08:00 UTC
in August/September/early October and 09:00 UTC after the October clock change.
The [ECB statistical calendar](https://www.ecb.europa.eu/press/calendars/statscal/html/index.en.html)
explicitly states that its times are Frankfurt local time.

- `ea_house_purchase_loans` / `ea_loans_house_purchase_yy` uses
  [BSI.M.U2.Y.U.A22T.A.I.U2.2250.Z01.A](https://data.ecb.europa.eu/data/datasets/BSI/BSI.M.U2.Y.U.A22T.A.I.U2.2250.Z01.A):
  adjusted MFI loans to households and NPISH for **house purchase**, annual
  growth, working day and seasonally adjusted, adjusted for transfers and
  notional cash pooling, changing euro area composition. The original print
  comes from row 1.2, final annual-growth column, of the dated release annex.
  It is distinct from the broader all-household headline.
- `ea_house_purchase_cost` / `ea_cost_borrow_house` uses
  [MIR.M.U2.B.A2C.AM.R.A.2250.EUR.N](https://data.ecb.europa.eu/data/datasets/MIR/MIR.M.U2.B.A2C.AM.R.A.2250.EUR.N):
  the composite cost of borrowing for new euro-denominated house-purchase loans
  to households and NPISH, percent per annum, changing euro area composition.
  This is the issuer's weighted AAR/NDER composite, separate from APRC, consumer
  credit, corporate borrowing and individual fixation buckets. Seasonal
  adjustment is not stated in this headline and is recorded as `not-stated`.

The live Data Portal CSV must match the exact series key, all key dimensions,
units, scale, status, latest period and published rate. Only the latest due
headline is corroborated, at its printed precision. Revised CSV history and
the portal's update clock never certify earlier release vintages. In particular,
July mortgage-loan growth was first printed **3.0%**, then revised to **3.1%** in
September's annex and the rolling series. The July release remains 3.0%.

Both parsers validate issuer URLs, reference periods and publication identities.
The loans fetch additionally validates the dated press page and its unique annex
link. A changed calendar, dimension, structure, missing due publication or
disagreement enters `needs_review`; the six-hour newly due publication grace
leaves the event pending. Unknown editions and calendar expiry fail closed.
No FF alias is added: the broader FF household-loan title is not this mortgage
measure.

Cards: `cat:ea_cost_borrow_house` aliases the already approved `pan_mir` concept
(`pan:ecb_cost_borrow_house`). The narrower loan-growth concept has a new strict
19-column **`ea_loans_house_purchase`** row in `draft_rows_v2.3.psv`, with timing
`UNKNOWN`, source URL in its rationale, real composition/coverage caveats in
`flag`, and pending owner approval in the matching timing metadata. There are
now **six proposals / nine measure ids**; approved release-card coverage is
**58/67**. Main's 294 approved cards and 86/86 approved tier-1 mappings remain.

Saved originals include the [July BSI release](https://www.ecb.europa.eu/press/stats/md/html/ecb.md2607~e7127e7d02.en.html)
and [annex](https://www.ecb.europa.eu/press/pdf/md/ecb.md2607_annex~b04670b43a.en.pdf),
the [August BSI release](https://www.ecb.europa.eu/press/stats/md/html/ecb.md2608~406f8d243d.en.html)
and [annex](https://www.ecb.europa.eu/press/pdf/md/ecb.md2608_annex~1dd30faa76.en.pdf),
the [July MIR release](https://www.ecb.europa.eu/press/stats/mfi/html/ecb.mir260902~d54675e442.en.html)
and [August MIR release](https://www.ecb.europa.eu/press/stats/mfi/html/ecb.mir2610~8e4898ad10.en.html),
both series' CSV and portal metadata, and the general and family calendars.
Each fixture retains its issuer URL, capture instant and SHA-256. Each family
has two golden cases in two distinct issuer editions.

ECB-step local validation passed: **1,473 passed, 1 skipped** across all
`tests/test_macro_*.py`, `tests/test_indicator_cards.py` and
`tests/test_macro_indicators_docs.py` (398.29 seconds). The 63 focused ECB
cases, all 15 saved body/URL/hash pairs, generated-card and generated-doc
freshness, repository-wide Ruff and `mkdocs build --strict` also passed.

Fresh schema-v9 production-copy verification passed at
`D:\Temp\mdh-m9f-gjw0p4dh\verification.json` (SQLite backup beside it).
All **nine** families returned `ok` for dry run and collection, then `no_change`
for the repeat. All **13** latest read-API measure cards were issuer-certified,
with the correct official/private source classes. The new ECB cards are August
2026 loan growth **+3.1% y/y**, released 25 September at 08:00 UTC, and August
borrowing cost **3.60% per annum**, released 1 October at 08:00 UTC. SQLite
integrity was `ok` and the production main file was unchanged. Production was
read only as stable main/WAL bytes; all snapshot, database, collection, ledger
and report writes stayed under `D:\Temp`.

## Cards v2.2 integration (24 October 2026)

Merged main's owner-approved v2.2 cards (#132, `6109c89`) into the housing
branch. Main's 294 approved cards, 24 promotion records, master-table and timing
hashes, 86/86 approved tier-1 mappings and id de-duplication remain intact.
The card status described in older step notes below is historical; this section
records the current approved-versus-proposed split.

| Conflict | Resolution |
| --- | --- |
| `aliases_v2.2.json` (renamed by main) | Kept every main alias; added construction m/m and NAR existing-sales m/m concept aliases. The NAR level already has an approved v2.2 id. Only the eight still-unapproved housing measures remain explicit concept gaps. |
| `cards_v2.2.json` | Regenerated from main's unchanged approved master/timing and the reconciled aliases; no proposals enter the shipped dataset. |
| `draft_rows_v2.2.psv` | Kept main's empty promoted generation and its matching timing hash. Moved the five remaining housing concepts into v2.3; removed the existing-home-sales proposal because its concept is approved. |
| `tests/test_indicator_cards.py` | Kept main's promotion, alias, de-duplication and 86/86 tier-1 tests; updated housing coverage and added real v2.3 draft/metadata isolation checks. |
| `docs/macro-indicators.md` | Kept main's v2.2 documentation and regenerated Part B with all housing families. |

Approved housing context:

- `cat:us_construction_spending_mm` aliases `us_construction_spending`.
- `cat:us_existing_home_sales` resolves directly to the approved v2.2
  `us_existing_home_sales` card; `cat:us_existing_home_sales_mm` aliases that
  concept. The SAAR level and monthly change keep separate measure identities.

The next draft generation is `draft_rows_v2.3.psv` with matching
`timing_v2.3-draft.json`:

| Draft key | Proposed measure ids | Real caveat in `flag` |
| --- | --- | --- |
| `us_fhfa_hpi` | `cat:us_fhfa_hpi_mm`; `cat:us_fhfa_hpi_yy` | Purchase-only index; excludes all-transactions measures |
| `us_pmms_30y` | `cat:us_pmms_30y` | Survey week differs from publication date |
| `us_pending_home_sales` | `cat:us_pending_home_sales_mm` | Seasonal adjustment not stated in public headline |
| `us_case_shiller` | `cat:us_case_shiller_national_yy`; `cat:us_case_shiller_20_yy` | Three-month moving average |
| `ea_hpi` | `cat:ea21_hpi_qq`; `cat:ea21_hpi_yy` | Fixed EA21 composition |

All five proposals retain timing **UNKNOWN**, no timing basis or predictive
screen, and issuer source URLs in their rationales. Approval state is
**pending_owner_approval** in draft timing metadata, never in the PSV `flag` or
rendered failure modes. The loader rejects missing/non-pending state and
approval wording in caveats. `coverage --with-drafts` validates every draft
generation and lists these proposals separately, including those outside the
86-id tier-1 snapshot. Default approved lookups do not return them.

The approved release-catalogue coverage is **57/65**. The remaining eight ids
are proposed by those five drafts. All 86 external tier-1 ids still resolve to
approved cards, and their approved count is unchanged by draft opt-in. Release
collectors, reviewed calendars, FF titles and schema-v9 persistence are retained.
Required local checks, current-copy verification and final-head CI evidence are
tracked in PR #130 for this integration checkpoint.

The fresh schema-v9 production-backup check passed for all seven families at
`D:\Temp\mdh-m9f-_aq370zc\verification.json`: dry run and copy collection `ok`,
repeat `no_change`, eleven latest issuer-certified measures with the correct
official/private source classes, SQLite integrity `ok`, production main file
unchanged. Production was read only as stable main/WAL bytes; all verification
writes stayed under `D:\Temp`. The focused integration suite passed 435 tests,
and repository Ruff, regenerated card/doc freshness and strict docs build pass.

## Earlier current-main integration (24 October 2026)

Merged `origin/main` at `fd59e26` into the feature branch without rebasing
(merge commit `6de8e24`). All 27 conflicted files were resolved, keeping both
main's fixes and housing coverage.
The schema-8 and conflicting-base limitations recorded in the earlier step
reports below are historical. The integrated code includes schema v9, BoJ
clock enrichment, main's per-stage schedules and FF-independent calendar seeding,
the `duckdb<2` bound, hermetic credential tests, Eurostat offsets, the generated
macro-indicators documentation and version 0.3.7.

| Conflict group | Resolution |
| --- | --- |
| Scheduling, discovery, catalogue and collection | Retained all six housing families and main's single release-instant path. Housing seeds only stages in its reviewed editions, with the correct issuer calendar URL. Main's flash/final membership restrictions remain. |
| Schema, store, BoJ and resolver | Kept main's schema-v9 migration and append-only BoJ clock enrichment, including historical query cutoffs and missing-meeting handling. |
| Card aliases, coverage and generated JSON | Kept main's approved aliases and de-duplication, plus the construction alias and explicit housing card gaps. Regenerated JSON from the authoritative inputs. |
| Consensus and FF aliases | Kept main's v5 stage-vocabulary and EUR trade evidence fixes, plus the four exact housing aliases. Removed duplicate calendar seeding introduced by the overlapping merge. |
| Tests | Kept main's review regressions, archive-present/missing cases and v9 assertions; retained housing goldens, PMMS survey-week comparisons and private issuer labels. Every new housing test stubs imported credential bindings and blocks network. |
| Documentation | Kept main's corrected event-previous contract, BoJ timing notes and source-path links; regenerated macro-indicators Part B to include housing coverage. |

The required `tests/test_macro_*.py`, `tests/test_indicator_cards.py` and
`tests/test_macro_indicators_docs.py` suite passed **1,279 tests, 1 skipped**
in 403.66 seconds. The focused integration suite also passed all 130 tests.
Repository-wide Ruff and `mkdocs build --strict` passed locally. Both
indicator-card and documentation freshness checks pass after regeneration.
The docs workflow now has a read-only build job on pull requests; deployment
remains restricted to main. Current-production verification explicitly requires
source schema 9 before private migration, and all verifier SQLite snapshots and
rate-ledger writes are confined to its new directory under `D:\Temp`.

The current-production schema-v9 verification passed for **all six families**:
`D:\Temp\mdh-m9f-b6ezheho\verification.json`, with the writable database copy
beside it. The source was the current production main/WAL bytes, read twice
for a stable private snapshot; SQLite opened only private files. The report
records `source_schema: 9`, dry run and copy collection `ok` for every family,
repeat collection `no_change`, all nine latest read-API measures certified
against their issuers with the correct official/private source classes,
SQLite integrity `ok`, and `source_main_unchanged: true`.

Reproduce the complete current check with bytecode writes disabled:

```powershell
& C:\ProgramData\spyder-6\python.exe -B scripts\verify_macro_m9f.py --source C:\ProgramData\InvestmentCommittee\live\db\macro_releases.sqlite --destination-root D:\Temp
```

The GitHub strict docs job passed on the merge commit. Final-head Python
3.9–3.12 and docs results are tracked in PR #130; the next family starts only
after those checks pass.

## Certified so far (24 October 2026)

| Family | Issuer | Source class | Frequency | Reviewed editions through 31 December 2026 | Golden cases |
| --- | --- | --- | --- | --- | --- |
| Japan new residential housing starts | MLIT | Official, free | Monthly | 31 Aug, 30 Sep, 30 Oct, 30 Nov, 25 Dec; 14:00 Asia/Tokyo | 2 dated releases: July +8.2%; August +6.1% y/y |
| Canada total housing starts, SAAR | CMHC | Official, free | Monthly | 16 Sep, 16 Oct, 17 Nov, 15 Dec; 08:15 America/Toronto | 2 dated releases: August 229,046; September 248,896 |
| Germany residential dwellings permitted | Destatis | Official, free | Monthly | 18 Sep, 16 Oct, 18 Nov, 18 Dec; 08:00 Europe/Berlin | 4 measurements in 2 dated publications: July 22,500 / +1.9%; August 20,600 / +6.8% |
| BoE house-purchase mortgage approvals | Bank of England | Official, free | Monthly | 1 Sep, 29 Sep, 29 Oct, 30 Nov; 09:30 Europe/London; next Jan 2027 | 2 dated publications: July 56,100; August 54,900 |
| UK HPI, NSA annual change | HM Land Registry / ONS | Official, free | Monthly | 16 Sep, 21 Oct, 18 Nov, 16 Dec; 09:30 Europe/London, observed seconds retained | 2 dated reports: July +1.4%, August +0.9% |
| Euro area house-purchase loan growth | ECB BSI | Official, free | Monthly | 27 Aug, 25 Sep, 27 Oct, 26 Nov, 30 Dec; 10:00 Europe/Berlin | 2 dated annexes: July 3.0%, August 3.1% |
| Euro area house-purchase borrowing cost | ECB MIR | Official, free | Monthly | 2 Sep, 1 Oct, 2 Nov, 2 Dec; 10:00 Europe/Berlin; November data publish January 2027 | 2 dated releases: July 3.54%, August 3.60% |
| Euro area HPI, NSA q/q and y/y | Eurostat | Official, free | Quarterly | 2 Jul (Q1), 1 Oct (Q2), 11:00 Europe/Luxembourg; reviewed July–December, next edition 11 Jan 2027 | 4 measurements in 2 distinct issuer publications |
| US FHFA monthly purchase-only HPI, SA m/m and y/y | FHFA | Official, free | Monthly | 25 Aug, 29 Sep, 27 Oct, 24 Nov, 29 Dec; 09:00 America/New_York | 4 measurements in 2 distinct publications (25 Aug and 29 Sep) |
| US total construction spending, SA m/m | Census | Official, free | Monthly | 3 Aug, 1 Sep, 1 Oct, 2 Nov, 1 Dec; 10:00 America/New_York | 2 distinct publications: June -0.1% (3 Aug) and August +0.9% (1 Oct) |
| US PMMS 30-year fixed-rate mortgage average, NSA | Freddie Mac | Official issuer, free | Weekly | 22 editions from 6 Aug to 31 Dec; 12:00 America/New_York; Thanksgiving release 25 Nov | 2 dated editions: 15 Oct archive entry (7.40%) and 22 Oct current publication (7.32%) |
| US existing-home sales national public headline | NAR | Private issuer, public press release only | Monthly | 11 Aug, 10 Sep, 13 Oct, 12 Nov, 9 Dec; schedule 10:00 America/New_York | 2 independently printed headline measures in the 13 Oct edition: 3.99 million SAAR and +1.5% m/m |
| US pending home sales national public m/m headline | NAR | Private issuer, public press release only | Monthly | 18 Aug, 17 Sep, 20 Oct, 18 Nov, 17 Dec; 10:00 America/New_York | 2 distinct editions: August +0.3% (17 Sep) and September -7.3% (20 Oct) |
| US Case-Shiller national and 20-city NSA y/y headlines | S&P Dow Jones Indices / Cotality | Private issuer, public press release only | Monthly | 25 Aug, 29 Sep, 27 Oct, 24 Nov, 29 Dec; 09:00 America/New_York | 4 headline cases in 2 dated publications: June national +1.5%, 20-city +2.1%; July national +1.9%, 20-city +2.5% |

Calendars: [FHFA](https://www.fhfa.gov/data/hpi) and
[Census](https://www.census.gov/economic-indicators/calendar-listview-2026.html), plus the
[Freddie Mac PMMS calendar](https://www.freddiemac.com/pmms/docs/PMMS_Publication_Calendar.pdf).
The saved Census calendar in `tests/fixtures/macro_releases/us_monthly` is
shared evidence for the construction schedule. Edition metadata lives in
`housing_editions.py`; unknown dates fail closed.

Issuer golden URLs:

- [FHFA August quarterly report, including June monthly table](https://www.fhfa.gov/document/d/hpi/fhfa-house-price-index-report-2026q2).
- [FHFA September monthly report](https://www.fhfa.gov/document/d/hpi/fhfa-hpi-monthly-september-2026).
- [Census June construction release](https://www.census.gov/construction/c30/pdf/pr202606.pdf).
- [Census rolling construction release, August edition captured 24 October](https://www.census.gov/construction/c30/pdf/release.pdf).
- [Freddie Mac archive, including the 15 October edition](https://www.freddiemac.com/pmms/archive).
- [Freddie Mac current PMMS publication, 22 October edition captured 24 October](https://www.freddiemac.com/pmms).
- [NAR existing sales, September headline in the 13 October edition](https://www.nar.realtor/newsroom/existing-home-sales-increased-1-5-in-september-according-to-nar-report).
- [NAR pending sales, August headline in the 17 September edition](https://www.nar.realtor/newsroom/nar-pending-home-sales-report-shows-0-point-3-percent-increase-in-august).
- [NAR pending sales, September headline in the 20 October edition](https://www.nar.realtor/newsroom/pending-home-sales-decreased-7-3-percent-in-september).
- [S&P public June Case-Shiller release, 25 August](https://press.spglobal.com/2026-08-25-S-P-Cotality-Case-Shiller-Index-Reports-Annual-Gain-in-June-2026).
- [S&P public July Case-Shiller release, 29 September](https://press.spglobal.com/2026-09-29-S-P-Cotality-Case-Shiller-Index-Reports-Annual-Gain-in-July-2026).

Each fixture has its original body, issuer URL, capture timestamp and SHA-256
in `tests/fixtures/macro_releases/m9f`; `golden-set.json` states expected values.
The July archive link returned HTTP 500 during capture. The June PDF supplies
the second distinct construction golden; this is not a coverage gap.

Construction uses the printed monthly change in **total** public and private
value put in place. It does not calculate a rate from rounded dollar levels or
use residential, private, public or annual-growth subtotals. The parser checks
the release clock and zone, title, current month, prior comparison month and
seasonal adjustment. Unknown structures enter `needs_review`, retaining the
document without certifying a vintage. The rolling release is subject to the
existing release-instant gate and six-hour publication-delay grace.

PMMS collects the 30-year fixed-rate average directly from the issuer's dated
archive tables. Column order, term, percent unit, archive year, duplicate dates
and missing reviewed weeks are checked before the batch is written. The latest
due edition must be present, subject to the same six-hour publication grace.
Only reviewed August-December editions are certified; older archive rows are
left out. The current publication supplies a separate golden with matching panel
and headline dates/rates. The issuer-hosted press-room page returned HTTP 403;
the public archive and current page provide stable free issuer evidence instead.

The PMMS reference period is the calendar's **survey week ending**, not the
publication date. The 22 October release covers the week ending 21 October.
Thanksgiving's 25 November release covers through Tuesday 24 November, so the
previous-period comparison uses reviewed calendar endpoints instead of always
subtracting seven days. The writer preserves Saturday week endings for DOL
claims and separately enforces PMMS's reviewed endpoints.

## Latest cards verified on a production backup

| Family | Reference period | Edition | Issuer release time | Latest official card |
| --- | --- | --- | --- | --- |
| MLIT housing starts | August 2026 | 30 September 2026 | 14:00 JST / 05:00 UTC | +6.1% y/y, NSA |
| CMHC housing starts | September 2026 | 16 October 2026 | 08:15 EDT / 12:15 UTC | 248,896 units, total monthly SAAR |
| Destatis residential permits | August 2026 | 16 October 2026 | 08:00 CEST / 06:00 UTC | 20,600 dwellings; +6.8% y/y, NSA |
| BoE house-purchase approvals | August 2026 | 29 September 2026 | 09:30 BST / 08:30 UTC | 54,900 net approvals, SA, rounded headline |
| UK HPI | August 2026 | 21 October 2026 | 09:30:08 BST / 08:30:08 UTC | +0.9% y/y, NSA |
| ECB house-purchase loans | August 2026 | 25 September 2026 | 10:00 CEST / 08:00 UTC | +3.1% y/y, adjusted loans, SCA |
| ECB house-purchase borrowing cost | August 2026 | 1 October 2026 | 10:00 CEST / 08:00 UTC | 3.60% per annum, new-business composite |
| Eurostat HPI (EA21) | Q2 2026 | 1 October 2026 | 11:00 CEST / 09:00 UTC | +1.1% q/q; +4.0% y/y, NSA |
| FHFA | July 2026 | 29 September 2026 | 09:00 EDT / 13:00 UTC | +0.3% m/m; +2.6% y/y, purchase-only SA |
| Construction spending | August 2026 | 1 October 2026 | 10:00 EDT / 14:00 UTC | +0.9% m/m, total SA |
| PMMS 30-year mortgage rate | Survey week ending 21 October 2026 | 22 October 2026 | 12:00 EDT / 16:00 UTC | 7.32%, NSA weekly average |
| Existing-home sales (private NAR headline) | September 2026 | 13 October 2026 | Page timestamp 10:01 EDT / 14:01 UTC; scheduled 10:00 EDT | 3.99 million SAAR; +1.5% m/m |
| Pending home sales (private NAR headline) | September 2026 | 20 October 2026 | 10:00 EDT / 14:00 UTC | -7.3% m/m |
| Case-Shiller (private S&P public annual headlines) | July 2026 | 29 September 2026 | 09:00 EDT / 13:00 UTC | National +1.9% y/y; 20-city +2.5% y/y, NSA |

Live verification report:
`D:\Temp\mdh-m9f-sl4iuv5p\verification.json`.
The database copy is beside it as `macro_releases.sqlite`.
Dry run and copy collection were `ok` for both families; collection added four
FHFA vintages and one construction vintage. A repeat returned `no_change` for
both. `PRAGMA quick_check` returned `ok`; source main-file size and modification
time were unchanged. The report retains the full read-API provenance.

PMMS step verification report:
`D:\Temp\mdh-m9f-mnhadbyx\verification.json` (database copy beside it).
Dry run and copy collection were `ok` for all three implemented families:
12 PMMS, four FHFA and one construction vintage were added. A repeat returned
`no_change` for all three. The latest PMMS card is official at 7.32%, published
22 October at 16:00 UTC, for the survey week ending 21 October.
SQLite integrity was `ok`; production main-file size and modification time
were unchanged. This run used the same command below with `us_pmms` appended
to the family list. The full macro and indicator-card regression rerun passed:
**1,173 passed, 1 skipped**. PMMS offline checks, repository-wide lint and card
freshness pass. No new dependency was added, and M9f tests forbid network.

The verifier reads a stable private snapshot of production's main/WAL bytes,
then uses SQLite's backup API. SQLite opens only the private snapshot and copy.
All writes go to the copy. Reproduce with the project interpreter:

```powershell
& C:\ProgramData\spyder-6\python.exe scripts\verify_macro_m9f.py --source C:\ProgramData\InvestmentCommittee\live\db\macro_releases.sqlite --destination-root D:\Temp --families us_fhfa_hpi us_construction_spending
```

## Indicator cards and consensus

- `cat:us_construction_spending_mm` aliases the approved v2.1 concept
  `us_construction_spending`; the master table and its timing remain unchanged.
- FHFA has no approved concept card. `draft_rows_v2.2.psv` proposes
  `us_fhfa_hpi`, covers both measures, flags pending owner approval, and uses
  timing `UNKNOWN`. Its 19-column schema matches the file introduced in #125;
  merge the rows by key when combining the two branches. Drafts are not loaded
  into the approved v2.1 dataset.
- PMMS also has no approved concept card. Its `us_pmms_30y` draft uses the same
  format, pending owner approval and timing `UNKNOWN`. It is not added to the
  approved v2.1 cards.
- The exact FF title `Construction Spending m/m`, USD, aliases the total SA
  m/m measure. Other countries, spending levels and residential subtotals do
  not match. No FHFA or PMMS FF alias has been added.
- NAR existing and pending sales are separate draft concepts, with timing
  `UNKNOWN` and pending owner approval. Existing sales uses the concept key
  already proposed in #125; both files retain the same strict 19-column format.
  Only the exact USD FF titles `Existing Home Sales` (level) and
  `Pending Home Sales m/m` (monthly percent change) are added.

## Remaining steps, in order

1. NBS 70-city prices: investigate
   issuer stability/free availability and either certify or document the gap.

These families are pending implementation, not established source gaps.

## Eurostat HPI step

Family `ea_hpi` publishes `ea21_hpi_qq` and `ea21_hpi_yy`, quarterly nominal
rates for all newly built and existing residential properties purchased by
households, **not seasonally adjusted**, fixed **EA21** composition. The issuer's
printed growth rates are collected directly; ratios from rounded index levels
and the dataset's `updated` timestamp do not certify releases.

Issuer evidence and golden cases:

- [2 July publication](https://ec.europa.eu/eurostat/web/products-euro-indicators/w/2-02072026-bp):
  Q1 2026 **+1.0% q/q, +4.7% y/y**. This release also prints EA20; only the
  explicitly labelled EA21 row matches the headline and catalogue identity.
- [1 October publication](https://ec.europa.eu/eurostat/web/products-euro-indicators/w/2-01102026-bp):
  Q2 2026 **+1.1% q/q, +4.0% y/y**. Its unnumbered euro-area row is accepted
  only with the explicit EA21 geographical note. Q1 revisions in the annex
  remain raw evidence and do not overwrite the July edition's original values.
- [Release calendar](https://ec.europa.eu/eurostat/news/euro-indicators/release-calendar)
  and the saved full-2026 `eventsJson` response confirm those two editions in
  the reviewed July–December window. The next release is **11 January 2027**,
  outside that window; no extra Q3 edition is invented for December.

All three original bodies, issuer URLs, capture times and SHA-256 hashes are
saved under `tests/fixtures/macro_releases/m9f`. The four golden cases are in
`golden-set.json`. The article metadata confirms 09:00 UTC, corresponding to
11:00 Europe/Luxembourg on both dates. Calendar rows marked `allDay` supply
dates only; their `11:00Z` fields are not treated as exact UTC release clocks.

The collector gates requests through the shared release-instant path, checks
the live calendar against reviewed metadata, validates the issuer URL and
offset-aware article timestamp, and requires agreement between the annual and
quarterly narrative and the current-period EA21 table cells. Changed titles,
definitions, adjustment, area, periods, columns, rates, duplicate/missing dates,
or additional in-horizon editions fail closed. An edition-specific 404 has the
shared six-hour publication grace, then enters `needs_review`. Collection
after the reviewed calendar horizon also requires review. Scheduled events
use only the `regular` stage and the issuer calendar URL, including when the
FF archive is absent.

The approved v2.1 master has no HPI concept. A strict 19-column `ea_hpi` row is
therefore proposed in `draft_rows_v2.2.psv`, **pending owner approval**, timing
**UNKNOWN**. It covers both EA21 measures without changing the approved table.
No FF title with verified exact HPI measure and geography has been added.

Focused housing, integration, card and generated-doc tests: **391 passed**.
Repository-wide Ruff and regenerated card/doc freshness checks pass. Current
strict docs build passes. The full required regression and CI results are
recorded in the PR alongside this step's pushed commit.

Current schema-v9 production-backup verification passed for **all seven
implemented families** at `D:\Temp\mdh-m9f-8xjiohnn\verification.json` (database
copy beside it). Dry run and copy collection returned `ok`; repeat collection
returned `no_change`. The read API returned all eleven expected latest measures
with their correct official/private source classes, including both EA21 HPI
rates at 09:00 UTC on 1 October. SQLite integrity was `ok` and the production
main file was unchanged. All source reads were stable raw main/WAL bytes;
all SQLite snapshot, ledger and collection writes stayed under `D:\Temp`.

## NAR existing-sales step

The collector follows the unique **Read the full news release** link on NAR's
existing-sales overview and accepts values only from the publisher's newsroom
press release. It stores only the national headline level in millions SAAR and
the printed national m/m rate, checking the title, summary and introductory
paragraph against each other. Inventory, prices, regional values and licensed
tables are not collected. The exact USD FF title `Existing Home Sales` maps to
the level; no m/m alias is invented. The pending v2.2 row uses the same concept
key as #125, extends its ids with the headline m/m measure, and keeps timing
`UNKNOWN` and pending owner approval.

Source ownership is explicit in read-API cards: `issuer_id=NAR`,
`source_class=private`. Resolution status `official` means verified against
the issuer; it does not mean a government source. The calendar permits a fetch
at 10:00 ET; the press page's own timestamp is retained (13 October: 10:01 ET).
Values cannot be released before either the scheduled instant or that page clock.

Issuer evidence: [13 October public release](https://www.nar.realtor/newsroom/existing-home-sales-increased-1-5-in-september-according-to-nar-report)
and [2026 release calendar](https://www.nar.realtor/press-releases/nar-releases-2026-statistical-news-release-schedule).
The older [August release URL](https://www.nar.realtor/newsroom/nar-existing-home-sales-report-shows-2-0-decrease-in-august)
now redirects to the rolling overview. It is not an archived publication and
does not certify an August vintage. Therefore the existing-sales golden set
has **two headline cases in one saved edition**, not two archived editions.
This archive limitation is explicit; no earlier issuer edition or history is
invented. A retired/redirected current press link enters `needs_review`.

The existing-sales live backup check passed at
`D:\Temp\mdh-m9f-z7h2uycr\verification.json`: dry run and collection `ok`,
repeat `no_change`, both latest cards issuer-certified and labelled private,
SQLite integrity `ok`, and the production source unchanged. The saved-publication
and end-to-end housing tests passed (50 cases before the redirect regression
was added); the full macro regression suite is repeated with the pending-sales
step below.

## NAR pending-sales step

Pending sales uses the same restricted issuer-link discovery and page-clock
validation. It certifies only the national m/m headline, matching summary,
direction, title and the opening paragraph's reference month. The two saved
dated editions exercise both increases and decreases. Index levels, y/y rates,
regional values, licensed tables and calculated history are excluded. The public
headline does not state seasonal adjustment, so its catalogue metadata records
`not-stated` rather than assuming a class. Read cards explicitly identify NAR
as a private issuer.

The saved NAR calendar validates all ten existing/pending editions through
December. The release-instant, current-edition and six-hour publication-grace
rules apply to both families. A changed structure, mismatched national values,
wrong month, unknown date, retired link or stale current release after the
grace period enters `needs_review`, retaining issuer evidence without writing
an official value.

Live verification for all five completed families passed on
`D:\Temp\mdh-m9f-ysoldsca\macro_releases.sqlite`; the full report is
`D:\Temp\mdh-m9f-ysoldsca\verification.json`. Dry run and collection were
`ok` for every family, and the repeat was `no_change` for all five. All seven
latest measure cards were issuer-certified, including both NAR families with
`source_class=private`. SQLite integrity was `ok` and the production main-file
size/modification time were unchanged. Pending sales added one September
vintage at -7.3% m/m, published 20 October at 14:00 UTC. Its 25 focused offline
tests, repository-wide Ruff, generated-card freshness and draft-format checks
passed; the full macro/cards regression is recorded after completion.

Regression maintenance: the old consensus negative case now uses unsupported
`Existing Home Sales m/m`; the exact `Existing Home Sales` level is covered by
the new positive NAR test. Daily-runner replay tests now use a private empty FF
archive and explicitly seed the reviewed schedule in the failed-fetch scenario.
This removes dependence on the local machine's live FF archive (which masked
the missing fixture schedule on Windows). All 20 daily-runner tests and the
55 combined consensus/NAR tests pass independently of those live captures.

Final NAR-step verification (24 October 2026): all `tests/test_macro_*.py`
plus `tests/test_indicator_cards.py` passed on the pushed implementation and
isolated replay fixtures: **1,202 passed, 1 skipped** in 367 seconds.
Repository-wide `ruff check .`, generated-card freshness, all four strict
19-column drafts and `git diff --check` also passed. No dependency was added.
The latest GitHub matrix was still running at the last check. An
[earlier Python 3.11 job](https://github.com/selvaz/market-data-hub/actions/runs/40133498030/job/120990028685)
reported DuckDB `ResourceInUseException` failures in storage code unchanged by
M9f, as well as the replay-fixture schedule dependency corrected above; no
repository-wide CI success is claimed.

## Case-Shiller step

The issuer now brands this family S&P **Cotality** Case-Shiller (formerly
CoreLogic). The free [S&P press archive](https://press.spglobal.com/index.php?s=2429&keywords=Case-Shiller&titles_only=1&l=25)
provides stable headline search and dated release URLs. The main `spdji`
announcement/index endpoints returned HTTP 403 to the collector; the issuer's
own public press host returned HTTP 200 for both saved releases and the archive.
This is an endpoint limitation, with the public issuer route certified instead.

Only the national and 20-city **NSA annual** headline rates are collected. The
parser checks the issuer hostname, URL edition, title, dateline, reference month,
publication rule, NSA definition and agreement between the national lead and
the year-over-year summary. It reads the 20-city annual narrative, not the
10-city figure, city observations, unrounded supporting tables or SA monthly
rates. Cards identify `issuer_id=SPDJI`, `source_class=private`.

Both saved releases state last-Tuesday publication at 09:00 ET. The five dates
through 31 December were individually reviewed and enumerated in
`housing_editions.py`; runtime does not extrapolate editions. Missing newly due
links have the six-hour publication grace; missing older/delayed releases,
redirected/retired URLs, conflicting links and changed publication structures
enter `needs_review`, preserving documents without partial certification.

Optional `collect --with-redistributors` history uses FRED/ALFRED `CSUSHPINSA`
and `SPCS20RNSA`, `units=pc1`, with an explicit reviewed as-of date. Daily
headline collection needs no FRED key. Saved real FRED responses retain
`source=FRED`, mirror evidence role, unverified values, provider-history revision
kind and date-only snapshot precision. They never supply an issuer release
clock or become official after a numerical match. The July snapshot contains
revised June history, which is tested separately from June's first press print.

The exact USD FF title `S&P/CS Composite-20 HPI y/y` maps only to the 20-city NSA
annual measure ([FF measure definition](https://www.forexfactory.com/calendar/240-us-spcs-composite-20-hpi-yy)).
No national, 10-city, index-level or monthly alias is inferred. Neither annual
concept is approved in v2.1: a single strict 19-column `us_case_shiller` draft
groups both measures, pending owner approval, timing `UNKNOWN`. Its header was
checked against #125's branch; the new key does not collide there.

Live Case-Shiller verification passed at
`D:\Temp\mdh-m9f-cp7v1jlq\verification.json` (database copy beside it): dry
run and collection `ok`, four headline vintages added across the two issuer
editions, repeat `no_change`, both latest cards official with private issuer
labels, integrity `ok`, source backup unchanged.

**Current production-schema limitation:** production advanced to schema 9
during this step; this feature branch remains on its original schema-8 base.
The first attempt to verify a fresh current-production snapshot stopped at the
schema guard before collection. The proposed upstream `10a7cc0` cherry-pick
was rejected and was not performed. The successful live issuer run instead
used today's already-verified production backup
`D:\Temp\mdh-m9f-ysoldsca\macro_releases.sqlite` as its read-only source and
created the new copy above. This validates live issuer collection on a production
backup; it does not certify this branch against the current schema-9 production
DB. No schema guard was bypassed, no DB version was downgraded, and no production
write or branch merge was performed.

Final Case-Shiller regression (24 October 2026): all `tests/test_macro_*.py`
and `tests/test_indicator_cards.py` passed: **1,229 passed, 1 skipped** in
347 seconds. Repository-wide Ruff, generated-card freshness, all five strict
19-column pending drafts and `git diff --check` passed. No dependency was added,
and saved-publication tests prohibit network access. Reproduce the live check
using the compatible production backup source:

```powershell
& C:\ProgramData\spyder-6\python.exe scripts\verify_macro_m9f.py --source D:\Temp\mdh-m9f-ysoldsca\macro_releases.sqlite --destination-root D:\Temp --families us_case_shiller
```

## Validation for the construction step

- All `tests/test_macro_*.py` plus `tests/test_indicator_cards.py`:
  **1,159 passed, 1 skipped** (24 October 2026).
- Repository-wide `ruff check .`: passed.
- Generated indicator-card JSON freshness and `git diff --check`: passed.
- Live backup verification for both implemented housing families: passed,
  including the unchanged second collection and latest official read-API cards.

The regression suite records FHFA's unapproved concept as an explicit card gap.
Consensus aliases must be exact and justified, but need not exist for every
family; FHFA deliberately has no FF alias. No new dependency was added.
