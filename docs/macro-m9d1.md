# Japan and China official publications (M9d-1)

Base: v0.3.9 (`a4d4e6a`). The external catalogue supplied with the brief has
Japan national CPI, GDP, Tankan, industrial production and trade, and China GDP,
CPI, official NBS PMI, industrial production and retail sales. Policy decisions
and Caixin are excluded. Tokyo core CPI is an explicit brief addition. No rows
for Japan labour, household spending or retail, or China PPI, fixed investment or
trade were present in that catalogue; no definitions for those are invented.

The checkout's approved classification is v2.3, including owner-approved v2.1
concepts. Existing exact `cat:` identifiers already present in the approved
table are reused. Missing concepts are reported rather than created.

## Source and edition policy

Dates authorize fetching; a calendar never supplies a numeric observation.
All data are issuer-printed levels or rates, with stages separate from measure
identities. Unknown dates, source URLs, columns, clocks and titles fail closed
with `needs review`. Reviewed calendars end on 2026-12-31.

NBS English translations can post after the domestic release. Saved English
publications retain their own publication dates and clocks. The domestic
calendar supplies future permissions, which still require exact issuer
publication metadata before certification; a delayed unreviewed English edition
is not silently assigned the scheduled domestic date.

| Family | Collected measure | Issuer / source class | Reviewed editions | Golden publications |
|---|---|---|---|---|
| cn_cpi | National CPI y/y, NSA, % | NBS English / official | Sep 10, Oct 15; Nov 9, Dec 9 scheduled | Aug and Sep 2026 |
| cn_nbs_pmi | Official manufacturing PMI, SA, index (50 neutral) | NBS English / official | Sep 1, Oct 1; Oct 31, Nov 30, Dec 31 scheduled | Aug and Sep 2026 |
| cn_activity | Industrial value-added output and consumer-goods retail sales, monthly y/y, NSA, % | NBS English national economy / official | Sep 15, Oct 19; Nov 16, Dec 15 scheduled | Aug and Sep 2026 (4 cases) |
| cn_gdp | Real GDP cumulative y/y, NSA, % | NBS English national economy and original Chinese accounting tables / official | Jul 16 (Q2), Oct 19 first Q3 announcement, Oct 20 Q3 accounting; next edition January 2027 | Q2 4.7%, Q3 4.6% on both Oct 19 and 20 (3 cases) |
| jp_gdp | Real GDP q/q, SA, % | Cabinet Office ESRI English summaries / official | Aug 17 preliminary, Sep 8 revised; Nov 16 preliminary, Dec 8 revised scheduled | Q2 first 0.3%, second 0.4% |
| jp_cpi | National headline and ex-fresh-food CPI y/y, NSA, % | Statistics Bureau / e-Stat English monthly reports / official | Sep 18, Oct 23; Nov 20, Dec 18 scheduled | Aug and Sep 2026 (4 cases) |
| jp_tokyo_cpi | Ku-area of Tokyo ex-fresh-food CPI y/y, NSA, % | Statistics Bureau / e-Stat English monthly reports / official | Aug 28, Oct 2 preliminary; Oct 30, Nov 27, Dec 25 scheduled | Aug 1.8%, Sep 2.7% |
| jp_tankan | Large manufacturers current business-conditions DI, percentage points | BoJ English summary / official | Jul 1, Oct 1; Dec 14 scheduled | Q2 22, Q3 24 |
| jp_trade_balance | Customs-cleared goods balance, NSA, billion JPY | MOF / Japan Customs English releases / official | Sep 29 revised, Oct 21 preliminary; Oct 29, Nov 18/27, Dec 16/25 scheduled | Aug -1,111.913; Sep -536.558 |

Customs prints the balance in millions of JPY; division by 1,000 is only a unit
conversion to the catalogue's billions. Exports minus imports is never computed.
The September 29 report explicitly says exports detailed / imports provisional
(HS-code basis), so it is `revised`, not the catalogue's generic `final` stage.
The calendar's detailed releases receive this same honest stage; fully final
imports/balances are not certified from a partially provisional publication.

