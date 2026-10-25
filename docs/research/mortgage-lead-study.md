# Do mortgage rates and purchase applications lead US home sales?

Research date: 2026-10-25. The PMMS timing amendment and MBA change card were
approved by the owner on 2026-10-25 for classification v2.4. The PALI follow-up
below remains a proposal in v2.5 drafts. Production is unchanged.

**Recommendation:** use PMMS as **CTX**, with no timing basis, and a potential
MBA purchase measure as **UNKNOWN / HYPOTHESIS**, referenced to NAR existing
closed sales. The literature supports mortgage transmission and an applications
to closings delay. It does **not** establish a stable, universal sequence in
which applications precede pending contracts. Our revised-data tests find
useful one-month PMMS forecast content for new sales and starts, but the
largest simple correlations are at month zero and the relationship is unstable
across periods. There is insufficient freely accessible NAR/MBA history to
validate the proposed closed-sales chain locally.

Repository state matters: at base commit `5e04a4b`, `us_pmms_30y` is in
`draft_rows_v2.3.psv` as UNKNOWN with no basis, rather than an approved CTX row.
CTX here is the owner's stated contextual use and this report's recommendation.
The approved permits and starts cards are untouched. They concern other
references/transforms; their LEAD classifications cannot be transferred to PMMS.
See [indicator-card semantics](../indicator-cards.md).

## Literature verdict and strongest evidence

The following are direct findings, paraphrased with their quantitative content
preserved. Interpretations are explicitly separated. The search prioritized
publisher methodology, central-bank research and peer-reviewed work; checked
2026-10-25.

