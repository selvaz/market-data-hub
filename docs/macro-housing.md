# M9f housing release families

Branch `feat/m9f-housing` is based on `origin/integrate/m2b` at `e301c52`.
[PR #130](https://github.com/selvaz/market-data-hub/pull/130) targets `main` and
stacks on [#128](https://github.com/selvaz/market-data-hub/pull/128). Do not merge
before the integration PR. Work is delivered one family at a time.

## Certified so far (24 October 2026)

| Family | Issuer | Source class | Frequency | Reviewed editions through 31 December 2026 | Golden cases |
| --- | --- | --- | --- | --- | --- |
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

1. Eurostat quarterly HPI, y/y and q/q.
2. ECB house-purchase loan growth and composite cost of borrowing.
3. ONS/HM Land Registry UK HPI, y/y.
4. BoE mortgage approvals for house purchase.
5. Destatis permits, CMHC starts, MLIT starts and NBS 70-city prices: investigate
   issuer stability/free availability and either certify or document the gap.

These families are pending implementation, not established source gaps.

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