Tankan is an official BoJ survey and is in scope separately from policy
decisions. The source table does not label seasonal adjustment; its metadata
therefore says `not-stated`, rather than certifying the catalogue's unconfirmed
`SA` flag. The printed actual DI is collected directly, never the forecast or
the change column. This source/catalogue metadata difference is explicit.

Tokyo tables include the next month's mid-month preliminary figure in a report
listed under the preceding national survey month. The English note explicitly
labels the latest month preliminary; the parser requires that note and the
exact Tokyo table header. Tokyo core reuses the approved `jp_tokyo_cpi` concept.

e-Stat CPI files are bound to reviewed monthly-report file IDs. Collection also
checks the issuer listing's exact geography, monthly table, survey month,
reviewed publication counter (national 1, Tokyo preliminary addition 2) and
calendar-matching date. An update timestamp alone
does not authorize an edition. Only the explicitly printed annual-change sheet
is read; no rates are computed from index levels. The 2025-base revision is
explicit in both the source listing and table headers.

The brief explicitly requests preliminary Q3 GDP. Its stage is `preliminary`,
correcting the external catalogue's generic `final` stage without changing the
indicator identity. The catalogue's **cumulative** definition is preserved:
Q3's 4.6% is January–September, distinct from standalone Q3's 4.5%.
The original Chinese GDP table is used to preserve the October 20 edition;
the English accounting translation is dated October 21. The first GDP
announcement in the October 19 English national-economy release is retained
as its own earlier edition, with the same printed cumulative 4.6%. Header mapping:
`绝对额(亿元)` = current-price levels; `比上年同期增长(%)` = annual growth;
`三季度` = standalone Q3; `前三季度` = first three quarters; `上半年` = first half.
Only the last GDP cell (cumulative growth at constant prices) is collected.

Calendar: <https://www.stats.gov.cn/english/PressRelease/ReleaseCalendar/202512/t20251226_1962154.html>.
Each saved fixture has its issuer URL, retrieval timestamp and SHA-256.

## Availability findings

METI's catalogue URL returned HTTP 403 from this server on 2026-10-27.
Industrial production collection is stopped for this issuer; no alternative
endpoint, redistributor, browser session or bot-protection bypass is attempted.

The Statistics Bureau CPI calendar lists October Tokyo preliminary CPI for
2026-10-30, not 2026-10-22. A numeric Tokyo card dated October 22 would require
an issuer publication proving that edition.
Calendar: <https://www.stat.go.jp/english/data/cpi/1582.htm>.

## Verification on 2026-10-27

`scripts/verify_macro_m9d1.py` returned `ok` on a stable production-byte snapshot
and SQLite backup under `D:\Temp\mdh-m9d1-resume-live-20261027\mdh-m9d1-ym00zx9f`.
It added 23 vintages across all nine families; the second collection added zero.
The source main/WAL/SHM fingerprints and all pre-existing vintage fingerprints
were unchanged; the copy's SQLite integrity check returned `ok`.

The 2026-10-19..27 UTC read window contained official cards for China industrial
output (5.5%) and retail sales (1.1%), China cumulative Q3 GDP (4.6%, October 19
first announcement and October 20 preliminary accounting), Japan trade
(-536.558 billion JPY), and Japan national headline
and core CPI (both 2.1%). Japan's October 23 08:30 JST CPI release appears at
October 22 23:30 UTC; it is national CPI, separate from Tokyo preliminary CPI.

Approved-card coverage is 93/93, with `unmapped: []` and no card gaps. The
blocked METI collection and absent catalogue rows above remain explicit scope
limitations. Future permissions extend through December 31; any as-yet
unreviewed archive ID, rescheduled translation date or table format stops with
`needs review`, even if a calendar date has arrived.