| Source, author, year | Direct finding and location | Interpretation for this study |
| --- | --- | --- |
| [Daily monetary policy shocks and new home sales](https://www.sciencedirect.com/science/article/abs/pii/S030439320800113X), **James D. Hamilton, 2008**, *Journal of Monetary Economics* 55(7), 1171–1190, DOI 10.1016/j.jmoneco.2008.08.010. [Author's working paper](https://econweb.ucsd.edu/~jhamilton/daily_shocks.pdf). | Introduction and section 4: mortgage rates respond immediately to news about future monetary policy. Different new-home purchases respond to rate changes from the preceding month through roughly six months earlier. The largest sales response to policy news occurs after **15 weeks**; heterogeneous household search times explain the distributed delay. | Supports rates preceding new sales over several months in this model/sample. Fifteen weeks is a response to identified policy news, not a universal PMMS cross-correlation peak. Our monthly growth specification differs from the paper's model. |
| [The Housing Boom and the Decline in Mortgage Rates](https://libertystreeteconomics.newyorkfed.org/2021/09/the-housing-boom-and-the-decline-in-mortgage-rates/), **Haoyang Liu, David Lucca, Dean Parker and Gabriela Rays-Wahba, New York Fed, 2021**. | Local projections on quarterly 1975–2020 data: a one percentage point mortgage-rate decline is followed by **5–10% higher existing sales** and **over 10% higher single-family permits**. Specifications include lagged housing activity, unemployment, inflation and the one-year Treasury rate; adding contemporaneous controls changes persistence and magnitude. | Strong evidence for a housing-activity transmission channel. The reported construction measure is permits, not HOUST. This source does not supply a common monthly starts lead or validate weekly PMMS forecasts. |
| [Greenbook, Part 2, January 28, 1998](https://www.federalreserve.gov/monetarypolicy/files/fomc19980204gbpt219980128.pdf), **Federal Reserve Board staff, 1998**, footnote 7, printed II-19, PDF page 23. | Purchase mortgage applications and new-home sales have a strong contemporaneous relationship because contracts and applications occur around the same time. Existing sales are recorded at closing; applications precede these sales by **about 1.5 months on average**, with the closing process generally taking one to two months. | Direct, authoritative support for applications leading closed sales. It is an older staff assessment, not a modern out-of-sample validation of the MBA index. It does not establish an additional applications-to-pending lead. |
| [Pending Home Sales methodology](https://www.nar.realtor/research-and-statistics/housing-statistics/pending-home-sales/methodology), **NAR Research, undated**, corroborated by its [background note](https://www.nar.realtor/research-and-statistics/housing-statistics/pending-home-sales/background). | A pending observation represents an accepted contract. Most become completed transactions **one to two months later**; NAR's sample has **over 80% settling within two months**, with some settling in months three and four. Cancellations can disrupt conversion. NAR also cautions that the statistical relationship between mortgage applications and existing sales is less satisfactory than the pending-sales relationship. | The contract-to-closing lead is stronger than the claim that applications are the earliest stage for every buyer. Application timing relative to pending status remains a hypothesis. |
| [Weekly Applications Survey FAQ](https://www.mba.org/docs/default-source/research-and-forecasts/faqs/res_sf_wasfaq.pdf?sfvrsn=c0a4c637_1), **MBA Research, undated** (coverage statement dated September 2011), questions 3, 6, 7, 9; [methodology](https://www.mba.org/docs/default-source/research-and-forecasts/res_sf_wasmethodology.pdf), Purchase Index definition. | Applications are counted when creditworthiness is assessed, potentially at preapproval or rate lock. An application is not necessarily approved, accepted or funded. The Purchase Index covers purchase applications; refinance applications have a separate index. Results are issued Wednesday at **07:00 Eastern**, covering the preceding Saturday–Friday. Retail/direct applications are covered, rather than every distribution channel. | Purchase-only is the appropriate candidate. A weekly application count is not an origination count or a count of unique completed home sales. The old FAQ's coverage percentage should not be treated as a current coverage estimate. |

**Verdict (a):** there is credible evidence that rates affect subsequent sales
and construction, with Hamilton documenting a distributed new-sales response
over roughly one to six months. The New York Fed documents a substantial
sales/permits response. Neither supports assigning every rate observation a
fixed lead over all four candidate references. No uniform PMMS-to-starts delay
was established by the strongest sources reviewed; the HOUST test below is
needed independently.

**Verdict (b):** purchase applications plausibly precede existing closings by
about one to two months, with direct Fed staff support. Applications preceding
pending contracts is **our inference to test**, not a direct finding of these
sources. Preapproval can precede a contract; a property-specific application or
rate lock can occur around or after it. Earlier weekly publication also differs
from an earlier economic event.

Several caveats affect both chains:

- **Refinance contamination:** the Market Composite includes purchase and
  refinance demand. Use Purchase only; refinancing reacts strongly to rate
  incentives and is not a housing transaction. MBA's methodology separates
  these purposes.
- **Cash buyers and sample coverage:** applications omit purchases without a
  mortgage, and the cash share can change. NAR documents cash transactions in
  its [2026 research update](https://cms.nar.realtor/sites/default/files/2026-03/2026-03-research-update-report-03-17-2026.pdf).
  Application-to-total-sales conversion is therefore variable (our inference).
- **Application versus origination:** approvals, withdrawals, cancellations,
  repeated shopping and lender coverage can change conversion. The MBA FAQ
  explicitly distinguishes an application from a funded loan; it does not
  warrant assuming one application equals one closing.
- **Weekly noise and holidays:** seasonal and holiday adjustment, shifting
  calendar weeks and sampling composition can obscure a signal. These are
  general weekly-data problems documented by **Thomas D. Evans, Brian C.
  Monsell and Michael Sverchkov, BLS, 2021**, [Review of Available Programs for
  Seasonal Adjustment of Weekly Data](https://www.bls.gov/osmr/research-papers/2021/st210020.htm).
  A four-week average is a useful descriptive choice, not a validated lead.
- **Different economic events:** [Census Survey of Construction definitions](https://www.census.gov/construction/soc/definitions.html),
  **Census, undated**, count a new sale at contract/deposit, potentially before
  construction; they do not follow it through closing. Starts measure actual
  construction, including multifamily rental activity. Thus neither is simply
  a later stage of the existing-home purchase pipeline.
- **PMMS break and endogeneity:** [Freddie Mac, 2022, methodology explanation](https://www.freddiemac.com/research/insight/20221103-freddie-macs-newly-enhanced-mortgage-rate-survey)
  and [current PMMS FAQ](https://www.freddiemac.com/pmms) describe the switch on
  November 17, 2022 from lender surveying to LPA purchase-application data.
  The rate concerns selected conventional conforming borrowers, rather than
  every buyer's realized financing cost. The post-2020 sample straddles this
  break. Current PMMS is itself application-based, so treating it as a separate
  exogenous event before every application is unwarranted (our inference).

## Data, availability and reproducibility

The script uses the existing `FRED_API_KEY` environment variable and makes no
database connection. Freddie Mac, NAR and Census/HUD are the originating
publishers; **FRED/ALFRED are redistributors used for research**, not issuer
certification. Data are frozen as of **2026-10-25**, with an observation cutoff
of **2026-09-30**. HSN1F ends in August because September was not yet available.
The script retains missing months and never interpolates them.

[PMMS / MORTGAGE30US](https://fred.stlouisfed.org/series/MORTGAGE30US),
[existing sales / EXHOSLUSM495S](https://fred.stlouisfed.org/series/EXHOSLUSM495S),
[new sales / HSN1F](https://fred.stlouisfed.org/series/HSN1F), and
[starts / HOUST](https://fred.stlouisfed.org/series/HOUST) identify the research
series. All activity inputs used in estimation are seasonally adjusted monthly
levels (SAAR where applicable). PMMS is a weekly percent interest rate.

The inventory in the generated appendix is the actual response, not assumed
historical coverage. Existing sales has only **13 monthly levels / 12 growth
observations**. An explicit ALFRED request for 2024-10-25 returned HTTP 400 and
reported no ALFRED series. A current-date request does work; that does not
establish an archive. The probe is included in cached metadata. We neither
stitch older snapshots nor substitute prices, inventories or single-family-only
sales for total existing sales.

FRED's catalog search for `mortgage purchase applications` returns no series.
The `pending home sales` search returns housing inventory and unrelated
geographic matches, rather than a national pending-sales series. NAR has public
current releases, but its [historical pending-sales file](https://store.realtor/pending-home-sales-historical-data-file-2026-download/)
is sold as two spreadsheets covering 2001–2025. No free, sufficiently long,
authorized national pending history was located in the official sources
checked. Recent public headlines are not a consistent revised level history.
PHSI and MBA are therefore absent inputs, not zero-valued series. Every missing
pair is reported N/A rather than given a fabricated estimate.

Only aggregate statistics and input provenance appear here. Raw responses stay
under ignored `.tmp/mortgage-lead/`; neither observations nor credentials are
committed. Exact offline reproduction requires retaining that local snapshot.
Future downloads can differ because availability and revisions change even
when an observation cutoff is held fixed. FRED's [real-time-period API note](https://fred.stlouisfed.org/docs/api/fred/realtime_period.html)
explains the distinction between observation dates and vintage dates.

From the repository root, using its normal research Python interpreter:

```powershell
& 'C:\ProgramData\spyder-6\python.exe' scripts/research/mortgage_lead_study.py --fetch --as-of 2026-10-25 --end 2026-09-30 --output .tmp/mortgage-lead/statistics.json --tables .tmp/mortgage-lead/tables.md
# Offline, deterministic rerun of the frozen input:
& 'C:\ProgramData\spyder-6\python.exe' scripts/research/mortgage_lead_study.py --as-of 2026-10-25 --end 2026-09-30 --output .tmp/mortgage-lead/statistics.json --tables .tmp/mortgage-lead/tables.md
& 'C:\ProgramData\spyder-6\python.exe' -m pytest tests/test_mortgage_lead_study.py -q --basetemp D:\Temp\pytest-mdh-mortgage
```

The standalone script uses existing numpy/pandas/requests dependencies. Seed
**1729** fixes resampling. The offline fixture generates a known two-month
inverse rate-to-sales relationship and checks lag recovery, Granger rejection,
forecast improvement, byte-identical output on repeated runs, missing-month
handling and credential-safe failure. Determinism means identical inputs and
software environment; it does not promise identical floating-point bytes
across numerical-library versions. The actual run used numpy 2.4.6 and pandas
3.0.3. The generated appendix below comes directly from `--tables`.

## Test design and interpretation

1. **Transforms:** average all observations in each calendar month, then first
   difference PMMS in percentage points. For activity, use
   `100 * (log(level_t) - log(level_(t-1)))`. Monthly releases are already
   monthly observations. This avoids correlations in trending levels, but
   differencing alone is not a proof of stationarity or structural stability.
   Weekly PMMS is grouped by FRED's observation date; no within-week proration
   is attempted. Monthly averages are unknown until the month is complete.
2. **Cross-correlations:** `corr(X_(t-k), Y_t)` for **k=0…12**. Positive k means
   X precedes Y. The largest absolute correlation is the descriptive peak;
   signs, lag-zero peaks and sample sizes are retained. IID Pearson p-values
   and the 13-lag Bonferroni correction are shown. A separate **499-draw,
   12-month-block permutation** of X, keeping Y ordered, tests the maximum
   absolute correlation across all 13 lags. This preserves within-block
   dependence and adjusts lag search, but assumes exchangeability of blocks.
   Its minimum reported p is 0.002. None of these tests identifies causality
   or a matched turning-point lead; no peak-lag confidence interval is estimated.
3. **Granger:** fit a bivariate VAR with an intercept and candidate orders
   **1…12**. Choose orders independently by joint Gaussian AIC and BIC on the
   same rows trimmed for the maximum lag. Test all lagged X terms in the Y
   equation jointly using the classical nested OLS F-test. Full-sample orders
   are descriptive. Reported p-values do not adjust for order selection,
   multiple pairs or heteroskedasticity. They are exploratory evidence of
   incremental lagged association, not structural identification.
4. **Out of sample:** expanding-window **direct** forecasts of monthly growth
   at **h=1,2,3,6,12**. Baseline: intercept plus own activity lags (AR, including
   order zero). Augmented model: the same kind of own lags plus rate-change
   lags (ARX). Each model/horizon selects its order by BIC using only the initial
   training window and freezes it; coefficients refit at each origin. Initial
   window is `max(36, min(120, floor(n/2)))` calendar months; candidate order is
   `min(12, floor((initial-h-10)/5))`. All candidate models use the same complete
   training rows. For a forecast made at t, features are dated t or earlier,
   and only outcomes dated t or earlier enter training. This prevents
   overlapping-horizon training leakage. The target is growth **in month t+h**,
   not cumulative growth through h.
5. **Metric:** RMSE(ARX)/RMSE(AR); below one favors adding rates. Absolute RMSE
   is in log-growth percentage points. A paired circular block bootstrap of
   forecast errors (**499 draws, 12-month blocks**) supplies conditional 95%
   percentile intervals. These intervals are exploratory, do not redo model
   selection, and are weak with few blocks. They are not adjusted for choosing
   among periods/horizons. This is **pseudo-OOS on revised, period-dated data**:
   period-t releases are assumed known. Publication lags, release vintages and
   future seasonal revisions are not reconstructed. It is not an implementable
   real-time trading or nowcasting backtest.
6. **Stability:** repeat the analysis independently before 2008, in 2009–2019,
   and from 2020. The whole-sample result includes 2008, while those three
   subperiods omit it as requested. Transforms are computed before slicing;
   lag regressions and forecasting are confined to each sliced period. At
   least 24 pairs are needed for a correlation, 40 complete maximum-lag rows
   for Granger, 60 paired transformed months for OOS eligibility, and 12
   forecast errors for a horizon. Missing history fails these gates openly.

## Main numerical findings

| Predictor -> reference | Paired growth months | Best lag / correlation | Peak significance | Granger AIC; BIC (order, p) | One-month OOS RMSE ratio (95% block interval) |
| --- | ---: | --- | --- | --- | --- |
| PMMS change -> new sales growth (ML1) | 664 | **0m / -0.300** | IID p=2.67e-15; Bonferroni p=3.47e-14; block max-r p=0.002 | 3, 1.28e-13; 2, 1.97e-13 | **0.956 [0.930, 0.986]**, 544 forecasts |
| PMMS change -> starts growth (ML2) | 665 | **0m / -0.125** | IID p=0.00124; Bonferroni p=0.0161; block max-r p=0.002 | 4, 6.31e-09; 2, 4.09e-07 | **0.939 [0.901, 0.983]**, 545 forecasts |
| PMMS change -> existing sales growth | 12 | N/A | N/A | N/A | N/A: insufficient history |
| PMMS change -> pending sales growth | 0 | N/A | N/A | N/A | N/A: no admitted history |
| MBA Purchase -> pending, existing, new sales, starts | 0 each | N/A for each | N/A for each | N/A for each | N/A for each: no admitted MBA history |

The contemporaneous peak does not contradict significant lagged terms: the
statistics ask different questions. ML1 has r=-0.203 at +1m; starts has r=-0.113
at +1m and -0.124 at +2m, almost as large as its month-zero correlation.
Granger adds conditional information beyond past activity; it does not convert
the largest raw correlation into a validated lead.

The apparently useful full-sample one-month forecasts are not stable enough
for a universal timing class. Before 2008, ML1/ML2 ratios are 0.939/0.929;
in 2009–2019 they deteriorate to **1.023/1.005**, with BIC Granger p-values
**0.186/0.805**. From 2020 the one-month ratios improve to 0.955/0.905, but there
are only 40/41 forecasts, BIC Granger p-values are **0.111/0.314**, and block
max-correlation p-values are 0.364/0.532. The descriptive subperiod peaks also
move to weak, sometimes opposite-signed lags. Rejection in the long sample
therefore does not establish persistence across monetary and housing regimes.

At h=2,3,6,12 the full-sample new-sales ratios are 1.002, 1.002, 1.002, 0.998;
starts ratios are 0.998, 1.000, 1.003, 0.996. Their intervals include or touch
one at displayed precision. These are small changes, rather than a broad
predictive result across horizons. The appendix preserves all absolute errors,
orders, forecast dates and periods rather than selecting only a good horizon.

## What MBA makes available, and what collection would permit

The [October 21, 2026 public release](https://www.mba.org/news-and-research/newsroom/news/2026/10/21/mortgage-applications-decrease-in-latest-mba-weekly-survey)
and [September 30 release](https://www.mba.org/news-and-research/newsroom/news/2026/09/30/mortgage-applications-decrease-in-latest-mba-weekly-survey),
**MBA Research, 2026**, publish **SA Purchase weekly percent change, NSA
Purchase weekly percent change and NSA Purchase year-over-year change**,
alongside composite/refinance changes, application shares and selected
mortgage rates/points. The inspected free releases do **not** provide the
Purchase Index level. Do not confuse the reported percent change with an
index level or the composite with Purchase.

The regular publication time is **Wednesday 07:00 America/New_York** for the
prior Saturday–Friday period, per MBA's FAQ and [subscription description](https://www.mba.org/home/product/weekly-applications-survey-single-user-subscription-70669).
That is 11:00 UTC in daylight time and 12:00 UTC in standard time. Holidays can
change the schedule or combine weeks; [MBA's December 2017 holiday notice](https://newslink.mba.org/?p=32935)
explicitly illustrates a skipped release followed by two weeks of results.
Store the observation week and actual publication timestamp separately.

MBA's [survey page and historical-report catalog](https://www.mba.org/news-and-research/research-and-economics/single-family-research/weekly-applications-survey/)
offers index history from March 1990 **for purchase**. The subscription includes
weekly levels but says historical data are not included in the annual
subscription. Sample reports are not a free complete history. No subscription,
licensed table, private endpoint or third-party licensed history was scraped.

| Route checked | Finding / consequence |
| --- | --- |
| MBA public press releases | Free changes and commentary; no level in the releases inspected. They support a narrowly named purchase-applications **w/w change** concept, not a promise to collect the exact index level for free. |
| MBA research catalog | Historical index values and individual historical variables are commercial products. No free full purchase history located. |
| FRED / ALFRED | API catalog search found no Purchase Applications series; no official mirror identified. Research probes are captured in local metadata. |
| [OECD Data Explorer](https://data-explorer.oecd.org/) and OECD-domain search | No identifiable MBA Purchase series or free historical download located. This is a bounded negative search, not proof that no official dataset anywhere contains it; no unidentified OECD proxy was substituted. |
| [Fannie Mae Weekly Mortgage Applications Data](https://www.fanniemae.com/data-and-insights/surveys-indices/weekly-mortgage-applications-data) | Free official PALI/RALI history is a separate possible research source. [Fannie Mae's June 9, 2026 announcement](https://capitalmarkets.fanniemae.com/mortgage-backed-securities/fannie-mae-launches-purchase-application-level-index) identifies PALI as its DU-based purchase index. It is **not MBA**, so it is not used to claim validation of an MBA card. |

Own collection could preserve the free purchase-change headlines and release
timestamps. Chaining rounded weekly changes would produce only an approximate
rebased proxy; it cannot recover MBA's exact level, historical seasonal
revisions or missing releases. A monthly **average of exact index levels**
cannot be obtained from an arbitrary initial level plus rounded changes with
the same fidelity. Testing that proxy would require explicitly naming it as
such and documenting its construction; this script requires authorized level
inputs for `MBA_PURCHASE` and does not silently synthesize them.

The script's collection-planning arithmetic yields **61 monthly levels / about
266 complete weeks** before a first one-month screen can meet the 60-growth-
month gate. Prefer **120 monthly levels / about 522 weeks (ten years)** for a
more useful multi-horizon comparison across conditions. These are design
floors, not statistical power guarantees; longer histories are desirable for
12-lag inference and regime stability. The earliest eligible screen does not
support every horizon. Allow additional time for partial first months, release
lags and missed weeks, and collect the reference series alongside applications.
Waiting for future data cannot recreate pre-2008 or 2009–2019 stability tests:
those need existing authorized history. An exact MBA-level study still needs
an authorized level source, regardless of how long rounded headlines are kept.

## Proposed card fields for the owner

These are recommendations, not edits to either approved or draft package data.
The schema's tested descriptive basis is named `DESCRIPTIVE`, not `TESTED`.
CTX carries no timing claim and therefore no basis. UNKNOWN differs from CTX:
it says a proposed relationship remains unclassified. `pan_mir` provides the
UNKNOWN/HYPOTHESIS precedent; the US permits/starts rows show why each tested
relationship must retain its own reference, direction and transform.

| Field | `us_pmms_30y` | Proposed `us_mba_purchase` |
| --- | --- | --- |
| `timing_class` | **CTX** | **UNKNOWN** |
| `timing_basis` | **null** (PSV `—`) | **HYPOTHESIS** |
| `reference` | US mortgage financing conditions; ML1/ML2 separately name new sales/starts | US existing-home sales (NAR closed sales); pending is a separate untested reference |
| `class_pair` / approved `relationship_keys` | null / empty; no approved relationship promoted | null / empty |
| `evidence.measurement` (`ev_meas`) | **M**, retaining the current draft's rating and its borrower/sample limitations | **M**, conditional on an identifiable purchase-only release and adjustment basis |
| `evidence.mechanism` (`ev_mech`) | **mixed**: financing transmission is credible, but the usable timing relationship is unstable | **mixed**: applications often precede financed closings, but cash, fallout, coverage and contract timing vary |
| `evidence.local_timing` (`ev_timing`) | **lit-local**: US literature plus this exploratory local study; no approved v2 screen | **lit-US**: US primary literature; no local application-history test |
| `descriptive_peak` | `ML1 [monthly delta PMMS -> monthly new-sales log growth]: +0m, r=-0.300; ML2 [monthly delta PMMS -> monthly starts log growth]: +0m, r=-0.125; peak-lag intervals not estimated; not a validated lead` | `not estimated` |
| `predictive_screen` (timing-file source text) | `Approved v2 screen not tested. Exploratory ML1/ML2 revised-data h=1m RMSE ratios 0.956/0.939; stability mixed; no PRED-PASS assigned. See mortgage-lead-study.md.` | `not tested` |
| Compact rationale | Financing context with some one-month forecast information, not a stable general activity lead. Hamilton and New York Fed establish a mechanism; ML1/ML2 peaks and period tests prevent a universal LEAD claim. | A 1–2 month lead over closed sales is a literature-backed hypothesis (Fed 1998; NAR contract-to-close methodology). There is no local MBA validation and no demonstrated extra lead over pending contracts. |

For an implementation restricted to free releases, the concept label should
explicitly say **MBA purchase applications, SA w/w change**. Reserve an exact
**MBA Purchase Applications Index level** label for an authorized level source.
The timing proposal is conditional on that measurement distinction; it does
not authorize a collector or a licensing decision.

The PMMS `predictive_screen` proposal is a free-text note, yielding no structured
passing relationship. The approved screen schema permits passing horizons
3m/6m/12m and a specific existing protocol; this independent one-month study
does not reproduce that protocol. It must not manufacture `PRED-PASS`,
`v2-pred-pass (US)` or matched turning-point estimates. Conversely, "approved
screen not tested" does not erase the exploratory tests and their numbers.

## PALI follow-up: full free history, proposal only (2026-10-25)

The [Fannie Mae official workbook](https://www.fanniemae.com/mortgage-application-index/download)
contains **1,189 weekly levels from January 9, 2004 through October 16, 2026**.
This is Fannie Mae's own DU-based **Purchase Application-Level Index**, not MBA.
UPB (`WK_TOTAL_PALI_$`) and application count (`WK_TOTAL_PALI_CT`) are separate
**NSA** indices, each based at January 9, 2004=100. Their levels do not reveal
raw dollars or counts. The issuer's [methodology](https://www.fanniemae.com/media/document/pdf/fannie-mae-pali-rali-methodology.pdf)
allows annual history refreshes and describes duplicate filtering, withdrawn
applications and changes in DU share as limits on interpretation.

We average weekly levels by their Friday reference date within each month,
then apply the study's `100 * delta log` activity transform. The cutoff is
**September 30, 2026**, excluding the unfinished October average. There are
**273 observed monthly PALI averages / 272 monthly growth observations**.
January 2004 averages the four available Fridays starting January 9; it does
not invent the missing January 2 observation. There are **271** paired growth
months for new sales (through August), **272** for starts (through September),
and only **12** for existing sales. Existing sales therefore fails the study's
minimum gates; its correlation, Granger and OOS results are N/A.

The test reuses the study's 0-12 lag correlations, 499-draw block max-r test,
AIC/BIC Granger selection and expanding direct AR-vs-ARX forecasts at
h=1,2,3,6,12 with the same training-only lag selection and error bootstrap.
Both the 2004-2014 and 2015-2026 slices cover the complete history without a
2008 gap; 2009-2019 and 2020 onward retain the original study's comparisons.
The **no-PALI baseline** uses activity's own lags and an intercept. Each
augmented regression adds one PALI variant, never a mixture with refinance.

| Predictor -> target | Paired months | Largest absolute correlation | Granger AIC / BIC (order; p) | h=1 RMSE ratio, 95% block interval; forecasts |
| --- | ---: | --- | --- | --- |
| PALI UPB -> new sales | 271 | +0m; r=0.118 | 12; 0.184 / 12; 0.184 | 1.001 [0.997, 1.007]; 151 |
| PALI UPB -> starts | 272 | +12m; r=-0.078 | 12; 0.808 / 12; 0.808 | 1.004 [0.999, 1.008]; 152 |
| PALI count -> new sales | 271 | +0m; r=0.116 | 12; 0.288 / 12; 0.288 | 1.001 [0.997, 1.006]; 151 |
| PALI count -> starts | 272 | +12m; r=-0.075 | 12; 0.822 / 12; 0.822 | 1.004 [0.999, 1.007]; 152 |
| Either PALI variant -> existing sales | 12 | N/A | N/A | N/A: insufficient history |

Stability is mixed. For UPB -> new sales, h=1 ratios are **0.988 / 0.962**
in 2004-2014 / 2015 onward, but **1.002 / 0.954** in 2009-2019 / 2020 onward.
For UPB -> starts the corresponding ratios are **0.933 / 0.902** and
**1.006 / 0.927**. The whole-sample ratios are above one. Peak lags/signs shift
across slices (new sales: +3m/-0.134 early, +0m/+0.158 later; starts:
+12m/-0.128 early, +4m/+0.112 later). Stronger isolated subperiod forecast
results do not validate a stable economic lead. The generated tables retain
both variants, all lags, horizons, p-values, sample sizes and intervals.

**Proposed PALI classification: UNKNOWN, no timing basis, HARD / M / mixed /
lit-US**, referencing existing-home closed sales, with new sales and starts
separately named exploratory tests. This proposal is in `draft_rows_v2.5.psv`;
only the owner can promote it. No approved v2 PRED-PASS is assigned. Neither
PALI results nor its free history validate the separate MBA card.

**Limits:** the predictor is NSA while the targets are SA. This faithful
application of the study method does not remove calendar seasonality; a
seasonally adjusted or year-over-year robustness study would be a separate
specification. All data are revised and period-dated, not release-vintage
information sets. Complete-month PALI averages become known only after the
last contributing week's publication. Thus OOS here is the same exploratory
pseudo-OOS as the original study, not an implementable real-time forecast.
Pull-through, cash purchases, DU coverage and annual history revisions remain
material. The existing-sales history limitation prevents testing the most
natural closing reference.

Reproduce offline (the FRED snapshot remains untracked; no DB is opened):

```powershell
& 'C:\ProgramData\spyder-6\python.exe' scripts/research/pali_lead_study.py --workbook tests/fixtures/macro_releases/mortgage_applications/pali-history.xlsx --snapshot .tmp/mortgage-lead/observations.json --end 2026-09-30 --output .tmp/pali-lead/statistics.json --tables .tmp/pali-lead/tables.md
```

Input hashes: PALI workbook `955d0993ab6a65d93795139a8a72479ba02702ef98be70473152d72812984a9a`; original FRED snapshot
`56fb92c98eb67d6f51c8a27f1106e6354535616f9fda94f86d09320d0b114f6a`. The published workbook and metadata are golden fixtures;
the statistical output contains no raw observations or credentials.

<!-- BEGIN GENERATED PALI STATISTICS -->

<!-- Generated by mortgage_lead_study.py; statistics only. -->

| Series | Monthly levels | First | Last |
| --- | ---: | --- | --- |
| PALI_UPB | 273 | 2004-01 | 2026-09 |
| PALI_COUNT | 273 | 2004-01 | 2026-09 |
| HSN1F | 272 | 2004-01 | 2026-08 |
| HOUST | 273 | 2004-01 | 2026-09 |
| EXHOSLUSM495S | 13 | 2025-09 | 2026-09 |

| X -> Y | Period | n | Best k | r | p (IID) | p x 13 | Block max-r p | Granger AIC lag / p | Granger BIC lag / p |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| PALI_UPB -> HSN1F | full | 271 | 0 | 0.118 | 0.0531 | 0.69 | 0.026 | 12 / 0.184 | 12 / 0.184 |
| PALI_UPB -> HSN1F | 2004_2014 | 131 | 3 | -0.134 | 0.132 | 1 | 0.28 | 12 / 0.0122 | 1 / 0.876 |
| PALI_UPB -> HSN1F | 2015_onward | 140 | 0 | 0.158 | 0.0619 | 0.804 | 0.06 | 12 / 0.29 | 1 / 0.0964 |
| PALI_UPB -> HSN1F | 2009_2019 | 132 | 3 | -0.166 | 0.0598 | 0.777 | 0.048 | 12 / 0.391 | 12 / 0.391 |
| PALI_UPB -> HSN1F | 2020_onward | 80 | 11 | -0.194 | 0.11 | 1 | 0.446 | 12 / 0.84 | 2 / 0.966 |
| PALI_UPB -> HOUST | full | 272 | 12 | -0.078 | 0.208 | 1 | 0.354 | 12 / 0.808 | 12 / 0.808 |
| PALI_UPB -> HOUST | 2004_2014 | 131 | 12 | -0.128 | 0.167 | 1 | 0.03 | 12 / 0.645 | 1 / 0.634 |
| PALI_UPB -> HOUST | 2015_onward | 141 | 4 | 0.112 | 0.195 | 1 | 0.546 | 12 / 0.0224 | 1 / 0.821 |
| PALI_UPB -> HOUST | 2009_2019 | 132 | 12 | -0.104 | 0.258 | 1 | 0.142 | 12 / 0.752 | 12 / 0.752 |
| PALI_UPB -> HOUST | 2020_onward | 81 | 10 | 0.186 | 0.121 | 1 | 0.204 | 12 / 0.236 | 2 / 0.647 |
| PALI_UPB -> EXHOSLUSM495S | full | 12 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| PALI_UPB -> EXHOSLUSM495S | 2004_2014 | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| PALI_UPB -> EXHOSLUSM495S | 2015_onward | 12 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| PALI_UPB -> EXHOSLUSM495S | 2009_2019 | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| PALI_UPB -> EXHOSLUSM495S | 2020_onward | 12 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| PALI_COUNT -> HSN1F | full | 271 | 0 | 0.116 | 0.0561 | 0.729 | 0.024 | 12 / 0.288 | 12 / 0.288 |
| PALI_COUNT -> HSN1F | 2004_2014 | 131 | 3 | -0.127 | 0.153 | 1 | 0.356 | 12 / 0.00956 | 1 / 0.813 |
| PALI_COUNT -> HSN1F | 2015_onward | 140 | 0 | 0.155 | 0.0672 | 0.874 | 0.068 | 12 / 0.272 | 1 / 0.0825 |
| PALI_COUNT -> HSN1F | 2009_2019 | 132 | 3 | -0.162 | 0.0669 | 0.87 | 0.07 | 12 / 0.464 | 12 / 0.464 |
| PALI_COUNT -> HSN1F | 2020_onward | 80 | 11 | -0.195 | 0.108 | 1 | 0.458 | 12 / 0.858 | 1 / 0.997 |
| PALI_COUNT -> HOUST | full | 272 | 12 | -0.075 | 0.227 | 1 | 0.404 | 12 / 0.822 | 12 / 0.822 |
| PALI_COUNT -> HOUST | 2004_2014 | 131 | 12 | -0.122 | 0.185 | 1 | 0.044 | 12 / 0.602 | 1 / 0.635 |
| PALI_COUNT -> HOUST | 2015_onward | 141 | 4 | 0.113 | 0.187 | 1 | 0.514 | 12 / 0.079 | 1 / 0.741 |
| PALI_COUNT -> HOUST | 2009_2019 | 132 | 12 | -0.104 | 0.26 | 1 | 0.198 | 12 / 0.74 | 2 / 0.995 |
| PALI_COUNT -> HOUST | 2020_onward | 81 | 10 | 0.193 | 0.107 | 1 | 0.174 | 12 / 0.37 | 2 / 0.602 |
| PALI_COUNT -> EXHOSLUSM495S | full | 12 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| PALI_COUNT -> EXHOSLUSM495S | 2004_2014 | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| PALI_COUNT -> EXHOSLUSM495S | 2015_onward | 12 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| PALI_COUNT -> EXHOSLUSM495S | 2009_2019 | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| PALI_COUNT -> EXHOSLUSM495S | 2020_onward | 12 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |

| X -> Y | Period | h | Forecasts | First / last target | AR / ARX lag | RMSE AR | RMSE ARX | Ratio | Block CI 95% |
| --- | --- | ---: | ---: | --- | --- | ---: | ---: | ---: | --- |
| PALI_UPB -> HSN1F | full | 1 | 151 | 2014-02 / 2026-08 | 1 / 1 | 7.899 | 7.910 | 1.001 | [0.997, 1.007] |
| PALI_UPB -> HSN1F | full | 2 | 150 | 2014-03 / 2026-08 | 0 / 1 | 8.117 | 8.162 | 1.006 | [0.994, 1.017] |
| PALI_UPB -> HSN1F | full | 3 | 149 | 2014-04 / 2026-08 | 0 / 1 | 8.140 | 8.238 | 1.012 | [0.988, 1.033] |
| PALI_UPB -> HSN1F | full | 6 | 146 | 2014-07 / 2026-08 | 0 / 1 | 8.142 | 8.216 | 1.009 | [1.000, 1.017] |
| PALI_UPB -> HSN1F | full | 12 | 140 | 2015-01 / 2026-08 | 0 / 1 | 8.134 | 8.269 | 1.017 | [0.993, 1.044] |
| PALI_UPB -> HSN1F | 2004_2014 | 1 | 66 | 2009-07 / 2014-12 | 0 / 1 | 8.680 | 8.575 | 0.988 | [0.964, 1.040] |
| PALI_UPB -> HSN1F | 2004_2014 | 2 | 65 | 2009-08 / 2014-12 | 0 / 1 | 8.695 | 8.964 | 1.031 | [0.980, 1.070] |
| PALI_UPB -> HSN1F | 2004_2014 | 3 | 64 | 2009-09 / 2014-12 | 1 / 1 | 8.768 | 8.817 | 1.006 | [0.987, 1.040] |
| PALI_UPB -> HSN1F | 2004_2014 | 6 | 61 | 2009-12 / 2014-12 | 0 / 1 | 8.938 | 9.120 | 1.020 | [0.993, 1.037] |
| PALI_UPB -> HSN1F | 2004_2014 | 12 | 55 | 2010-06 / 2014-12 | 0 / 1 | 7.439 | 7.989 | 1.074 | [0.988, 1.216] |
| PALI_UPB -> HSN1F | 2015_onward | 1 | 70 | 2020-11 / 2026-08 | 0 / 1 | 8.264 | 7.952 | 0.962 | [0.911, 1.014] |
| PALI_UPB -> HSN1F | 2015_onward | 2 | 69 | 2020-12 / 2026-08 | 0 / 1 | 8.017 | 8.129 | 1.014 | [0.990, 1.041] |
| PALI_UPB -> HSN1F | 2015_onward | 3 | 68 | 2021-01 / 2026-08 | 0 / 1 | 8.082 | 8.346 | 1.033 | [1.001, 1.082] |
| PALI_UPB -> HSN1F | 2015_onward | 6 | 65 | 2021-04 / 2026-08 | 0 / 1 | 7.949 | 8.145 | 1.025 | [1.000, 1.054] |
| PALI_UPB -> HSN1F | 2015_onward | 12 | 59 | 2021-10 / 2026-08 | 0 / 1 | 7.989 | 8.191 | 1.025 | [0.990, 1.058] |
| PALI_UPB -> HSN1F | 2009_2019 | 1 | 66 | 2014-07 / 2019-12 | 1 / 1 | 6.838 | 6.854 | 1.002 | [0.983, 1.021] |
| PALI_UPB -> HSN1F | 2009_2019 | 2 | 65 | 2014-08 / 2019-12 | 0 / 1 | 7.347 | 7.418 | 1.010 | [0.986, 1.030] |
| PALI_UPB -> HSN1F | 2009_2019 | 3 | 64 | 2014-09 / 2019-12 | 0 / 1 | 7.237 | 7.352 | 1.016 | [0.982, 1.055] |
| PALI_UPB -> HSN1F | 2009_2019 | 6 | 61 | 2014-12 / 2019-12 | 0 / 1 | 7.338 | 7.592 | 1.035 | [0.997, 1.080] |
| PALI_UPB -> HSN1F | 2009_2019 | 12 | 55 | 2015-06 / 2019-12 | 0 / 1 | 7.336 | 7.525 | 1.026 | [1.012, 1.045] |
| PALI_UPB -> HSN1F | 2020_onward | 1 | 40 | 2023-05 / 2026-08 | 0 / 1 | 7.826 | 7.463 | 0.954 | [0.930, 0.988] |
| PALI_UPB -> HSN1F | 2020_onward | 2 | 39 | 2023-06 / 2026-08 | 0 / 1 | 7.636 | 7.833 | 1.026 | [1.007, 1.049] |
| PALI_UPB -> HSN1F | 2020_onward | 3 | 38 | 2023-07 / 2026-08 | 0 / 1 | 7.668 | 7.477 | 0.975 | [0.933, 1.037] |
| PALI_UPB -> HSN1F | 2020_onward | 6 | 35 | 2023-10 / 2026-08 | 0 / 2 | 7.637 | 7.887 | 1.033 | [0.991, 1.081] |
| PALI_UPB -> HSN1F | 2020_onward | 12 | 29 | 2024-04 / 2026-08 | 2 / 1 | 8.794 | 8.441 | 0.960 | [0.901, 0.991] |
| PALI_UPB -> HOUST | full | 1 | 152 | 2014-02 / 2026-09 | 1 / 1 | 8.365 | 8.397 | 1.004 | [0.999, 1.008] |
| PALI_UPB -> HOUST | full | 2 | 151 | 2014-03 / 2026-09 | 0 / 1 | 8.925 | 8.995 | 1.008 | [0.998, 1.018] |
| PALI_UPB -> HOUST | full | 3 | 150 | 2014-04 / 2026-09 | 0 / 1 | 8.944 | 9.029 | 1.010 | [0.988, 1.028] |
| PALI_UPB -> HOUST | full | 6 | 147 | 2014-07 / 2026-09 | 0 / 1 | 8.977 | 9.019 | 1.005 | [0.999, 1.011] |
| PALI_UPB -> HOUST | full | 12 | 141 | 2015-01 / 2026-09 | 0 / 1 | 8.949 | 8.953 | 1.000 | [0.973, 1.036] |
| PALI_UPB -> HOUST | 2004_2014 | 1 | 66 | 2009-07 / 2014-12 | 0 / 1 | 9.070 | 8.462 | 0.933 | [0.890, 0.988] |
| PALI_UPB -> HOUST | 2004_2014 | 2 | 65 | 2009-08 / 2014-12 | 0 / 3 | 9.129 | 9.969 | 1.092 | [1.032, 1.153] |
| PALI_UPB -> HOUST | 2004_2014 | 3 | 64 | 2009-09 / 2014-12 | 2 / 2 | 9.589 | 9.950 | 1.038 | [1.000, 1.094] |
| PALI_UPB -> HOUST | 2004_2014 | 6 | 61 | 2009-12 / 2014-12 | 0 / 1 | 9.287 | 9.454 | 1.018 | [1.002, 1.039] |
| PALI_UPB -> HOUST | 2004_2014 | 12 | 55 | 2010-06 / 2014-12 | 0 / 1 | 9.498 | 9.601 | 1.011 | [0.984, 1.039] |
| PALI_UPB -> HOUST | 2015_onward | 1 | 71 | 2020-11 / 2026-09 | 0 / 1 | 8.315 | 7.503 | 0.902 | [0.846, 0.946] |
| PALI_UPB -> HOUST | 2015_onward | 2 | 70 | 2020-12 / 2026-09 | 0 / 1 | 8.323 | 8.400 | 1.009 | [0.981, 1.033] |
| PALI_UPB -> HOUST | 2015_onward | 3 | 69 | 2021-01 / 2026-09 | 1 / 1 | 8.493 | 8.528 | 1.004 | [0.995, 1.014] |
| PALI_UPB -> HOUST | 2015_onward | 6 | 66 | 2021-04 / 2026-09 | 0 / 1 | 8.048 | 8.055 | 1.001 | [0.988, 1.017] |
| PALI_UPB -> HOUST | 2015_onward | 12 | 60 | 2021-10 / 2026-09 | 0 / 1 | 8.192 | 8.236 | 1.005 | [0.972, 1.043] |
| PALI_UPB -> HOUST | 2009_2019 | 1 | 66 | 2014-07 / 2019-12 | 1 / 1 | 7.138 | 7.182 | 1.006 | [1.002, 1.009] |
| PALI_UPB -> HOUST | 2009_2019 | 2 | 65 | 2014-08 / 2019-12 | 0 / 1 | 8.148 | 8.352 | 1.025 | [1.004, 1.046] |
| PALI_UPB -> HOUST | 2009_2019 | 3 | 64 | 2014-09 / 2019-12 | 0 / 1 | 8.101 | 8.223 | 1.015 | [0.996, 1.039] |
| PALI_UPB -> HOUST | 2009_2019 | 6 | 61 | 2014-12 / 2019-12 | 0 / 1 | 8.224 | 8.393 | 1.020 | [0.996, 1.036] |
| PALI_UPB -> HOUST | 2009_2019 | 12 | 55 | 2015-06 / 2019-12 | 0 / 1 | 7.477 | 7.574 | 1.013 | [0.956, 1.110] |
| PALI_UPB -> HOUST | 2020_onward | 1 | 41 | 2023-05 / 2026-09 | 0 / 1 | 9.016 | 8.361 | 0.927 | [0.875, 0.987] |
| PALI_UPB -> HOUST | 2020_onward | 2 | 40 | 2023-06 / 2026-09 | 0 / 1 | 8.553 | 8.572 | 1.002 | [0.896, 1.081] |
| PALI_UPB -> HOUST | 2020_onward | 3 | 39 | 2023-07 / 2026-09 | 0 / 1 | 8.448 | 8.616 | 1.020 | [0.966, 1.069] |
| PALI_UPB -> HOUST | 2020_onward | 6 | 36 | 2023-10 / 2026-09 | 0 / 1 | 8.547 | 8.578 | 1.004 | [0.988, 1.020] |
| PALI_UPB -> HOUST | 2020_onward | 12 | 30 | 2024-04 / 2026-09 | 0 / 1 | 8.140 | 8.801 | 1.081 | [1.001, 1.181] |
| PALI_COUNT -> HSN1F | full | 1 | 151 | 2014-02 / 2026-08 | 1 / 1 | 7.899 | 7.910 | 1.001 | [0.997, 1.006] |
| PALI_COUNT -> HSN1F | full | 2 | 150 | 2014-03 / 2026-08 | 0 / 1 | 8.117 | 8.161 | 1.005 | [0.994, 1.017] |
| PALI_COUNT -> HSN1F | full | 3 | 149 | 2014-04 / 2026-08 | 0 / 1 | 8.140 | 8.236 | 1.012 | [0.989, 1.031] |
| PALI_COUNT -> HSN1F | full | 6 | 146 | 2014-07 / 2026-08 | 0 / 1 | 8.142 | 8.219 | 1.009 | [1.000, 1.018] |
| PALI_COUNT -> HSN1F | full | 12 | 140 | 2015-01 / 2026-08 | 0 / 1 | 8.134 | 8.255 | 1.015 | [0.992, 1.041] |
| PALI_COUNT -> HSN1F | 2004_2014 | 1 | 66 | 2009-07 / 2014-12 | 0 / 1 | 8.680 | 8.572 | 0.988 | [0.965, 1.036] |
| PALI_COUNT -> HSN1F | 2004_2014 | 2 | 65 | 2009-08 / 2014-12 | 0 / 1 | 8.695 | 8.944 | 1.029 | [0.980, 1.066] |
| PALI_COUNT -> HSN1F | 2004_2014 | 3 | 64 | 2009-09 / 2014-12 | 1 / 1 | 8.768 | 8.838 | 1.008 | [0.990, 1.036] |
| PALI_COUNT -> HSN1F | 2004_2014 | 6 | 61 | 2009-12 / 2014-12 | 0 / 1 | 8.938 | 9.126 | 1.021 | [0.992, 1.046] |
| PALI_COUNT -> HSN1F | 2004_2014 | 12 | 55 | 2010-06 / 2014-12 | 0 / 1 | 7.439 | 8.024 | 1.079 | [0.989, 1.227] |
| PALI_COUNT -> HSN1F | 2015_onward | 1 | 70 | 2020-11 / 2026-08 | 0 / 1 | 8.264 | 7.936 | 0.960 | [0.908, 1.014] |
| PALI_COUNT -> HSN1F | 2015_onward | 2 | 69 | 2020-12 / 2026-08 | 0 / 1 | 8.017 | 8.123 | 1.013 | [0.992, 1.037] |
| PALI_COUNT -> HSN1F | 2015_onward | 3 | 68 | 2021-01 / 2026-08 | 0 / 1 | 8.082 | 8.346 | 1.033 | [1.001, 1.081] |
| PALI_COUNT -> HSN1F | 2015_onward | 6 | 65 | 2021-04 / 2026-08 | 0 / 1 | 7.949 | 8.150 | 1.025 | [0.999, 1.056] |
| PALI_COUNT -> HSN1F | 2015_onward | 12 | 59 | 2021-10 / 2026-08 | 0 / 1 | 7.989 | 8.192 | 1.025 | [0.990, 1.060] |
| PALI_COUNT -> HSN1F | 2009_2019 | 1 | 66 | 2014-07 / 2019-12 | 1 / 1 | 6.838 | 6.853 | 1.002 | [0.985, 1.019] |
| PALI_COUNT -> HSN1F | 2009_2019 | 2 | 65 | 2014-08 / 2019-12 | 0 / 1 | 7.347 | 7.417 | 1.009 | [0.986, 1.030] |
| PALI_COUNT -> HSN1F | 2009_2019 | 3 | 64 | 2014-09 / 2019-12 | 0 / 1 | 7.237 | 7.380 | 1.020 | [0.987, 1.059] |
| PALI_COUNT -> HSN1F | 2009_2019 | 6 | 61 | 2014-12 / 2019-12 | 0 / 1 | 7.338 | 7.597 | 1.035 | [0.998, 1.078] |
| PALI_COUNT -> HSN1F | 2009_2019 | 12 | 55 | 2015-06 / 2019-12 | 0 / 1 | 7.336 | 7.517 | 1.025 | [1.010, 1.043] |
| PALI_COUNT -> HSN1F | 2020_onward | 1 | 40 | 2023-05 / 2026-08 | 0 / 1 | 7.826 | 7.442 | 0.951 | [0.926, 0.988] |
| PALI_COUNT -> HSN1F | 2020_onward | 2 | 39 | 2023-06 / 2026-08 | 0 / 1 | 7.636 | 7.826 | 1.025 | [1.005, 1.047] |
| PALI_COUNT -> HSN1F | 2020_onward | 3 | 38 | 2023-07 / 2026-08 | 0 / 1 | 7.668 | 7.480 | 0.976 | [0.934, 1.036] |
| PALI_COUNT -> HSN1F | 2020_onward | 6 | 35 | 2023-10 / 2026-08 | 0 / 2 | 7.637 | 7.888 | 1.033 | [0.987, 1.087] |
| PALI_COUNT -> HSN1F | 2020_onward | 12 | 29 | 2024-04 / 2026-08 | 2 / 1 | 8.794 | 8.432 | 0.959 | [0.898, 0.991] |
| PALI_COUNT -> HOUST | full | 1 | 152 | 2014-02 / 2026-09 | 1 / 1 | 8.365 | 8.395 | 1.004 | [0.999, 1.007] |
| PALI_COUNT -> HOUST | full | 2 | 151 | 2014-03 / 2026-09 | 0 / 1 | 8.925 | 8.996 | 1.008 | [0.997, 1.020] |
| PALI_COUNT -> HOUST | full | 3 | 150 | 2014-04 / 2026-09 | 0 / 1 | 8.944 | 9.029 | 1.010 | [0.988, 1.028] |
| PALI_COUNT -> HOUST | full | 6 | 147 | 2014-07 / 2026-09 | 0 / 1 | 8.977 | 9.021 | 1.005 | [0.999, 1.011] |
| PALI_COUNT -> HOUST | full | 12 | 141 | 2015-01 / 2026-09 | 0 / 1 | 8.949 | 8.947 | 1.000 | [0.973, 1.034] |
| PALI_COUNT -> HOUST | 2004_2014 | 1 | 66 | 2009-07 / 2014-12 | 0 / 1 | 9.070 | 8.459 | 0.933 | [0.889, 0.988] |
| PALI_COUNT -> HOUST | 2004_2014 | 2 | 65 | 2009-08 / 2014-12 | 0 / 3 | 9.129 | 9.991 | 1.094 | [1.030, 1.161] |
| PALI_COUNT -> HOUST | 2004_2014 | 3 | 64 | 2009-09 / 2014-12 | 2 / 2 | 9.589 | 9.963 | 1.039 | [1.002, 1.098] |
| PALI_COUNT -> HOUST | 2004_2014 | 6 | 61 | 2009-12 / 2014-12 | 0 / 1 | 9.287 | 9.466 | 1.019 | [1.003, 1.041] |
| PALI_COUNT -> HOUST | 2004_2014 | 12 | 55 | 2010-06 / 2014-12 | 0 / 1 | 9.498 | 9.607 | 1.011 | [0.982, 1.043] |
| PALI_COUNT -> HOUST | 2015_onward | 1 | 71 | 2020-11 / 2026-09 | 0 / 1 | 8.315 | 7.499 | 0.902 | [0.845, 0.946] |
| PALI_COUNT -> HOUST | 2015_onward | 2 | 70 | 2020-12 / 2026-09 | 0 / 1 | 8.323 | 8.393 | 1.008 | [0.981, 1.032] |
| PALI_COUNT -> HOUST | 2015_onward | 3 | 69 | 2021-01 / 2026-09 | 1 / 1 | 8.493 | 8.526 | 1.004 | [0.995, 1.013] |
| PALI_COUNT -> HOUST | 2015_onward | 6 | 66 | 2021-04 / 2026-09 | 0 / 1 | 8.048 | 8.061 | 1.002 | [0.989, 1.017] |
| PALI_COUNT -> HOUST | 2015_onward | 12 | 60 | 2021-10 / 2026-09 | 0 / 1 | 8.192 | 8.233 | 1.005 | [0.971, 1.043] |
| PALI_COUNT -> HOUST | 2009_2019 | 1 | 66 | 2014-07 / 2019-12 | 1 / 1 | 7.138 | 7.179 | 1.006 | [1.003, 1.008] |
| PALI_COUNT -> HOUST | 2009_2019 | 2 | 65 | 2014-08 / 2019-12 | 0 / 1 | 8.148 | 8.365 | 1.027 | [1.004, 1.049] |
| PALI_COUNT -> HOUST | 2009_2019 | 3 | 64 | 2014-09 / 2019-12 | 0 / 1 | 8.101 | 8.218 | 1.014 | [0.995, 1.039] |
| PALI_COUNT -> HOUST | 2009_2019 | 6 | 61 | 2014-12 / 2019-12 | 0 / 1 | 8.224 | 8.406 | 1.022 | [0.997, 1.038] |
| PALI_COUNT -> HOUST | 2009_2019 | 12 | 55 | 2015-06 / 2019-12 | 0 / 1 | 7.477 | 7.564 | 1.012 | [0.956, 1.111] |
| PALI_COUNT -> HOUST | 2020_onward | 1 | 41 | 2023-05 / 2026-09 | 0 / 1 | 9.016 | 8.383 | 0.930 | [0.874, 0.989] |
| PALI_COUNT -> HOUST | 2020_onward | 2 | 40 | 2023-06 / 2026-09 | 0 / 1 | 8.553 | 8.569 | 1.002 | [0.896, 1.080] |
| PALI_COUNT -> HOUST | 2020_onward | 3 | 39 | 2023-07 / 2026-09 | 0 / 1 | 8.448 | 8.621 | 1.020 | [0.967, 1.070] |
| PALI_COUNT -> HOUST | 2020_onward | 6 | 36 | 2023-10 / 2026-09 | 0 / 1 | 8.547 | 8.603 | 1.007 | [0.991, 1.024] |
| PALI_COUNT -> HOUST | 2020_onward | 12 | 30 | 2024-04 / 2026-09 | 0 / 1 | 8.140 | 8.765 | 1.077 | [0.996, 1.176] |

| X -> Y (full sample) | k=0 | k=1 | k=2 | k=3 | k=4 | k=5 | k=6 | k=7 | k=8 | k=9 | k=10 | k=11 | k=12 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| PALI_UPB -> HSN1F | 0.118 | 0.017 | -0.017 | -0.060 | -0.004 | -0.007 | -0.002 | 0.028 | -0.020 | -0.046 | 0.015 | -0.052 | -0.053 |
| PALI_UPB -> HOUST | 0.074 | -0.014 | -0.052 | 0.016 | 0.033 | 0.071 | -0.006 | -0.038 | 0.018 | 0.010 | 0.043 | -0.028 | -0.078 |
| PALI_COUNT -> HSN1F | 0.116 | 0.014 | -0.018 | -0.055 | -0.004 | -0.002 | -0.001 | 0.030 | -0.012 | -0.047 | 0.005 | -0.050 | -0.047 |
| PALI_COUNT -> HOUST | 0.068 | -0.018 | -0.059 | 0.019 | 0.033 | 0.070 | -0.006 | -0.038 | 0.010 | 0.006 | 0.047 | -0.028 | -0.075 |

<!-- END GENERATED PALI STATISTICS -->

## Generated statistical appendix

`n` in the correlation/Granger summary is the unlagged paired transformed
sample. A correlation at lag k uses fewer observations; the JSON retains its
own `n`. Granger uses the common 12-lag sample (652/653 rows in the full ML1/ML2
tests). N/A marks unavailable or inadequate history, not a null finding.
Bootstrap intervals below are rounded to three decimals.

<!-- BEGIN GENERATED STATISTICS -->

Input snapshot SHA-256: `56fb92c98eb67d6f51c8a27f1106e6354535616f9fda94f86d09320d0b114f6a`.

<!-- Generated by mortgage_lead_study.py; statistics only. -->

| Series | Monthly levels | First | Last |
| --- | ---: | --- | --- |
| MORTGAGE30US | 666 | 1971-04 | 2026-09 |
| MBA_PURCHASE | 0 | N/A | N/A |
| PHSI | 0 | N/A | N/A |
| EXHOSLUSM495S | 13 | 2025-09 | 2026-09 |
| HSN1F | 764 | 1963-01 | 2026-08 |
| HOUST | 813 | 1959-01 | 2026-09 |

| X -> Y | Period | n | Best k | r | p (IID) | p x 13 | Block max-r p | Granger AIC lag / p | Granger BIC lag / p |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| MORTGAGE30US -> PHSI | full | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| MORTGAGE30US -> PHSI | pre_2008 | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| MORTGAGE30US -> PHSI | 2009_2019 | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| MORTGAGE30US -> PHSI | 2020_onward | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| MORTGAGE30US -> EXHOSLUSM495S | full | 12 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| MORTGAGE30US -> EXHOSLUSM495S | pre_2008 | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| MORTGAGE30US -> EXHOSLUSM495S | 2009_2019 | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| MORTGAGE30US -> EXHOSLUSM495S | 2020_onward | 12 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| MORTGAGE30US -> HSN1F | full | 664 | 0 | -0.300 | 2.67e-15 | 3.47e-14 | 0.002 | 3 / 1.28e-13 | 2 / 1.97e-13 |
| MORTGAGE30US -> HSN1F | pre_2008 | 440 | 0 | -0.387 | 3.55e-17 | 4.61e-16 | 0.002 | 4 / 4.56e-15 | 2 / 6.12e-15 |
| MORTGAGE30US -> HSN1F | 2009_2019 | 132 | 10 | 0.150 | 0.0983 | 1 | 0.622 | 2 / 0.117 | 1 / 0.186 |
| MORTGAGE30US -> HSN1F | 2020_onward | 80 | 0 | -0.225 | 0.0448 | 0.582 | 0.364 | 2 / 0.221 | 1 / 0.111 |
| MORTGAGE30US -> HOUST | full | 665 | 0 | -0.125 | 0.00124 | 0.0161 | 0.002 | 4 / 6.31e-09 | 2 / 4.09e-07 |
| MORTGAGE30US -> HOUST | pre_2008 | 440 | 0 | -0.186 | 8.89e-05 | 0.00116 | 0.002 | 4 / 8.85e-11 | 2 / 5.72e-08 |
| MORTGAGE30US -> HOUST | 2009_2019 | 132 | 9 | 0.160 | 0.0767 | 0.996 | 0.47 | 2 / 0.379 | 1 / 0.805 |
| MORTGAGE30US -> HOUST | 2020_onward | 81 | 9 | -0.165 | 0.165 | 1 | 0.532 | 2 / 0.395 | 1 / 0.314 |
| MBA_PURCHASE -> PHSI | full | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| MBA_PURCHASE -> PHSI | pre_2008 | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| MBA_PURCHASE -> PHSI | 2009_2019 | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| MBA_PURCHASE -> PHSI | 2020_onward | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| MBA_PURCHASE -> EXHOSLUSM495S | full | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| MBA_PURCHASE -> EXHOSLUSM495S | pre_2008 | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| MBA_PURCHASE -> EXHOSLUSM495S | 2009_2019 | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| MBA_PURCHASE -> EXHOSLUSM495S | 2020_onward | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| MBA_PURCHASE -> HSN1F | full | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| MBA_PURCHASE -> HSN1F | pre_2008 | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| MBA_PURCHASE -> HSN1F | 2009_2019 | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| MBA_PURCHASE -> HSN1F | 2020_onward | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| MBA_PURCHASE -> HOUST | full | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| MBA_PURCHASE -> HOUST | pre_2008 | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| MBA_PURCHASE -> HOUST | 2009_2019 | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| MBA_PURCHASE -> HOUST | 2020_onward | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |

| X -> Y | Period | h | Forecasts | First / last target | AR / ARX lag | RMSE AR | RMSE ARX | Ratio | Block CI 95% |
| --- | --- | ---: | ---: | --- | --- | ---: | ---: | ---: | --- |
| MORTGAGE30US -> HSN1F | full | 1 | 544 | 1981-05 / 2026-08 | 0 / 1 | 7.540 | 7.209 | 0.956 | [0.930, 0.986] |
| MORTGAGE30US -> HSN1F | full | 2 | 543 | 1981-06 / 2026-08 | 0 / 1 | 7.541 | 7.556 | 1.002 | [0.995, 1.010] |
| MORTGAGE30US -> HSN1F | full | 3 | 542 | 1981-07 / 2026-08 | 1 / 1 | 7.580 | 7.593 | 1.002 | [0.999, 1.005] |
| MORTGAGE30US -> HSN1F | full | 6 | 539 | 1981-10 / 2026-08 | 0 / 1 | 7.517 | 7.536 | 1.002 | [1.000, 1.005] |
| MORTGAGE30US -> HSN1F | full | 12 | 533 | 1982-04 / 2026-08 | 0 / 1 | 7.454 | 7.441 | 0.998 | [0.990, 1.008] |
| MORTGAGE30US -> HSN1F | pre_2008 | 1 | 320 | 1981-05 / 2007-12 | 0 / 1 | 7.099 | 6.666 | 0.939 | [0.894, 0.977] |
| MORTGAGE30US -> HSN1F | pre_2008 | 2 | 319 | 1981-06 / 2007-12 | 0 / 1 | 7.103 | 7.139 | 1.005 | [0.993, 1.021] |
| MORTGAGE30US -> HSN1F | pre_2008 | 3 | 318 | 1981-07 / 2007-12 | 1 / 1 | 7.157 | 7.175 | 1.003 | [0.998, 1.008] |
| MORTGAGE30US -> HSN1F | pre_2008 | 6 | 315 | 1981-10 / 2007-12 | 0 / 1 | 7.055 | 7.083 | 1.004 | [0.999, 1.009] |
| MORTGAGE30US -> HSN1F | pre_2008 | 12 | 309 | 1982-04 / 2007-12 | 0 / 1 | 6.926 | 6.898 | 0.996 | [0.987, 1.007] |
| MORTGAGE30US -> HSN1F | 2009_2019 | 1 | 66 | 2014-07 / 2019-12 | 1 / 1 | 6.838 | 6.995 | 1.023 | [0.997, 1.052] |
| MORTGAGE30US -> HSN1F | 2009_2019 | 2 | 65 | 2014-08 / 2019-12 | 0 / 1 | 7.347 | 7.356 | 1.001 | [0.977, 1.019] |
| MORTGAGE30US -> HSN1F | 2009_2019 | 3 | 64 | 2014-09 / 2019-12 | 0 / 1 | 7.237 | 7.342 | 1.014 | [1.001, 1.030] |
| MORTGAGE30US -> HSN1F | 2009_2019 | 6 | 61 | 2014-12 / 2019-12 | 0 / 1 | 7.338 | 7.424 | 1.012 | [0.986, 1.031] |
| MORTGAGE30US -> HSN1F | 2009_2019 | 12 | 55 | 2015-06 / 2019-12 | 0 / 1 | 7.336 | 7.371 | 1.005 | [1.001, 1.009] |
| MORTGAGE30US -> HSN1F | 2020_onward | 1 | 40 | 2023-05 / 2026-08 | 0 / 1 | 7.826 | 7.472 | 0.955 | [0.904, 0.999] |
| MORTGAGE30US -> HSN1F | 2020_onward | 2 | 39 | 2023-06 / 2026-08 | 0 / 1 | 7.636 | 7.834 | 1.026 | [1.007, 1.043] |
| MORTGAGE30US -> HSN1F | 2020_onward | 3 | 38 | 2023-07 / 2026-08 | 0 / 1 | 7.668 | 7.506 | 0.979 | [0.938, 1.037] |
| MORTGAGE30US -> HSN1F | 2020_onward | 6 | 35 | 2023-10 / 2026-08 | 0 / 1 | 7.637 | 7.795 | 1.021 | [1.003, 1.058] |
| MORTGAGE30US -> HSN1F | 2020_onward | 12 | 29 | 2024-04 / 2026-08 | 2 / 2 | 8.794 | 9.135 | 1.039 | [0.994, 1.130] |
| MORTGAGE30US -> HOUST | full | 1 | 545 | 1981-05 / 2026-09 | 0 / 1 | 8.070 | 7.575 | 0.939 | [0.901, 0.983] |
| MORTGAGE30US -> HOUST | full | 2 | 544 | 1981-06 / 2026-09 | 0 / 1 | 8.034 | 8.017 | 0.998 | [0.987, 1.008] |
| MORTGAGE30US -> HOUST | full | 3 | 543 | 1981-07 / 2026-09 | 0 / 1 | 8.035 | 8.032 | 1.000 | [0.994, 1.005] |
| MORTGAGE30US -> HOUST | full | 6 | 540 | 1981-10 / 2026-09 | 0 / 1 | 8.051 | 8.077 | 1.003 | [0.999, 1.008] |
| MORTGAGE30US -> HOUST | full | 12 | 534 | 1982-04 / 2026-09 | 0 / 1 | 8.073 | 8.039 | 0.996 | [0.985, 1.008] |
| MORTGAGE30US -> HOUST | pre_2008 | 1 | 320 | 1981-05 / 2007-12 | 0 / 1 | 7.316 | 6.794 | 0.929 | [0.900, 0.960] |
| MORTGAGE30US -> HOUST | pre_2008 | 2 | 319 | 1981-06 / 2007-12 | 0 / 1 | 7.251 | 7.233 | 0.998 | [0.977, 1.015] |
| MORTGAGE30US -> HOUST | pre_2008 | 3 | 318 | 1981-07 / 2007-12 | 0 / 1 | 7.251 | 7.218 | 0.995 | [0.988, 1.004] |
| MORTGAGE30US -> HOUST | pre_2008 | 6 | 315 | 1981-10 / 2007-12 | 0 / 1 | 7.271 | 7.309 | 1.005 | [0.997, 1.014] |
| MORTGAGE30US -> HOUST | pre_2008 | 12 | 309 | 1982-04 / 2007-12 | 0 / 1 | 7.297 | 7.277 | 0.997 | [0.982, 1.013] |
| MORTGAGE30US -> HOUST | 2009_2019 | 1 | 66 | 2014-07 / 2019-12 | 1 / 1 | 7.138 | 7.172 | 1.005 | [1.000, 1.009] |
| MORTGAGE30US -> HOUST | 2009_2019 | 2 | 65 | 2014-08 / 2019-12 | 0 / 1 | 8.148 | 8.270 | 1.015 | [0.999, 1.027] |
| MORTGAGE30US -> HOUST | 2009_2019 | 3 | 64 | 2014-09 / 2019-12 | 0 / 1 | 8.101 | 8.127 | 1.003 | [0.983, 1.027] |
| MORTGAGE30US -> HOUST | 2009_2019 | 6 | 61 | 2014-12 / 2019-12 | 0 / 1 | 8.224 | 8.369 | 1.018 | [0.999, 1.035] |
| MORTGAGE30US -> HOUST | 2009_2019 | 12 | 55 | 2015-06 / 2019-12 | 0 / 1 | 7.477 | 7.339 | 0.982 | [0.932, 1.062] |
| MORTGAGE30US -> HOUST | 2020_onward | 1 | 41 | 2023-05 / 2026-09 | 0 / 1 | 9.016 | 8.161 | 0.905 | [0.835, 0.986] |
| MORTGAGE30US -> HOUST | 2020_onward | 2 | 40 | 2023-06 / 2026-09 | 0 / 1 | 8.553 | 8.565 | 1.001 | [0.901, 1.081] |
| MORTGAGE30US -> HOUST | 2020_onward | 3 | 39 | 2023-07 / 2026-09 | 0 / 1 | 8.448 | 8.740 | 1.035 | [0.979, 1.074] |
| MORTGAGE30US -> HOUST | 2020_onward | 6 | 36 | 2023-10 / 2026-09 | 0 / 1 | 8.547 | 8.589 | 1.005 | [0.987, 1.024] |
| MORTGAGE30US -> HOUST | 2020_onward | 12 | 30 | 2024-04 / 2026-09 | 0 / 1 | 8.140 | 8.351 | 1.026 | [0.982, 1.078] |

| X -> Y (full sample) | k=0 | k=1 | k=2 | k=3 | k=4 | k=5 | k=6 | k=7 | k=8 | k=9 | k=10 | k=11 | k=12 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| MORTGAGE30US -> HSN1F | -0.300 | -0.203 | -0.041 | -0.010 | 0.046 | -0.040 | -0.034 | 0.007 | -0.009 | -0.015 | 0.010 | -0.061 | 0.059 |
| MORTGAGE30US -> HOUST | -0.125 | -0.113 | -0.124 | -0.072 | -0.070 | -0.071 | -0.019 | 0.001 | -0.024 | 0.007 | -0.011 | -0.062 | 0.057 |

<!-- END GENERATED STATISTICS -->
