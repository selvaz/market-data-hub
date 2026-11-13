# Japan: step 1 test-download report

Test date: **13 November 2026**. Live download evidence was collected at 12:34-12:43 UTC on the specified Windows machine. Scope: source discovery, test downloads and integration proposals only. No repository files, configuration, databases or scheduled jobs were changed.

**Result: all nine required indicators have a tested, free, key-free machine-readable route.** Use ESRI, Statistics Bureau/e-Stat public files, METI, OECD SDMX and BoJ. BIS supplies a key-free policy-target time series. An e-Stat API application ID is optional for this coverage, because its public CSV/XLSX downloads work without one. Some mirrors are older, have different coverage or carry different revisions; those differences are recorded below.

## Test method and status definitions

Every download described as tested was fetched with `<local path>`, Python `requests`, a normal browser User-Agent and `timeout=30`. CSVs were parsed, XLSX workbooks were opened read-only with `data_only=True`, BoJ JSON was checked for application status and non-null observations, and relevant text-based issuer PDFs were extracted for cross-checks. HTTP 200 alone was not considered evidence of usable data. Search/browser results were used to discover and document sources, not as substitutes for local requests tests.

The common request was:

```python
import requests

response = requests.get(
    url,
    params=params,
    headers={"User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36"
    )},
    timeout=30,
)
```

**Working** means a successful download with the intended data extracted. **Failing for current coverage** includes valid but stale data, missing observations and definition mismatches, as well as transport errors. **Needs key** means the API rejected a request without credentials; it does not apply to public file downloads. All units below are percentage values as printed, e.g. `2.1` means 2.1%, not a decimal fraction of 0.021.

Release lag means calendar days from the end of the reference month/quarter to the issuer release, unless stated otherwise. A provider update date, observation date and download timestamp are separate fields. Estimates/ranges are explicitly identified.

## Required-indicator results

