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

Calendars: [FHFA](https://www.fhfa.gov/data/hpi) and
[Census](https://www.census.gov/economic-indicators/calendar-listview-2026.html).
The saved Census calendar in `tests/fixtures/macro_releases/us_monthly` is
shared evidence for the construction schedule. Edition metadata lives in
`housing_editions.py`; unknown dates fail closed.

Issuer golden URLs:

- [FHFA August quarterly report, including June monthly table](https://www.fhfa.gov/document/d/hpi/fhfa-house-price-index-report-2026q2).
- [FHFA September monthly report](https://www.fhfa.gov/document/d/hpi/fhfa-hpi-monthly-september-2026).
- [Census June construction release](https://www.census.gov/construction/c30/pdf/pr202606.pdf).
- [Census rolling construction release, August edition captured 24 October](https://www.census.gov/construction/c30/pdf/release.pdf).

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

## Latest cards verified on a production backup

| Family | Reference period | Edition | Issuer release time | Latest official card |
| --- | --- | --- | --- | --- |
| FHFA | July 2026 | 29 September 2026 | 09:00 EDT / 13:00 UTC | +0.3% m/m; +2.6% y/y, purchase-only SA |
| Construction spending | August 2026 | 1 October 2026 | 10:00 EDT / 14:00 UTC | +0.9% m/m, total SA |

Live verification report:
`D:\Temp\mdh-m9f-sl4iuv5p\verification.json`.
The database copy is beside it as `macro_releases.sqlite`.
Dry run and copy collection were `ok` for both families; collection added four
FHFA vintages and one construction vintage. A repeat returned `no_change` for
both. `PRAGMA quick_check` returned `ok`; source main-file size and modification
time were unchanged. The report retains the full read-API provenance.

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
- The exact FF title `Construction Spending m/m`, USD, aliases the total SA
  m/m measure. Other countries, spending levels and residential subtotals do
  not match. No FHFA FF alias has been added.

## Remaining steps, in order

1. Freddie Mac PMMS 30-year weekly mortgage rate.
2. NAR existing and pending home sales: public-release headlines, private source.
3. Case-Shiller national and 20-city: public-release headlines, private source;
   FRED may supply redistributor history only.
4. Eurostat quarterly HPI, y/y and q/q.
5. ECB house-purchase loan growth and composite cost of borrowing.
6. ONS/HM Land Registry UK HPI, y/y.
7. BoE mortgage approvals for house purchase.
8. Destatis permits, CMHC starts, MLIT starts and NBS 70-city prices: investigate
   issuer stability/free availability and either certify or document the gap.

These families are pending implementation, not established source gaps.
