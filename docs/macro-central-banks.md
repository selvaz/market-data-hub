# M9c: official central-bank policy decisions

Implementation branch: `feat/m9c-central-banks`, based on `feat/m9b-us-ea-uk-rest`
(PR #122). [PR #123](https://github.com/selvaz/market-data-hub/pull/123) targets
`main`; it was opened before final verification. No merge or deployment was done.

All six families returned `ok` on a private SQLite backup of production. The
read API returned 40 official 2026 decision cards. Recollection returned
`no_change`, with zero new vintages. **The exact-publication-time requirement
remains incomplete for historical BOJ statements**: these official decisions
carry `date_only` precision, a null clock in `policy_decision.published_at`, and
an explicit `time_missing_reason`. No time is inferred from meeting hours.

| Bank / issuer publication | Collected concept | Latest announcement found | Decided rate and change | Rate vote | Publication time |
|---|---|---|---|---|---|
| [Fed statement](https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm) | FOMC target range, both endpoints | 2026-09-16 | 3.75–4.00%; +25 bp | 12–0 | 14:00 New York / 18:00 UTC |
| [ECB decision](https://www.ecb.europa.eu/press/pr/date/2026/html/ecb.mp260910~314e508016.en.html) | Deposit facility rate; MRO and marginal lending rates retained too | 2026-09-10 | DFR 2.50%; +25 bp (MRO 2.65%, MLF 2.90%) | No numerical split published in the decision | 14:15 Frankfurt / 12:15 UTC |
| [BoE summary and minutes](https://www.bankofengland.co.uk/monetary-policy-summary-and-minutes/2026/september-2026) | Bank Rate and the Bank Rate vote | 2026-09-17; vote taken at meeting ending 2026-09-16 | 3.75%; 0 bp | 6–3 | 12:00 London / 11:00 UTC |
| [BOJ statement](https://www.boj.or.jp/en/mopo/mpmdeci/mpr_2026/k260918a.pdf) | Uncollateralized overnight call rate guideline | 2026-09-18 | Around 1.25%; +25 bp | 7–2 | Exact time unavailable from retained issuer evidence; date only |
| [PBOC-authorized NIFC announcement](https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125440/3876551/2026102008303870640/index.html) | 1Y and over-5Y LPR, each with its own change | 2026-10-20 | 1Y 3.00%; over-5Y 3.50%; both 0 bp | No vote split published | 09:00 Beijing / 01:00 UTC |
| [Bank of Canada announcement](https://www.bankofcanada.ca/2026/09/fad-press-release-2026-09-02/) | Target for the overnight rate | 2026-09-02 | 2.25%; 0 bp | No numerical split published | 09:45 Toronto / 13:45 UTC |

The scalar vintage for the Fed is explicitly the upper bound; its card retains
the complete target range. LPR's scalar is explicitly 1Y; both tenors remain
in the card. BOJ and LPR changes compare consecutive dated **issuer statements**,
including the previous statement's URL and rates in `supporting_sources`.
They never use market rates or news. A hold still creates a separate decision
edition with change zero. Publication date and vote date remain separate for BoE.

## Reviewed calendars through 31 December 2026

Saved issuer calendars and source metadata are in
`tests/fixtures/macro_releases/m9c/calendar-sources.json`; the explicit registry
is `market_data_hub/macro_releases/policy_editions.py`.

| Bank | Issuer calendar | Reviewed announcement dates in 2026 | Remaining after 24 October |
|---|---|---|---|
| Fed | [FOMC calendars](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm) | Jan 28; Mar 18; Apr 29; Jun 17; Jul 29; Sep 16; Oct 28; Dec 9 | Oct 28; Dec 9 |
| ECB | [Governing Council meetings](https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html) | Feb 5; Mar 19; Apr 30; Jun 11; Jul 23; Sep 10; Oct 29; Dec 17 | Oct 29; Dec 17 |
| BoE | [MPC dates](https://www.bankofengland.co.uk/monetary-policy/upcoming-mpc-dates) | Feb 5; Mar 19; Apr 30; Jun 18; Jul 30; Sep 17; Nov 5; Dec 17 | Nov 5; Dec 17 |
| BOJ | [MPM schedule](https://www.boj.or.jp/en/mopo/mpmsche_minu/index.htm) | Jan 23; Mar 19; Apr 28; Jun 16; Jul 31; Sep 18; Oct 30; Dec 18 | Oct 30; Dec 18 |
| PBOC/NIFC | [NIFC publication rule](https://www.chinamoney.com.cn/chinese/bklpr/) and [PBOC dated archive](https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125440/3876551/index.html) | Jan 20; Feb 24; Mar 20; Apr 20; May 20; Jun 22; Jul 20; Aug 20; Sep 20; Oct 20; Nov 20; Dec 21 | Nov 20; Dec 21 |
| Bank of Canada | [2026 announcement schedule](https://www.bankofcanada.ca/2025/08/bank-canada-publishes-2026-schedule-policy-interest-rate-announcements-other-major-publications/) | Jan 28; Mar 18; Apr 29; Jun 10; Jul 15; Sep 2; Oct 28; Dec 9 | Oct 28; Dec 9 |

LPR has a monthly publication rule rather than policy meetings: the 20th,
postponed for holidays. Past editions use the dates actually printed by PBOC
(including September 20); November 20 and December 21 are reviewed future
dates under NIFC's rule. The catalogue's approximate 09:15 is superseded by
NIFC's published 09:00 rule and the visible 09:00 publication stamps. Canada's
2026 schedule explicitly says 09:45, superseding the catalogue's approximate
10:00. BoE's [policy procedure](https://www.bankofengland.co.uk/monetary-policy)
specifies noon; [ECB's decision index](https://www.ecb.europa.eu/press/govcdec/mopo/html/index.en.html)
specifies 14:15. Future BOJ release clocks remain undecided.

The collector gates fixed-time editions at the actual issuer-local release
instant, with time-zone and DST conversion. BOJ may be fetched on the meeting
date, but the statement must independently confirm that date; only an exactly
matching issuer RSS item can establish its variable clock. An unknown or
emergency edition, changed format, nonconsecutive comparison statement, or
calendar beyond 2026 fails closed with `needs_review` and retained raw evidence.

## Golden publications and card coverage

`golden-set.json` records 14 golden cases with issuer URLs and SHA-256 checked
original publications. `publication-replay.json` supplies the full 40-decision
offline daily replay, including comparison statements and issuer listings.

| Bank | Golden announcement dates | Coverage |
|---|---|---|
| Fed | Jan 28; Jul 29; Sep 16 | Named-voter format, numeric split, hold and increase |
| ECB | Jul 23; Sep 10 | Hold and increase; deposit/MRO/MLF association |
| BoE | Feb 5; Jul 30; Sep 17 | 5–4 and 6–3 Bank Rate votes; meeting and release dates |
| BOJ | Jul 31; Sep 18 | Hold and increase, 8–1 and 7–2; date-only clock limitation |
| PBOC/NIFC | Sep 20; Oct 20 | Both tenors and consecutive-statement comparisons |
| Bank of Canada | Jul 15; Sep 2 | Holds are distinct cards with zero change |

All six catalogue ids alias existing approved v2.1 concepts, one alias per line.
No new card was invented and no M9c card gap exists. Overall catalogue coverage
is 50 mapped out of 54; the four pre-existing certified gaps remain unchanged.
The generated indicator-card dataset was rebuilt.

## Backup verification and limitations

Run: `scripts/verify_macro_m9c.py --source <production file> --destination-root D:\Temp`.
The verifier reads stable main/WAL bytes into a private snapshot and uses
SQLite's backup API there. SQLite never opens production, even with `mode=ro`.

Verified copy: `D:\Temp\mdh-m9c-e6qduv77\macro_releases.sqlite`.
Full report: `D:\Temp\mdh-m9c-e6qduv77\verification.json`.

- Dry run: all six families `ok`; 40 candidate vintages.
- Copy collection: all six families `ok`; 40 new official decision cards.
- Repeat collection: every family `no_change`; zero new vintages. Exit code 3 is
  the collector's documented successful no-work outcome. The saved report was
  reassessed after correcting the verifier's initial zero-only exit-code check.
- Read API: six latest official cards; 6 decisions per meeting-based bank and
  10 LPR announcements. SQLite `quick_check`: `ok`.
- Production main file: 73,482,240 bytes before and after; SHA-256 unchanged:
  `66007da1e928b44e2792b531de60788b06740bee46f4a01d39f99a32a0fe5c41`.
- Exact-time completeness: **false**, solely because the retained BOJ historical
  statements/calendar and current issuer RSS do not establish those clocks.
  A synthetic RSS unit test checks matching behavior; it is not historical
  timestamp evidence and is not a golden publication.

Future decisions have not happened and were not collected. They remain scheduled.
No production writes, deployment, new dependencies, or network calls in the
M9c tests were introduced. Final local test results are recorded in the PR.