| Required indicator | Issuer source and local test status | Unit, frequency and adjustment | Last observation and extracted value | Release lag / availability | Free mirror and qualification | Key needed for recommended route? |
|---|---|---|---|---|---|---|
| Activity: real GDP q/q | **Tested and working**, ESRI published-rate CSV [G1](#g1-real-gdp) | %, Q, real, SA, non-annualised q/q | **2026 Q2: +0.4%**, second estimate | Released 8 Sep 2026: **70 days** after 30 Jun. First estimate released 17 Aug: **48 days**. Dates verified in issuer archive. | FRED `JPNRGDPEXP`: working level series through Q2; derived q/q **0.360456%**, rounds to 0.4%. Different metadata/base labels require review before treating it as an exact edition mirror. | No |
| Activity: industrial production m/m | **Tested and working**, METI English HTML rate table and XLSX [I1](#i1-industrial-production) | %, M, SA, **mining and manufacturing**; supporting index 2020=100 | **2026-09: +2.2%**, preliminary. SA index **104.1**, Aug **101.9** | Issuer page and workbook listing: 30 Oct 2026, 08:50 JST: **30 days** | OECD current broad-industry series works: Sep **+1.9646365%**; broader `BTE` coverage, so not an automatic same-definition fallback. FRED `JPNPROINDMISMEI` stops Mar 2024. | No |
| Leading: OECD CLI | **Tested and working**, OECD SDMX [L1](#l1-oecd-composite-leading-indicator) | Index, long-run average=100; M, amplitude adjusted | **2026-10: 100.2095**. OECD Sep: **100.2295** | OECD schedule lists 9 Nov 2026 update, **9 days** after Oct end. This is a scheduled dataset update, not an observation-level timestamp returned by the API. | FRED `JPNLOLITOAASTSAM` works through **2026-09: 100.347182439759**. It has an older/different snapshot; even Sep differs. | No for OECD; FRED API requires its free key |
| Leading: Tankan large manufacturers DI | **Tested and working**, BoJ JSON and CSV [T1](#t1-tankan-large-manufacturing-enterprises) | DI, percentage points, Q; actual current business conditions; adjustment not stated in issuer summary | **2026 Q3 / Sep survey: 24**. API date code **202603** means Q3 | Summary released **1 Oct 2026**, **1 day** after quarter end; API `LAST_UPDATE=20261002` is one day later, not the initial release date | Same issuer's PDF summary also works and confirms 24. OECD BTS manufacturing candidate returns **19**, so it is not certified as this exact measure. | No |
| Inflation: CPI headline y/y | **Tested and working**, Statistics Bureau national monthly XLSX and long CSV via e-Stat [C1](#c1-national-headline-and-core-cpi) | %, M, NSA, published 12-month change; 2025-base publication | **2026-09: +2.1%** | e-Stat issuer listing: **23 Oct 2026, 08:30 JST**, **23 days** | OECD **COICOP 2018** flow works, Sep **2.1%**. Old OECD flow is stale; FRED candidate's last non-missing observation is Jun 2021. | No |
| Inflation: core CPI excluding fresh food y/y | **Tested and working**, same issuer XLSX and separate long CSV [C1](#c1-national-headline-and-core-cpi) | %, M, NSA; all items **less fresh food**, retaining energy | **2026-09: +2.1%** | Same release: **23 days** | Long CSV is a working same-issuer alternative. No independent exact-definition mirror certified. OECD non-food/non-energy CPI is a different aggregate. | No |
| Labour: unemployment rate | **Tested and working**, Statistics Bureau public historical workbook via e-Stat [U1](#u1-unemployment) | % of labour force, M, SA, both sexes, age 15+ | **2026-09: 2.4%** | Issuer listing: **30 Oct 2026, 08:30 JST**, **30 days** | OECD KEI works and matches Sep **2.4%**. FRED `LRUNTTTTJPM156S` and `LRHUTTTTJPM156S` work through **Aug: 2.5%**. Previously tested `LRUN64TTJPM156S` is age 15-64, Aug **2.6%**, and is not equivalent. | No |
| Consumption: real household spending y/y | **Tested and working**, Statistics Bureau FIES Table 1 XLSX [H1](#h1-household-spending-selected-consumption-measure) | %, M, real y/y, NSA, consumption expenditures of two-or-more-person households | **2026-09: -6.8%**; supporting nominal spending **JPY 289,536 per household per month** | Issuer summary: **10 Nov 2026**, **41 days** | No independent exact FIES mirror certified. OECD retail volume works only through Jun 2026 and is a different consumption measure. FRED retail candidate stops Oct 2023. | No |
| Policy: BoJ short-term policy target | **Tested and working**, key-free BIS CSV; issuer statement cross-check [P1](#p1-boj-policy-rate) | % p.a.; BIS D, end-of-period target. Issuer decision series E | BIS **2026-11-10: 1.25%**; issuer **30 Oct 2026 decision: 1.25%**, unchanged | Issuer decision: same-day publication. BIS last observation is **3 calendar days** before test date; BIS publishes daily observations on a weekly update cadence | BIS is the working free mirror of the BoJ target. BoJ discount rate **1.5%** and actual call rate **1.232%** are separate instruments, not substitutes. | No |

## Reproducible source calls and filters

### G1. Real GDP

Download the [ESRI second-estimate real SA q/q CSV](https://www.esri.cao.go.jp/jp/sna/data/data_list/sokuhou/files/2026/qe262_2/tables/ritu-jk2622.csv). Local result: HTTP 200, **19,152 bytes**. Decode as **CP932**, parse as CSV, and select `GDP(Expenditure Approach)` in the table headed `Real, Seasonally Adjusted Series (Quarter-to-Quarter Percent Change)`. The final populated quarterly row is `4- 6.` following `2026/ 1- 3.`, and its GDP field is `0.4`. Year labels are carried forward; blank trailing rows are not observations. Do not select the annualised-growth table.

Bind the download to the dated [2026 release archive](https://www.esri.cao.go.jp/en/sna/data/sokuhou/files/2026/toukei_2026.html) and its [second-estimate menu](https://www.esri.cao.go.jp/en/sna/data/sokuhou/files/2026/qe262_2/gdemenuea.html); both returned HTTP 200 locally. The archive labels this edition **benchmark year 2020**. The CSV itself is not proof of a publication timestamp.

FRED mirror test: `series_id=JPNRGDPEXP`, Q, SA. Q2 value **598950.5**, Q1 **596799.3**. FRED metadata says **billions of chained 2015 yen**, so preserve that metadata discrepancy for review. The calculation `100 * (598950.5 / 596799.3 - 1) = 0.3604561869` is derived, even though its one-decimal result agrees with the issuer rate. Do not label it an independently published q/q observation or attach the current snapshot to an earlier GDP edition.

### I1. Industrial production

Both the [METI English latest-results page](https://www.meti.go.jp/english/statistics/tyo/iip/index.html) and [historical-download listing](https://www.meti.go.jp/english/statistics/tyo/iip/b2020_result-2.html) returned HTTP 200 locally. This retest did **not** reproduce the prior 403. The latest-results HTML contains a structured table with `Production`, SA index **104.1**, and previous-month change **2.2** for September 2026, preliminary.

Download the [English SA monthly industry workbook](https://www.meti.go.jp/english/statistics/tyo/iip/xls/b2020_gsm1e.xlsx): HTTP 200, **366,085 bytes**. Select sheet **`Production`**, `Item_Number=1000000000`, `Item_Name=Mining and manufacturing`. Month headers are on row 3; final header is `p 202609`. August and September values are **101.9** and **104.1**. The rate calculated from the rounded indices is **2.1589793916%**, or **2.2%** at the issuer's precision. The rate printed in HTML is preferable for a published release card; a calculation from index levels must be marked derived.

The [Japanese counterpart](https://www.meti.go.jp/statistics/tyo/iip/xls/b2020_gsm1j.xlsx) also returned HTTP 200, **367,457 bytes**. It is a same-issuer distribution alternative, not an independent confirmation.

The exact working OECD calls are:

```text
https://sdmx.oecd.org/public/rest/v1/data/OECD.SDD.STES,DSD_STES@DF_INDSERV,4.3/JPN.M.PRVM.IX.BTE.Y._Z._Z.N?startPeriod=2026-08&format=csvfilewithlabels
https://sdmx.oecd.org/public/rest/v1/data/OECD.SDD.STES,DSD_KEI@DF_KEI,4.0/JPN.M.PRVM.GR.BTE.Y.G1?startPeriod=2026-08&format=csvfilewithlabels
```

Both returned HTTP 200. The index is **2015=100**, August **92.73591**, September **94.55783**. The directly supplied monthly growth is **1.96463654223973%**. `BTE` is industry including energy, and the returned adjustment is calendar-and-seasonally adjusted. Coverage, adjustment and values differ from METI's selected measure. Do not silently use it as a fallback. The manufacturing-only `C` series is also narrower than the requested METI mining-and-manufacturing series.

### L1. OECD composite leading indicator

The OECD is the issuer for the required OECD CLI. Japan's ESRI leading CI is a separate indicator and should not replace it.

```text
https://sdmx.oecd.org/public/rest/v1/data/OECD.SDD.STES,DSD_STES@DF_CLI,4.1/JPN.M.LI.IX._Z.AA.IX._Z.H?startPeriod=2026-08&format=csvfilewithlabels
```

Local result: HTTP 200, **1,262 bytes**. Latest **2026-10 = 100.2095**; September **100.2295**. Key dimensions: `REF_AREA=JPN`, `FREQ=M`, `MEASURE=LI`, `UNIT_MEASURE=IX`, `ACTIVITY=_Z`, `ADJUSTMENT=AA`, `TRANSFORMATION=IX`, `TIME_HORIZ=_Z`, `METHODOLOGY=H`. Select the amplitude-adjusted CLI, rather than the normalised CLI, component series or business-confidence index.

The [OECD CLI update schedule](https://www.oecd.org/en/data/datasets/oecd-composite-leading-indicators-clis.html) lists **9 November 2026**. That page was readable through web browsing but returned **403 to local requests**; the SDMX endpoints worked. Treat the schedule as contextual release evidence, not a timestamp supplied by the CSV.

FRED `JPNLOLITOAASTSAM` is the amplitude-adjusted, seasonally adjusted monthly mirror. Its latest downloaded observation was **September = 100.347182439759**, and FRED series metadata reported last update **15 October 2026**. The OECD and FRED September snapshots differ by approximately **0.11768 index points**. The cause was not established in this test. Preserve both source vintages and raise a reconciliation flag; never use the FRED current head as evidence of the OECD November edition.

### T1. Tankan large manufacturing enterprises

Discover codes using:

```text
https://www.stat-search.boj.or.jp/api/v1/getMetadata?format=json&lang=en&db=CO
```

Local result: HTTP 200, application status 200, about **99 MB** uncompressed. Cache this metadata; avoid downloading it on each run. The exact matching metadata entry is `TK99F1000601GCQ01000`, **D.I./Business Conditions/Large Enterprises/Manufacturing/Actual result**, unit `% points`, quarterly.

```text
https://www.stat-search.boj.or.jp/api/v1/getDataCode?format=json&lang=en&db=CO&code=TK99F1000601GCQ01000&startDate=202501
https://www.stat-search.boj.or.jp/api/v1/getDataCode?format=csv&lang=en&db=CO&code=TK99F1000601GCQ01000&startDate=202601
```

Both worked locally: JSON **624 bytes**, CSV **786 bytes**. JSON `RESULTSET[0].VALUES` pairs `SURVEY_DATES` with `VALUES`; the last pair is **202603, 24**. Quarter codes use `YYYYQQ`, not calendar months. The forecast code is different (`TK99F1000601GCQ11000`) and must not be selected. Metadata warns of a continuity break between the December 2003 and March 2004 surveys.

The [September Tankan summary](https://www.boj.or.jp/en/statistics/tk/gaiyo/2026/tka2609.pdf) returned HTTP 200, **229,883 bytes**. Its first page dates the publication **1 October 2026** and confirms large manufacturers' current DI **24**; the preceding June actual is **22**. This also distinguishes the API's 2 October update date from first publication. The [BoJ API manual](https://www.stat-search.boj.or.jp/info/api_manual_en.pdf) documents the key-free JSON/CSV service and quarter date encoding.

OECD BTS was also discovered and tested:

```text
https://sdmx.oecd.org/public/rest/v1/data/OECD.SDD.STES,DSD_STES@DF_BTS,4.0/JPN.Q.......?startPeriod=2026-Q2&format=csvfilewithlabels
```

The manufacturing current-business candidate `BU.PB.C.Y._Z.C.N` returns **2026 Q3 = 19**. It is not certified as the exact large-enterprise actual DI. Keep the BoJ API as primary and the existing issuer PDF route as a fallback; no independent exact mirror was established.

### C1. National headline and core CPI

The issuer is the Statistics Bureau; e-Stat distributes its public files without API authentication.

**Monthly release workbook:**

```text
https://www.e-stat.go.jp/en/stat-search/file-download?statInfId=000040513539&fileKind=4
```

HTTP 200, **1,020,710 bytes**, valid XLSX. Select sheet **`am01-1 (3)`**, Table 1-1 Subgroup Index for Japan, with `H8 = Monthly, % Change over the year`. For the row with period **202609** in column B and code **03** in column C, headline **I695 = 2.1**, fresh-food-excluding core **J695 = 2.1**. Validate labels in `I12` and `J12`; avoid Tokyo, month-on-month sheets and alternative exclusion aggregates. The [file metadata listing](https://www.e-stat.go.jp/en/stat-search/files?layout=dataset&stat_infid=000040513539) returned HTTP 200 and records **23 October 2026, 08:30 JST**.

**Separate long-history rate CSV:**

```text
https://www.e-stat.go.jp/en/stat-search/file-download?statInfId=000040482953&fileKind=1
```

HTTP 200, **236,191 bytes**; 687 CSV rows including metadata. Decode the downloaded bytes as **CP932**, despite the response advertising UTF-8. English header row identifies `All items` and `All items, less fresh food`; the group-code row identifies **0001** and **0161**. The final data row is **202609**, and both selected columns contain **2.1**. This is a published y/y-rate table, so no 12-month calculation from rounded index levels is needed. Its [metadata listing](https://www.e-stat.go.jp/en/stat-search/files?layout=dataset&stat_infid=000040482953) also dates publication to **23 October, 08:30 JST**. This is the preferred key-free historical alternative, including for exact Japanese core CPI. It is the same issuer and is not an independent mirror or a historical first-release archive.

**OECD headline mirror, verified after structure discovery:**

```text
https://sdmx.oecd.org/public/rest/v1/data/OECD.SDD.TPS,DSD_PRICES_COICOP2018@DF_PRICES_C2018_ALL,1.0/JPN.M.N.CPI.PA._T.N.GY?startPeriod=2026-08&format=csvfilewithlabels
```

HTTP 200, **1,078 bytes**. `METHODOLOGY=N`, `MEASURE=CPI`, `UNIT_MEASURE=PA`, `EXPENDITURE=_T`, `ADJUSTMENT=N`, `TRANSFORMATION=GY`; **September = 2.1**. The old `DSD_PRICES@DF_PRICES_ALL` flow returns 404 for the 2026 window; a latest-observations query succeeds but ends in **June 2021**. This is a stale flow, not a general OECD access failure.

The returned new-flow expenditure list includes `_TXCP01_NRG`, all items excluding food and energy. That is **not** Japan's fresh-food-only exclusion. No exact independent core mirror was certified; use issuer CSV/XLSX instead. The Statistics Bureau [CPI information page](https://www.stat.go.jp/english/data/cpi/) records the August 2026 switch to 2025-base monthly publication, so parsers must validate the base and headings.

### U1. Unemployment

```text
https://www.e-stat.go.jp/en/stat-search/file-download?statInfId=000031831358&fileKind=0
```

HTTP 200, **283,616 bytes**. Although the download label says Excel, the payload is **XLSX**. It opens with `openpyxl`; `xlrd` rejects it as XLSX. Select the first sheet, **seasonally adjusted series** (`季節調整値`), whole Japan, both sexes, unemployment-rate column **T**. **T895 = 2.4**, September 2026; August is **2.5**. The workbook has separate original-series and seasonal-factor sheets; neither is the selected series. Preserve carried-forward year labels, including Japanese era labels. The [metadata listing](https://www.e-stat.go.jp/en/stat-search/files?layout=dataset&stat_infid=000031831358) records **30 October 2026, 08:30 JST**.

Working key-free mirror:

```text
https://sdmx.oecd.org/public/rest/v1/data/OECD.SDD.STES,DSD_KEI@DF_KEI,4.0/JPN.M.UNEMP.PT_LF._T.Y._Z?startPeriod=2026-08&format=csvfilewithlabels
```

HTTP 200, **925 bytes**, August **2.5**, September **2.4**. FRED metadata explicitly identifies `LRUNTTTTJPM156S` and `LRHUTTTTJPM156S` as age **15 years or over**, monthly, SA; both latest observations are **August = 2.5**. Those are definition-compatible candidate mirrors subject to edition reconciliation. `LRUN64TTJPM156S` is explicitly **15-64**, so it must not fill the national age-15+ card.

### H1. Household spending: selected consumption measure

Download [FIES Table 1, major household-budget figures](https://www.stat.go.jp/data/kakei/sokuhou/tsuki/zuhyou/fies_t1.xlsx): HTTP 200, **43,666 bytes**. Select the first sheet, Table 1, two-or-more-person households, consumption expenditures, section **Change over the year in % (Real)**. **I91 = -6.8** for September 2026. The supporting level **I39 = 289536** is nominal yen per household per month, and must not be presented as a real expenditure level. The [issuer summary](https://www.stat.go.jp/english/data/kakei/156.html) returned HTTP 200 and dates release to **10 November 2026**. This moving workbook needs archiving on each release if first-release evidence is required.

Household spending meets the consumption requirement without needing retail sales. Do not substitute workers' households, all-household quarterly data or a retail-sales index under the same card ID.

Retail alternative tested:

```text
https://sdmx.oecd.org/public/rest/v1/data/OECD.SDD.STES,DSD_STES@DF_INDSERV,4.3/JPN.M.TOVM.IX.G47.Y._Z._Z.N?lastNObservations=3&format=csvfilewithlabels
```

HTTP 200, latest **2026-06 = 100.1613**, index 2015=100; May **104.3079**, April **103.0348**. The corresponding OECD KEI directly supplied June m/m rate is **-3.9753317088%**. The mirror is behind the September issuer consumption release and is a different measure. FRED `JPNSLRTTO02IXOBSAM` is older still. No independent exact FIES mirror was certified, and no fresh METI retail route is claimed as tested.

### P1. BoJ policy rate

The exact target is the guideline for the uncollateralised overnight call rate. The [30 October BoJ statement](https://www.boj.or.jp/en/mopo/mpmdeci/mpr_2026/k261030a.pdf) returned HTTP 200, **175,859 bytes**, and states a target around **1.25%**. It is a text-based PDF, suitable for the hub's existing statement parser; the machine-readable time series comes from BIS:

```text
https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/D.JP?startPeriod=2026-10-01&format=csv
```

HTTP 200, **72,834 bytes**. The v1 equivalent `https://stats.bis.org/api/v1/data/WS_CBPOL/D.JP?startPeriod=2026-10-01&format=csv` also worked with an identical body hash. Select `FREQ=D`, `REF_AREA=JP`, `TIME_PERIOD`, `OBS_VALUE`; latest **2026-11-10 = 1.25**. `SOURCE_REF=Bank of Japan`. The compilation notes explicitly describe the post-24-September guideline as 1.25%. Retain those notes: Japan's policy framework and effective-date conventions change over history. For a monthly panel, use the last business-day target, not a monthly average of the actual market rate. BIS documents its [central-bank-policy-rate coverage and weekly update cadence](https://www.bis.org/statistics/cbpol.htm).

Issuer API alternatives were tested and rejected as definitions of this card:

```text
https://www.stat-search.boj.or.jp/api/v1/getDataCode?format=json&lang=en&db=IR01&code=MADR1Z@D&startDate=202610
https://www.stat-search.boj.or.jp/api/v1/getDataCode?format=json&lang=en&db=FM01&code=STRDCLUCON&startDate=202610
```

Both returned HTTP 200/application status 200. Latest non-null observations are **11 November: 1.5% basic discount/basic loan rate** and **11 November: 1.232% actual average overnight call rate**. FRED `IRSTCI01JPM156N` is likewise a market call-rate series, latest **September = 1.043%**. None is the 1.25% target. FRED `IRSTCB01JPM156N` is stale and must not be used as the current target.

## OECD structure discovery: verified dataflows, not guessed keys

These structure requests all returned HTTP 200 locally:

```text
https://sdmx.oecd.org/public/rest/v1/dataflow/OECD.SDD.STES/all/latest?references=none
https://sdmx.oecd.org/public/rest/v1/dataflow/OECD.SDD.STES/DSD_STES@DF_INDSERV/latest?references=all
https://sdmx.oecd.org/public/rest/v1/dataflow/OECD.SDD.STES/DSD_STES@DF_CLI/latest?references=all
https://sdmx.oecd.org/public/rest/v1/dataflow/OECD.SDD.STES/DSD_KEI@DF_KEI/latest?references=all
https://sdmx.oecd.org/public/rest/v1/dataflow/OECD.SDD.STES/DSD_STES@DF_BTS/latest?references=all
https://sdmx.oecd.org/public/rest/v1/dataflow/OECD.SDD.TPS/all/latest?references=none
https://sdmx.oecd.org/public/rest/v1/dataflow/OECD.SDD.TPS/DSD_PRICES@DF_PRICES_ALL/latest?references=all
https://sdmx.oecd.org/public/rest/v1/dataflow/OECD.SDD.TPS/DSD_PRICES_COICOP2018@DF_PRICES_C2018_ALL/latest?references=all
```

Discovered versions: industry **4.3**, CLI **4.1**, KEI **4.0**, BTS **4.0**, old/new prices **1.0**. The structure defines these series-key orders, excluding time:

| Structure | Ordered key dimensions |
|---|---|
| STES industry / CLI / BTS | `REF_AREA.FREQ.MEASURE.UNIT_MEASURE.ACTIVITY.ADJUSTMENT.TRANSFORMATION.TIME_HORIZ.METHODOLOGY` |
| KEI | `REF_AREA.FREQ.MEASURE.UNIT_MEASURE.ACTIVITY.ADJUSTMENT.TRANSFORMATION` |
| Prices, including COICOP 2018 | `REF_AREA.FREQ.METHODOLOGY.MEASURE.UNIT_MEASURE.EXPENDITURE.ADJUSTMENT.TRANSFORMATION` |

For discovery, country/frequency queries were used after reading the structure, then exact keys were tested separately for CLI, headline CPI, unemployment and IP. Parse CSV by column names because `csvfilewithlabels` includes both code and label columns. Pin a verified flow version and detect future structure changes rather than constructing unverified keys.

## FRED retest and rejected candidates

All FRED series/observation calls below returned HTTP 200. The API requires a free key already present on the machine. It was used **only by environment-variable reference** and was never printed or included in a report URL.

```python
import os

url = "https://api.stlouisfed.org/fred/series/observations"
params = {
    "series_id": series_id,
    "api_key": os.environ["FRED_API_KEY"],
    "file_type": "json",
    "sort_order": "desc",
    "limit": 3,
}
# Same common requests call as above.
# Metadata: https://api.stlouisfed.org/fred/series
# with series_id, file_type=json and the same environment-variable key.
```

| FRED series | Last downloaded non-missing observation/value | Assessment |
|---|---|---|
| `JPNLOLITOAASTSAM` | 2026-09: 100.347182439759 | Working CLI mirror; older/different snapshot than OECD current head |
| `JPNRGDPEXP` | 2026 Q2: 598950.5 | Working real GDP level; q/q must be derived and base/edition reconciled |
| `LRUN64TTJPM156S` | 2026-08: 2.6% | Working but **wrong age coverage** for national age-15+ unemployment |
| `LRUNTTTTJPM156S` | 2026-08: 2.5% | Working age-15+ candidate; one month behind issuer/OECD KEI |
| `LRHUTTTTJPM156S` | 2026-08: 2.5% | Working monthly age-15+ candidate; one month behind issuer/OECD KEI |
| `IRSTCI01JPM156N` | 2026-09: 1.043% | Working actual call rate, **wrong instrument** for policy target |
| `JPNPROINDMISMEI` | 2024-03: 92.31278, index | **Failing for current coverage**, stale |
| `JPNCPIALLMINMEI` | 2021-06: 101.29799999999999, index | **Failing for current coverage**. Initial last-3 query returned 2022 dates with `.` values; an expanded last-60 query found the last numeric value in Jun 2021. The apparent 2022 endpoint is not a usable observation. |
| `JPNSLRTTO02IXOBSAM` | 2023-10: 110.551259665752, index | **Failing for current coverage**, stale retail index |
| `IRSTCB01JPM156N` | 2023-12: 0.3% | **Failing for current coverage**, stale; not certified as today's policy target |

Additional failed/incomplete routes:

| Route | Local result | Consequence |
|---|---|---|
| Old OECD prices flow `DSD_PRICES@DF_PRICES_ALL,1.0/JPN.M......?startPeriod=2026-06&format=csvfilewithlabels` | HTTP **404**. `lastNObservations=2` instead returns HTTP 200, ending Jun 2021 | Use the discovered COICOP 2018 flow for current headline CPI |
| OECD CLI schedule webpage | HTTP **403** to requests, readable via web browsing | API access is working; publication-date evidence must be sourced separately |
| e-Stat JSON API without `appId` | HTTP **200**, application `RESULT.STATUS=100`, authentication failed | **Needs key** for API, while public CSV/XLSX works without one |
| Exploratory BoJ flat-file path `/ssi/mtshtml/bs_original_tankan.html` | HTTP **404** | No flat-file route certified; exact Tankan JSON/CSV API works |

## e-Stat registration, if the owner wants its API

Exact unauthenticated test:

```text
https://api.e-stat.go.jp/rest/3.0/app/json/getStatsList?searchWord=consumer+price+index&limit=1
```

It returned authentication error status 100 inside an HTTP 200 response. An authenticated download was **not** tested, and no e-Stat API table/filter mapping is claimed as verified.

The owner can [register for free](https://www.e-stat.go.jp/mypage/user/preregister), verify the registration email, log into My Page and use **API / Issue Application ID**. The [official user guide](https://www.e-stat.go.jp/api/api/api/index.php/en/api-info/api-guide) permits a localhost URL for a non-public application. Keep the ID in an environment variable, for example `ESTAT_APP_ID`, and pass it as `appId` without logging it. Discover `statsDataId` via `getStatsList`, then inspect `getMetaInfo` before constructing `getStatsData` category filters. A `statInfId` public-file ID must not be assumed to equal an API `statsDataId`.

Free registration would add an optional API route; **it is not a blocker for any of the nine selected indicators**.

## Repository findings and proposed integration

The requested configuration paths are actually under **`market_data_hub/config/`**, rather than a root-level `config/` directory. Read-only inspection covered `macro_releases/japan_macro.py`, `boj_policy.py`, `catalogue.py`, `adapters.py`, `models.py`, `fred.py`, `eurostat.py`, `nbp_policy.py`, `market_data_hub/config/macro_series.yaml` and `market_data_hub/config/concepts/*.yaml`.

The hub already has cards for GDP, both national CPIs, Tankan and BoJ decisions. `japan_macro.py` currently reads reviewed GDP CSVs, CPI monthly workbooks and Tankan PDFs. `boj_policy.py` already extracts the target from dated issuer statements. Its existing card ID is **`boj_policy_rate_decision`**. Reuse it instead of creating a duplicate `jp_policy_rate_decision` card. The existing central-bank frequency is `E` (event), which differs from a daily policy-state series.

Eurostat provides the pattern for validated dimensions, units, estimate stages and dated release evidence. Its parsers do not infer a first publication from a dataset update timestamp. NBP provides the pattern for selecting one exact instrument, retaining effective/publication dates and failing when labels/schema change. Extend those conventions; the Japan data does not belong in the Eurostat or NBP adapters themselves.

### Cards and adapters

| Indicator / proposed card ID | Existing or new | Adapter proposal | Primary evidence and parsing rule |
|---|---|---|---|
| `jp_gdp_qq` | Existing | Extend `japan_macro.py` edition/menu discovery | G1 ESRI published q/q CSV; retain preliminary vs second estimate and dated archive binding |
| `jp_industrial_production_mm` | New | Add `meti_activity.py`, called by the Asia adapter | I1 METI published HTML rate; workbook validates coverage/index and provides derived history. Preserve preliminary flag `p`. Use mining/manufacturing, not OECD `BTE`. |
| `jp_oecd_cli` | New if a card is wanted; concept/history can use macro source alone, following current CLI practice | Add shared `oecd_sdmx.py` data client/collector; do not invent historical release events from a current CSV | L1 exact OECD amplitude-adjusted CLI key; store it as issuer provider history with retrieval/vintage provenance |
| `jp_tankan_large_manufacturers_di` | Existing | Add shared `boj_timeseries.py` API helper and extend `japan_macro.py` Tankan route | T1 exact actual-result code. Keep issuer summary for release-edition binding and existing PDF fallback. API is best for numeric history. |
| `jp_cpi_yy` | Existing | Extend `japan_macro.py` with validated long-CSV history/backup | C1 monthly workbook for dated release; long CSV group **0001** for current revised history |
| `jp_core_cpi_yy` | Existing | Same extension | C1 monthly workbook / long CSV group **0161**. Exclusion must be fresh food only. |
| `jp_unemployment_rate` | New | Extend `japan_macro.py` with LFS workbook parser | U1 national age-15+, both sexes, SA rate; bind current snapshot to issuer listing, not fabricated past releases |
| `jp_household_spending_yy` | New | Extend `japan_macro.py` with FIES Table 1 parser | H1 real y/y, two-or-more-person households. Archive moving source file on each collection. |
| `boj_policy_rate_decision` | Existing | Retain/extend `boj_policy.py`; add separate BIS policy-history collector/helper | P1 statement is primary for event/target; BIS `D.JP` supplies daily state/history. Do not infer no-change meeting events from a flat daily series. |

### Concepts, primary and secondary sources

Use `family: releases`, `calendar: country:JP`, units/frequencies matching the definitions below. Existing country release concepts use lowercase card-like concept IDs; existing OECD CLI concepts use country-prefixed names such as `DE_OECD_CLI`, so **`JP_OECD_CLI`** follows that convention. The uppercase macro IDs below are **proposed new symbols**, not currently configured or implemented.

| Concept | Primary source | Secondary source / qualification | Unit and frequency |
|---|---|---|---|
| `jp_gdp_qq` | `kind: release`, `id: jp_gdp_qq`, issuer ESRI | `kind: macro`, proposed `JP_REAL_GDP_QQ_FRED_DERIVED`, computed from `JPNRGDPEXP`; derived, edition/base review required. Raw level cannot directly fill a rate concept. | %, Q, SA q/q |
| `jp_industrial_production_mm` | `kind: release`, `id: jp_industrial_production_mm`, issuer METI | Same-issuer Japanese workbook, proposed derived `JP_METI_IP_MM`, if published HTML is unavailable. OECD `BTE` is comparison-only until scope is explicitly changed. | %, M, SA |
| `JP_OECD_CLI` | `kind: macro`, proposed `JP_OECD_CLI_SDMX`, exact L1 key | `kind: macro`, `JPNLOLITOAASTSAM` via FRED, with age/vintage and discrepancy checks. Suggested history mode: single primary series; do not splice different snapshots silently. | Index, M, amplitude adjusted |
| `jp_tankan_large_manufacturers_di` | `kind: release`, existing card ID, BoJ API bound to summary edition | Existing same-issuer PDF route as parser fallback; optional proposed `JP_TANKAN_LARGE_MFG_DI` for revised API history. No independent exact mirror certified. | Percentage-point DI, Q |
| `jp_cpi_yy` | `kind: release`, `id: jp_cpi_yy`, national issuer monthly workbook | `kind: macro`, proposed `JP_CPI_YY_OECD`, exact C2018 headline key; same-issuer long CSV is an additional backup. | %, M, NSA y/y |
| `jp_core_cpi_yy` | `kind: release`, `id: jp_core_cpi_yy`, issuer monthly workbook | `kind: macro`, proposed `JP_CORE_CPI_YY_STATJP`, direct rate from long CSV code 0161; mark same issuer. No exact independent mirror certified. | %, M, NSA y/y |
| `jp_unemployment_rate` | `kind: release`, `id: jp_unemployment_rate`, issuer LFS workbook | `kind: macro`, proposed `JP_UNEMPLOYMENT_RATE_OECD` from KEI; FRED `LRUNTTTTJPM156S` is an additional, lagged age-compatible candidate. | %, M, SA, age 15+ |
| `jp_household_spending_yy` | `kind: release`, `id: jp_household_spending_yy`, issuer FIES | **No independent secondary certified.** A proposed macro history symbol `JP_HOUSEHOLD_SPENDING_YY_STATJP` can retain archived same-issuer FIES snapshots; it does not add source independence. Do not use retail as its secondary. | %, M, real NSA y/y |
| `jp_policy_rate` | `kind: release`, `id: boj_policy_rate_decision`, BoJ statement | `kind: macro`, proposed `BIS_JP_POLICY_RATE`, `WS_CBPOL / D.JP`. Resolve event state and daily observations using effective dates; use last business day for monthly panels. | % p.a., policy state from E / D inputs |

In `market_data_hub/config/macro_series.yaml`, add missing useful FRED inputs under the existing FRED grouping: `JPNLOLITOAASTSAM`, `JPNRGDPEXP`, and age-compatible `LRUNTTTTJPM156S`, after checking for duplicates. The non-FRED primary symbols need actual collectors/source dispatch and explicit issuer/distributor metadata before being configured. A YAML entry alone will not download OECD, BoJ, BIS or Statistics Bureau data.

Suggested initial expected issuer lags, to calibrate against a longer release history before implementation: GDP **48 days first / 70 days second**, IP **30-35 days**, CPI **23 days**, unemployment **30-35 days**, FIES **41-45 days**, CLI **up to 45 days conservatively** with its actual schedule, Tankan **1 day after quarter end** and an extra day possible in the API, policy decisions **0 days**. Allow BIS a **weekly** update cadence. These are not proposed `max_age_business_days` settings: the concept resolver's age anchor and business-day calendar need to be checked separately. Existing CLI concepts use 60 business days; reuse that only after checking the newer OECD primary cadence.

Keep issuer and redistributor evidence distinct, as the current `Observation`/`SourceSeries` contracts require. Retain reference period, estimate stage, effective date, first-publication date/precision, provider update date and retrieved-at separately. Store computed rates with their inputs/formula and `published=False`. Current-history snapshots cannot establish the as-published history for earlier release events. Event-driven policy state should be refreshed by meeting schedule/effective dates, rather than expiring just because an unchanged target has an old decision date. Add any future implemented series to the hub's generated data catalogue through its normal workflow.

## Open questions for the owner

1. **Consumption choice:** accept real FIES spending y/y for two-or-more-person households as the required consumption column? It is fully working and key-free. If retail sales is preferred, specify nominal y/y versus real volume m/m and run a separate issuer retail test before substituting it.
2. **IP definition:** retain METI's familiar mining-and-manufacturing SA m/m measure, as proposed? OECD broad industry is available but differs in scope, adjustment and current value. It should have a separate card if wanted.
3. **Policy representation:** use the BoJ target as proposed, with existing decision events plus BIS daily/month-end state? Actual call rate and basic discount rate can be separate optional indicators.
4. **Mirror disagreements:** what tolerance should trigger owner review for CLI revisions and other current-history discrepancies? The live CLI same-month difference is already large enough to record explicitly. Proposed default: preserve both, prefer the issuer and prohibit silent vintage splicing.
5. **Historical release coverage:** is revised current history sufficient for step 2, or are historical first-release vintages required? GDP edition CSVs and CPI monthly files support part of this; moving METI/FIES/LFS files and current OECD/BoJ API histories need collection-time archiving and separate release evidence.
6. **Optional e-Stat API:** does the owner want to register a free appId for a future API adapter? There is no present coverage blocker, and authenticated table IDs/filters would still need a live test after registration.

These questions affect the next implementation step. They do not prevent the tested source coverage documented here.
